"""
╔════════════════════════════════════════════════════════════════════════════╗
║   TERMINAL PRIME V56 — PURE SCALPING M1 + EXÉCUTION RÉELLE DERIV           ║
║                                                                            ║
║  ⚙️ NOUVEAU DANS CETTE VERSION :                                          ║
║   • Correction de l'erreur d'autorisation (est_autorise).                  ║
║   • Suppression totale de l'IA/Groq pour une exécution 100% mathématique   ║
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
import websocket
import pandas as pd
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
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

DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN", "").strip()
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089").strip()
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

class TradeState(Enum):
    TRADE_OPEN      = "TRADE_OUVERT"
    TRADE_PARTIAL   = "TP1_PARTIEL_BE"
    TRADE_WIN       = "GAGNÉ"
    TRADE_LOSS      = "PERDU"

# ==========================================
# LISTES DE PAIRES
# ==========================================

VOLATILE_PAIRS  = ["V10","V25","V50","V75","V100"]
COMMODITY_PAIRS = ["XAUUSD","XAGUSD"]
ELITE_PAIRS_MT5 = VOLATILE_PAIRS + COMMODITY_PAIRS

# ==========================================
# VARIABLES D'ÉTAT GLOBALES
# ==========================================

utilisateurs_actifs  = set()
derniere_alerte_auto = {}
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}

trades_actifs     = {}
trades_historique = {}
pnl_total  = {}
daily_stats = {}
lock_trade = Lock()

# ==========================================
# GESTION DES AUTORISATIONS
# ==========================================

def est_autorise(uid):
    if uid == ADMIN_ID: 
        return True
    return uid in utilisateurs_autorises

# ==========================================
# KEEP ALIVE (FLASK)
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
# PONT DERIV (Exécution réelle)
# ==========================================

DERIV_REST_BASE = "https://api.derivws.com"
_deriv_account_id_cache = None

def _deriv_headers():
    return {
        "Deriv-App-ID": DERIV_APP_ID,
        "Authorization": f"Bearer {DERIV_API_TOKEN}",
    }

def deriv_get_account_id(force_refresh=False):
    global _deriv_account_id_cache
    if _deriv_account_id_cache and not force_refresh:
        return _deriv_account_id_cache
    if not DERIV_API_TOKEN or not DERIV_APP_ID:
        raise RuntimeError("DERIV_API_TOKEN ou DERIV_APP_ID manquant.")

    resp = requests.get(f"{DERIV_REST_BASE}/trading/v1/options/accounts", headers=_deriv_headers(), timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Deriv get_accounts HTTP {resp.status_code}")

    data = resp.json()
    comptes = data.get("accounts") or data.get("data") or (data if isinstance(data, list) else [])
    
    cible = None
    for c in comptes:
        est_demo = bool(c.get("is_virtual") or c.get("is_demo") or str(c.get("type", "")).lower() == "demo")
        if DERIV_ACCOUNT_TYPE == "demo" and est_demo:
            cible = c; break
        if DERIV_ACCOUNT_TYPE == "real" and not est_demo:
            cible = c; break
    if not cible and comptes:
        cible = comptes[0]

    _deriv_account_id_cache = cible.get("account_id") or cible.get("loginid")
    return _deriv_account_id_cache

def _deriv_obtenir_ws_url(force_refresh=False):
    account_id = deriv_get_account_id(force_refresh=force_refresh)
    resp = requests.post(f"{DERIV_REST_BASE}/trading/v1/options/accounts/{account_id}/otp", headers=_deriv_headers(), timeout=10)
    data = resp.json()
    interieur = data.get("data", data)
    return (data.get("url") or data.get("websocket_url") or interieur.get("url"))

def _deriv_trading_request(payload, timeout=10, _retry=True):
    cle_attendue = None
    for k in ("buy", "sell", "portfolio", "proposal_open_contract", "contract_update", "balance"):
        if k in payload:
            cle_attendue = k; break
    ws = None
    try:
        url = _deriv_obtenir_ws_url()
        ws = websocket.create_connection(url, timeout=timeout, header=[f"Deriv-App-ID: {DERIV_APP_ID}"])
        ws.send(json.dumps(payload))
        debut = time.time()
        while time.time() - debut < timeout:
            resp = json.loads(ws.recv())
            if resp.get("error"): raise RuntimeError(f"Deriv API erreur: {resp['error'].get('message')}")
            if cle_attendue and cle_attendue in resp: return resp
            if resp.get("msg_type") == cle_attendue: return resp
        raise TimeoutError(f"Deriv: pas de réponse sous {timeout}s")
    except Exception as e:
        if _retry and any(m in str(e) for m in ("401", "Unauthorized", "OTP")):
            return _deriv_trading_request(payload, timeout=timeout, _retry=False)
        raise
    finally:
        if ws: ws.close()

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
    return resp.get("buy", {}).get("contract_id")

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
    return resp.get("balance", {})

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
    gran_real = granularite if granularite in (60, 300, 900, 3600) else 3600
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
            if ws: ws.close()
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
            if ws: ws.close()
            time.sleep(0.5)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.0015):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real: return False
    decalage = abs(prix_bot - prix_real) / prix_real
    return decalage <= tolerance

# ==========================================
# GESTION DU RISQUE ET DES TRADES
# ==========================================

def init_daily_stats(uid):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    if uid not in daily_stats or daily_stats[uid]["date"] != today:
        daily_stats[uid] = {
            "date": today, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0,
            "consecutive_losses": 0, "paused_until": None
        }
    return daily_stats[uid]

def utilisateur_peut_trader(uid):
    stats = init_daily_stats(uid)
    if stats["pnl"] <= -(CAPITAL_ACTUEL * RISK_CONFIG["daily_loss_limit_pct"] / 100.0): return False, "🛑 Limite de perte atteinte."
    if stats["paused_until"] and time.time() < stats["paused_until"]: return False, "⏸️ Pause anti-tilt active."
    if stats["trades"] >= RISK_CONFIG["max_trades_per_day"]: return False, "🛑 Limite de trades/jour atteinte."
    return True, None

def calculer_position_size(capital, risk_pct, prix_entree, prix_sl):
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)
    if distance_sl <= 0: return {"montant_risque": montant_risque, "distance_sl": 0}
    return {"montant_risque": round(montant_risque, 2), "distance_sl": round(distance_sl, 5)}

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, executer_reel=False):
    trade_id = "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"], entry_price, sl)
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
        "sl": sl, "tp1": tp1, "tp_final": tp_final, "state": TradeState.TRADE_OPEN,
        "timestamp_open": time.time(), "partial_closed": False, "partial_pnl": 0.0,
        "breakeven_active": False, "sizing": sizing, "deriv_contract_final": deriv_contract_final,
        "reel": executer_reel
    }
    return trade_id

def fermer_trade_complet(uid, exit_price, win):
    with lock_trade:
        if uid not in trades_actifs: return None
        trade = trades_actifs[uid]
        try:
            if trade.get("reel") and trade.get("deriv_contract_final"):
                try: deriv_fermer_contrat(trade["deriv_contract_final"])
                except: pass

            risque_initial = trade["sizing"]["montant_risque"]
            portion_restante = (1 - RISK_CONFIG["partial_tp_ratio"]) if trade["partial_closed"] else 1.0
            risque_portion = risque_initial * portion_restante

            if win:
                gain_ratio = abs(exit_price - trade["entry_price"]) / trade["sizing"]["distance_sl"] if trade["sizing"]["distance_sl"] > 0 else 1
                pnl_final = risque_portion * gain_ratio
            else:
                pnl_final = -risque_portion

            pnl_trade_total = trade.get("partial_pnl", 0.0) + pnl_final
            
            if uid not in trades_historique: trades_historique[uid] = []
            trades_historique[uid].append({
                "symbol": trade["symbol"], "direction": trade["direction"],
                "pnl": pnl_trade_total, "win": win, "timestamp": time.time()
            })

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_final
            stats = init_daily_stats(uid)
            stats["pnl"] += pnl_final
            stats["trades"] += 1
            if win:
                stats["wins"] += 1
                stats["consecutive_losses"] = 0
            else:
                stats["losses"] += 1
                stats["consecutive_losses"] += 1
                if stats["consecutive_losses"] >= RISK_CONFIG["max_consecutive_losses"]:
                    stats["paused_until"] = time.time() + (RISK_CONFIG["pause_duration_minutes"] * 60)
            
            return {"trade_id": trade["trade_id"], "pnl": pnl_trade_total, "win": win}
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
    return uid in trades_actifs

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
    c5  = obtenir_donnees_deriv(symbole, 300) 
    c1  = obtenir_donnees_deriv(symbole, 60)  

    if not c5 or not c1 or len(c5) < 30 or len(c1) < 20: return None

    try:
        df5  = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"])} for c in c5])
        df1  = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"])} for c in c1])
        px = float(df1['close'].iloc[-1])

        # BIAIS M5
        ema13_5, ema50_5 = _ema(df5['close'], 13), _ema(df5['close'], 50)
        direction = "BULL" if ema13_5.iloc[-2] > ema50_5.iloc[-2] else "BEAR"

        # CONDITIONS M1
        ema21_1 = _ema(df1['close'], 21)
        distance_ema = abs(px - float(ema21_1.iloc[-2])) / px
        if distance_ema > 0.0015: return None

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
            "direction": signal_dir,
            "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp, 5), "px": round(px, 5)
        }
    except:
        return None

# ==========================================
# PANNEAU DE CONTRÔLE ET COMMANDES TELEGRAM
# ==========================================

def obtenir_clavier(uid):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("🟢 AUTO-TRADING: ON" if CONTROL_STATE["auto_trading_active"] else "🔴 AUTO-TRADING: OFF"))
    markup.row(KeyboardButton("📊 STATUS LIVE"), KeyboardButton(f"⚙️ MODE ({CONTROL_STATE['mode']})"))
    markup.row(KeyboardButton("📜 HISTORIQUE"), KeyboardButton("📊 RAPPORT DU JOUR"))
    markup.row(KeyboardButton("🛑 STOP D'URGENCE"))
    return markup

@bot.message_handler(commands=['menu', 'controle', 'start'])
def afficher_menu(message):
    uid = message.chat.id
    if est_autorise(uid):
        utilisateurs_actifs.add(uid)
        init_daily_stats(uid)
        bot.send_message(uid, "🎛️ *PANNEAU DE CONTRÔLE - PURE SCALPING*", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and (m.text.startswith("🟢 AUTO-TRADING") or m.text.startswith("🔴 AUTO-TRADING")))
def toggle_auto_trading(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return bot.send_message(uid, "❌ Réservé à l'admin.")
    CONTROL_STATE["auto_trading_active"] = not CONTROL_STATE["auto_trading_active"]
    etat = "🟢 ACTIVÉ" if CONTROL_STATE["auto_trading_active"] else "🔴 DÉSACTIVÉ"
    bot.send_message(uid, f"⚙️ *Auto-trading : {etat}*", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("⚙️ MODE"))
def toggle_mode(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return bot.send_message(uid, "❌ Réservé à l'admin.")
    CONTROL_STATE["mode"] = "MULTI" if CONTROL_STATE["mode"] == "SOLO" else "SOLO"
    bot.send_message(uid, f"⚙️ Mode : *{CONTROL_STATE['mode']}*", reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🛑 STOP D'URGENCE")
def stop_urgence(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return
    CONTROL_STATE["auto_trading_active"] = False
    CONTROL_STATE["stop_urgence_actif"] = not CONTROL_STATE["stop_urgence_actif"]
    etat = "🛑 STOP D'URGENCE ACTIF (Trading bloqué)" if CONTROL_STATE["stop_urgence_actif"] else "✅ STOP D'URGENCE LEVÉ"
    bot.send_message(uid, etat, reply_markup=obtenir_clavier(uid))

@bot.message_handler(func=lambda m: m.text == "📊 STATUS LIVE")
def status_live(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    lignes = ["📊 *STATUS LIVE*\n━━━━━━━━━━━━━━━━━━━━━━"]
    lignes.append(f"Auto-trading : {'🟢 ON' if CONTROL_STATE['auto_trading_active'] else '🔴 OFF'}")
    lignes.append(f"Mode : {CONTROL_STATE['mode']}")
    lignes.append(f"Stop d'urgence : {'🛑 ACTIF' if CONTROL_STATE['stop_urgence_actif'] else '✅ inactif'}")
    try:
        positions = deriv_positions_ouvertes()
        if not positions:
            lignes.append("Aucun contrat ouvert sur Deriv actuellement.")
        else:
            lignes.append(f"*{len(positions)} contrat(s) ouvert(s) :*")
            for p in positions:
                lignes.append(f"  {p.get('symbol')} {p.get('contract_type')} | Mise {p.get('buy_price')}$ | P&L {p.get('profit', 0):+.2f}$")
    except Exception as e:
        lignes.append(f"⚠️ Impossible de récupérer les positions : {e}")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 RAPPORT DU JOUR")
def rapport_bouton(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    stats = init_daily_stats(uid)
    txt = (f"📊 *RAPPORT DU JOUR*\n"
           f"Trades exécutés : {stats['trades']}/{RISK_CONFIG['max_trades_per_day']}\n"
           f"✅ Gagnés : {stats['wins']} | ❌ Perdus : {stats['losses']}\n"
           f"💰 P&L : {stats['pnl']:+.2f} USD\n"
           f"🏦 P&L total cumulé : {pnl_total.get(uid,0):+.2f} USD")
    bot.send_message(uid, txt, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📜 HISTORIQUE")
def historique_bouton(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    hist = trades_historique.get(uid, [])
    if not hist: return bot.send_message(uid, "📭 Aucun trade dans l'historique.")
    lignes = ["📜 *HISTORIQUE (10 derniers trades)*\n━━━━━━━━━━━━━━━━━━━━━━"]
    for t in hist[-10:][::-1]:
        emoji = "✅" if t["win"] else "❌"
        date_str = datetime.datetime.fromtimestamp(t["timestamp"]).strftime("%d/%m %H:%M")
        lignes.append(f"{emoji} {t['symbol']} {t['direction']} | {t['pnl']:+.2f}$ | {date_str}")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

# ==========================================
# BOUCLE PRINCIPALE (SCAN & MONITOR)
# ==========================================

def scanner_marche_auto():
    while True:
        try:
            time.sleep(5) 
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres: continue

            resultats = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(analyser_scalping_multi_tf, p): p for p in ELITE_PAIRS_MT5}
                for future in as_completed(futures, timeout=10):
                    try:
                        paire = futures[future]
                        res = future.result()
                        if res:
                            px = obtenir_prix_broker_realtime(paire)
                            if px and valider_prix_avant_signal(paire, px):
                                resultats.append((paire, res, px))
                    except: pass

            for paire, res, px in resultats:
                cle = f"{paire}_SCALP"
                if cle in derniere_alerte_auto and (time.time() - derniere_alerte_auto[cle] < 30):
                    continue 
                derniere_alerte_auto[cle] = time.time()
                
                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue
                    peut_trader, _ = utilisateur_peut_trader(uid)
                    if not peut_trader: continue
                    
                    if peut_ouvrir_automatiquement(paire):
                        try:
                            trade_id = ouvrir_trade(
                                uid, paire, res["direction"], px, res["sl"], res["tp1"], res["tp"],
                                executer_reel=True
                            )
                            bot.send_message(uid, f"⚡ *SCALP AUTO OUVERT* : {paire} {res['direction']} @ {px:.5f}", parse_mode="Markdown")
                        except Exception as e:
                            print(f"[Exec Auto] Erreur: {e}")

        except Exception as e: print(f"[Scanner] {e}")

def monitorer_trades_actifs():
    while True:
        try:
            time.sleep(2) 
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
    print("💼 TERMINAL PRIME V56 PURE SCALPING DÉMARRÉ", flush=True)
    bot.infinity_polling()
