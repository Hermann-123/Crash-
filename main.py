"""
╔════════════════════════════════════════════════════════════════════════════╗
║   TERMINAL PRIME V56 — PURE SCALPING M1 + EXÉCUTION RÉELLE DERIV           ║
║                                                                            ║
║  ⚙️ NOUVEAU DANS CETTE VERSION :                                          ║
║   • Suppression totale de Groq (LLM) pour une exécution 100% mathématique  ║
║     et une latence réduite au minimum.                                     ║
║   • Stratégie Pro Momentum Scalper (M1/M5) intégrée.                       ║
║   • Risk Management agressif (Trailing stop rapide, signaux de 10s).       ║
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
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()

DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN", "").strip()
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "").strip()
DERIV_ACCOUNT_TYPE = os.environ.get("DERIV_ACCOUNT_TYPE", "demo")

# ==========================================
# RISK MANAGEMENT — CONFIGURATION SCALPING PRO
# ==========================================

RISK_CONFIG = {
    "risk_per_trade_pct": 1.0,           
    "daily_loss_limit_pct": 5.0,         
    "max_consecutive_losses": 4,         
    "pause_duration_minutes": 60,        
    "partial_tp_ratio": 0.85,            
    "breakeven_buffer_pct": 0.0002,      
    "trailing_stop_activation_rr": 0.8,  
    "trailing_stop_distance_pct": 0.0015,
    "max_trades_per_day": 15,            
    "max_trade_age_hours": 1,            
    "signal_validity_seconds": 10,       
    "max_rr_degradation_pct": 25,
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
# KEEP ALIVE
# ==========================================

app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime V56 — Pure Scalping"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# PONT DERIV (exécution réelle)
# ==========================================

DERIV_REST_BASE = "https://api.derivws.com"

def _deriv_headers():
    return {
        "Deriv-App-ID": DERIV_APP_ID,
        "Authorization": f"Bearer {DERIV_API_TOKEN}",
    }

_deriv_account_id_cache = None

def deriv_get_account_id(force_refresh=False):
    global _deriv_account_id_cache
    if _deriv_account_id_cache and not force_refresh:
        return _deriv_account_id_cache
    if not DERIV_API_TOKEN or not DERIV_APP_ID:
        raise RuntimeError("DERIV_API_TOKEN ou DERIV_APP_ID manquant.")

    resp = requests.get(f"{DERIV_REST_BASE}/trading/v1/options/accounts",
                        headers=_deriv_headers(), timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Deriv get_accounts HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    comptes = data.get("accounts") or data.get("data") or (data if isinstance(data, list) else [])
    if not comptes:
        raise RuntimeError("Deriv: aucun compte Options trouvé.")

    def est_demo(c):
        return bool(c.get("is_virtual") or c.get("is_demo")
                    or str(c.get("account_type", "")).lower() == "demo"
                    or str(c.get("type", "")).lower() == "demo")

    cible = None
    for c in comptes:
        if DERIV_ACCOUNT_TYPE == "demo" and est_demo(c):
            cible = c; break
        if DERIV_ACCOUNT_TYPE == "real" and not est_demo(c):
            cible = c; break
    if not cible:
        cible = comptes[0]

    account_id = cible.get("account_id") or cible.get("accountId") or cible.get("id") or cible.get("loginid")
    _deriv_account_id_cache = account_id
    print(f"[Deriv] Compte sélectionné: {account_id}", flush=True)
    return account_id

def _deriv_obtenir_ws_url(force_refresh=False):
    account_id = deriv_get_account_id(force_refresh=force_refresh)
    resp = requests.post(f"{DERIV_REST_BASE}/trading/v1/options/accounts/{account_id}/otp",
                        headers=_deriv_headers(), timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Deriv OTP HTTP {resp.status_code}")
    data = resp.json()
    interieur = data.get("data", data)
    url = (data.get("url") or data.get("websocket_url") or interieur.get("url"))
    return url

def _deriv_trading_request(payload, timeout=10, _retry=True):
    cle_attendue = None
    for k in ("buy", "sell", "portfolio", "proposal_open_contract",
              "contract_update", "balance"):
        if k in payload:
            cle_attendue = k
            break

    ws = None
    try:
        url = _deriv_obtenir_ws_url()
        ws = websocket.create_connection(url, timeout=timeout,
                                         header=[f"Deriv-App-ID: {DERIV_APP_ID}"])
        ws.send(json.dumps(payload))
        debut = time.time()
        while time.time() - debut < timeout:
            resp = json.loads(ws.recv())
            if resp.get("error"):
                raise RuntimeError(f"Deriv API erreur: {resp['error'].get('message')}")
            if cle_attendue and cle_attendue in resp:
                return resp
            if resp.get("msg_type") == cle_attendue:
                return resp
        raise TimeoutError(f"Deriv: pas de réponse sous {timeout}s")
    except Exception as e:
        if _retry and any(m in str(e) for m in ("401", "Unauthorized", "OTP")):
            return _deriv_trading_request(payload, timeout=timeout, _retry=False)
        raise
    finally:
        try:
            if ws: ws.close()
        except Exception:
            pass

def deriv_symbole(symbole_bot):
    mapping = {"XAUUSD":"frxXAUUSD","XAGUSD":"frxXAGUSD"}
    if symbole_bot in mapping: return mapping[symbole_bot]
    if symbole_bot in VOLATILE_PAIRS: return f"R_{symbole_bot.replace('V','')}"
    return f"frx{symbole_bot}"

def deriv_ouvrir_contrat(symbole, direction, stake, multiplier, sl=None, tp=None):
    sym = deriv_symbole(symbole)
    contract_type = "MULTUP" if direction.upper() == "BUY" else "MULTDOWN"

    limit_order = {}
    if sl is not None: limit_order["stop_loss"] = round(float(sl), 5)
    if tp is not None: limit_order["take_profit"] = round(float(tp), 5)

    payload = {
        "buy": 1, "price": round(stake, 2),
        "parameters": {
            "amount": round(stake, 2), "basis": "stake", "contract_type": contract_type,
            "currency": "USD", "symbol": sym, "multiplier": multiplier,
        }
    }
    if limit_order: payload["parameters"]["limit_order"] = limit_order

    resp = _deriv_trading_request(payload)
    contract_id = resp.get("buy", {}).get("contract_id")
    print(f"[Deriv] Contrat ouvert {sym} {contract_type} mise={stake} → ID={contract_id}", flush=True)
    return contract_id

def deriv_modifier_contrat(contract_id, sl=None, tp=None):
    limit_order = {}
    if sl is not None: limit_order["stop_loss"] = round(float(sl), 5)
    if tp is not None: limit_order["take_profit"] = round(float(tp), 5)
    return _deriv_trading_request({"contract_update": 1, "contract_id": contract_id, "limit_order": limit_order})

def deriv_fermer_contrat(contract_id):
    return _deriv_trading_request({"sell": contract_id, "price": 0})

def deriv_statut_contrat(contract_id):
    resp = _deriv_trading_request({"proposal_open_contract": 1, "contract_id": contract_id})
    return resp.get("proposal_open_contract", {})

def deriv_positions_ouvertes():
    resp = _deriv_trading_request({"portfolio": 1})
    return resp.get("portfolio", {}).get("contracts", [])

def deriv_connecter():
    resp = _deriv_trading_request({"balance": 1})
    solde = resp.get("balance", {})
    print(f"[Deriv] Connecté — solde: {solde.get('balance')} {solde.get('currency')}", flush=True)
    return solde

def peut_ouvrir_automatiquement(symbole):
    if not CONTROL_STATE["auto_trading_active"]: return False
    if CONTROL_STATE["stop_urgence_actif"]: return False
    if CONTROL_STATE["mode"] == "SOLO": return len(trades_actifs) == 0
    return symbole not in {t.get("symbol") for t in trades_actifs.values()}

# ==========================================
# UTILITAIRES PRIX
# ==========================================

_candles_cache = {}
_candles_cache_lock = Lock()
CANDLES_CACHE_TTL = 10 

def _obtenir_donnees_deriv_reseau(symbole_brut, granularite=300):
    sym = deriv_symbole(symbole_brut)
    gran_valides = (60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400)
    gran_real = granularite if granularite in gran_valides else 14400
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=4)
            ws.send(json.dumps({"ticks_history": sym, "end": "latest", "count": 250, "style": "candles", "granularity": gran_real}))
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res and "error" not in res:
                return res["candles"]
        except:
            try: ws.close()
            except: pass
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

def obtenir_prix_broker_realtime(symbole):
    sym = deriv_symbole(symbole)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=3)
            ws.send(json.dumps({"ticks": sym}))
            res = json.loads(ws.recv())
            ws.close()
            if "tick" in res:
                return float(res["tick"]["quote"])
        except:
            try: ws.close()
            except: pass
            time.sleep(0.5)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real: return False
    decalage = abs(prix_bot - prix_real) / prix_real
    return decalage <= tolerance

# ==========================================
# GESTION DU RISQUE
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

def utilisateur_en_pause(uid):
    stats = init_daily_stats(uid)
    if stats["paused_until"] and time.time() < stats["paused_until"]:
        return True, stats["paused_until"]
    return False, None

def daily_loss_limit_atteinte(uid):
    stats = init_daily_stats(uid)
    limite = -(CAPITAL_ACTUEL * RISK_CONFIG["daily_loss_limit_pct"] / 100.0)
    return stats["pnl"] <= limite

def utilisateur_peut_trader(uid):
    stats = init_daily_stats(uid)
    if daily_loss_limit_atteinte(uid): return False, "🛑 Limite de perte journalière atteinte."
    en_pause, _ = utilisateur_en_pause(uid)
    if en_pause: return False, "⏸️ Pause anti-tilt active."
    if stats["trades"] >= RISK_CONFIG["max_trades_per_day"]: return False, "🛑 Limite de trades/jour atteinte."
    return True, None

def calculer_position_size(capital, risk_pct, prix_entree, prix_sl, symbole):
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)
    if distance_sl <= 0: return {"montant_risque": montant_risque, "lot_factor": 0, "distance_sl": 0}
    return {"montant_risque": round(montant_risque, 2), "lot_factor": round(montant_risque / distance_sl, 4), "distance_sl": round(distance_sl, 5)}

def enregistrer_resultat_trade(uid, pnl, win, pnl_pour_bilan=None):
    stats = init_daily_stats(uid)
    stats["pnl"] += pnl
    stats["trades"] += 1
    valeur_bilan = pnl_pour_bilan if pnl_pour_bilan is not None else pnl
    if win:
        stats["wins"] += 1
        stats["consecutive_losses"] = 0
    else:
        stats["losses"] += 1
        stats["consecutive_losses"] += 1
    stats["best_trade"] = max(stats["best_trade"], valeur_bilan)
    stats["worst_trade"] = min(stats["worst_trade"], valeur_bilan)
    if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
    return stats

# ==========================================
# EXÉCUTION DES TRADES
# ==========================================

def create_trade_id(): return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance,
                 label="SIGNAL", strategie_nom_ia="?", ia_score=None, contexte_marche=None, executer_reel=False):
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"], entry_price, sl, symbole)
    deriv_contract_tp1, deriv_contract_final = None, None

    if executer_reel:
        stake_total = CONTROL_STATE["stake_usd"]
        multiplier = CONTROL_STATE["multiplier"]
        stake_tp1 = round(stake_total * RISK_CONFIG["partial_tp_ratio"], 2)
        stake_final = round(stake_total - stake_tp1, 2)
        deriv_contract_tp1 = deriv_ouvrir_contrat(symbole, direction, stake_tp1, multiplier, sl=sl, tp=tp1)
        deriv_contract_final = deriv_ouvrir_contrat(symbole, direction, stake_final, multiplier, sl=sl, tp=tp_final)

    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole, "direction": direction, "entry_price": entry_price,
        "sl": sl, "sl_original": sl, "tp1": tp1, "tp_final": tp_final,
        "strategy": strategy, "confiance": confiance, "label": label,
        "strategie_nom_ia": strategie_nom_ia, "ia_score": ia_score,
        "state": TradeState.TRADE_OPEN, "timestamp_open": time.time(),
        "partial_closed": False, "partial_pnl": 0.0, "breakeven_active": False, "trailing_active": False,
        "sizing": sizing, "deriv_contract_tp1": deriv_contract_tp1, "deriv_contract_final": deriv_contract_final,
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
                    if not statut.get("is_sold"): deriv_fermer_contrat(trade["deriv_contract_final"])
                except: pass

            risque_initial = trade["sizing"]["montant_risque"]
            portion_restante = (1 - RISK_CONFIG["partial_tp_ratio"]) if trade.get("partial_closed") else 1.0
            risque_portion = risque_initial * portion_restante

            if win:
                gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
                pnl_final = risque_portion * gain_ratio
            else:
                pnl_final = -risque_portion

            pnl_trade_total = trade.get("partial_pnl", 0.0) + pnl_final
            trade["state"] = TradeState.TRADE_WIN if win else TradeState.TRADE_LOSS
            duration_seconds = time.time() - trade["timestamp_open"]

            if uid not in trades_historique: trades_historique[uid] = []
            trades_historique[uid].append({
                "trade_id": trade["trade_id"], "symbol": trade["symbol"], "direction": trade["direction"],
                "entry": trade["entry_price"], "exit": exit_price, "pnl": pnl_trade_total, "win": win,
                "timestamp": time.time(), "label": trade.get("label","")
            })

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
            enregistrer_resultat_trade(uid, pnl_final, win, pnl_pour_bilan=pnl_trade_total)
            ia_enregistrer_resultat(trade["symbol"], trade.get("strategie_nom_ia", "?"), trade.get("ia_score", 0), "M1", win)
            return {"trade_id": trade["trade_id"], "pnl": pnl_trade_total, "win": win, "duration": duration_seconds}
        except: return None
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
                except: pass

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_partiel
            init_daily_stats(uid)["pnl"] += pnl_partiel
            return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}
        except: return None

def appliquer_trailing_stop(uid, prix_current):
    if uid not in trades_actifs: return False
    trade = trades_actifs[uid]
    if not trade["breakeven_active"]: return False
    distance_trail = prix_current * RISK_CONFIG["trailing_stop_distance_pct"]
    
    nouveau_sl = None
    if trade["direction"] == "BUY":
        if (prix_current - distance_trail) > trade["sl"]:
            trade["sl"] = prix_current - distance_trail
            nouveau_sl = trade["sl"]
    else:
        if (prix_current + distance_trail) < trade["sl"]:
            trade["sl"] = prix_current + distance_trail
            nouveau_sl = trade["sl"]

    if nouveau_sl is not None and trade.get("reel") and trade.get("deriv_contract_final"):
        try: deriv_modifier_contrat(trade["deriv_contract_final"], sl=nouveau_sl)
        except: pass
        return True
    return False

def utilisateur_a_trade_actif(uid):
    return uid in trades_actifs and trades_actifs[uid]["state"] in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL)

# ==========================================
# ANALYSE & STRATÉGIES M1 SCALPING
# ==========================================

def _ema(series, span): return series.ewm(span=span, adjust=False).mean()

def calculer_atr(df, period=7):
    try:
        tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-2])
    except: return 0.0

def detecter_chandeliers_pdf(df):
    if len(df) < 3: return "NONE", 0
    try:
        last, prev = df.iloc[-2], df.iloc[-3]
        o, h, l, c = float(last['open']), float(last['high']), float(last['low']), float(last['close'])
        po, pc = float(prev['open']), float(prev['close'])
        body, rng = abs(c - o), h - l
        if rng == 0: return "NONE", 0
        uw, lw = h - max(o, c), min(o, c) - l
        
        if lw > body * 1.8 and uw < body: return "PIN_BULL", lw
        if uw > body * 1.8 and lw < body: return "PIN_BEAR", uw
        if pc < po and c > o and c > po and o < pc: return "ENGULFING_BULL", body
        if pc > po and c < o and c < po and o > pc: return "ENGULFING_BEAR", body
        return "NONE", 0
    except: return "NONE", 0

def analyser_scalping_multi_tf(symbole):
    """
    🔥 STRATÉGIE PRO MOMENTUM SCALPER (M1 / M5)
    Identifie la micro-tendance sur M5, et attend un micro-pullback 
    sur les moyennes mobiles en M1 avec une bougie de rejet.
    """
    c5  = obtenir_donnees_deriv(symbole, 300) 
    c1  = obtenir_donnees_deriv(symbole, 60)  

    if not c5 or not c1 or len(c5) < 30 or len(c1) < 20:
        return None

    try:
        df5  = pd.DataFrame(c5).astype(float)
        df1  = pd.DataFrame(c1).astype(float)
        px = float(df1['close'].iloc[-1])

        # BIAIS M5
        ema13_5, ema50_5 = _ema(df5['close'], 13), _ema(df5['close'], 50)
        direction = "BULL" if ema13_5.iloc[-2] > ema50_5.iloc[-2] else "BEAR"

        # CONDITIONS M1
        ema21_1 = _ema(df1['close'], 21)
        distance_ema = abs(px - float(ema21_1.iloc[-2])) / px
        
        if distance_ema > 0.0015: 
            return None

        # DECLENCHEUR
        pattern, _ = detecter_chandeliers_pdf(df1)
        if (direction == "BULL" and pattern not in ("PIN_BULL", "ENGULFING_BULL")) or \
           (direction == "BEAR" and pattern not in ("PIN_BEAR", "ENGULFING_BEAR")):
            return None

        # RISQUE SERRÉ (ATR M1)
        atr1 = calculer_atr(df1, period=7) 
        if atr1 <= 0: return None

        bougie1 = df1.iloc[-2]
        if direction == "BULL":
            signal_dir = "BUY"
            sl = min(float(bougie1['low']) - (atr1 * 0.5), px - (atr1 * 1.5))
            dist = px - sl
            if dist <= 0: return None
            tp1, tp = px + dist * 1.2, px + dist * 2.5
        else:
            signal_dir = "SELL"
            sl = max(float(bougie1['high']) + (atr1 * 0.5), px + (atr1 * 1.5))
            dist = sl - px
            if dist <= 0: return None
            tp1, tp = px - dist * 1.2, px - dist * 2.5

        rr = abs(tp - px) / dist
        if rr < 1.5: return None 

        return {
            "action": "🟢 ACHAT SCALP" if signal_dir == "BUY" else "🔴 VENTE SCALP",
            "tendance": direction, "force": f"Impulsion M5 {direction}",
            "msg": f"Scalping M1 : Rejet sur EMA21 + {pattern.replace('_',' ')}",
            "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp, 5),
            "rr": round(rr, 2), "px": round(px, 5),
            "strategie": 2, "confiance": 85,
            "label": "PRO MOMENTUM SCALPER (M1)",
            "zones_confluence": ["EMA21 M1 Rejection"],
        }
    except Exception as e:
        print(f"[Analyse {symbole}] Erreur: {e}")
        return None

# ==========================================
# VALIDATION DÉTERMINISTE (Moteur IA local)
# ==========================================

IA_CONFIG = {
    "seuil_acceptation": 75,
    "poids": {
        "tendance_h1": 15, "ema_alignement": 20, "atr_volatilite": 20, "qualite_cassure": 45
    }
}

ia_historique = []

def moteur_ia_valider_signal(symbole, signal, strategie_nom):
    # Validation ultra-rapide et mathématique uniquement
    try:
        score_base = signal.get("confiance", 50)
        rr = signal.get("rr", 0)
        
        # Pénalisations / Bonus
        if rr >= 2.0: score_base += 10
        elif rr < 1.5: score_base -= 15
        
        score_final = max(0, min(100, score_base))
        return {
            "accepte": score_final >= IA_CONFIG["seuil_acceptation"], 
            "score": score_final, 
            "justification": ["Setup Momentum validé mathématiquement"]
        }
    except:
        return {"accepte": False, "score": 0, "justification": []}

def ia_enregistrer_resultat(symbol, strategie_nom, score, timeframe, win):
    ia_historique.append({
        "symbol": symbol, "strategie": strategie_nom, "score": score,
        "timeframe": timeframe, "win": win, "ts": time.time()
    })

def cerveau_pro_trader(symbole):
    signaux_valides = []
    signal_brut = analyser_scalping_multi_tf(symbole)
    if signal_brut:
        verdict = moteur_ia_valider_signal(symbole, signal_brut, "SCALPING_MULTI_TF")
        if verdict["accepte"]:
            signal_brut["ia_score"] = verdict["score"]
            signal_brut["ia_justification"] = verdict["justification"]
            signal_brut["strategie_nom_ia"] = "SCALPING_MULTI_TF"
            signaux_valides.append(signal_brut)
    return signaux_valides

# ==========================================
# PANNEAU DE CONTRÔLE ET COMMANDES TELEGRAM
# ==========================================

def obtenir_clavier(uid):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("🟢 AUTO-TRADING: ON" if CONTROL_STATE["auto_trading_active"] else "🔴 AUTO-TRADING: OFF"))
    markup.row(KeyboardButton("📊 STATUS LIVE"), KeyboardButton(f"⚙️ MODE ({CONTROL_STATE['mode']})"))
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("🛑 STOP D'URGENCE"))
    return markup

@bot.message_handler(commands=['menu', 'controle'])
def afficher_menu(message):
    if est_autorise(message.chat.id):
        bot.send_message(message.chat.id, "🎛️ *PANNEAU DE CONTRÔLE*", reply_markup=obtenir_clavier(message.chat.id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and (m.text.startswith("🟢 AUTO-TRADING") or m.text.startswith("🔴 AUTO-TRADING")))
def toggle_auto_trading(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return bot.send_message(uid, "❌ Réservé à l'admin.")
    CONTROL_STATE["auto_trading_active"] = not CONTROL_STATE["auto_trading_active"]
    etat = "🟢 ACTIVÉ" if CONTROL_STATE["auto_trading_active"] else "🔴 DÉSACTIVÉ"
    bot.send_message(uid, f"⚙️ *Auto-trading : {etat}*", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    utilisateurs_actifs.add(uid)
    init_daily_stats(uid)
    bot.send_message(uid, "💼 *TERMINAL PRIME V56 — PURE SCALPING*\nUtilise /menu pour accéder aux contrôles.", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(commands=['iaconfig'])
def gerer_ia_config(message):
    if message.chat.id != ADMIN_ID: return
    txt = (f"🤖 *PARAMÈTRES MOTEUR IA (Local)*\n"
           f"Seuil d'acceptation : {IA_CONFIG['seuil_acceptation']}%\n"
           f"Trades enregistrés : {len(ia_historique)}")
    bot.send_message(message.chat.id, txt, parse_mode="Markdown")

@bot.message_handler(commands=['iastats'])
def ia_stats(message):
    if message.chat.id != ADMIN_ID: return
    wins = sum(1 for h in ia_historique if h["win"])
    total = len(ia_historique)
    wr = (wins / total * 100) if total else 0
    bot.send_message(message.chat.id, f"📊 *Winrate Global* : {wr:.1f}% sur {total} trades", parse_mode="Markdown")

# ==========================================
# BOUCLE PRINCIPALE (SCAN & MONITOR)
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(5) # Scan hyper-rapide pour M1
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres: continue

            resultats = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(cerveau_pro_trader, p): p for p in ELITE_PAIRS_MT5}
                for future in as_completed(futures, timeout=10):
                    try:
                        paire = futures[future]
                        signaux = future.result()
                        for res in signaux:
                            px = obtenir_prix_broker_realtime(paire)
                            if px and valider_prix_avant_signal(paire, px):
                                resultats.append((paire, res, px))
                    except: pass

            for paire, res, px in resultats:
                cle = f"{paire}_{res.get('strategie_nom_ia', 'PRO')}"
                if cle in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle] < 60):
                    continue # Évite le spam sur le même setup
                derniere_alerte_auto[cle] = time.time()
                
                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue
                    peut_trader, _ = utilisateur_peut_trader(uid)
                    if not peut_trader: continue

                    entry_dir = "BUY" if "BUY" in res["action"] else "SELL"
                    
                    if peut_ouvrir_automatiquement(paire):
                        try:
                            trade_id, sizing = ouvrir_trade(
                                uid, paire, entry_dir, px, res["sl"], res["tp1"], res["tp"],
                                res["strategie"], res["confiance"], res["label"],
                                res.get("strategie_nom_ia"), res.get("ia_score"),
                                executer_reel=True
                            )
                            bot.send_message(uid, f"⚡ *SCALP AUTO OUVERT* : {paire} {entry_dir} @ {px:.5f}", parse_mode="Markdown")
                        except Exception as e:
                            print(f"[Exec Auto] Erreur: {e}")

        except Exception as e: print(f"[Scanner] {e}")

def monitorer_trades_actifs():
    while True:
        try:
            time.sleep(2) # Monitor ultra-réactif pour le scalping
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs[uid]
                px = obtenir_prix_broker_realtime(trade["symbol"])
                if not px: continue
                dir_ = trade["direction"]

                if trade["state"] == TradeState.TRADE_OPEN:
                    hit_sl = (dir_ == "BUY" and px <= trade["sl"]) or (dir_ == "SELL" and px >= trade["sl"])
                    hit_tp1 = (dir_ == "BUY" and px >= trade["tp1"]) or (dir_ == "SELL" and px <= trade["tp1"])
                    if hit_sl:
                        fermer_trade_complet(uid, px, win=False)
                        try: bot.send_message(uid, f"❌ Stop Loss touché sur {trade['symbol']}")
                        except: pass
                    elif hit_tp1:
                        fermer_trade_partiel(uid, px)
                        try: bot.send_message(uid, f"🟡 TP1 atteint ({trade['symbol']}). SL -> Breakeven.")
                        except: pass
                
                elif trade["state"] == TradeState.TRADE_PARTIAL:
                    appliquer_trailing_stop(uid, px)
                    hit_tp = (dir_ == "BUY" and px >= trade["tp_final"]) or (dir_ == "SELL" and px <= trade["tp_final"])
                    hit_sl = (dir_ == "BUY" and px <= trade["sl"]) or (dir_ == "SELL" and px >= trade["sl"])
                    if hit_tp or hit_sl:
                        fermer_trade_complet(uid, px, win=True)
                        try: bot.send_message(uid, f"✅ Trade terminé sur {trade['symbol']}")
                        except: pass
        except: pass

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=monitorer_trades_actifs, daemon=True).start()
    print("💼 TERMINAL PRIME V56 SCALPING DÉMARRÉ", flush=True)
    bot.infinity_polling()
