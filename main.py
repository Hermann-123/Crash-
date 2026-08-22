"""
╔════════════════════════════════════════════════════════════════════════════╗
║   TERMINAL PRIME V55 — GROQ + EXÉCUTION RÉELLE DERIV + PANNEAU CONTRÔLE     ║
║                                                                            ║
║  ⚙️ NOUVEAU DANS CETTE VERSION :                                          ║
║   • Exécution réelle via l'API Deriv (gratuite, pas de MT5/VPS/MetaApi)   ║
║   • Panneau de contrôle Telegram (/menu) : auto-trading ON/OFF,           ║
║     mode SOLO/MULTI, status live, stop d'urgence, réglage de la mise      ║
║   • Quand auto-trading est ON : le bot ouvre/ferme les trades TOUT SEUL   ║
║     sur ton compte Deriv dès qu'un signal passe le calcul + Groq.         ║
║   • Quand auto-trading est OFF : comportement notification + bouton       ║
║     "Copier" manuel.                                                      ║
╚════════════════════════════════════════════════════════════════════════════╝
"""

import os
import datetime
import random
import time
import string
import json
import math
import websocket
import pandas as pd
import ta
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Lock
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# CONFIGURATION ET PURGE DES ESPACES (.strip)
# ==========================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8658287331:AAFGW1gnfIdqMDwiUxMwYpBfUnNrIJ_3Ons").strip()
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X").strip()

DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN", "").strip()
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089").strip()

# ==========================================
# RISK MANAGEMENT — CONFIGURATION GLOBALE
# ==========================================

RISK_CONFIG = {
    "risk_per_trade_pct": 1.0,
    "daily_loss_limit_pct": 5.0,
    "max_consecutive_losses": 3,
    "pause_duration_minutes": 120,
    "partial_tp_ratio": 0.85,
    "breakeven_buffer_pct": 0.0005,
    "trailing_stop_activation_rr": 1.0,
    "trailing_stop_distance_pct": 0.003,
    "max_trades_per_day": 8,
    "max_trade_age_hours": 12,
    "signal_validity_seconds": 45,
    "max_rr_degradation_pct": 40,
}

# ==========================================
# ÉTAT DU PANNEAU DE CONTRÔLE
# ==========================================

CONTROL_STATE = {
    "auto_trading_active": False,
    "mode": "SOLO",
    "stake_usd": 1.0,
    "multiplier": 10,
    "stop_urgence_actif": False,
}

# ==========================================
# ÉTATS DE TRADE
# ==========================================

class TradeState(Enum):
    SIGNAL_SENT     = "SIGNAL_ENVOYÉ"
    TRADE_OPEN      = "TRADE_OUVERT"
    TRADE_PARTIAL   = "TP1_PARTIEL_BE"
    TRADE_WIN       = "GAGNÉ"
    TRADE_LOSS      = "PERDU"
    CANCELLED       = "ANNULÉ"

# ==========================================
# LISTES DE PAIRES
# ==========================================

VOLATILE_PAIRS  = ["V10","V25","V50","V75","V100"]
COMMODITY_PAIRS = ["XAUUSD","XAGUSD"]
FOREX_PAIRS     = ["AUDUSD","CADJPY","CHFJPY","EURJPY","USDCAD","AUDJPY",
                   "EURAUD","EURUSD","AUDCAD","USDCHF","CADCHF","EURCHF",
                   "USDJPY","GBPUSD"]

ELITE_PAIRS_MT5 = VOLATILE_PAIRS + COMMODITY_PAIRS
ALL_PAIRS       = VOLATILE_PAIRS + COMMODITY_PAIRS + FOREX_PAIRS

NOMS_AFFICHAGE = {
    "XAUUSD":"🥇 GOLD","XAGUSD":"🥈 ARGENT",
    "V10":"🔥 V10","V25":"🔥 V25","V50":"🔥 V50",
    "V75":"⚡ V75","V100":"💥 V100",
}

# ==========================================
# VARIABLES D'ÉTAT GLOBALES
# ==========================================

user_prefs           = {}
plateforme_trading   = {}
utilisateurs_actifs  = set()
derniere_alerte_auto = {}
signaux_cache        = {}

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees           = {}

volatility_pairs_active = {
    "V10": True, "V25": True, "V50": True, "V75": True, "V100": True,
}

trades_actifs     = {}
trades_historique = {}
prix_broker       = {}

pnl_total  = {}
win_count  = {}
loss_count = {}

contexte_marche_cache = {}
daily_stats = {}
lock_trade = Lock()

# ==========================================
# KEEP ALIVE (FLASK)
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime V55 — Deriv Edition"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# PONT DERIV API (WEBSOCKETS)
# ==========================================

DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

def _deriv_request(payload, timeout=10):
    token_clean = DERIV_API_TOKEN.strip()
    if not token_clean:
        raise RuntimeError("DERIV_API_TOKEN manquant ou vide sur Render.")

    cle_attendue = None
    for k in ("buy", "sell", "portfolio", "proposal_open_contract",
              "contract_update", "balance", "proposal"):
        if k in payload:
            cle_attendue = k
            break

    ws = None
    try:
        ws = websocket.create_connection(DERIV_WS_URL, timeout=timeout)
        ws.send(json.dumps({"authorize": token_clean}))
        auth_resp = json.loads(ws.recv())
        if auth_resp.get("error"):
            raise RuntimeError(f"Deriv authorize refusé : {auth_resp['error'].get('message')}")

        ws.send(json.dumps(payload))
        debut = time.time()
        while time.time() - debut < timeout:
            resp = json.loads(ws.recv())
            if resp.get("error"):
                raise RuntimeError(f"Deriv API erreur : {resp['error'].get('message')}")
            if cle_attendue and cle_attendue in resp:
                return resp
            if resp.get("msg_type") == cle_attendue:
                return resp
        raise TimeoutError(f"Deriv: pas de réponse '{cle_attendue}' sous {timeout}s")
    finally:
        try:
            if ws: ws.close()
        except Exception:
            pass

def deriv_symbole(symbole_bot):
    return prefixer_symbole(symbole_bot)

def deriv_ouvrir_contrat(symbole, direction, stake, multiplier, sl=None, tp=None):
    sym = deriv_symbole(symbole)
    contract_type = "MULTUP" if direction.upper() == "BUY" else "MULTDOWN"

    limit_order = {}
    if sl is not None:
        limit_order["stop_loss"] = round(float(sl), 5)
    if tp is not None:
        limit_order["take_profit"] = round(float(tp), 5)

    payload = {
        "buy": 1,
        "price": round(stake, 2),
        "parameters": {
            "amount": round(stake, 2),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "symbol": sym,
            "multiplier": multiplier,
        }
    }
    if limit_order:
        payload["parameters"]["limit_order"] = limit_order

    resp = _deriv_request(payload)
    buy_info = resp.get("buy", {})
    contract_id = buy_info.get("contract_id")
    print(f"[Deriv] Contrat ouvert {sym} {contract_type} mise={stake} → contract_id={contract_id}", flush=True)
    return contract_id

def deriv_modifier_contrat(contract_id, sl=None, tp=None):
    limit_order = {}
    if sl is not None:
        limit_order["stop_loss"] = round(float(sl), 5)
    if tp is not None:
        limit_order["take_profit"] = round(float(tp), 5)
    payload = {"contract_update": 1, "contract_id": contract_id, "limit_order": limit_order}
    return _deriv_request(payload)

def deriv_fermer_contrat(contract_id):
    payload = {"sell": contract_id, "price": 0}
    return _deriv_request(payload)

def deriv_statut_contrat(contract_id):
    payload = {"proposal_open_contract": 1, "contract_id": contract_id}
    resp = _deriv_request(payload)
    return resp.get("proposal_open_contract", {})

def deriv_positions_ouvertes():
    payload = {"portfolio": 1}
    resp = _deriv_request(payload)
    return resp.get("portfolio", {}).get("contracts", [])

def deriv_connecter():
    payload = {"balance": 1}
    resp = _deriv_request(payload)
    solde = resp.get("balance", {})
    print(f"[Deriv] Connecté — solde: {solde.get('balance')} {solde.get('currency')}", flush=True)
    return solde

def peut_ouvrir_automatiquement(symbole):
    if not CONTROL_STATE["auto_trading_active"]:
        return False
    if CONTROL_STATE["stop_urgence_actif"]:
        return False
    if CONTROL_STATE["mode"] == "SOLO":
        return len(trades_actifs) == 0
    return symbole not in {t.get("symbol") for t in trades_actifs.values()}

# ==========================================
# UTILITAIRES PRIX
# ==========================================

def prefixer_symbole(s):
    mapping = {"XAUUSD":"frxXAUUSD","XAGUSD":"frxXAGUSD"}
    if s in mapping:
        return mapping[s]
    if s in VOLATILE_PAIRS:
        return f"R_{s.replace('V','')}"
    return f"frx{s}"

_candles_cache = {}
_candles_cache_lock = Lock()
CANDLES_CACHE_TTL = 20

def _obtenir_donnees_deriv_reseau(symbole_brut, granularite=300):
    if symbole_brut in ALL_PAIRS:
        tf_map = {300: "5min", 900: "15min", 3600: "1hour"}
        tf = tf_map.get(granularite, "4hour")
        mapping_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}
        sym_fmp = mapping_fmp.get(symbole_brut, symbole_brut)
        try:
            url = f"https://financialmodelingprep.com/api/v3/historical-chart/{tf}/{sym_fmp}?apikey={FMP_API_KEY}"
            res = requests.get(url, timeout=3).json()
            if isinstance(res, list) and len(res) > 0:
                bougies = []
                for idx, b in enumerate(reversed(res[:250])):
                    epoch_val = None
                    date_str = b.get("date")
                    if date_str:
                        try:
                            epoch_val = int(datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").timestamp())
                        except Exception:
                            try:
                                epoch_val = int(datetime.datetime.strptime(date_str, "%Y-%m-%d").timestamp())
                            except Exception:
                                epoch_val = None
                    if epoch_val is None:
                        epoch_val = int(time.time()) - (250 - idx) * granularite
                    bougies.append({
                        "open":  float(b["open"]), "high":  float(b["high"]),
                        "low":   float(b["low"]), "close": float(b["close"]),
                        "epoch": epoch_val
                    })
                return bougies
        except Exception:
            pass

    sym = prefixer_symbole(symbole_brut)
    gran_real = granularite if granularite in (60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400) else 14400
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect(f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}", timeout=4)
            ws.send(json.dumps({"ticks_history": sym, "end": "latest", "count": 250, "style": "candles", "granularity": gran_real}))
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res:
                return res["candles"]
        except Exception:
            try: ws.close()
            except Exception: pass
            time.sleep(0.2)
    return None

def obtenir_donnees_deriv(symbole_brut, granularite=300):
    cle = (symbole_brut, granularite)
    now = time.time()
    with _candles_cache_lock:
        cached = _candles_cache.get(cle)
        if cached and (now - cached[0]) < CANDLES_CACHE_TTL:
            return cached[1]
    data = _obtenir_donnees_deriv_reseau(symbole_brut, granularite)
    if data is not None:
        with _candles_cache_lock:
            _candles_cache[cle] = (now, data)
    return data

def obtenir_donnees_h4(symbole):
    data = obtenir_donnees_deriv(symbole, 14400)
    if data and len(data) > 20: return data
    h1 = obtenir_donnees_deriv(symbole, 3600)
    if not h1 or len(h1) < 8: return None
    agg = []
    for i in range(0, len(h1) - 3, 4):
        chunk = h1[i:i+4]
        agg.append({
            "open":  float(chunk[0]["open"]),
            "high":  max(float(c["high"]) for c in chunk),
            "low":   min(float(c["low"])  for c in chunk),
            "close": float(chunk[-1]["close"]),
            "epoch": int(time.time())
        })
    return agg

def obtenir_prix_broker_realtime(symbole):
    try:
        mapping_fmp = {"XAUUSD":"FOREX:XAUUSD","XAGUSD":"FOREX:XAGUSD"}
        sym_fmp = mapping_fmp.get(symbole, symbole)
        url = f"https://financialmodelingprep.com/api/v3/quote/{sym_fmp}?apikey={FMP_API_KEY}"
        res = requests.get(url, timeout=3).json()
        if isinstance(res, list) and len(res) > 0:
            prix = float(res[0]["price"])
            prix_broker[symbole] = {"price": prix, "source": "FMP", "timestamp": time.time()}
            return prix
    except Exception:
        pass

    sym = prefixer_symbole(symbole)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect(f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}", timeout=3)
            ws.send(json.dumps({"ticks": sym}))
            res = json.loads(ws.recv())
            ws.close()
            if "tick" in res:
                prix = float(res["tick"]["quote"])
                prix_broker[symbole] = {"price": prix, "source": "Deriv", "timestamp": time.time()}
                return prix
        except Exception:
            try: ws.close()
            except Exception: pass
            time.sleep(0.5)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real: return False
    decalage = abs(prix_bot - prix_real) / prix_real
    return decalage <= tolerance

# ==========================================
# GESTION DU RISQUE ET STATS
# ==========================================

def get_today_str(): return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def init_daily_stats(uid):
    today = get_today_str()
    if uid not in daily_stats or daily_stats[uid]["date"] != today:
        daily_stats[uid] = {
            "date": today, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0,
            "consecutive_losses": 0, "paused_until": None,
            "best_trade": 0.0, "worst_trade": 0.0,
        }
    return daily_stats[uid]

def est_autorise(uid):
    if uid == ADMIN_ID: return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[uid]
    return False

def utilisateur_peut_trader(uid):
    stats = init_daily_stats(uid)
    if stats["pnl"] <= -(CAPITAL_ACTUEL * RISK_CONFIG["daily_loss_limit_pct"] / 100.0):
        return False, "🛑 Limite de perte journalière atteinte."
    if stats["paused_until"] and time.time() < stats["paused_until"]:
        return False, "⏸️ Pause anti-tilt active."
    if stats["trades"] >= RISK_CONFIG["max_trades_per_day"]:
        return False, "🛑 Limite de trades/jour atteinte."
    return True, None

def calculer_position_size(capital, risk_pct, prix_entree, prix_sl, symbole):
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)
    if distance_sl <= 0: return {"montant_risque": montant_risque, "lot_factor": 0, "distance_sl": 0}
    lot_factor = montant_risque / distance_sl
    return {"montant_risque": round(montant_risque, 2), "lot_factor": round(lot_factor, 4), "distance_sl": round(distance_sl, 5)}

def enregistrer_resultat_trade(uid, pnl, win, pnl_pour_bilan=None):
    stats = init_daily_stats(uid)
    stats["pnl"] += pnl
    stats["trades"] += 1
    valeur_bilan = pnl_pour_bilan if pnl_pour_bilan is not None else pnl
    if win:
        stats["wins"] += 1
        stats["consecutive_losses"] = 0
        win_count[uid] = win_count.get(uid, 0) + 1
    else:
        stats["losses"] += 1
        stats["consecutive_losses"] += 1
        loss_count[uid] = loss_count.get(uid, 0) + 1
    stats["best_trade"] = max(stats["best_trade"], valeur_bilan)
    stats["worst_trade"] = min(stats["worst_trade"], valeur_bilan)
    if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
    return stats

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance,
                 label="SIGNAL", strategie_nom_ia="?", ia_score=None, gemini_score=None,
                 contexte_marche=None, executer_reel=False):
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"], entry_price, sl, symbole)

    deriv_contract_tp1 = None
    deriv_contract_final = None
    if executer_reel:
        stake_total = CONTROL_STATE["stake_usd"]
        multiplier = CONTROL_STATE["multiplier"]
        stake_tp1 = round(stake_total * RISK_CONFIG["partial_tp_ratio"], 2)
        stake_final = round(stake_total - stake_tp1, 2)

        deriv_contract_tp1 = deriv_ouvrir_contrat(symbole, direction, stake_tp1, multiplier, sl=sl, tp=tp1)
        deriv_contract_final = deriv_ouvrir_contrat(symbole, direction, stake_final, multiplier, sl=sl, tp=tp_final)

    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole, "direction": direction, "entry_price": entry_price,
        "sl": sl, "sl_original": sl, "tp1": tp1, "tp_final": tp_final, "strategy": strategy,
        "confiance": confiance, "label": label, "state": TradeState.TRADE_OPEN,
        "timestamp_open": time.time(), "partial_closed": False, "partial_pnl": 0.0,
        "breakeven_active": False, "trailing_active": False, "sizing": sizing,
        "deriv_contract_tp1": deriv_contract_tp1, "deriv_contract_final": deriv_contract_final,
        "reel": executer_reel,
    }
    return trade_id, sizing

def fermer_trade_complet(uid, exit_price, win):
    with lock_trade:
        if uid not in trades_actifs: return None
        trade = trades_actifs[uid]
        try:
            if trade.get("reel") and trade.get("deriv_contract_final"):
                try:
                    statut = deriv_statut_contrat(trade["deriv_contract_final"])
                    if not statut.get("is_sold"):
                        deriv_fermer_contrat(trade["deriv_contract_final"])
                except Exception as e:
                    print(f"[Deriv Erreur Clôture] {e}", flush=True)

            portion_restante = (1 - RISK_CONFIG["partial_tp_ratio"]) if trade.get("partial_closed") else 1.0
            risque_portion = trade["sizing"]["montant_risque"] * portion_restante
            if win:
                gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
                pnl_final = risque_portion * gain_ratio
            else:
                pnl_final = -risque_portion

            pnl_trade_total = trade.get("partial_pnl", 0.0) + pnl_final
            trade["state"] = TradeState.TRADE_WIN if win else TradeState.TRADE_LOSS
            
            if uid not in trades_historique: trades_historique[uid] = []
            trades_historique[uid].append({
                "trade_id": trade["trade_id"], "symbol": trade["symbol"], "direction": trade["direction"],
                "entry": trade["entry_price"], "exit": exit_price, "pnl": pnl_trade_total,
                "win": win, "timestamp": time.time(), "label": trade.get("label","")
            })
            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
            enregistrer_resultat_trade(uid, pnl_final, win, pnl_pour_bilan=pnl_trade_total)
            return {"trade_id": trade["trade_id"], "pnl": pnl_trade_total, "win": win}
        except Exception: return None
        finally: trades_actifs.pop(uid, None)

def fermer_trade_partiel(uid, exit_price):
    with lock_trade:
        if uid not in trades_actifs: return None
        trade = trades_actifs[uid]
        if trade["partial_closed"]: return None
        try:
            gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
            pnl_partiel = trade["sizing"]["montant_risque"] * gain_ratio * RISK_CONFIG["partial_tp_ratio"]
            trade["partial_closed"] = True
            trade["partial_pnl"] = pnl_partiel
            trade["breakeven_active"] = True
            trade["state"] = TradeState.TRADE_PARTIAL
            buffer = trade["entry_price"] * RISK_CONFIG["breakeven_buffer_pct"]
            trade["sl"] = trade["entry_price"] + buffer if trade["direction"] == "BUY" else trade["entry_price"] - buffer

            if trade.get("reel") and trade.get("deriv_contract_final"):
                try: deriv_modifier_contrat(trade["deriv_contract_final"], sl=trade["sl"])
                except Exception: pass

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_partiel
            init_daily_stats(uid)["pnl"] += pnl_partiel
            return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}
        except Exception: return None

def appliquer_trailing_stop(uid, prix_current):
    if uid not in trades_actifs: return False
    trade = trades_actifs[uid]
    if not trade["breakeven_active"]: return False
    distance_trail = prix_current * RISK_CONFIG["trailing_stop_distance_pct"]
    if trade["direction"] == "BUY":
        nouveau_sl = prix_current - distance_trail
        if nouveau_sl > trade["sl"]:
            trade["sl"] = nouveau_sl
            if trade.get("reel") and trade.get("deriv_contract_final"):
                try: deriv_modifier_contrat(trade["deriv_contract_final"], sl=nouveau_sl)
                except Exception: pass
            return True
    else:
        nouveau_sl = prix_current + distance_trail
        if nouveau_sl < trade["sl"]:
            trade["sl"] = nouveau_sl
            if trade.get("reel") and trade.get("deriv_contract_final"):
                try: deriv_modifier_contrat(trade["deriv_contract_final"], sl=nouveau_sl)
                except Exception: pass
            return True
    return False

def utilisateur_a_trade_actif(uid):
    return uid in trades_actifs and trades_actifs[uid]["state"] in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL)

# ==========================================
# STRATÉGIES & MOTEUR IA
# ==========================================

def _ema(series, span): return series.ewm(span=span, adjust=False).mean()

def calculer_adx(df, period=14):
    try:
        high, low, close = df['high'], df['low'], df['close']
        plus_dm, minus_dm = high.diff(), -low.diff()
        plus_dm[plus_dm < 0], minus_dm[minus_dm < 0] = 0, 0
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, 1e-9))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        return float(dx.rolling(period).mean().iloc[-2]) if not dx.isna().iloc[-2] else 20.0
    except Exception: return 20.0

def calculer_atr(df, period=14):
    try:
        tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-2])
    except Exception: return 0.0

def detecter_chandeliers_pdf(df):
    if len(df) < 3: return "NONE", 0
    try:
        last, prev = df.iloc[-2], df.iloc[-3]
        o, h, l, c = float(last['open']), float(last['high']), float(last['low']), float(last['close'])
        po, pc = float(prev['open']), float(prev['close'])
        body, rng = abs(c - o), h - l
        if rng == 0: return "NONE", 0
        lw, uw = min(o, c) - l, h - max(o, c)
        if lw > body * 1.8 and uw < body: return "PIN_BULL", lw
        if uw > body * 1.8 and lw < body: return "PIN_BEAR", uw
        if pc < po and c > o: return "ENGULFING_BULL", body
        if pc > po and c < o: return "ENGULFING_BEAR", body
        return "NONE", 0
    except Exception: return "NONE", 0

def analyser_trend_pullback_confluence(symbole):
    c1h, c15 = obtenir_donnees_deriv(symbole, 3600), obtenir_donnees_deriv(symbole, 900)
    if not c1h or len(c1h) < 60 or not c15 or len(c15) < 30: return None
    try:
        df1h, df15 = pd.DataFrame(c1h), pd.DataFrame(c15)
        for df in (df1h, df15):
            for col in ('open', 'high', 'low', 'close'): df[col] = df[col].astype(float)
        
        px = df15['close'].iloc[-1]
        score = 0.0

        ema21_h1, ema55_h1 = _ema(df1h['close'], 21), _ema(df1h['close'], 55)
        adx_h1 = calculer_adx(df1h)
        direction = "BULL" if ema21_h1.iloc[-2] > ema55_h1.iloc[-2] else "BEAR"
        score += min(20, max(0, (adx_h1 - 12) * 1.3))

        dist_ema = abs(px - float(_ema(df15['close'], 21).iloc[-2])) / px
        if dist_ema <= 0.010: score += 15
        else: return None

        pattern, _ = detecter_chandeliers_pdf(df15)
        score += 20 if pattern != "NONE" else 8
        if score < 45: return None

        atr15 = calculer_atr(df15)
        if direction == "BULL":
            signal_dir, sl = "BUY", min(df15.iloc[-2]['low'] - atr15 * 0.15, px - atr15 * 1.2)
            distance = px - sl
            if distance <= 0: return None
            tp_final, tp1 = px + distance * 2.0, px + distance * 1.0
        else:
            signal_dir, sl = "SELL", max(df15.iloc[-2]['high'] + atr15 * 0.15, px + atr15 * 1.2)
            distance = sl - px
            if distance <= 0: return None
            tp_final, tp1 = px - distance * 2.0, px - distance * 1.0

        rr = abs(tp_final - px) / distance if distance > 0 else 0
        if rr < 1.4: return None

        return {
            "action": "🟢 ACHAT (BUY)" if signal_dir == "BUY" else "🔴 VENTE (SELL)",
            "tendance": direction, "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp_final, 5),
            "rr": round(rr, 2), "px": round(px, 5), "strategie": 1, "confiance": int(min(97, round(score))),
            "label": "TREND PULLBACK & CONFLUENCE"
        }
    except Exception: return None

def cerveau_pro_trader(symbole):
    signaux = []
    signal_brut = analyser_trend_pullback_confluence(symbole)
    if not signal_brut: return []
    signal_brut["ia_score"] = 85.0
    signaux.append(signal_brut)
    return signaux

# ==========================================
# DIAGNOSTIC ET TEST /testderiv (AVEC PURGE)
# ==========================================

@bot.message_handler(commands=['testderiv'])
def test_deriv_reel(message):
    uid = message.chat.id
    if not est_autorise(uid): return

    token_clean = DERIV_API_TOKEN.strip()
    if not token_clean:
        return bot.send_message(uid,
            "❌ *DERIV_API_TOKEN absente*\n"
            "Aucune variable d'environnement DERIV_API_TOKEN configurée sur Render.",
            parse_mode="Markdown")

    longueur = len(token_clean)
    apercu = f"{token_clean[:2]}...{token_clean[-2:]}" if longueur >= 6 else "(trop court)"

    bot.send_message(uid,
        f"🔬 *DIAGNOSTIC DERIV_API_TOKEN*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Longueur : {longueur} caractères\n"
        f"Aperçu : `{apercu}`\n"
        f"Espace/retour ligne supprimés : ✅ Oui\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Appel réel en cours vers l'API Deriv...", parse_mode="Markdown")

    try:
        ws = websocket.create_connection(DERIV_WS_URL, timeout=10)
        ws.send(json.dumps({"authorize": token_clean}))
        resp = json.loads(ws.recv())
        ws.close()

        if resp.get("error"):
            bot.send_message(uid,
                f"❌ *AUTHORIZE REFUSÉ PAR DERIV*\n"
                f"Message exact : `{resp['error'].get('message')}`\n"
                f"Code erreur : `{resp['error'].get('code')}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Si le problème persiste, vérifie que les cases **Trade** et **Read** "
                f"sont cochées lors de la création du jeton sur Deriv.",
                parse_mode="Markdown")
        else:
            info = resp.get("authorize", {})
            bot.send_message(uid,
                f"✅ *CONNEXION DERIV RÉUSSIE*\n"
                f"Compte : `{info.get('loginid')}`\n"
                f"Devise : {info.get('currency')}\n"
                f"Type : {'Réel' if not info.get('is_virtual') else 'Démo (virtuel)'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Le token fonctionne. Tu peux activer l'auto-trading via /menu.",
                parse_mode="Markdown")
    except Exception as e:
        bot.send_message(uid, f"❌ *Erreur réseau* : `{type(e).__name__}: {e}`", parse_mode="Markdown")

# ==========================================
# PANNEAU DE CONTRÔLE & COMMANDES TELEGRAM
# ==========================================

def obtenir_clavier(uid):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    etat_auto = "🟢 AUTO-TRADING: ON" if CONTROL_STATE["auto_trading_active"] else "🔴 AUTO-TRADING: OFF"
    markup.row(KeyboardButton(etat_auto))
    markup.row(KeyboardButton("📊 STATUS LIVE"), KeyboardButton(f"⚙️ MODE ({CONTROL_STATE['mode']})"))
    markup.row(KeyboardButton("📊 RAPPORT DU JOUR"))
    markup.row(KeyboardButton("🛑 STOP D'URGENCE"))
    return markup

@bot.message_handler(commands=['menu', 'controle'])
def afficher_menu(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    bot.send_message(uid, "🎛️ *PANNEAU DE CONTRÔLE*", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and (m.text.startswith("🟢 AUTO-TRADING") or m.text.startswith("🔴 AUTO-TRADING")))
def toggle_auto_trading(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return
    CONTROL_STATE["auto_trading_active"] = not CONTROL_STATE["auto_trading_active"]
    etat = "🟢 ACTIVÉ" if CONTROL_STATE["auto_trading_active"] else "🔴 DÉSACTIVÉ"
    bot.send_message(uid, f"⚙️ *Auto-trading : {etat}*", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    utilisateurs_actifs.add(uid)
    init_daily_stats(uid)
    bot.send_message(uid,
        "💼 *TERMINAL PRIME V55 — DERIV EDITION*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Utilise /menu pour afficher les boutons de contrôle.",
        reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

# ==========================================
# SCANNER PRINCIPAL
# ==========================================

def _analyser_une_paire(paire):
    try:
        return [(paire, res, res["px"]) for res in cerveau_pro_trader(paire)]
    except Exception: return []

def scanner_marche_auto():
    while True:
        try:
            time.sleep(15)
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres: continue

            resultats = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(_analyser_une_paire, p): p for p in ELITE_PAIRS_MT5}
                for future in as_completed(futures, timeout=25):
                    try: resultats.extend(future.result())
                    except Exception: pass

            for paire, res, px in resultats:
                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue
                    peut_trader, _ = utilisateur_peut_trader(uid)
                    if not peut_trader: continue

                    entry_direction = "BUY" if "BUY" in res["action"] else "SELL"

                    if peut_ouvrir_automatiquement(paire):
                        try:
                            trade_id, sizing = ouvrir_trade(
                                uid, paire, entry_direction, px,
                                res["sl"], res.get("tp1", res["tp"]), res["tp"],
                                res["strategie"], res["confiance"],
                                label=res["label"], executer_reel=True
                            )
                            txt_auto = f"🤖 *TRADE AUTO OUVERT SUR DERIV !*\nActif : {paire}\nDirection : {res['action']}\nPrix : {px}"
                            bot.send_message(uid, txt_auto, parse_mode="Markdown")
                        except Exception as e:
                            print(f"[Auto-Trading Error] {e}", flush=True)
        except Exception as e:
            print(f"[Scanner Error] {e}", flush=True)

# ==========================================
# MONITORING ET LANCEMENT
# ==========================================

def monitorer_trades_actifs():
    while True:
        try:
            time.sleep(5)
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs.get(uid)
                if not trade: continue
                prix_current = obtenir_prix_broker_realtime(trade["symbol"])
                if not prix_current: continue
                direction = trade["direction"]

                if trade["state"] == TradeState.TRADE_OPEN:
                    hit_tp1 = (direction == "BUY" and prix_current >= trade["tp1"]) or (direction == "SELL" and prix_current <= trade["tp1"])
                    hit_sl  = (direction == "BUY" and prix_current <= trade["sl"]) or (direction == "SELL" and prix_current >= trade["sl"])

                    if hit_sl:
                        fermer_trade_complet(uid, prix_current, win=False)
                        try: bot.send_message(uid, f"❌ Trade {trade['symbol']} fermé au Stop Loss.", parse_mode="Markdown")
                        except Exception: pass
                        continue
                    if hit_tp1:
                        partiel = fermer_trade_partiel(uid, prix_current)
                        if partiel:
                            try: bot.send_message(uid, f"🟡 TP1 atteint sur {trade['symbol']} (85% sécurisé, SL→BE).", parse_mode="Markdown")
                            except Exception: pass
                        continue

                elif trade["state"] == TradeState.TRADE_PARTIAL:
                    appliquer_trailing_stop(uid, prix_current)
                    hit_tp_final = (direction == "BUY" and prix_current >= trade["tp_final"]) or (direction == "SELL" and prix_current <= trade["tp_final"])
                    hit_be_sl    = (direction == "BUY" and prix_current <= trade["sl"]) or (direction == "SELL" and prix_current >= trade["sl"])

                    if hit_tp_final or hit_be_sl:
                        fermer_trade_complet(uid, prix_current, win=True)
                        try: bot.send_message(uid, f"🎉 Trade {trade['symbol']} clôturé avec succès !", parse_mode="Markdown")
                        except Exception: pass
                        continue
        except Exception: pass

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=monitorer_trades_actifs, daemon=True).start()
    print("💼 TERMINAL PRIME V55 — DERIV EDITION DÉMARRÉ", flush=True)
    bot.infinity_polling()
