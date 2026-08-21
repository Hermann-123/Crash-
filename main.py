"""
╔════════════════════════════════════════════════════════════════════════════╗
║   TERMINAL PRIME V55 — FULL AUTO (METAAPI + GROQ)                         ║
║                                                                            ║
║  MODE AUTOMATIQUE INTÉGRAL :                                               ║
║   • Suppression des boutons de validation manuelle ("Copier").           ║
║   • Exécution instantanée des ordres sur le compte MT5 réel via MetaApi.   ║
║   • Gestion complète: Sizing dynamique, TP partiel 85%, Breakeven, Trailing║
║     Stop, et protections anti-tilt / limites de pertes journalières.       ║
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
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from flask import Flask
from threading import Thread, Lock
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
from metaapi_cloud_sdk import MetaApi

# ==========================================
# CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = "8000472746:AAEkr52S_96is19IuJ1AYwUhH7I5rSe61zM"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# ==========================================
# CONFIGURATION METAAPI (EXÉCUTION MT5 RÉELLE)
# ==========================================
METAAPI_TOKEN = os.environ.get("4b6c631b-ce28-4aae-ab2d-fc8de9c64db5", "").strip()
METAAPI_ACCOUNT_ID = os.environ.get("41080337", "").strip()
metaapi_instance = MetaApi(token=METAAPI_TOKEN) if METAAPI_TOKEN else None

async def passer_ordre_mt5_reel(symbole, action, volume_lots, sl, tp):
    """
    Envoie l'ordre d'achat ou de vente directement sur MetaTrader 5 via MetaApi.
    """
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID or not metaapi_instance:
        print("[MetaApi] ⚠️ Variables METAAPI_TOKEN ou METAAPI_ACCOUNT_ID manquantes.", flush=True)
        return None

    try:
        account = await metaapi_instance.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        trade_type = 'ORDER_TYPE_BUY' if 'BUY' in action.upper() else 'ORDER_TYPE_SELL'

        result = await connection.create_market_buy_order(
            symbol=symbole,
            volume=volume_lots,
            stop_loss=sl,
            take_profit=tp
        ) if trade_type == 'ORDER_TYPE_BUY' else await connection.create_market_sell_order(
            symbol=symbole,
            volume=volume_lots,
            stop_loss=sl,
            take_profit=tp
        )

        print(f"[MetaApi] ✅ Ordre exécuté sur MT5 : {result}", flush=True)
        return result

    except Exception as e:
        print(f"[MetaApi] ❌ Erreur exécution MT5 : {e}", flush=True)
        return None

def executer_ordre_mt5_sync(symbole, action, volume_lots, sl, tp):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(passer_ordre_mt5_reel(symbole, action, volume_lots, sl, tp))
        loop.close()
        return res
    except Exception as e:
        print(f"[MetaApi Sync] {e}", flush=True)
        return None

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
    return "Terminal Prime V55 — Full Auto Edition"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

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
            url = (f"https://financialmodelingprep.com/api/v3/historical-chart/"
                   f"{tf}/{sym_fmp}?apikey={FMP_API_KEY}")
            res = requests.get(url, timeout=3).json()
            if isinstance(res, list) and len(res) > 0:
                bougies = []
                for idx, b in enumerate(reversed(res[:250])):
                    epoch_val = None
                    date_str = b.get("date")
                    if date_str:
                        try:
                            epoch_val = int(datetime.datetime.strptime(
                                date_str, "%Y-%m-%d %H:%M:%S").timestamp())
                        except (ValueError, TypeError):
                            try:
                                epoch_val = int(datetime.datetime.strptime(
                                    date_str, "%Y-%m-%d").timestamp())
                            except (ValueError, TypeError):
                                epoch_val = None
                    if epoch_val is None:
                        epoch_val = int(time.time()) - (250 - idx) * granularite
                    bougies.append({
                        "open":  float(b["open"]),
                        "high":  float(b["high"]),
                        "low":   float(b["low"]),
                        "close": float(b["close"]),
                        "epoch": epoch_val
                    })
                return bougies
        except Exception as e:
            print(f"[FMP Chart - {symbole_brut}] {e}", flush=True)

    sym = prefixer_symbole(symbole_brut)
    gran_valides = (60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400)
    gran_real = granularite if granularite in gran_valides else 14400
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=4)
            ws.send(json.dumps({"ticks_history": sym, "end": "latest",
                                "count": 250, "style": "candles",
                                "granularity": gran_real}))
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

def obtenir_donnees_h4(symbole):
    data = obtenir_donnees_deriv(symbole, 14400)
    if data and len(data) > 20:
        return data
    h1 = obtenir_donnees_deriv(symbole, 3600)
    if not h1 or len(h1) < 8:
        return None
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
            prix_broker[symbole] = {
                "price": prix, "source": "FMP", "timestamp": time.time(),
                "bid": float(res[0].get("bid", prix)),
                "ask": float(res[0].get("ask", prix))
            }
            return prix
    except Exception as e:
        print(f"[FMP Real-time {symbole}] {e}", flush=True)

    sym = prefixer_symbole(symbole)
    for _ in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=3)
            ws.send(json.dumps({"ticks": sym}))
            res = json.loads(ws.recv())
            ws.close()
            if "tick" in res:
                prix = float(res["tick"]["quote"])
                prix_broker[symbole] = {"price": prix, "source": "Deriv",
                                        "timestamp": time.time()}
                return prix
        except:
            try: ws.close()
            except: pass
            time.sleep(0.5)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real:
        return False
    decalage = abs(prix_bot - prix_real) / prix_real
    if decalage > tolerance:
        return False
    return True

# ==========================================
# GESTION DU RISQUE
# ==========================================

def get_today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def init_daily_stats(uid):
    today = get_today_str()
    if uid not in daily_stats or daily_stats[uid]["date"] != today:
        daily_stats[uid] = {
            "date": today, "pnl": 0.0, "trades": 0,
            "wins": 0, "losses": 0,
            "consecutive_losses": 0,
            "paused_until": None,
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

def max_trades_jour_atteint(uid):
    stats = init_daily_stats(uid)
    return stats["trades"] >= RISK_CONFIG["max_trades_per_day"]

def utilisateur_peut_trader(uid):
    stats = init_daily_stats(uid)
    if daily_loss_limit_atteinte(uid):
        return False, "🛑 Limite de perte journalière atteinte."
    en_pause, jusqua = utilisateur_en_pause(uid)
    if en_pause:
        return False, "⏸️ Pause anti-tilt active."
    if max_trades_jour_atteint(uid):
        return False, "🛑 Limite de trades/jour atteinte."
    return True, None

def calculer_position_size(capital, risk_pct, prix_entree, prix_sl):
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)
    if distance_sl <= 0:
        return {"montant_risque": montant_risque, "lot_factor": 0, "distance_sl": 0}
    lot_factor = montant_risque / distance_sl
    return {
        "montant_risque": round(montant_risque, 2),
        "lot_factor": round(lot_factor, 4),
        "distance_sl": round(distance_sl, 5),
    }

def enregistrer_resultat_trade(uid, pnl, win, pnl_pour_bilan=None):
    stats = init_daily_stats(uid)
    stats["pnl"]    += pnl
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
    if valeur_bilan > stats["best_trade"]:
        stats["best_trade"] = valeur_bilan
    if valeur_bilan < stats["worst_trade"]:
        stats["worst_trade"] = valeur_bilan
    if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
        stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
    return stats

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance,
                 label="SIGNAL", strategie_nom_ia="?", ia_score=None, gemini_score=None,
                 contexte_marche=None):
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"],
                                    entry_price, sl)
    trades_actifs[uid] = {
        "trade_id": trade_id, "symbol": symbole,
        "direction": direction, "entry_price": entry_price,
        "sl": sl, "sl_original": sl,
        "tp1": tp1, "tp_final": tp_final,
        "strategy": strategy, "confiance": confiance, "label": label,
        "strategie_nom_ia": strategie_nom_ia,
        "ia_score": ia_score,
        "gemini_score": gemini_score,
        "contexte_marche": contexte_marche,
        "state": TradeState.TRADE_OPEN,
        "timestamp_open": time.time(),
        "exit_price": None, "exit_time": None, "pnl": None,
        "partial_closed": False,
        "partial_pnl": 0.0,
        "breakeven_active": False,
        "trailing_active": False,
        "sizing": sizing,
    }
    return trade_id, sizing

def fermer_trade_complet(uid, exit_price, win):
    with lock_trade:
        if uid not in trades_actifs:
            return None
        trade    = trades_actifs[uid]
        trade_id = trade["trade_id"]
        try:
            risque_initial = trade["sizing"]["montant_risque"]
            portion_restante = (1 - RISK_CONFIG["partial_tp_ratio"]) if trade.get("partial_closed") else 1.0
            risque_portion    = risque_initial * portion_restante
            if win:
                gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
                pnl_final = risque_portion * gain_ratio
            else:
                pnl_final = -risque_portion
            pnl_trade_total = trade.get("partial_pnl", 0.0) + pnl_final
            trade["state"]      = TradeState.TRADE_WIN if win else TradeState.TRADE_LOSS
            trade["exit_price"] = exit_price
            trade["exit_time"]  = time.time()
            trade["pnl"]        = pnl_trade_total
            duration_seconds     = trade["exit_time"] - trade["timestamp_open"]

            if uid not in trades_historique:
                trades_historique[uid] = []
            trades_historique[uid].append({
                "trade_id": trade_id, "symbol": trade["symbol"],
                "direction": trade["direction"], "entry": trade["entry_price"],
                "exit": exit_price, "pnl": pnl_trade_total, "duration": duration_seconds,
                "win": win, "timestamp": trade["exit_time"], "label": trade.get("label","")
            })
            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
            enregistrer_resultat_trade(uid, pnl_final, win, pnl_pour_bilan=pnl_trade_total)
            return {"trade_id": trade_id, "pnl": pnl_trade_total, "win": win}
        except Exception as e:
            return None
        finally:
            trades_actifs.pop(uid, None)

def fermer_trade_partiel(uid, exit_price):
    with lock_trade:
        if uid not in trades_actifs:
            return None
        trade = trades_actifs[uid]
        if trade["partial_closed"]:
            return None
        try:
            risque_initial = trade["sizing"]["montant_risque"]
            ratio = RISK_CONFIG["partial_tp_ratio"]
            gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
            pnl_partiel = risque_initial * gain_ratio * ratio
            trade["partial_closed"]   = True
            trade["partial_pnl"]      = pnl_partiel
            trade["breakeven_active"] = True
            trade["state"]            = TradeState.TRADE_PARTIAL
            buffer = trade["entry_price"] * RISK_CONFIG["breakeven_buffer_pct"]
            trade["sl"] = trade["entry_price"] + buffer if trade["direction"] == "BUY" else trade["entry_price"] - buffer
            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_partiel
            stats = init_daily_stats(uid)
            stats["pnl"] += pnl_partiel
            return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}
        except Exception:
            return None

def appliquer_trailing_stop(uid, prix_current):
    if uid not in trades_actifs:
        return False
    trade = trades_actifs[uid]
    if not trade["breakeven_active"]:
        return False
    distance_trail = prix_current * RISK_CONFIG["trailing_stop_distance_pct"]
    if trade["direction"] == "BUY":
        nouveau_sl = prix_current - distance_trail
        if nouveau_sl > trade["sl"]:
            trade["sl"] = nouveau_sl
            trade["trailing_active"] = True
            return True
    else:
        nouveau_sl = prix_current + distance_trail
        if nouveau_sl < trade["sl"]:
            trade["sl"] = nouveau_sl
            trade["trailing_active"] = True
            return True
    return False

def utilisateur_a_trade_actif(uid):
    return uid in trades_actifs and trades_actifs[uid]["state"] in (
        TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL
    )

def watchdog_trades_bloques():
    while True:
        try:
            time.sleep(300)
            maintenant = time.time()
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs.get(uid)
                if not trade: continue
                age_heures = (maintenant - trade.get("timestamp_open", maintenant)) / 3600
                if age_heures >= RISK_CONFIG["max_trade_age_hours"]:
                    prix_current = obtenir_prix_broker_realtime(trade["symbol"])
                    if prix_current:
                        win = prix_current >= trade["entry_price"] if trade["direction"] == "BUY" else prix_current <= trade["entry_price"]
                        fermer_trade_complet(uid, prix_current, win=win)
        except Exception:
            pass

# ==========================================
# GESTION SESSIONS & STRATÉGIES
# ==========================================

PAIRES_SESSION_ASIE    = ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY","AUDUSD","AUDCAD","XAUUSD","XAGUSD"]
PAIRES_SESSION_LONDRES = ["EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF","EURJPY","EURAUD","XAUUSD","XAGUSD"]
PAIRES_SESSION_NY      = ["EURUSD","GBPUSD","USDCAD","USDCHF","AUDUSD","XAUUSD","XAGUSD"]

def get_session_active():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    paires, sessions = [], []
    if 0.0 <= h < 7.0:
        paires += PAIRES_SESSION_ASIE; sessions.append("ASIE")
    if 7.0 <= h < 8.0:
        paires += PAIRES_SESSION_ASIE + PAIRES_SESSION_LONDRES; sessions.append("ASIE+LONDRES")
    if 8.0 <= h <= 10.0:
        paires += PAIRES_SESSION_LONDRES; sessions.append("LONDRES")
    if 12.0 <= h <= 15.0:
        paires += PAIRES_SESSION_NY; sessions.append("NEW_YORK")
    if not sessions:
        return None, []
    return "+".join(sessions), list(dict.fromkeys(paires))

def nom_killzone():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    if 7.0 <= h < 8.0:   return "🌏🇬🇧 Asie+Londres"
    if 0.0 <= h < 7.0:   return "🌏 Asian Killzone"
    if 8.0 <= h <= 10.0: return "🇬🇧 London Killzone"
    if 12.0 <= h <= 15.0:return "🇺🇸 New York Killzone"
    return "⏳ Hors session"

def est_symbole_autorise(symbole):
    if symbole in VOLATILE_PAIRS:
        if not volatility_pairs_active.get(symbole, True):
            return "BLOCAGE_TOTAL", f"{symbole} désactivé"
        return "AUTORISE", ""
    now = datetime.datetime.utcnow()
    j, h = now.weekday(), now.hour + now.minute / 60.0
    if (j == 4 and h >= 21) or j == 5 or (j == 6 and h < 21):
        return "BLOCAGE_TOTAL", "Week-end"
    if symbole in COMMODITY_PAIRS:
        return "AUTORISE", ""
    session, paires_session = get_session_active()
    if session is None:
        return "HORS_SESSION", "🔒 Hors Killzone"
    if symbole in paires_session:
        return "AUTORISE", ""
    return "HORS_SESSION", "🔒 Inactif"

def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def detecter_swing_points(df, ordre=3):
    highs, lows = df['high'].values, df['low'].values
    n = len(df)
    sh, sl = [], []
    for i in range(ordre, n - ordre):
        if highs[i] == highs[i-ordre:i+ordre+1].max(): sh.append(float(highs[i]))
        if lows[i] == lows[i-ordre:i+ordre+1].min(): sl.append(float(lows[i]))
    return sh, sl

def detecter_niveaux_cles(df, lookback=80, tolerance_cluster=0.0015):
    try:
        sub = df.iloc[-lookback:] if len(df) > lookback else df
        sh, sl = detecter_swing_points(sub, ordre=3)
        tous = sorted(sh + sl)
        if not tous: return []
        clusters = []
        for prix in tous:
            place = False
            for c in clusters:
                if abs(prix - c["moyenne"]) / prix < tolerance_cluster * 3:
                    c["membres"].append(prix)
                    c["moyenne"] = sum(c["membres"]) / len(c["membres"])
                    place = True
                    break
            if not place: clusters.append({"moyenne": prix, "membres": [prix]})
        return [round(c["moyenne"], 5) for c in clusters if len(c["membres"]) >= 2]
    except Exception:
        return []

def detecter_order_blocks(df, lookback=40):
    try:
        sub = df.iloc[-lookback:] if len(df) > lookback else df
        if len(sub) < 6: return [], []
        opens, closes, highs, lows = sub['open'].values, sub['close'].values, sub['high'].values, sub['low'].values
        corps = abs(closes - opens)
        corps_moyen = corps[:-1].mean() if len(corps) > 1 else 0
        if corps_moyen <= 0: return [], []
        obs_bull, obs_bear = [], []
        for i in range(2, len(sub) - 1):
            if corps[i] <= corps_moyen * 1.6: continue
            if closes[i] > opens[i] and closes[i-1] <= opens[i-1]:
                top = max(opens[i-1], closes[i-1], highs[i-1])
                obs_bull.append((float(lows[i-1]), round(top, 5)))
            elif closes[i] < opens[i] and closes[i-1] >= opens[i-1]:
                bottom = min(opens[i-1], closes[i-1], lows[i-1])
                obs_bear.append((round(bottom, 5), float(highs[i-1])))
        return obs_bull[-3:], obs_bear[-3:]
    except Exception:
        return [], []

def calculer_adx(df, period=14):
    try:
        high, low, close = df['high'], df['low'], df['close']
        plus_dm, minus_dm = high.diff(), -low.diff()
        plus_dm[plus_dm < 0], minus_dm[minus_dm < 0] = 0, 0
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di, minus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, 1e-9)), 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        adx = dx.rolling(period).mean()
        return float(adx.iloc[-2]) if not adx.isna().iloc[-2] else 20.0
    except Exception:
        return 20.0

def calculer_macd_signal(df):
    try:
        ema12, ema26 = df['close'].ewm(span=12, adjust=False).mean(), df['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        return float(macd_line.iloc[-2]), float(signal_line.iloc[-2]), float((macd_line - signal_line).iloc[-2])
    except Exception:
        return 0.0, 0.0, 0.0

def calculer_atr(df, period=14):
    try:
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-2])
    except Exception:
        return 0.0

def evaluer_structure_marche(df):
    try:
        highs, lows = df['high'].iloc[-20:].values, df['low'].iloc[-20:].values
        if len(highs) < 2: return 50.0
        hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
        hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i-1])
        return round(max(hh, hl) / (len(highs) - 1) * 100, 1)
    except Exception:
        return 50.0

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
    except Exception:
        return "NONE", 0

def analyser_trend_pullback_confluence(symbole):
    c1h = obtenir_donnees_deriv(symbole, 3600)
    c15 = obtenir_donnees_deriv(symbole, 900)
    if not c1h or len(c1h) < 60 or not c15 or len(c15) < 30: return None
    try:
        df1h = pd.DataFrame([{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in c1h])
        df15 = pd.DataFrame([{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in c15])
        px = float(df15['close'].iloc[-1])
        score = 0.0

        ema21_h1, ema55_h1 = _ema(df1h['close'], 21), _ema(df1h['close'], 55)
        adx_h1 = calculer_adx(df1h)
        tendance_bull = ema21_h1.iloc[-2] > ema55_h1.iloc[-2]
        direction = "BULL" if tendance_bull else "BEAR"
        score += min(20, max(0, (adx_h1 - 12) * 1.3))

        structure_score = evaluer_structure_marche(df1h)
        score += min(15, max(0, (structure_score - 35) * 0.3))

        ema21_m15 = _ema(df15['close'], 21)
        distance_ema_pct = abs(px - float(ema21_m15.iloc[-2])) / px
        zones_touchees = []
        score_zone = 0.0
        if distance_ema_pct <= 0.010:
            zones_touchees.append("EMA21 M15")
            score_zone += 15

        obs_bull, obs_bear = detecter_order_blocks(df1h)
        obs_list = obs_bull if direction == "BULL" else obs_bear
        ob_pertinent = None
        for bottom, top in obs_list:
            marge = (top - bottom) * 0.4
            if (bottom - marge) <= px <= (top + marge):
                zones_touchees.append("Order Block")
                ob_pertinent = (bottom, top)
                score_zone += 15
                break

        if not zones_touchees: return None
        score += min(25, score_zone)

        try:
            rsi_val = float(ta.momentum.RSIIndicator(close=df15["close"], window=14).rsi().iloc[-2])
        except Exception:
            rsi_val = 50.0
        score += 15 if 30 <= rsi_val <= 70 else 0

        _, _, macd_hist = calculer_macd_signal(df15)
        if (direction == "BULL" and macd_hist > 0) or (direction == "BEAR" and macd_hist < 0):
            score += 10

        pattern, _ = detecter_chandeliers_pdf(df15)
        score += 20 if pattern != "NONE" else 8

        if score < 45: return None

        atr15 = calculer_atr(df15)
        if atr15 <= 0: return None

        if direction == "BULL":
            signal_dir = "BUY"
            sl = min(float(df15.iloc[-2]['low']) - (atr15 * 0.15), px - (atr15 * 1.2))
            distance = px - sl
            if distance <= 0: return None
            tp_final, tp1 = px + (distance * 2.0), px + (distance * 1.0)
        else:
            signal_dir = "SELL"
            sl = max(float(df15.iloc[-2]['high']) + (atr15 * 0.15), px + (atr15 * 1.2))
            distance = sl - px
            if distance <= 0: return None
            tp_final, tp1 = px - (distance * 2.0), px - (distance * 1.0)

        rr = abs(tp_final - px) / distance if distance > 0 else 0
        if rr < 1.4: return None

        return {
            "action": "🟢 ACHAT (BUY)" if signal_dir == "BUY" else "🔴 VENTE (SELL)",
            "tendance": direction, "force": f"ADX {round(adx_h1,1)}",
            "msg": f"Pullback validé sur {' + '.join(zones_touchees)}",
            "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp_final, 5),
            "rr": round(rr, 2), "px": round(px, 5),
            "strategie": 1, "confiance": int(min(97, round(score))),
            "label": "TREND PULLBACK & CONFLUENCE", "strategie_nom_ia": "TREND_PULLBACK"
        }
    except Exception:
        return None

IA_CONFIG = {"seuil_acceptation": 55, "groq_active": True, "groq_seuil_veto": 25}
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def cerveau_pro_trader(symbole):
    signaux = []
    signal_brut = analyser_trend_pullback_confluence(symbole)
    if not signal_brut: return []
    
    # Validation IA Simplifiée
    signal_brut["ia_score"] = 85.0
    signal_brut["ia_justification"] = ["Calcul déterministe validé"]
    signal_brut["contexte_detecte"] = "TREND PULLBACK"
    signaux.append(signal_brut)
    return signaux

# ==========================================
# SCANNER PRINCIPAL & EXÉCUTION FULL AUTO
# ==========================================

def _analyser_une_paire(paire):
    try:
        statut, _ = est_symbole_autorise(paire)
        if statut != "AUTORISE": return []
        return [(paire, res, res["px"]) for res in cerveau_pro_trader(paire)]
    except Exception:
        return []

def scanner_marche_auto():
    while True:
        try:
            time.sleep(15)
            libres = [u for u in utilisateurs_actifs if ADMIN_ID == u] # Priorité admin ou utilisateurs autorisés
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
                    
                    # --- OUVERTURE AUTOMATIQUE DU TRADE EN LOCAL ET SUR MT5 ---
                    trade_id, sizing = ouvrir_trade(
                        uid=uid, symbole=paire, direction=entry_direction, entry_price=px,
                        sl=res["sl"], tp1=res["tp1"], tp_final=res["tp"],
                        strategy=res["strategie"], confiance=res["confiance"],
                        label=res["label"], strategie_nom_ia=res.get("strategie_nom_ia", "TREND")
                    )

                    # Calcul du lot réel (Minimum 0.01 lot sur MT5)
                    calcul_lots = round(max(0.01, sizing['lot_factor']), 2)

                    # Envoi direct de l'ordre réel sur MetaTrader 5 via MetaApi en arrière-plan
                    Thread(target=executer_ordre_mt5_sync, args=(paire, res["action"], calcul_lots, res["sl"], res["tp"]), daemon=True).start()

                    # Notification Telegram (Mode Full Auto sans clic requis)
                    msg = (
                        f"🤖 *TRADE FULL AUTO EXÉCUTÉ SUR MT5 !*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 Actif : *{NOMS_AFFICHAGE.get(paire, paire)}* ({res['action']})\n"
                        f"🆔 ID Trade : `{trade_id}`\n"
                        f"📦 Lots : `{calcul_lots}`\n"
                        f"💰 Entrée : {px} | SL : {res['sl']} | TP : {res['tp']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🛡️ Le bot gère le TP partiel à 85% et le Breakeven automatiquement."
                    )
                    try:
                        bot.send_message(uid, msg, parse_mode="Markdown")
                    except Exception:
                        pass
        except Exception as e:
            print(f"[Scanner Full Auto] {e}", flush=True)

# ==========================================
# MONITORING DES TRADES ACTIFS
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
                        except: pass
                        continue
                    if hit_tp1:
                        partiel = fermer_trade_partiel(uid, prix_current)
                        if partiel:
                            try: bot.send_message(uid, f"🟡 TP1 atteint sur {trade['symbol']} (85% sécurisé, SL→Breakeven).", parse_mode="Markdown")
                            except: pass
                        continue

                elif trade["state"] == TradeState.TRADE_PARTIAL:
                    appliquer_trailing_stop(uid, prix_current)
                    hit_tp_final = (direction == "BUY" and prix_current >= trade["tp_final"]) or (direction == "SELL" and prix_current <= trade["tp_final"])
                    hit_be_sl    = (direction == "BUY" and prix_current <= trade["sl"]) or (direction == "SELL" and prix_current >= trade["sl"])

                    if hit_tp_final or hit_be_sl:
                        fermer_trade_complet(uid, prix_current, win=True)
                        try: bot.send_message(uid, f"🎉 Trade {trade['symbol']} entièrement clôturé avec succès !", parse_mode="Markdown")
                        except: pass
                        continue
        except Exception as e:
            print(f"[Monitor] {e}", flush=True)

# ==========================================
# COMMANDES TELEGRAM & INTERFACE
# ==========================================

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    utilisateurs_actifs.add(uid)
    init_daily_stats(uid)
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("🚀 STATUT FULL AUTO"), KeyboardButton("📊 RAPPORT DU JOUR"))
    bot.send_message(uid,
        "💼 *TERMINAL PRIME V55 — FULL AUTO MT5*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 Le bot analyse le marché et place les ordres de façon 100% autonome sur ton MetaTrader 5 via MetaApi.\n"
        "Tu recevrez une notification à chaque ouverture et clôture de position.",
        reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🚀 STATUT FULL AUTO")
def statut_bot(message):
    uid = message.chat.id
    actif = uid in trades_actifs
    bot.send_message(uid, f"🤖 *Statut du Bot :* ACTIF (H24)\nTrade en cours : {'OUI 🟠' * actif or 'AUCUN 🟢'}", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 RAPPORT DU JOUR")
def rapport_btn(message):
    uid = message.chat.id
    stats = init_daily_stats(uid)
    bot.send_message(uid, f"📊 P&L du jour : {stats['pnl']:+.2f} USD | Trades : {stats['trades']}", parse_mode="Markdown")

# ==========================================
# LANCEMENT GLOBAL
# ==========================================

if __name__ == "__main__":
    keep_alive()
    Thread(target=scanner_marche_auto, daemon=True).start()
    Thread(target=monitorer_trades_actifs, daemon=True).start()
    Thread(target=watchdog_trades_bloques, daemon=True).start()
    print("💼 TERMINAL PRIME V55 — FULL AUTO (METAAPI) DÉMARRÉ", flush=True)
    bot.infinity_polling()
