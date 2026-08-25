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
║   • Quand auto-trading est OFF (par défaut, sécurité) : comportement      ║
║     identique à avant — notifications + bouton "Copier" manuel.          ║
║                                                                            ║
║  ⚠️ Trades exécutés via des contrats "Multiplicateurs" Deriv (CFD à       ║
║  effet de levier avec SL/TP intégrés). Pas de fermeture partielle native  ║
║  sur ce produit : la logique "TP1 85% + 15% final" est reproduite en     ║
║  ouvrant DEUX contrats séparés à l'entrée (85% de la mise avec TP1,       ║
║  15% avec le TP final) — chacun se ferme automatiquement côté Deriv.      ║
║                                                                            ║
║  Variable d'environnement à ajouter sur Render :                        ║
║     DERIV_API_TOKEN  -> ton token API Deriv (gratuit, app.deriv.com →    ║
║                         Paramètres → Sécurité et niveaux → API Token)    ║
║  (en plus de celles déjà utilisées : TELEGRAM_TOKEN régénéré, FMP_API_KEY,║
║   GROQ_API_KEY)                                                           ║
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

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")   # ⚠️ mets ton NOUVEAU token régénéré sur Render, jamais en dur ici
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ADMIN_ID = 5968288964
CAPITAL_ACTUEL = 40650
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")

DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN", "")
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "")   # ⚠️ App ID de la NOUVELLE API (ex: "32Oh8ivJRXsJrKUqVgYhR"), trouvé sur developers.deriv.com → Registered Apps
DERIV_ACCOUNT_TYPE = os.environ.get("DERIV_ACCOUNT_TYPE", "demo")   # "demo" ou "real" — quel compte Options utiliser
DERIV_LEGACY_APP_ID = os.environ.get("DERIV_LEGACY_APP_ID", "1089")  # app_id public legacy, utilisé UNIQUEMENT pour les données de marché (ticks/candles)

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
    "signal_validity_seconds": 20,   # ✅ V57: réduit (45→20) — un signal de scalping M1 périme vite
    "max_rr_degradation_pct": 40,
}

# ==========================================
# ✅ NOUVEAU : ÉTAT DU PANNEAU DE CONTRÔLE
# ==========================================

CONTROL_STATE = {
    "auto_trading_active": False,   # démarre OFF par sécurité — à activer via le menu Telegram
    "mode": "SOLO",                 # SOLO = un seul trade actif à la fois (comme l'EA vidéo) | MULTI = plusieurs en parallèle
    "stake_usd": 1.0,                # mise en USD par trade sur Deriv — reste petit en démo/tests
    "multiplier": 10,                # effet de levier du contrat Multiplicateur
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

# ✅ V64 : Gold/Argent désactivés par défaut — le backtest EMA a montré une
# espérance négative sur Gold, et l'Argent n'a pas encore été testé. Active
# via /Commodities XAUUSD ON une fois testé si tu veux les réintégrer.
commodity_pairs_active = {
    "XAUUSD": False, "XAGUSD": False,
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
    return "Terminal Prime V55 — Deriv Edition"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# ✅ NOUVEAU : PONT DERIV (exécution réelle, gratuite, sans PC/VPS)
# ==========================================
# Même style que le reste du bot (obtenir_donnees_deriv, etc.) : connexion
# websocket synchrone ouverte à la demande, avec authorize + requête + lecture
# de la réponse correspondante, puis fermeture. Pas d'asyncio nécessaire.
#
# Produit utilisé : "Multiplicateurs" Deriv (CFD à effet de levier avec
# stop_loss/take_profit intégrés, exécutés automatiquement côté serveur
# Deriv — pas besoin de surveiller nous-mêmes le déclenchement du SL/TP).
#
# ⚠️ Pas de fermeture partielle native sur ce produit : on ouvre DEUX
# contrats séparés à l'entrée pour reproduire "TP1 85% + 15% final" :
#   - contrat A : 85% de la mise, cible = TP1
#   - contrat B : 15% de la mise, cible = TP final
# Quand le contrat A se termine (gagné ou perdu), on ajuste le SL du
# contrat B (breakeven) via contract_update.

DERIV_REST_BASE = "https://api.derivws.com"

def _deriv_headers():
    return {
        "Deriv-App-ID": DERIV_APP_ID,
        "Authorization": f"Bearer {DERIV_API_TOKEN}",
    }

_deriv_account_id_cache = None
_deriv_ws_cache = {"url": None, "obtained_at": 0}
_deriv_lock = Lock()

def deriv_get_account_id(force_refresh=False):
    """
    REST: liste les comptes Options du login (GET /trading/v1/options/accounts)
    et choisit celui correspondant à DERIV_ACCOUNT_TYPE (demo/real).
    Résultat mis en cache (le compte ne change pas en cours de route).
    """
    global _deriv_account_id_cache
    if _deriv_account_id_cache and not force_refresh:
        return _deriv_account_id_cache
    if not DERIV_API_TOKEN or not DERIV_APP_ID:
        raise RuntimeError("DERIV_API_TOKEN ou DERIV_APP_ID manquant (variables d'environnement Render).")

    resp = requests.get(f"{DERIV_REST_BASE}/trading/v1/options/accounts",
                        headers=_deriv_headers(), timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Deriv get_accounts HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    comptes = data.get("accounts") or data.get("data") or (data if isinstance(data, list) else [])
    if not comptes:
        raise RuntimeError(f"Deriv: aucun compte Options trouvé — réponse brute: {resp.text[:300]}")

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
        cible = comptes[0]  # repli: le seul compte disponible

    account_id = cible.get("account_id") or cible.get("accountId") or cible.get("id") or cible.get("loginid")
    if not account_id:
        raise RuntimeError(f"Deriv: impossible de trouver l'account_id dans: {cible}")

    _deriv_account_id_cache = account_id
    print(f"[Deriv] Compte Options sélectionné: {account_id}", flush=True)
    return account_id

def _deriv_obtenir_ws_url(force_refresh=False):
    """
    REST: demande un OTP (POST /trading/v1/options/accounts/{id}/otp) et
    récupère l'URL WebSocket prête à l'emploi. L'OTP est de courte durée,
    donc on en redemande un à chaque connexion (pas de cache long).
    """
    account_id = deriv_get_account_id(force_refresh=force_refresh)
    resp = requests.post(f"{DERIV_REST_BASE}/trading/v1/options/accounts/{account_id}/otp",
                        headers=_deriv_headers(), timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Deriv OTP HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    interieur = data.get("data", data)
    url = (data.get("url") or data.get("websocket_url") or data.get("ws_url")
           or interieur.get("url") or interieur.get("websocket_url") or interieur.get("ws_url"))
    if not url:
        raise RuntimeError(f"Deriv OTP: pas d'URL dans la réponse: {resp.text[:300]}")
    return url

def _deriv_trading_request(payload, timeout=10, _retry=True):
    """
    Ouvre une connexion WebSocket fraîche (URL avec OTP intégré, obtenue
    juste avant), envoie une requête de trading, retourne la réponse
    correspondante. Redemande un nouvel OTP et retente une fois en cas
    d'échec d'auth (OTP expiré entre-temps).
    """
    cle_attendue = None
    for k in ("buy", "sell", "portfolio", "proposal_open_contract",
              "contract_update", "balance", "proposal"):
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
        raise TimeoutError(f"Deriv: pas de réponse '{cle_attendue}' sous {timeout}s")
    except Exception as e:
        if _retry and any(m in str(e) for m in ("401", "Unauthorized", "OTP", "otp")):
            return _deriv_trading_request(payload, timeout=timeout, _retry=False)
        raise
    finally:
        try:
            if ws: ws.close()
        except Exception:
            pass

def deriv_symbole(symbole_bot):
    """Traduit le symbole interne du bot vers le symbole Deriv (réutilise prefixer_symbole)."""
    return prefixer_symbole(symbole_bot)

def deriv_ouvrir_contrat(symbole, direction, stake, multiplier, sl=None, tp=None):
    """
    Ouvre un contrat Multiplicateur. direction: "BUY" -> MULTUP, "SELL" -> MULTDOWN.
    stake : mise en USD. multiplier : effet de levier (ex 10, 20... selon l'actif).
    sl/tp : niveaux de PRIX (pas de distance) — convertis en limit_order.
    Retourne le contract_id.
    """
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

    resp = _deriv_trading_request(payload)
    buy_info = resp.get("buy", {})
    contract_id = buy_info.get("contract_id")
    print(f"[Deriv] Contrat ouvert {sym} {contract_type} mise={stake} → contract_id={contract_id}", flush=True)
    return contract_id

def deriv_modifier_contrat(contract_id, sl=None, tp=None):
    """Met à jour le SL/TP d'un contrat ouvert (ex: passage en breakeven)."""
    limit_order = {}
    if sl is not None:
        limit_order["stop_loss"] = round(float(sl), 5)
    if tp is not None:
        limit_order["take_profit"] = round(float(tp), 5)
    payload = {"contract_update": 1, "contract_id": contract_id,
               "limit_order": limit_order}
    return _deriv_trading_request(payload)

def deriv_fermer_contrat(contract_id):
    """Ferme (vend) un contrat au marché immédiatement."""
    payload = {"sell": contract_id, "price": 0}
    return _deriv_trading_request(payload)

def deriv_statut_contrat(contract_id):
    """
    Retourne l'état actuel d'un contrat : is_sold (0/1), profit, current_spot...
    Utilisé par le monitoring pour détecter qu'un TP/SL a été exécuté
    automatiquement côté Deriv (sans qu'on ait eu besoin de le déclencher).
    """
    payload = {"proposal_open_contract": 1, "contract_id": contract_id}
    resp = _deriv_trading_request(payload)
    return resp.get("proposal_open_contract", {})

def deriv_positions_ouvertes():
    """Liste les contrats actuellement ouverts sur le compte."""
    payload = {"portfolio": 1}
    resp = _deriv_trading_request(payload)
    return resp.get("portfolio", {}).get("contracts", [])

def deriv_connecter():
    """Vérifie que le token/app_id fonctionnent (appelée au démarrage / à l'activation)."""
    payload = {"balance": 1}
    resp = _deriv_trading_request(payload)
    solde = resp.get("balance", {})
    print(f"[Deriv] Connecté — solde: {solde.get('balance')} {solde.get('currency')}", flush=True)
    return solde

def peut_ouvrir_automatiquement(symbole):
    """Respecte le mode SOLO/MULTI et le stop d'urgence avant toute ouverture auto."""
    if not CONTROL_STATE["auto_trading_active"]:
        return False
    if CONTROL_STATE["stop_urgence_actif"]:
        return False
    if CONTROL_STATE["mode"] == "SOLO":
        return len(trades_actifs) == 0
    return symbole not in {t.get("symbol") for t in trades_actifs.values()}

# ==========================================
# UTILITAIRES PRIX (base V38)
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
    for tentative in range(2):
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            # ✅ FIX: l'abonnement live "ticks" est rejeté par Deriv pour ces
            # symboles ("Symbol R_25 is invalid"), alors que "ticks_history"
            # (même endpoint que celui déjà utilisé partout ailleurs dans le
            # bot pour les bougies, et qui fonctionne de manière fiable)
            # accepte parfaitement ces mêmes symboles. On demande donc juste
            # le tout dernier tick via ticks_history au lieu d'un abonnement.
            ws.send(json.dumps({
                "ticks_history": sym, "end": "latest", "count": 1, "style": "ticks"
            }))
            res = json.loads(ws.recv())
            ws.close()
            historique = res.get("history", {})
            prix_liste = historique.get("prices", [])
            if prix_liste:
                prix = float(prix_liste[-1])
                prix_broker[symbole] = {"price": prix, "source": "Deriv",
                                        "timestamp": time.time()}
                return prix
            else:
                print(f"[Deriv Real-time {symbole}] réponse sans 'history.prices': {res}", flush=True)
        except Exception as e:
            print(f"[Deriv Real-time {symbole}] tentative {tentative+1}/2 échouée: "
                  f"{type(e).__name__}: {e}", flush=True)
            try: ws.close()
            except Exception: pass
            time.sleep(0.5)
    return None

def valider_prix_avant_signal(symbole, prix_bot, tolerance=0.001):
    prix_real = obtenir_prix_broker_realtime(symbole)
    if not prix_real:
        print(f"[Validation {symbole}] Impossible obtenir prix broker", flush=True)
        return False
    decalage = abs(prix_bot - prix_real) / prix_real
    if decalage > tolerance:
        print(f"[Validation {symbole}] ÉCART {decalage*100:.2f}% — REJETÉ", flush=True)
        return False
    return True

# ==========================================
# GESTION DU RISQUE PROFESSIONNELLE
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
        return False, (f"🛑 Limite de perte journalière atteinte "
                       f"({RISK_CONFIG['daily_loss_limit_pct']}% du capital). "
                       f"Trading suspendu jusqu'à demain.")
    en_pause, jusqua = utilisateur_en_pause(uid)
    if en_pause:
        minutes_restantes = int((jusqua - time.time()) / 60)
        return False, (f"⏸️ Pause anti-tilt active après "
                       f"{RISK_CONFIG['max_consecutive_losses']} pertes consécutives.\n"
                       f"Reprise dans {minutes_restantes} minutes.")
    if max_trades_jour_atteint(uid):
        return False, (f"🛑 Limite de {RISK_CONFIG['max_trades_per_day']} trades/jour atteinte. "
                       f"Reviens demain — la discipline fait les gagnants.")
    return True, None

def calculer_position_size(capital, risk_pct, prix_entree, prix_sl, symbole):
    montant_risque = capital * (risk_pct / 100.0)
    distance_sl = abs(prix_entree - prix_sl)
    if distance_sl <= 0:
        return {"montant_risque": montant_risque, "lot_factor": 0, "distance_sl": 0}
    lot_factor = montant_risque / distance_sl
    return {
        "montant_risque": round(montant_risque, 2),
        "lot_factor": round(lot_factor, 4),
        "distance_sl": round(distance_sl, 5),
        "distance_sl_pct": round((distance_sl / prix_entree) * 100, 3) if prix_entree else 0
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
        print(f"[Risk] {uid} EN PAUSE anti-tilt ({stats['consecutive_losses']} pertes consécutives)", flush=True)
    return stats

# ==========================================
# OUVERTURE / FERMETURE DE TRADE
# ✅ MODIFIÉ : appelle Deriv pour une exécution RÉELLE quand
# executer_reel=True (auto-trading actif ou copie manuelle avec auto ON).
# ==========================================

def create_trade_id():
    return "TRD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def ouvrir_trade(uid, symbole, direction, entry_price, sl, tp1, tp_final, strategy, confiance,
                 label="SIGNAL", strategie_nom_ia="?", ia_score=None, gemini_score=None,
                 contexte_marche=None, executer_reel=False):
    """
    ✅ NOUVEAU paramètre executer_reel : si True, ouvre 2 vrais contrats
    Deriv (85% mise → TP1, 15% mise → TP final) AVANT d'enregistrer le
    trade localement. Si l'ouverture réelle échoue, le trade n'est PAS
    enregistré (on ne veut jamais suivre un trade fantôme).
    """
    trade_id = create_trade_id()
    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"],
                                    entry_price, sl, symbole)

    deriv_contract_tp1 = None
    deriv_contract_final = None
    if executer_reel:
        stake_total = CONTROL_STATE["stake_usd"]
        multiplier = CONTROL_STATE["multiplier"]
        stake_tp1 = round(stake_total * RISK_CONFIG["partial_tp_ratio"], 2)
        stake_final = round(stake_total - stake_tp1, 2)

        deriv_contract_tp1 = deriv_ouvrir_contrat(
            symbole, direction, stake_tp1, multiplier, sl=sl, tp=tp1)
        deriv_contract_final = deriv_ouvrir_contrat(
            symbole, direction, stake_final, multiplier, sl=sl, tp=tp_final)
        print(f"[Deriv] Trade réel ouvert {symbole} {direction} → "
              f"contrat TP1={deriv_contract_tp1} (${stake_tp1}) / "
              f"contrat FINAL={deriv_contract_final} (${stake_final})", flush=True)

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
        "deriv_contract_tp1": deriv_contract_tp1,
        "deriv_contract_final": deriv_contract_final,
        "reel": executer_reel,
    }
    print(f"[Trade Opened] {uid}: {trade_id} {symbole} {direction} @ {entry_price} "
          f"(Risque: ${sizing['montant_risque']}) — {'RÉEL' if executer_reel else 'simulé'}", flush=True)
    return trade_id, sizing

def fermer_trade_complet(uid, exit_price, win):
    with lock_trade:
        if uid not in trades_actifs:
            return None
        trade    = trades_actifs[uid]
        trade_id = trade["trade_id"]

        try:
            # ✅ Fermeture réelle Deriv : ferme le contrat "final" restant s'il
            # est encore ouvert (le contrat TP1, lui, s'est déjà auto-fermé
            # côté Deriv quand son propre TP/SL a été touché).
            if trade.get("reel") and trade.get("deriv_contract_final"):
                try:
                    statut = deriv_statut_contrat(trade["deriv_contract_final"])
                    if not statut.get("is_sold"):
                        deriv_fermer_contrat(trade["deriv_contract_final"])
                except Exception as e:
                    print(f"[Deriv] Erreur fermeture contrat final {trade['deriv_contract_final']}: {e}", flush=True)

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

            try:
                ia_enregistrer_resultat(
                    symbol=trade["symbol"],
                    strategie_nom=trade.get("strategie_nom_ia", "?"),
                    score=trade.get("ia_score") if trade.get("ia_score") is not None else trade.get("confiance", 0),
                    timeframe="H1",
                    win=win,
                    tp_atteint=win,
                    sl_atteint=(not win),
                    drawdown_pct=0,
                    avis_ia_score=trade.get("ia_score"),
                    gemini_score=trade.get("gemini_score"),
                    sl=trade.get("sl_original"),
                    tp=trade.get("tp_final"),
                    duree_secondes=duration_seconds,
                    contexte_marche=trade.get("contexte_marche"),
                )
            except Exception as e:
                print(f"[IA Learning] Erreur enregistrement: {e}", flush=True)

            print(f"[Trade Closed] {uid}: {trade_id} PnL final={pnl_final:.2f} | "
                  f"PnL total trade={pnl_trade_total:.2f}", flush=True)
            return {"trade_id": trade_id, "pnl": pnl_trade_total, "pnl_final_portion": pnl_final,
                    "win": win, "duration": duration_seconds}

        except Exception as e:
            print(f"[Trade Closed] ⚠️ ERREUR pendant la clôture de {uid}/{trade_id}: {e}", flush=True)
            try:
                bot.send_message(uid,
                    f"⚠️ Trade {trade.get('symbol','?')} clôturé (erreur interne lors du calcul détaillé).\n"
                    f"Consulte /historique pour vérifier. Le trading reprend normalement.",
                    parse_mode="Markdown")
            except Exception:
                pass
            return {"trade_id": trade_id, "pnl": 0.0, "pnl_final_portion": 0.0,
                    "win": win, "duration": time.time() - trade.get("timestamp_open", time.time()),
                    "erreur": True}

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

            # ✅ Avec Deriv, le contrat TP1 s'est déjà fermé automatiquement
            # côté serveur (SL/TP intégrés au contrat) — rien à fermer ici.
            # Il ne reste qu'à faire passer le contrat "final" en breakeven.

            trade["partial_closed"]   = True
            trade["partial_pnl"]      = pnl_partiel
            trade["breakeven_active"] = True
            trade["state"]            = TradeState.TRADE_PARTIAL

            buffer = trade["entry_price"] * RISK_CONFIG["breakeven_buffer_pct"]
            if trade["direction"] == "BUY":
                trade["sl"] = trade["entry_price"] + buffer
            else:
                trade["sl"] = trade["entry_price"] - buffer

            if trade.get("reel") and trade.get("deriv_contract_final"):
                try:
                    deriv_modifier_contrat(trade["deriv_contract_final"], sl=trade["sl"])
                except Exception as e:
                    print(f"[Deriv] Erreur modification SL breakeven: {e}", flush=True)

            pnl_total[uid] = pnl_total.get(uid, 0) + pnl_partiel
            stats = init_daily_stats(uid)
            stats["pnl"] += pnl_partiel

            print(f"[Partial TP] {uid}: {trade['trade_id']} 85% fermé (+{pnl_partiel:.2f}), "
                  f"SL → Breakeven {trade['sl']:.5f}", flush=True)

            return {"pnl_partiel": round(pnl_partiel, 2), "nouveau_sl": trade["sl"]}

        except Exception as e:
            print(f"[Partial TP] ⚠️ ERREUR pour {uid}: {e}", flush=True)
            return None

def appliquer_trailing_stop(uid, prix_current):
    if uid not in trades_actifs:
        return False
    trade = trades_actifs[uid]
    if not trade["breakeven_active"]:
        return False
    distance_trail = prix_current * RISK_CONFIG["trailing_stop_distance_pct"]
    nouveau_sl = None
    if trade["direction"] == "BUY":
        nouveau_sl_potentiel = prix_current - distance_trail
        if nouveau_sl_potentiel > trade["sl"]:
            trade["sl"] = nouveau_sl_potentiel
            nouveau_sl = nouveau_sl_potentiel
            trade["trailing_active"] = True
    else:
        nouveau_sl_potentiel = prix_current + distance_trail
        if nouveau_sl_potentiel < trade["sl"]:
            trade["sl"] = nouveau_sl_potentiel
            nouveau_sl = nouveau_sl_potentiel
            trade["trailing_active"] = True

    if nouveau_sl is not None:
        if trade.get("reel") and trade.get("deriv_contract_final"):
            try:
                deriv_modifier_contrat(trade["deriv_contract_final"], sl=nouveau_sl)
            except Exception as e:
                print(f"[Deriv] Erreur trailing stop: {e}", flush=True)
        return True
    return False

def utilisateur_a_trade_actif(uid):
    return uid in trades_actifs and trades_actifs[uid]["state"] in (
        TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL
    )

# ==========================================
# WATCHDOG ANTI-BLOCAGE
# ==========================================

def watchdog_trades_bloques():
    while True:
        try:
            time.sleep(300)
            maintenant = time.time()
            for uid in list(trades_actifs.keys()):
                trade = trades_actifs.get(uid)
                if not trade:
                    continue
                age_heures = (maintenant - trade.get("timestamp_open", maintenant)) / 3600
                if trade["state"] not in (TradeState.TRADE_OPEN, TradeState.TRADE_PARTIAL):
                    print(f"[Watchdog] {uid} état incohérent ({trade['state']}) → nettoyage forcé", flush=True)
                    trades_actifs.pop(uid, None)
                    try:
                        bot.send_message(uid,
                            "🔧 Un trade bloqué a été nettoyé automatiquement. "
                            "Tu peux recevoir de nouveaux signaux normalement.",
                            parse_mode="Markdown")
                    except Exception:
                        pass
                    continue
                if age_heures >= RISK_CONFIG["max_trade_age_hours"]:
                    prix_current = obtenir_prix_broker_realtime(trade["symbol"])
                    if prix_current:
                        if trade["direction"] == "BUY":
                            win_watchdog = prix_current >= trade["entry_price"]
                        else:
                            win_watchdog = prix_current <= trade["entry_price"]
                        print(f"[Watchdog] {uid} trade {trade['trade_id']} ouvert depuis "
                              f"{age_heures:.1f}h → clôture forcée", flush=True)
                        fermer_trade_complet(uid, prix_current, win=win_watchdog)
                        try:
                            bot.send_message(uid,
                                f"⏱️ Trade {trade['symbol']} clôturé automatiquement après "
                                f"{RISK_CONFIG['max_trade_age_hours']}h (sécurité anti-blocage).\n"
                                f"Consulte /historique pour le détail.",
                                parse_mode="Markdown")
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Watchdog] {e}", flush=True)

# ==========================================
# SESSIONS / KILLZONES
# ==========================================

PAIRES_SESSION_ASIE    = ["AUDJPY","CADJPY","CHFJPY","USDJPY","EURJPY","AUDUSD","AUDCAD","XAUUSD","XAGUSD"]
PAIRES_SESSION_LONDRES = ["EURUSD","GBPUSD","EURCHF","USDCHF","CADCHF","EURJPY","EURAUD","XAUUSD","XAGUSD"]
PAIRES_SESSION_NY      = ["EURUSD","GBPUSD","USDCAD","USDCHF","AUDUSD","XAUUSD","XAGUSD"]

def get_session_active():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    paires, sessions = [], []
    if 0.0 <= h < 7.0:
        paires += PAIRES_SESSION_ASIE;    sessions.append("ASIE")
    if 7.0 <= h < 8.0:
        paires += PAIRES_SESSION_ASIE + PAIRES_SESSION_LONDRES; sessions.append("ASIE+LONDRES")
    if 8.0 <= h <= 10.0:
        paires += PAIRES_SESSION_LONDRES; sessions.append("LONDRES")
    if 12.0 <= h <= 15.0:
        paires += PAIRES_SESSION_NY;      sessions.append("NEW_YORK")
    if not sessions:
        return None, []
    return "+".join(sessions), list(dict.fromkeys(paires))

def dans_killzone():
    session, _ = get_session_active()
    return session is not None

def nom_killzone():
    h = datetime.datetime.utcnow().hour + datetime.datetime.utcnow().minute / 60.0
    if 7.0 <= h < 8.0:   return "🌏🇬🇧 Asie+Londres (07h-08h)"
    if 0.0 <= h < 7.0:   return "🌏 Asian Killzone (00h-07h)"
    if 8.0 <= h <= 10.0: return "🇬🇧 London Killzone (08h-10h)"
    if 12.0 <= h <= 15.0:return "🇺🇸 New York Killzone (12h-15h)"
    return "⏳ Hors session"

def est_symbole_autorise(symbole):
    if symbole in VOLATILE_PAIRS:
        if not volatility_pairs_active.get(symbole, True):
            return "BLOCAGE_TOTAL", f"{symbole} désactivé"
        return "AUTORISE", ""
    now     = datetime.datetime.utcnow()
    j, h    = now.weekday(), now.hour + now.minute / 60.0
    weekend = (j == 4 and h >= 21) or j == 5 or (j == 6 and h < 21)
    if weekend:
        return "BLOCAGE_TOTAL", "Week-end"
    if symbole in COMMODITY_PAIRS:
        if not commodity_pairs_active.get(symbole, True):
            return "BLOCAGE_TOTAL", f"{symbole} désactivé"
        return "AUTORISE", ""
    session, paires_session = get_session_active()
    if session is None:
        return "HORS_SESSION", "🔒 Hors Killzone"
    if symbole in paires_session:
        return "AUTORISE", ""
    return "HORS_SESSION", f"🔒 {symbole} inactif en {session}"

# ==========================================
# STRATÉGIE : TREND PULLBACK & CONFLUENCE
# ==========================================

def calculer_cpr_journalier(symbole):
    h1 = obtenir_donnees_deriv(symbole, 3600)
    if not h1 or len(h1) < 30:
        return None
    try:
        df = pd.DataFrame([{
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
            "epoch": int(c["epoch"])
        } for c in h1])
        df['date'] = pd.to_datetime(df['epoch'], unit='s').dt.date
        daily = df.groupby('date').agg({'open':'first','high':'max',
                                         'low':'min','close':'last'}).reset_index()
        if len(daily) < 2:
            return None
        prev_day = daily.iloc[-2]
        pdh, pdl, pdc = float(prev_day['high']), float(prev_day['low']), float(prev_day['close'])
        pivot = (pdh + pdl + pdc) / 3
        bcpr  = (pdh + pdl) / 2
        tcpr  = (pivot - bcpr) + pivot
        top_cpr = max(bcpr, tcpr)
        bot_cpr = min(bcpr, tcpr)
        cpr_width_pct = ((top_cpr - bot_cpr) / pivot) * 100 if pivot else 0
        etat_cpr = "Étroit (Tendance)" if cpr_width_pct < 0.15 else "Large (Range)"
        return {
            "PDH": pdh, "PDL": pdl, "PIVOT": pivot,
            "TCPR": top_cpr, "BCPR": bot_cpr,
            "ETAT": etat_cpr, "WIDTH": cpr_width_pct
        }
    except Exception as e:
        print(f"[CPR/{symbole}] {e}", flush=True)
        return None

def detecter_chandeliers_pdf(df):
    if len(df) < 3:
        return "NONE", 0
    try:
        last = df.iloc[-2]
        prev = df.iloc[-3]
        o, h, l, c = float(last['open']), float(last['high']), float(last['low']), float(last['close'])
        po, pc = float(prev['open']), float(prev['close'])
        body  = abs(c - o)
        rng   = h - l
        if rng == 0:
            return "NONE", 0
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        if lower_wick > body * 1.8 and upper_wick < body:
            return "PIN_BULL", lower_wick
        if upper_wick > body * 1.8 and lower_wick < body:
            return "PIN_BEAR", upper_wick
        if pc < po and c > o and c > po and o < pc:
            return "ENGULFING_BULL", body
        if pc > po and c < o and c < po and o > pc:
            return "ENGULFING_BEAR", body
        if body > rng * 0.75:
            return ("MARUBOZU_BULL" if c > o else "MARUBOZU_BEAR"), body
        return "NONE", 0
    except Exception:
        return "NONE", 0

def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def detecter_swing_points(df, ordre=3):
    highs = df['high'].values
    lows = df['low'].values
    n = len(df)
    swing_highs, swing_lows = [], []
    for i in range(ordre, n - ordre):
        fenetre_h = highs[i-ordre:i+ordre+1]
        fenetre_l = lows[i-ordre:i+ordre+1]
        if highs[i] == fenetre_h.max():
            swing_highs.append(float(highs[i]))
        if lows[i] == fenetre_l.min():
            swing_lows.append(float(lows[i]))
    return swing_highs, swing_lows

def detecter_niveaux_cles(df, lookback=80, tolerance_cluster=0.0015):
    try:
        sub = df.iloc[-lookback:] if len(df) > lookback else df
        swing_highs, swing_lows = detecter_swing_points(sub, ordre=3)
        tous = sorted(swing_highs + swing_lows)
        if not tous:
            return []
        clusters = []
        for prix in tous:
            place = False
            for cl in clusters:
                if abs(prix - cl["moyenne"]) / prix < tolerance_cluster * 3:
                    cl["membres"].append(prix)
                    cl["moyenne"] = sum(cl["membres"]) / len(cl["membres"])
                    place = True
                    break
            if not place:
                clusters.append({"moyenne": prix, "membres": [prix]})
        return [round(c["moyenne"], 5) for c in clusters if len(c["membres"]) >= 2]
    except Exception:
        return []

def detecter_order_blocks(df, lookback=40):
    try:
        sub = df.iloc[-lookback:] if len(df) > lookback else df
        if len(sub) < 6:
            return [], []
        opens  = sub['open'].values
        closes = sub['close'].values
        highs  = sub['high'].values
        lows   = sub['low'].values
        corps  = abs(closes - opens)
        corps_moyen = corps[:-1].mean() if len(corps) > 1 else 0
        if corps_moyen <= 0:
            return [], []
        obs_bull, obs_bear = [], []
        for i in range(2, len(sub) - 1):
            if corps[i] <= corps_moyen * 1.6:
                continue
            if closes[i] > opens[i] and closes[i-1] <= opens[i-1]:
                top    = max(opens[i-1], closes[i-1], highs[i-1])
                bottom = float(lows[i-1])
                obs_bull.append((bottom, round(top, 5)))
            elif closes[i] < opens[i] and closes[i-1] >= opens[i-1]:
                top    = float(highs[i-1])
                bottom = min(opens[i-1], closes[i-1], lows[i-1])
                obs_bear.append((round(bottom, 5), top))
        return obs_bull[-3:], obs_bear[-3:]
    except Exception:
        return [], []

def analyser_trend_pullback_confluence(symbole):
    c1h = obtenir_donnees_deriv(symbole, 3600)
    c15 = obtenir_donnees_deriv(symbole, 900)
    if not c1h or len(c1h) < 60 or not c15 or len(c15) < 30:
        print(f"[DEBUG-TP] {symbole} REJET: données insuffisantes "
              f"(c1h={len(c1h) if c1h else 0}, c15={len(c15) if c15 else 0})", flush=True)
        return None
    try:
        df1h = pd.DataFrame([{
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"])
        } for c in c1h])
        df15 = pd.DataFrame([{
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"])
        } for c in c15])

        px = float(df15['close'].iloc[-1])
        score = 0.0

        ema21_h1 = _ema(df1h['close'], 21)
        ema55_h1 = _ema(df1h['close'], 55)
        adx_h1   = calculer_adx(df1h)
        tendance_bull = ema21_h1.iloc[-2] > ema55_h1.iloc[-2]
        direction = "BULL" if tendance_bull else "BEAR"
        score_tendance = min(20, max(0, (adx_h1 - 12) * 1.3))
        score += score_tendance

        structure_score = evaluer_structure_marche(df1h)
        score_structure = min(15, max(0, (structure_score - 35) * 0.3))
        score += score_structure

        ema21_m15 = _ema(df15['close'], 21)
        val_zone  = float(ema21_m15.iloc[-2])
        distance_ema_pct = abs(px - val_zone) / px if px else 1
        TOLERANCE_EMA = 0.010

        obs_bull, obs_bear = detecter_order_blocks(df1h)
        niveaux_cles = detecter_niveaux_cles(df1h)
        TOLERANCE_NIVEAU = 0.004

        zones_touchees = []
        score_zone = 0.0

        if distance_ema_pct <= TOLERANCE_EMA:
            zones_touchees.append("EMA21 M15")
            score_zone += max(5, 15 * (1 - distance_ema_pct / TOLERANCE_EMA))

        ob_pertinent = None
        obs_list = obs_bull if direction == "BULL" else obs_bear
        for bottom, top in obs_list:
            marge = (top - bottom) * 0.4
            if (bottom - marge) <= px <= (top + marge):
                zones_touchees.append(f"Order Block {'haussier' if direction=='BULL' else 'baissier'}")
                ob_pertinent = (bottom, top)
                score_zone += 15
                break

        niveau_pertinent = None
        for niveau in niveaux_cles:
            if abs(px - niveau) / px < TOLERANCE_NIVEAU:
                zones_touchees.append("Support/Résistance clé")
                niveau_pertinent = niveau
                score_zone += 12
                break

        if not zones_touchees:
            print(f"[DEBUG-TP] {symbole} REJET: aucune zone de pullback proche "
                  f"(dist EMA21={distance_ema_pct*100:.2f}%, tolérance={TOLERANCE_EMA*100:.1f}%, "
                  f"{len(obs_list)} OB détectés, {len(niveaux_cles)} niveaux clés détectés)", flush=True)
            return None

        score += min(25, score_zone)

        try:
            rsi_series = ta.momentum.RSIIndicator(close=df15["close"], window=14).rsi()
            rsi_val = float(rsi_series.iloc[-2])
        except Exception:
            rsi_val = 50.0

        score_rsi = 15 if 30 <= rsi_val <= 70 else (8 if 20 <= rsi_val <= 80 else 0)
        score += score_rsi

        macd_line, macd_signal, macd_hist = calculer_macd_signal(df15)
        momentum_ok = (macd_hist > 0) if direction == "BULL" else (macd_hist < 0)
        score_macd = 10 if momentum_ok else 0
        score += score_macd

        # ✅ V56 FIX: le pattern de secours "BOUGIE_DIRECTIONNELLE" acceptait
        # n'importe quelle bougie allant dans le sens de la tendance (très
        # fréquent, donc peu discriminant) avec un score non-nul. On exige
        # maintenant un vrai pattern de retournement/continuation identifié
        # (Pin Bar, Engulfing, Marubozu) — sinon le signal est rejeté ici,
        # avant même d'atteindre le calcul IA. Moins de signaux bruts, mais
        # chacun a un déclencheur d'entrée réellement justifié.
        pattern, _ = detecter_chandeliers_pdf(df15)
        patterns_valides_bull = ("PIN_BULL", "ENGULFING_BULL", "MARUBOZU_BULL")
        patterns_valides_bear = ("PIN_BEAR", "ENGULFING_BEAR", "MARUBOZU_BEAR")
        bougie_signal = df15.iloc[-2]

        if (direction == "BULL" and pattern in patterns_valides_bull) or \
           (direction == "BEAR" and pattern in patterns_valides_bear):
            score_pattern = 20
        else:
            print(f"[DEBUG-TP] {symbole} REJET: aucun pattern de retournement/continuation "
                  f"valide sur la bougie de signal (direction={direction}, pattern détecté={pattern})", flush=True)
            return None

        score += score_pattern

        SEUIL_SCORE_CONFLUENCE = 45
        if score < SEUIL_SCORE_CONFLUENCE:
            print(f"[DEBUG-TP] {symbole} REJET: score confluence {score:.1f} < seuil {SEUIL_SCORE_CONFLUENCE} "
                  f"(tendance={score_tendance:.1f} structure={score_structure:.1f} "
                  f"zone={score_zone:.1f} rsi={score_rsi} macd={score_macd} pattern={score_pattern})", flush=True)
            return None

        atr15 = calculer_atr(df15)
        if atr15 <= 0:
            return None

        cpr = calculer_cpr_journalier(symbole)

        if direction == "BULL":
            signal_dir = "BUY"
            sl_structure = float(bougie_signal['low']) - (atr15 * 0.15)
            sl_atr       = px - (atr15 * 1.2)
            sl_candidats = [sl_structure, sl_atr]
            if ob_pertinent:
                sl_candidats.append(ob_pertinent[0] - (atr15 * 0.1))
            sl = min(sl_candidats)
            distance_risque = px - sl
            if distance_risque <= 0:
                return None
            cible_objective = cpr["PDH"] if cpr and cpr["PDH"] > px else px + (distance_risque * 2.0)
            tp_final = max(cible_objective, px + (distance_risque * 1.4))
            tp1 = px + (distance_risque * 1.0)
        else:
            signal_dir = "SELL"
            sl_structure = float(bougie_signal['high']) + (atr15 * 0.15)
            sl_atr       = px + (atr15 * 1.2)
            sl_candidats = [sl_structure, sl_atr]
            if ob_pertinent:
                sl_candidats.append(ob_pertinent[1] + (atr15 * 0.1))
            sl = max(sl_candidats)
            distance_risque = sl - px
            if distance_risque <= 0:
                return None
            cible_objective = cpr["PDL"] if cpr and cpr["PDL"] < px else px - (distance_risque * 2.0)
            tp_final = min(cible_objective, px - (distance_risque * 1.4))
            tp1 = px - (distance_risque * 1.0)

        risque = abs(px - sl)
        rr = abs(tp_final - px) / risque if risque > 0 else 0
        if rr < 1.4:
            print(f"[DEBUG-TP] {symbole} REJET: R/R {rr:.2f} < 1.4 (score confluence "
                  f"pourtant OK à {score:.1f})", flush=True)
            return None

        confiance = int(min(97, round(score)))
        zones_txt = " + ".join(zones_touchees)

        print(f"[DEBUG-TP] {symbole} ✅ SIGNAL ÉMIS — score={score:.1f} direction={direction} "
              f"zones={zones_txt} pattern={pattern} rr={rr:.2f}", flush=True)

        return {
            "action": "🟢 ACHAT (BUY)" if signal_dir == "BUY" else "🔴 VENTE (SELL)",
            "tendance": direction, "force": f"ADX {round(adx_h1,1)} · Structure {structure_score}%",
            "msg": f"Pullback sur {zones_txt} + {pattern.replace('_',' ')} (score confluence {int(score)})",
            "sl": round(sl,5), "tp1": round(tp1,5), "tp": round(tp_final,5),
            "rr": round(rr,2), "px": round(px,5),
            "strategie": 1, "confiance": confiance,
            "label": "TREND PULLBACK & CONFLUENCE",
            "rsi_value": round(rsi_val,1), "adx_value": round(adx_h1,1),
            "zones_confluence": zones_touchees,
            "order_block": ob_pertinent, "niveau_cle": niveau_pertinent,
            "score_confluence_brut": round(score,1),
        }
    except Exception as e:
        print(f"[TrendPullback/{symbole}] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        return None

# ==========================================
# ✅ V57 NEW: STRATÉGIE DE SCALPING MULTI-TIMEFRAME (M1 → M30)
# ==========================================
# Empilement inspiré d'une méthode Fibonacci "golden pocket" + structure de
# session, adaptée à des timeframes courts pour du scalping réel (au lieu
# du swing H1/M15 de la stratégie précédente) :
#
#   M30 : biais directionnel (EMA9 vs EMA21) — la tendance "macro" du scalp
#   M15 : zone de retracement Fibonacci (38.2%-61.8%, "golden pocket") du
#         dernier swing détecté — la zone de valeur où on cherche un retour
#   M5  : le prix doit être ACTUELLEMENT dans cette zone (pas juste l'avoir
#         traversée) — condition de timing
#   M1  : bougie de confirmation (Pin Bar / Engulfing / Marubozu) dans le
#         sens du biais — le déclencheur d'entrée précis
#
# Le second avis du moteur IA (H1/H4/multi-TF, contexte marché, détection
# de faux signal) reste actif en aval, inchangé — il sert de filtre de
# sécurité macro même sur un scalp court terme.

def calculer_zone_fibonacci(df, lookback=20):
    """
    Détecte le dernier swing haut/bas sur la fenêtre récente et calcule la
    zone de retracement Fibonacci 38.2%-61.8% ("golden pocket") de ce swing.
    Retourne None si le swing est trop plat pour être exploitable.
    """
    try:
        sub = df.iloc[-lookback:].reset_index(drop=True)
        plus_haut = float(sub['high'].max())
        plus_bas = float(sub['low'].min())
        idx_haut = int(sub['high'].idxmax())
        idx_bas = int(sub['low'].idxmin())

        amplitude = plus_haut - plus_bas
        if amplitude <= 0:
            return None

        if idx_haut > idx_bas:
            # Le plus haut est venu APRÈS le plus bas → swing haussier →
            # zone d'achat en retracement (discount zone)
            direction_swing = "BULL"
            zone_haut = plus_haut - (amplitude * 0.382)
            zone_bas  = plus_haut - (amplitude * 0.618)
        else:
            direction_swing = "BEAR"
            zone_bas  = plus_bas + (amplitude * 0.382)
            zone_haut = plus_bas + (amplitude * 0.618)

        return {
            "bas": round(min(zone_bas, zone_haut), 5),
            "haut": round(max(zone_bas, zone_haut), 5),
            "direction_swing": direction_swing,
            "amplitude": amplitude,
        }
    except Exception:
        return None

def analyser_scalping_multi_tf(symbole, multiplicateur_tp=1.5, rr_min=1.2):
    """
    ✅ V58: Entrée resserrée sur M15 UNIQUEMENT (biais macro M30 conservé).
    Le backtest précédent montrait une espérance négative sur toutes les
    valeurs de R/R testées — le vrai problème identifié n'était pas le
    R/R, mais un décalage d'échelle : la zone et le biais se lisaient en
    M15/M30, alors que le stop loss était calculé sur l'ATR M1 (bien plus
    petit) — soufflé par du simple bruit intra-bougie sans rapport avec
    la lecture de tendance M15/M30. Tout est maintenant aligné sur M15 :
    biais M30, zone Fibonacci M15, confirmation M15, risque/cible sur
    ATR M15. Moins de bruit, cohérence d'échelle du début à la fin.

    ✅ multiplicateur_tp et rr_min restent réglables pour le backtest.
    """
    c30 = obtenir_donnees_deriv(symbole, 1800)
    c15 = obtenir_donnees_deriv(symbole, 900)

    print(f"[MARQUEUR-VERSION] V60-UNE-SEULE-CONDITION-MAJEURE actif", flush=True)

    if not c30 or not c15 or len(c30) < 30 or len(c15) < 40:
        print(f"[DEBUG-SCALP] {symbole} REJET: données insuffisantes "
              f"(M30={len(c30) if c30 else 0}, M15={len(c15) if c15 else 0})", flush=True)
        return None

    try:
        df30 = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                               "low":float(c["low"]),"close":float(c["close"])} for c in c30])
        df15 = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                               "low":float(c["low"]),"close":float(c["close"])} for c in c15])

        px = float(df15['close'].iloc[-1])

        # ── 1. BIAIS DIRECTIONNEL M30 ──
        ema9_30  = _ema(df30['close'], 9)
        ema21_30 = _ema(df30['close'], 21)
        direction = "BULL" if ema9_30.iloc[-2] > ema21_30.iloc[-2] else "BEAR"

        # ── 2. ZONE FIBONACCI (GOLDEN POCKET) SUR M15 ──
        zone = calculer_zone_fibonacci(df15, lookback=20)
        if not zone or zone["direction_swing"] != direction:
            print(f"[DEBUG-SCALP] {symbole} REJET: pas de zone Fibonacci "
                  f"cohérente avec le biais M30 ({direction})", flush=True)
            return None

        # ── 3. CONFIRMATION MAJEURE UNIQUE : le prix a touché la zone Fibo ──
        # ✅ FIX V59: retour à UNE seule condition d'entrée décisive (biais +
        # zone atteinte), comme demandé — plus de cumul de filtres qui se
        # multiplient entre eux et ramènent la probabilité near-zéro. Le
        # pattern de bougie n'est plus une porte bloquante : il reste
        # affiché à titre indicatif dans le message si détecté.
        recent_zone_touch = df15.iloc[-4:-1]  # 3 bougies avant celle en cours
        touche_zone = any(
            (zone["bas"] <= float(r['low']) <= zone["haut"]) or
            (zone["bas"] <= float(r['high']) <= zone["haut"]) or
            (float(r['low']) <= zone["bas"] and float(r['high']) >= zone["haut"])
            for _, r in recent_zone_touch.iterrows()
        )
        if not touche_zone:
            print(f"[DEBUG-SCALP] {symbole} REJET: prix n'a pas touché la zone Fibo "
                  f"[{zone['bas']}, {zone['haut']}] sur les 3 dernières bougies", flush=True)
            return None

        # Pattern détecté à titre indicatif seulement (n'est plus un filtre bloquant)
        pattern, _ = detecter_chandeliers_pdf(df15)


        # ── 5. RISQUE / CIBLE basés sur l'ATR M15 (même échelle que l'entrée) ──
        atr15 = calculer_atr(df15)
        if atr15 <= 0:
            return None

        bougie15 = df15.iloc[-2]
        if direction == "BULL":
            signal_dir = "BUY"
            sl = min(float(bougie15['low']) - atr15 * 0.15, px - atr15 * 1.0)
            distance = px - sl
            if distance <= 0:
                return None
            tp = px + distance * multiplicateur_tp
            tp1 = px + distance * 1.0
        else:
            signal_dir = "SELL"
            sl = max(float(bougie15['high']) + atr15 * 0.15, px + atr15 * 1.0)
            distance = sl - px
            if distance <= 0:
                return None
            tp = px - distance * multiplicateur_tp
            tp1 = px - distance * 1.0

        rr = abs(tp - px) / abs(px - sl) if abs(px - sl) > 0 else 0
        if rr < rr_min:
            print(f"[DEBUG-SCALP] {symbole} REJET: R/R {rr:.2f} < {rr_min}", flush=True)
            return None

        print(f"[DEBUG-SCALP] {symbole} ✅ SIGNAL ÉMIS — direction={direction} "
              f"zone_fibo=[{zone['bas']},{zone['haut']}] pattern={pattern} rr={rr:.2f}", flush=True)

        return {
            "action": "🟢 ACHAT (BUY)" if signal_dir == "BUY" else "🔴 VENTE (SELL)",
            "tendance": direction, "force": f"Biais M30 {direction}",
            "msg": f"Scalping M15 (biais M30) : pullback zone Fibo golden pocket + {pattern.replace('_',' ')}",
            "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp, 5),
            "rr": round(rr, 2), "px": round(px, 5),
            "strategie": 1, "confiance": 70,
            "label": "SCALPING M15 (biais M30)",
            "zones_confluence": ["Fibonacci Golden Pocket M15"],
            "order_block": None, "niveau_cle": None,
        }
    except Exception as e:
        print(f"[Scalping/{symbole}] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        return None

# ==========================================
# ✅ V61 : MOTEUR ORDER BLOCK — STRATÉGIE UNIQUE DE REMPLACEMENT
# ==========================================
# Suit strictement : STRUCTURE → BOS → DÉPLACEMENT → FVG → LIQUIDITÉ
# INTERNE → ORDER BLOCK → RETOUR EN ZONE → CONFIRMATION → ENTRY → SL → TP
#
# Architecture multi-timeframe (configurable ci-dessous) :
#   HTF (H1)  : contexte / tendance générale (filtre, pas un blocage strict)
#   MTF (M30) : détection BOS / FVG / Liquidité interne / Order Block
#   LTF (M15) : retour de prix + confirmation d'entrée optionnelle
#
# Toutes les valeurs importantes sont centralisées ici — rien en dur
# ailleurs dans le moteur.

ORDER_BLOCK_CONFIG = {
    "ENABLED": True,
    "BOS_REQUIRED": True,
    "FVG_REQUIRED": True,
    "LIQUIDITY_SWEEP_REQUIRED": True,
    # ✅ Section 8 de la spec : une 4e règle est évoquée dans le TITRE de la
    # vidéo ("Mes 4 règles") mais seules 3 règles sont réellement détaillées
    # dans le contenu visible. On ne fabrique pas de 4e condition — elle
    # reste désactivée et documentée comme non définie avec précision.
    "FOURTH_CONFIRMATION_ENABLED": False,
    "BOS_CONFIRMATION_MODE": "CLOSE",   # "CLOSE" ou "WICK"
    "MAX_OB_TOUCHES": 2,                # au-delà, l'OB est considéré "mitigé"
    "MIN_RR": 1.3,
    "HTF_GRANULARITE": 3600,   # H1
    "MTF_GRANULARITE": 1800,   # M30
    "LTF_GRANULARITE": 900,    # M15
}

# Mémoire de fraîcheur des Order Blocks détectés (touch_count) — persiste
# tant que le processus tourne, remise à zéro à chaque redéploiement.
_ob_touch_cache = {}

def _cle_ob(symbole, direction, ob):
    return (symbole, direction, round(ob["bas"], 2), round(ob["haut"], 2))

def detecter_bos(df, ordre_swing=2, mode="CLOSE", fenetre_recente=15):
    """
    RÈGLE 1 — Tendance = BOS. Cherche un BOS "récent" (dans les
    `fenetre_recente` dernières bougies CLÔTURÉES, jamais la bougie en
    cours de formation) plutôt que seulement les 2 dernières — un retour
    de prix dans l'Order Block se produit souvent plusieurs bougies après
    la cassure elle-même, pas immédiatement dessus.
    mode="CLOSE" exige une clôture au-delà du swing (pas une simple mèche) —
    mode="WICK" accepte une mèche.
    Retourne le BOS le plus RÉCENT trouvé : {direction, prix_swing_casse,
    idx_bos, idx_swing} ou None.
    """
    try:
        sub = df.iloc[:-1].reset_index(drop=True)  # exclut la bougie en formation
        n = len(sub)
        if n < ordre_swing * 2 + 6:
            return None

        highs = sub['high'].values
        lows = sub['low'].values
        closes = sub['close'].values

        debut_recherche = max(ordre_swing, n - fenetre_recente)
        meilleur = None
        for i in range(debut_recherche, n):
            # swings connus strictement AVANT la bougie i
            idx_highs = [j for j in range(ordre_swing, i - ordre_swing)
                         if highs[j] == highs[max(0, j-ordre_swing):j+ordre_swing+1].max()]
            idx_lows = [j for j in range(ordre_swing, i - ordre_swing)
                        if lows[j] == lows[max(0, j-ordre_swing):j+ordre_swing+1].min()]
            if not idx_highs or not idx_lows:
                continue
            swing_high_idx, swing_low_idx = idx_highs[-1], idx_lows[-1]
            swing_high, swing_low = float(highs[swing_high_idx]), float(lows[swing_low_idx])

            ref_haut = closes[i] if mode == "CLOSE" else highs[i]
            ref_bas  = closes[i] if mode == "CLOSE" else lows[i]
            if ref_haut > swing_high:
                meilleur = {"direction": "BULL", "prix_swing_casse": swing_high,
                            "idx_bos": i, "idx_swing": swing_high_idx}
            elif ref_bas < swing_low:
                meilleur = {"direction": "BEAR", "prix_swing_casse": swing_low,
                            "idx_bos": i, "idx_swing": swing_low_idx}
        return meilleur
    except Exception:
        return None


def _detecter_bos_ancien_non_utilise(df, ordre_swing=3, mode="CLOSE"):
    try:
        sub = df.iloc[:-1].reset_index(drop=True)  # exclut la bougie en formation
        n = len(sub)
        if n < ordre_swing * 2 + 6:
            return None

        highs = sub['high'].values
        lows = sub['low'].values
        closes = sub['close'].values

        limite = n - 2  # on ne considère les swings que AVANT les 2 dernières bougies
        idx_highs = [i for i in range(ordre_swing, limite - ordre_swing)
                     if highs[i] == highs[max(0, i-ordre_swing):i+ordre_swing+1].max()]
        idx_lows = [i for i in range(ordre_swing, limite - ordre_swing)
                    if lows[i] == lows[max(0, i-ordre_swing):i+ordre_swing+1].min()]
        if not idx_highs or not idx_lows:
            return None

        swing_high_idx, swing_low_idx = idx_highs[-1], idx_lows[-1]
        swing_high, swing_low = float(highs[swing_high_idx]), float(lows[swing_low_idx])

        for i in range(n - 2, n):
            ref_haut = closes[i] if mode == "CLOSE" else highs[i]
            ref_bas  = closes[i] if mode == "CLOSE" else lows[i]
            if ref_haut > swing_high:
                return {"direction": "BULL", "prix_swing_casse": swing_high,

                        "idx_bos": i, "idx_swing": swing_high_idx}
            if ref_bas < swing_low:
                return {"direction": "BEAR", "prix_swing_casse": swing_low,
                        "idx_bos": i, "idx_swing": swing_low_idx}
        return None
    except Exception:
        return None

def detecter_fvg_dans_zone(df, idx_debut, idx_fin, direction):
    """
    RÈGLE 2 — FVG associé au mouvement impulsif ayant produit le BOS
    (pas n'importe quel FVG du graphique). Cherche un gap 3-bougies entre
    idx_debut (le swing cassé) et idx_fin (la bougie du BOS).
    """
    try:
        meilleur = None
        borne = min(idx_fin, len(df) - 1)
        for i in range(max(idx_debut + 2, 2), borne + 1):
            h1, l1 = float(df['high'].iloc[i-2]), float(df['low'].iloc[i-2])
            h3, l3 = float(df['high'].iloc[i]), float(df['low'].iloc[i])
            if direction == "BULL" and h1 < l3:
                meilleur = {"type": "BULL", "haut": l3, "bas": h1, "idx": i}
            elif direction == "BEAR" and l1 > h3:
                meilleur = {"type": "BEAR", "haut": l1, "bas": h3, "idx": i}
        return meilleur
    except Exception:
        return None

def detecter_liquidity_sweep(df, idx_reference, direction, lookback=25):
    """
    RÈGLE 3 — Prise de liquidité interne avant le mouvement impulsif : un
    niveau mineur pris (mèche au-delà) puis rejeté immédiatement dans le
    sens du mouvement à venir. Une simple cassure ne suffit pas — il faut
    le rejet (clôture qui revient au-delà du niveau).
    """
    try:
        debut = max(0, idx_reference - lookback)
        sub = df.iloc[debut: idx_reference + 1]
        if len(sub) < 5:
            return None
        if direction == "BULL":
            niveau = float(sub['low'].iloc[:-2].min())
            derniere = sub.iloc[-1]
            if float(derniere['low']) < niveau and float(derniere['close']) > niveau:
                return {"type": "BULL", "niveau": niveau}
        else:
            niveau = float(sub['high'].iloc[:-2].max())
            derniere = sub.iloc[-1]
            if float(derniere['high']) > niveau and float(derniere['close']) < niveau:
                return {"type": "BEAR", "niveau": niveau}
        return None
    except Exception:
        return None

def detecter_order_block_zone(df, idx_swing, direction):
    """
    Identifie la zone d'Order Block : la dernière bougie OPPOSÉE pertinente
    juste avant le mouvement impulsif qui a cassé la structure. On ne
    considère pas chaque bougie opposée — seulement celle qui précède
    directement le déplacement (recherche bornée à 12 bougies en arrière).
    """
    try:
        for i in range(idx_swing, max(idx_swing - 12, 0), -1):
            o, c = float(df['open'].iloc[i]), float(df['close'].iloc[i])
            if direction == "BULL" and c < o:
                return {"haut": max(o, c, float(df['high'].iloc[i])),
                        "bas": float(df['low'].iloc[i]), "idx": i}
            if direction == "BEAR" and c > o:
                return {"haut": float(df['high'].iloc[i]),
                        "bas": min(o, c, float(df['low'].iloc[i])), "idx": i}
        return None
    except Exception:
        return None

def analyser_order_block_engine(symbole):
    """
    MOTEUR UNIQUE DE STRATÉGIE — Order Block.
    STRUCTURE → BOS → DÉPLACEMENT → FVG → LIQUIDITÉ INTERNE → ORDER BLOCK
    → RETOUR EN ZONE → CONFIRMATION (optionnelle) → ENTRY → SL → TP.

    Ne fabrique jamais de signal si une condition centrale échoue : retourne
    None (NO TRADE), jamais un signal dégradé pour "avoir quelque chose".
    """
    if not ORDER_BLOCK_CONFIG["ENABLED"]:
        return None

    c_htf = obtenir_donnees_deriv(symbole, ORDER_BLOCK_CONFIG["HTF_GRANULARITE"])
    c_mtf = obtenir_donnees_deriv(symbole, ORDER_BLOCK_CONFIG["MTF_GRANULARITE"])
    c_ltf = obtenir_donnees_deriv(symbole, ORDER_BLOCK_CONFIG["LTF_GRANULARITE"])

    if not c_htf or not c_mtf or not c_ltf or len(c_htf) < 50 or len(c_mtf) < 50 or len(c_ltf) < 20:
        print(f"[OB SCANNER] {symbole} REJECTED — REASON: insufficient data", flush=True)
        return None

    try:
        df_htf = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                                 "low":float(c["low"]),"close":float(c["close"])} for c in c_htf])
        df_mtf = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                                 "low":float(c["low"]),"close":float(c["close"])} for c in c_mtf])
        df_ltf = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                                 "low":float(c["low"]),"close":float(c["close"])} for c in c_ltf])

        px = float(df_ltf['close'].iloc[-1])

        # ── RÈGLE 1 : TENDANCE = BOS (sur MTF) ──
        bos = detecter_bos(df_mtf, mode=ORDER_BLOCK_CONFIG["BOS_CONFIRMATION_MODE"])
        if ORDER_BLOCK_CONFIG["BOS_REQUIRED"] and not bos:
            print(f"[OB SCANNER] {symbole} REJECTED — REASON: No BOS", flush=True)
            return None
        direction = bos["direction"]

        ema20_htf = _ema(df_htf['close'], 20)
        ema50_htf = _ema(df_htf['close'], 50)
        tendance_htf = "BULL" if ema20_htf.iloc[-2] > ema50_htf.iloc[-2] else "BEAR"

        # ── RÈGLE 2 : FVG associé au déplacement ──
        fvg = detecter_fvg_dans_zone(df_mtf, bos["idx_swing"], bos["idx_bos"], direction)
        if ORDER_BLOCK_CONFIG["FVG_REQUIRED"] and not fvg:
            print(f"[OB SCANNER] {symbole} REJECTED — REASON: No FVG", flush=True)
            return None

        # ── RÈGLE 3 : PRISE DE LIQUIDITÉ INTERNE ──
        liquidite = detecter_liquidity_sweep(df_mtf, bos["idx_swing"], direction)
        if ORDER_BLOCK_CONFIG["LIQUIDITY_SWEEP_REQUIRED"] and not liquidite:
            print(f"[OB SCANNER] {symbole} REJECTED — REASON: No liquidity sweep", flush=True)
            return None

        # ── IDENTIFICATION DE L'ORDER BLOCK ──
        ob = detecter_order_block_zone(df_mtf, bos["idx_swing"], direction)
        if not ob:
            print(f"[OB SCANNER] {symbole} REJECTED — REASON: No valid OB candle", flush=True)
            return None

        # ── FRAÎCHEUR ──
        cle = _cle_ob(symbole, direction, ob)
        touches = _ob_touch_cache.get(cle, 0)
        if touches >= ORDER_BLOCK_CONFIG["MAX_OB_TOUCHES"]:
            print(f"[OB SCANNER] {symbole} REJECTED — REASON: OB already mitigated ({touches} touches)", flush=True)
            return None

        # ── RETOUR DU PRIX DANS L'OB (LTF) ──
        if not (ob["bas"] <= px <= ob["haut"]):
            print(f"[OB SCANNER] {symbole} REJECTED — REASON: price not back in OB "
                  f"[{ob['bas']:.5f},{ob['haut']:.5f}], px={px}", flush=True)
            return None

        # ── CONFIRMATION D'ENTRÉE (module séparé, OPTIONNEL — n'est pas une
        # des 3 règles centrales de la vidéo, affine seulement le score) ──
        pattern, _ = detecter_chandeliers_pdf(df_ltf)
        patterns_bull = ("PIN_BULL", "ENGULFING_BULL", "MARUBOZU_BULL")
        patterns_bear = ("PIN_BEAR", "ENGULFING_BEAR", "MARUBOZU_BEAR")
        confirmation_ok = (direction == "BULL" and pattern in patterns_bull) or \
                           (direction == "BEAR" and pattern in patterns_bear)

        # ── SL / TP STRUCTURELS ──
        atr_mtf = calculer_atr(df_mtf)
        marge = atr_mtf * 0.15 if atr_mtf > 0 else ob["haut"] * 0.001

        if direction == "BULL":
            signal_dir = "BUY"
            sl = ob["bas"] - marge
            distance = px - sl
            if distance <= 0:
                return None
            niveaux = detecter_niveaux_cles(df_mtf)
            cibles = [n for n in niveaux if n > px]
            tp = min(cibles) if cibles else px + distance * 2.0
            tp1 = px + distance * 1.0
        else:
            signal_dir = "SELL"
            sl = ob["haut"] + marge
            distance = sl - px
            if distance <= 0:
                return None
            niveaux = detecter_niveaux_cles(df_mtf)
            cibles = [n for n in niveaux if n < px]
            tp = max(cibles) if cibles else px - distance * 2.0
            tp1 = px - distance * 1.0

        rr = abs(tp - px) / abs(px - sl) if abs(px - sl) > 0 else 0
        if rr < ORDER_BLOCK_CONFIG["MIN_RR"]:
            print(f"[OB SCANNER] {symbole} REJECTED — REASON: RR {rr:.2f} < {ORDER_BLOCK_CONFIG['MIN_RR']}", flush=True)
            return None

        # ── SCORE DE QUALITÉ (indépendant de l'IA — pas une probabilité garantie) ──
        score = 25 + (20 if fvg else 0) + (20 if liquidite else 0) + \
                (15 if confirmation_ok else 0) + (10 if touches == 0 else 0) + \
                (10 if direction == tendance_htf else 0)
        grade = "A+" if score >= 90 else "A" if score >= 75 else "B" if score >= 55 else "C"

        _ob_touch_cache[cle] = touches + 1

        print(f"[OB SCANNER] Symbol: {symbole} | BOS: YES | FVG: {'YES' if fvg else 'NO'} | "
              f"LIQUIDITY SWEEP: {'YES' if liquidite else 'NO'} | OB: {direction} | "
              f"FRESHNESS: {'HIGH' if touches==0 else 'USED'} | RR: {rr:.2f} | "
              f"SCORE: {score} ({grade}) | STATUS: VALID", flush=True)

        return {
            "action": "🟢 ACHAT (BUY)" if signal_dir == "BUY" else "🔴 VENTE (SELL)",
            "tendance": direction, "force": f"BOS {direction} confirmé (MTF)",
            "msg": f"Order Block {direction} — BOS+FVG+Liquidité confirmés (grade {grade})",
            "sl": round(sl, 5), "tp1": round(tp1, 5), "tp": round(tp, 5),
            "rr": round(rr, 2), "px": round(px, 5),
            "strategie": 1, "confiance": min(97, score),
            "label": "ORDER BLOCK ENGINE",
            "zones_confluence": [f"Order Block {direction}"],
            "order_block": (ob["bas"], ob["haut"]), "niveau_cle": None,
            "setup_score": score, "setup_grade": grade,
            "bos_confirmed": True, "fvg_confirmed": bool(fvg),
            "liquidity_sweep_confirmed": bool(liquidite),
        }
    except Exception as e:
        print(f"[OrderBlock/{symbole}] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        return None

# ==========================================
# ✅ V62 : DEUX STRATÉGIES ALTERNATIVES, PLUS HAUTE FRÉQUENCE
# ==========================================
# L'Order Block (3 conditions structurelles rares à aligner) produit très
# peu de signaux sur Gold. Ces deux stratégies reposent sur des conditions
# statistiques courantes (contact de bande, croisement de moyennes) —
# mécaniquement plus fréquentes. À comparer par backtest avant de choisir
# laquelle devient la stratégie unique du bot.

BOLLINGER_CONFIG = {
    "PERIODE": 20, "ECART_TYPE": 2.0,
    "RSI_SURVENTE": 30, "RSI_SURACHAT": 70,
    "MIN_RR": 1.0,
    "GRANULARITE": 900,  # M15
}

def analyser_bollinger_rsi_reversion(symbole):
    """
    Stratégie 1 — Retour à la moyenne : le prix touche/dépasse la bande de
    Bollinger extérieure (SMA20 ± 2 écarts-types) + RSI en zone extrême.
    Cible = la bande médiane (SMA20), pas un swing structurel lointain —
    plus fréquent par nature qu'une confluence à 3 conditions rares.
    """
    c15 = obtenir_donnees_deriv(symbole, BOLLINGER_CONFIG["GRANULARITE"])
    if not c15 or len(c15) < 40:
        return None
    try:
        df = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                             "low":float(c["low"]),"close":float(c["close"])} for c in c15])
        periode = BOLLINGER_CONFIG["PERIODE"]
        sma = df['close'].rolling(periode).mean()
        std = df['close'].rolling(periode).std()
        bande_haute = sma + BOLLINGER_CONFIG["ECART_TYPE"] * std
        bande_basse = sma - BOLLINGER_CONFIG["ECART_TYPE"] * std

        derniere = df.iloc[-2]  # bougie clôturée
        px = float(derniere['close'])
        milieu = float(sma.iloc[-2])
        haut, bas = float(bande_haute.iloc[-2]), float(bande_basse.iloc[-2])
        if pd.isna(milieu) or pd.isna(haut) or pd.isna(bas):
            return None

        try:
            rsi_val = float(ta.momentum.RSIIndicator(close=df["close"], window=14).rsi().iloc[-2])
        except Exception:
            rsi_val = 50.0

        direction = None
        if float(derniere['low']) <= bas and rsi_val <= BOLLINGER_CONFIG["RSI_SURVENTE"]:
            direction = "BULL"
        elif float(derniere['high']) >= haut and rsi_val >= BOLLINGER_CONFIG["RSI_SURACHAT"]:
            direction = "BEAR"
        if not direction:
            return None

        pattern, _ = detecter_chandeliers_pdf(df)
        bonus_pattern = 10 if (
            (direction == "BULL" and pattern in ("PIN_BULL","ENGULFING_BULL","MARUBOZU_BULL")) or
            (direction == "BEAR" and pattern in ("PIN_BEAR","ENGULFING_BEAR","MARUBOZU_BEAR"))
        ) else 0

        atr15 = calculer_atr(df)
        marge = atr15 * 0.2 if atr15 > 0 else abs(haut - bas) * 0.05

        if direction == "BULL":
            signal_dir = "BUY"
            sl = bas - marge
            distance = px - sl
            if distance <= 0: return None
            tp = milieu
            tp1 = px + distance * 0.6
        else:
            signal_dir = "SELL"
            sl = haut + marge
            distance = sl - px
            if distance <= 0: return None
            tp = milieu
            tp1 = px - distance * 0.6

        rr = abs(tp - px) / abs(px - sl) if abs(px - sl) > 0 else 0
        if rr < BOLLINGER_CONFIG["MIN_RR"]:
            return None

        score = 60 + bonus_pattern + (15 if (rsi_val <= 20 or rsi_val >= 80) else 0)
        print(f"[BOLLINGER] {symbole} ✅ SIGNAL — direction={direction} RSI={rsi_val:.1f} "
              f"bande=[{bas:.5f},{haut:.5f}] rr={rr:.2f}", flush=True)

        return {
            "action": "🟢 ACHAT (BUY)" if signal_dir == "BUY" else "🔴 VENTE (SELL)",
            "tendance": direction, "force": f"RSI {rsi_val:.1f}",
            "msg": f"Bollinger + RSI : retour à la moyenne ({pattern.replace('_',' ')})",
            "sl": round(sl,5), "tp1": round(tp1,5), "tp": round(tp,5),
            "rr": round(rr,2), "px": round(px,5),
            "strategie": 1, "confiance": min(95, score),
            "label": "BOLLINGER + RSI (retour à la moyenne)",
            "zones_confluence": ["Bande de Bollinger"],
            "order_block": None, "niveau_cle": None,
        }
    except Exception as e:
        print(f"[Bollinger/{symbole}] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        return None

EMA_CROSS_CONFIG = {
    "EMA_RAPIDE": 5, "EMA_LENTE": 20,
    "MIN_RR": 1.5,
    "GRANULARITE": 300,  # M5
}

def analyser_ema_cross(symbole):
    """
    Stratégie 2 — Croisement de moyennes mobiles (EMA5/EMA20 sur M5) : un
    signal mécanique et fréquent, sans condition structurelle à aligner.
    """
    c5 = obtenir_donnees_deriv(symbole, EMA_CROSS_CONFIG["GRANULARITE"])
    if not c5 or len(c5) < 40:
        return None
    try:
        df = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                             "low":float(c["low"]),"close":float(c["close"])} for c in c5])
        ema_rapide = _ema(df['close'], EMA_CROSS_CONFIG["EMA_RAPIDE"])
        ema_lente  = _ema(df['close'], EMA_CROSS_CONFIG["EMA_LENTE"])

        # Croisement sur la dernière bougie clôturée : rapide vs lente a
        # changé de côté entre l'avant-dernière et la dernière bougie.
        avant = ema_rapide.iloc[-3] - ema_lente.iloc[-3]
        maintenant = ema_rapide.iloc[-2] - ema_lente.iloc[-2]
        if pd.isna(avant) or pd.isna(maintenant):
            return None

        direction = None
        if avant <= 0 and maintenant > 0:
            direction = "BULL"
        elif avant >= 0 and maintenant < 0:
            direction = "BEAR"
        if not direction:
            return None

        px = float(df['close'].iloc[-2])
        atr5 = calculer_atr(df)
        if atr5 <= 0:
            return None

        bougie = df.iloc[-2]
        if direction == "BULL":
            signal_dir = "BUY"
            sl = min(float(bougie['low']) - atr5 * 0.3, px - atr5 * 1.2)
            distance = px - sl
            if distance <= 0: return None
            tp = px + distance * 1.8
            tp1 = px + distance * 1.0
        else:
            signal_dir = "SELL"
            sl = max(float(bougie['high']) + atr5 * 0.3, px + atr5 * 1.2)
            distance = sl - px
            if distance <= 0: return None
            tp = px - distance * 1.8
            tp1 = px - distance * 1.0

        rr = abs(tp - px) / abs(px - sl) if abs(px - sl) > 0 else 0
        if rr < EMA_CROSS_CONFIG["MIN_RR"]:
            return None

        print(f"[EMA-CROSS] {symbole} ✅ SIGNAL — direction={direction} rr={rr:.2f}", flush=True)

        return {
            "action": "🟢 ACHAT (BUY)" if signal_dir == "BUY" else "🔴 VENTE (SELL)",
            "tendance": direction, "force": f"Croisement EMA{EMA_CROSS_CONFIG['EMA_RAPIDE']}/{EMA_CROSS_CONFIG['EMA_LENTE']}",
            "msg": f"Croisement EMA {direction}",
            "sl": round(sl,5), "tp1": round(tp1,5), "tp": round(tp,5),
            "rr": round(rr,2), "px": round(px,5),
            "strategie": 1, "confiance": 65,
            "label": "CROISEMENT EMA 5/20",
            "zones_confluence": ["Croisement EMA"],
            "order_block": None, "niveau_cle": None,
        }
    except Exception as e:
        print(f"[EMACross/{symbole}] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        return None

def detecter_contexte_pdf(symbole):
    cached = contexte_marche_cache.get(symbole)
    if cached and (time.time() - cached["ts"]) < 120:
        return cached["contexte"]
    cpr = calculer_cpr_journalier(symbole)
    if not cpr:
        contexte = "INDECIS"
    elif cpr["ETAT"] == "Étroit (Tendance)":
        contexte = "JOUR_TENDANCE"
    else:
        contexte = "JOUR_RANGE"
    contexte_marche_cache[symbole] = {"contexte": contexte, "ts": time.time()}
    return contexte

# ==========================================
# MOTEUR IA DE VALIDATION DES SIGNAUX
# ==========================================

IA_CONFIG = {
    "seuil_acceptation": 78,
    "groq_active": True,
    "groq_seuil_veto": 32,
    "poids": {
        "tendance_h1":        12,
        "adx":                10,
        "rsi_coherence":      10,
        "macd_coherence":      8,
        "ema_alignement":      8,
        "atr_volatilite":      8,
        "structure_marche":   10,
        "distance_sr":         8,
        "qualite_cassure":    10,
        "spread":              6,
        "multi_tf_coherence": 10,
    },
    "poids_contexte": {
        "tendance_forte":      1.10,
        "range":               0.90,
        "tres_volatil":        0.80,
        "peu_volatil":         0.95,
        "consolidation":       0.90,
        "proche_cassure":      1.05,
    },
    "seuil_multi_tf_penalite": 30,
}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL   = "llama-3.1-8b-instant"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

ia_historique = []
ia_poids_ajustes = {}

def analyser_contexte_marche(symbole, df1h, df4h):
    try:
        ema20_h1 = df1h['close'].ewm(span=20, adjust=False).mean()
        ema50_h1 = df1h['close'].ewm(span=50, adjust=False).mean()
        pente_ema20 = (ema20_h1.iloc[-2] - ema20_h1.iloc[-10]) / max(abs(ema20_h1.iloc[-10]), 1e-9)
        adx = calculer_adx(df1h)
        atr = calculer_atr(df1h)
        px = float(df1h['close'].iloc[-2])
        atr_pct = (atr / px * 100) if px else 0
        recent_high = df1h['high'].iloc[-30:].max()
        recent_low  = df1h['low'].iloc[-30:].min()
        rng = recent_high - recent_low
        position_dans_range = (px - recent_low) / rng if rng > 0 else 0.5
        proche_cassure = position_dans_range > 0.9 or position_dans_range < 0.1
        if adx >= 25 and abs(pente_ema20) > 0.001:
            tendance = "HAUSSIERE" if ema20_h1.iloc[-2] > ema50_h1.iloc[-2] else "BAISSIERE"
        elif adx < 18:
            tendance = "RANGE"
        else:
            tendance = "INDECIS"
        if atr_pct > 1.2:
            volatilite = "TRES_VOLATIL"
        elif atr_pct < 0.05:
            volatilite = "PEU_VOLATIL"
        else:
            volatilite = "NORMALE"
        consolidation = (adx < 20 and atr_pct < 0.3)
        return {
            "tendance": tendance, "volatilite": volatilite,
            "consolidation": consolidation, "proche_cassure": proche_cassure,
            "adx": round(adx, 1), "atr_pct": round(atr_pct, 3),
            "position_dans_range": round(position_dans_range, 2),
        }
    except Exception as e:
        print(f"[Contexte Marché/{symbole}] {e}", flush=True)
        return {"tendance": "INDECIS", "volatilite": "NORMALE", "consolidation": False,
                "proche_cassure": False, "adx": 20.0, "atr_pct": 0.3, "position_dans_range": 0.5}

def contexte_vers_facteur_confiance(contexte, direction_signal):
    poids = IA_CONFIG["poids_contexte"]
    facteur = 1.0
    justification = []
    if contexte["tendance"] in ("HAUSSIERE", "BAISSIERE"):
        sens_marche = "BULL" if contexte["tendance"] == "HAUSSIERE" else "BEAR"
        if sens_marche == direction_signal:
            facteur *= poids["tendance_forte"]
            justification.append(f"Tendance {contexte['tendance'].lower()} confirmée")
        else:
            facteur *= (2 - poids["tendance_forte"])
            justification.append(f"Signal contraire à la tendance {contexte['tendance'].lower()}")
    elif contexte["tendance"] == "RANGE":
        facteur *= poids["range"]
        justification.append("Marché sans tendance claire (range)")
    if contexte["volatilite"] == "TRES_VOLATIL":
        facteur *= poids["tres_volatil"]
        justification.append("Volatilité excessive")
    elif contexte["volatilite"] == "PEU_VOLATIL":
        facteur *= poids["peu_volatil"]
        justification.append("Volatilité faible — momentum limité")
    if contexte["consolidation"]:
        facteur *= poids["consolidation"]
        justification.append("Marché en consolidation")
    if contexte["proche_cassure"]:
        facteur *= poids["proche_cassure"]
        justification.append("Prix proche d'une zone de cassure")
    return round(max(0.6, min(1.15, facteur)), 3), justification

def detecter_faux_signal(df1h, df5, signal, contexte):
    raisons = []
    penalite = 0
    direction = signal["tendance"] if signal["tendance"] in ("BULL", "BEAR") else \
                ("BULL" if "BUY" in signal["action"] else "BEAR")
    try:
        last5 = df5.iloc[-2]
        corps5 = abs(last5['close'] - last5['open'])
        range5 = last5['high'] - last5['low']
        if range5 > 0 and (corps5 / range5) < 0.35:
            penalite += 8
            raisons.append("Bougie de cassure au corps faible — élan douteux")
        cinq_dernieres = df5.iloc[-7:-2]
        if len(cinq_dernieres) == 5:
            hausses = sum(1 for i in range(len(cinq_dernieres))
                         if cinq_dernieres.iloc[i]['close'] > cinq_dernieres.iloc[i]['open'])
            if (direction == "BULL" and hausses >= 5) or (direction == "BEAR" and hausses <= 0):
                penalite += 10
                raisons.append("Mouvement déjà étendu — risque d'épuisement")
        try:
            delta = df1h['close'].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi_series = 100 - (100 / (1 + rs))
            px_recent = df1h['close'].iloc[-15:-2]
            rsi_recent = rsi_series.iloc[-15:-2]
            if direction == "BULL":
                prix_nouveau_haut = px_recent.iloc[-1] >= px_recent.max()
                rsi_pas_confirme = rsi_recent.iloc[-1] < rsi_recent.max() * 0.95
                if prix_nouveau_haut and rsi_pas_confirme:
                    penalite += 12
                    raisons.append("Divergence baissière RSI détectée")
            else:
                prix_nouveau_bas = px_recent.iloc[-1] <= px_recent.min()
                rsi_pas_confirme = rsi_recent.iloc[-1] > rsi_recent.min() * 1.05
                if prix_nouveau_bas and rsi_pas_confirme:
                    penalite += 12
                    raisons.append("Divergence haussière RSI détectée")
        except Exception:
            pass
        last1h = df1h.iloc[-2]
        corps1h = abs(last1h['close'] - last1h['open'])
        if direction == "BULL":
            meche_opposee = last1h['high'] - max(last1h['open'], last1h['close'])
        else:
            meche_opposee = min(last1h['open'], last1h['close']) - last1h['low']
        if corps1h > 0 and meche_opposee > corps1h * 1.5:
            penalite += 10
            raisons.append("Mèche de retournement récente dans le sens opposé")
        if contexte["volatilite"] == "TRES_VOLATIL":
            penalite += 5
            raisons.append("Volatilité excessive — risque de faux breakout accru")
    except Exception as e:
        print(f"[Faux Signal] {e}", flush=True)
    penalite = min(penalite, 35)
    return (penalite > 0), penalite, raisons

def analyser_coherence_multi_tf(symbole, direction_signal):
    tendances = {}
    poids_tf = {"M1": 1, "M5": 2, "M15": 3, "H1": 4}
    granularites = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
    for tf_nom, gran in granularites.items():
        try:
            candles = obtenir_donnees_deriv(symbole, gran)
            if not candles or len(candles) < 55:
                continue
            df = pd.DataFrame([{"close": float(c["close"])} for c in candles])
            ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-2]
            ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-2]
            tendances[tf_nom] = "BULL" if ema20 > ema50 else "BEAR"
        except Exception:
            continue
    if not tendances:
        return {"score": 50, "penalite": 0, "detail": tendances, "raisons": ["Données multi-TF indisponibles"]}
    total_poids = sum(poids_tf[tf] for tf in tendances)
    poids_aligne = sum(poids_tf[tf] for tf, t in tendances.items() if t == direction_signal)
    score_coherence = round((poids_aligne / total_poids) * 100, 1) if total_poids else 50
    penalite = 0
    raisons = []
    if tendances.get("H1") and tendances["H1"] != direction_signal:
        penalite += IA_CONFIG["seuil_multi_tf_penalite"]
        raisons.append("Signal contraire à la tendance H1 (unité supérieure)")
    elif tendances.get("M15") and tendances["M15"] != direction_signal:
        penalite += IA_CONFIG["seuil_multi_tf_penalite"] // 2
        raisons.append("Signal contraire à la tendance M15")
    if score_coherence >= 75:
        raisons.append(f"Cohérence multi-TF forte ({score_coherence}%)")
    elif score_coherence < 40:
        raisons.append(f"Cohérence multi-TF faible ({score_coherence}%)")
    return {"score": score_coherence, "penalite": penalite, "detail": tendances, "raisons": raisons}

def optimiser_gestion_risque(signal, contexte, df1h):
    try:
        atr = calculer_atr(df1h)
        px = signal["px"]
        sl_origine = signal["sl"]
        tp_origine = signal["tp"]
        direction = signal["tendance"] if signal["tendance"] in ("BULL", "BEAR") else \
                    ("BULL" if "BUY" in signal["action"] else "BEAR")
        distance_sl_origine = abs(px - sl_origine)
        distance_sl_atr = atr * 1.5
        marge = distance_sl_origine * 0.15
        distance_sl_bornee = max(distance_sl_origine - marge,
                                 min(distance_sl_origine + marge, distance_sl_atr))
        if direction == "BULL":
            sl_optimise = round(px - distance_sl_bornee, 5)
        else:
            sl_optimise = round(px + distance_sl_bornee, 5)
        distance_tp_origine = abs(tp_origine - px)
        rr_origine = signal.get("rr", 0)
        rr_optimise = round(distance_tp_origine / distance_sl_bornee, 2) if distance_sl_bornee > 0 else rr_origine
        if abs(distance_sl_bornee - distance_sl_origine) < distance_sl_origine * 0.02:
            note = "SL/TP de la stratégie déjà cohérents avec la volatilité (ATR)"
        else:
            note = f"SL affiné selon ATR (x1.5) — ajustement {'+' if distance_sl_bornee > distance_sl_origine else '-'}{abs(round((distance_sl_bornee/distance_sl_origine - 1) * 100, 1))}%"
        return {
            "sl_optimise": sl_optimise, "tp_optimise": tp_origine,
            "rr_optimise": rr_optimise, "note": note,
        }
    except Exception as e:
        print(f"[Gestion Risque] {e}", flush=True)
        return {"sl_optimise": signal.get("sl"), "tp_optimise": signal.get("tp"),
               "rr_optimise": signal.get("rr", 0), "note": "Optimisation indisponible — niveaux stratégie conservés"}

def calculer_adx(df, period=14):
    try:
        high, low, close = df['high'], df['low'], df['close']
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        tr = pd.concat([high - low, (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, 1e-9))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        adx = dx.rolling(period).mean()
        return float(adx.iloc[-2]) if not adx.isna().iloc[-2] else 20.0
    except Exception:
        return 20.0

def calculer_macd_signal(df):
    try:
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line
        return float(macd_line.iloc[-2]), float(signal_line.iloc[-2]), float(hist.iloc[-2])
    except Exception:
        return 0.0, 0.0, 0.0

def calculer_atr(df, period=14):
    try:
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-2])
    except Exception:
        return 0.0

def evaluer_structure_marche(df):
    try:
        highs = df['high'].iloc[-20:].values
        lows = df['low'].iloc[-20:].values
        if len(highs) < 2 or len(lows) < 2:
            return 50.0
        hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
        hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i-1])
        lh = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i-1])
        ll = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i-1])
        coherence_bull = (hh + hl) / (2 * (len(highs) - 1))
        coherence_bear = (lh + ll) / (2 * (len(lows) - 1))
        return round(max(coherence_bull, coherence_bear) * 100, 1)
    except Exception:
        return 50.0

def calculer_distance_support_resistance(df, px):
    try:
        recent_high = df['high'].iloc[-30:].max()
        recent_low = df['low'].iloc[-30:].min()
        rng = recent_high - recent_low
        if rng <= 0:
            return 50.0
        dist_high = abs(recent_high - px) / rng
        dist_low = abs(px - recent_low) / rng
        return round(min(dist_high, dist_low) * 100, 1)
    except Exception:
        return 50.0

def estimer_spread_relatif(symbole, px):
    if symbole in VOLATILE_PAIRS:
        return 0.02
    if symbole in COMMODITY_PAIRS:
        return 0.015
    return 0.03

def moteur_ia_valider_signal(symbole, signal, strategie_nom):
    try:
        c1h = obtenir_donnees_deriv(symbole, 3600)
        c5  = obtenir_donnees_deriv(symbole, 300)
        c4h = obtenir_donnees_h4(symbole)
        if not c1h or not c5:
            return {"accepte": False, "score": 0, "justification": ["Données insuffisantes"], "details": {}}

        df1h = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                               "low":float(c["low"]),"close":float(c["close"])} for c in c1h])
        df5  = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                               "low":float(c["low"]),"close":float(c["close"])} for c in c5])
        df4h = pd.DataFrame([{"open":float(c["open"]),"high":float(c["high"]),
                               "low":float(c["low"]),"close":float(c["close"])} for c in c4h]) if c4h else df1h

        px = signal["px"]
        direction = signal["tendance"] if signal["tendance"] in ("BULL", "BEAR") else \
                    ("BULL" if "BUY" in signal["action"] else "BEAR")

        scores = {}
        justifs_pos, justifs_neg = [], []

        try:
            ema20 = df1h['close'].ewm(span=20, adjust=False).mean().iloc[-2]
            ema50 = df1h['close'].ewm(span=50, adjust=False).mean().iloc[-2]
            tendance_bull = ema20 > ema50
            aligne = (tendance_bull and direction == "BULL") or (not tendance_bull and direction == "BEAR")
            scores["tendance_h1"] = 100 if aligne else 30
            (justifs_pos if aligne else justifs_neg).append(
                "Tendance H1 alignée avec le signal" if aligne else "Tendance H1 contraire au signal")
        except Exception:
            scores["tendance_h1"] = 50

        adx = calculer_adx(df1h)
        scores["adx"] = min(100, adx * 2.5)
        if adx >= 25:
            justifs_pos.append(f"ADX élevé ({adx:.1f}) — tendance forte")
        else:
            justifs_neg.append(f"ADX faible ({adx:.1f}) — tendance peu marquée")

        try:
            delta = df1h['close'].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi_val = float((100 - (100 / (1 + rs))).iloc[-2])
            if direction == "BULL":
                coherent = 40 <= rsi_val <= 70
            else:
                coherent = 30 <= rsi_val <= 60
            scores["rsi_coherence"] = 90 if coherent else 40
            (justifs_pos if coherent else justifs_neg).append(
                f"RSI cohérent ({rsi_val:.1f})" if coherent else f"RSI incohérent avec le signal ({rsi_val:.1f})")
        except Exception:
            scores["rsi_coherence"] = 50
            rsi_val = 50.0

        macd_line, signal_line, hist = calculer_macd_signal(df1h)
        macd_bull = hist > 0
        macd_ok = (macd_bull and direction == "BULL") or (not macd_bull and direction == "BEAR")
        scores["macd_coherence"] = 85 if macd_ok else 35
        (justifs_pos if macd_ok else justifs_neg).append(
            "MACD confirme la direction" if macd_ok else "MACD divergent du signal")

        try:
            e9  = df5['close'].ewm(span=9, adjust=False).mean().iloc[-2]
            e21 = df5['close'].ewm(span=21, adjust=False).mean().iloc[-2]
            aligne_m5 = (e9 > e21 and direction == "BULL") or (e9 < e21 and direction == "BEAR")
            scores["ema_alignement"] = 80 if aligne_m5 else 40
        except Exception:
            scores["ema_alignement"] = 50

        atr = calculer_atr(df1h)
        atr_pct = (atr / px * 100) if px else 0
        if 0.05 <= atr_pct <= 1.2:
            scores["atr_volatilite"] = 90
            justifs_pos.append("Volatilité favorable (ATR normal)")
        elif atr_pct > 1.2:
            scores["atr_volatilite"] = 45
            justifs_neg.append("Volatilité excessive — risque de faux breakout")
        else:
            scores["atr_volatilite"] = 55
            justifs_neg.append("Marché trop calme — momentum faible")

        structure_score = evaluer_structure_marche(df1h)
        scores["structure_marche"] = structure_score
        if structure_score >= 60:
            justifs_pos.append("Structure de marché claire")
        else:
            justifs_neg.append("Structure de marché peu lisible")

        dist_sr = calculer_distance_support_resistance(df1h, px)
        if strategie_nom == "OPEN_DRIVE":
            scores["distance_sr"] = 100 - dist_sr if dist_sr < 30 else 50
        else:
            scores["distance_sr"] = 100 - dist_sr if dist_sr < 25 else 40

        rr = signal.get("rr", 0)
        scores["qualite_cassure"] = min(100, rr * 30)
        if rr >= 2.0:
            justifs_pos.append(f"R/R solide ({rr}R)")
        else:
            justifs_neg.append(f"R/R faible ({rr}R)")

        spread_pct = estimer_spread_relatif(symbole, px)
        scores["spread"] = 90 if spread_pct < 0.025 else 60

        try:
            ema20_4h = df4h['close'].ewm(span=20, adjust=False).mean().iloc[-2]
            ema50_4h = df4h['close'].ewm(span=50, adjust=False).mean().iloc[-2]
            tendance_h4_bull = ema20_4h > ema50_4h
            coherent_tf = (tendance_h4_bull and direction == "BULL") or (not tendance_h4_bull and direction == "BEAR")
            scores["multi_tf_coherence"] = 90 if coherent_tf else 35
            (justifs_pos if coherent_tf else justifs_neg).append(
                "H1 et H4 alignés" if coherent_tf else "Divergence H1/H4 — prudence")
        except Exception:
            scores["multi_tf_coherence"] = 50

        poids = ia_poids_ajustes.get((strategie_nom, symbole), IA_CONFIG["poids"])
        total_poids = sum(poids.values())
        score_base = sum(scores.get(k, 50) * v for k, v in poids.items()) / total_poids

        contexte = analyser_contexte_marche(symbole, df1h, df4h)
        facteur_contexte, justif_contexte = contexte_vers_facteur_confiance(contexte, direction)
        justifs_pos.extend(j for j in justif_contexte if "confirmée" in j or "cassure" in j)
        justifs_neg.extend(j for j in justif_contexte if "contraire" in j or "sans tendance" in j
                           or "excessive" in j or "faible" in j or "consolidation" in j)

        risque_detecte, penalite_faux_signal, raisons_faux_signal = detecter_faux_signal(
            df1h, df5, signal, contexte)
        if risque_detecte:
            justifs_neg.extend(raisons_faux_signal)

        multi_tf = analyser_coherence_multi_tf(symbole, direction)
        if multi_tf["penalite"] > 0:
            justifs_neg.extend(multi_tf["raisons"])
        else:
            justifs_pos.extend(r for r in multi_tf["raisons"] if "forte" in r)

        score_final = (score_base * facteur_contexte) - penalite_faux_signal - multi_tf["penalite"]
        score_final = round(max(0, min(100, score_final)), 1)
        accepte = score_final >= IA_CONFIG["seuil_acceptation"]

        gestion_risque = optimiser_gestion_risque(signal, contexte, df1h)

        return {
            "accepte": accepte, "score": score_final, "score_base": round(score_base, 1),
            "justification": justifs_pos if accepte else (justifs_neg if justifs_neg else justifs_pos),
            "details": scores, "rsi_val": round(rsi_val, 1), "adx_val": round(adx, 1),
            "contexte_marche": contexte, "risque_faux_signal": risque_detecte,
            "penalite_faux_signal": penalite_faux_signal, "raisons_faux_signal": raisons_faux_signal,
            "multi_tf": multi_tf, "gestion_risque": gestion_risque,
        }
    except Exception as e:
        print(f"[Moteur IA/{symbole}] {e}", flush=True)
        return {"accepte": False, "score": 0, "justification": ["Erreur d'analyse IA"], "details": {}}

def groq_second_avis(symbole, signal, strategie_nom, verdict_calcul):
    if not IA_CONFIG["groq_active"] or not GROQ_API_KEY:
        return {"disponible": False, "score": None, "veto": False, "avis": "Groq désactivé"}
    try:
        contexte = verdict_calcul.get("contexte_marche", {})
        multi_tf = verdict_calcul.get("multi_tf", {})
        raisons_fs = verdict_calcul.get("raisons_faux_signal", [])
        gestion_risque = verdict_calcul.get("gestion_risque", {})
        prompt = (
            "Tu es un analyste de risque expert pour un bot de trading automatisé. "
            "Un signal a déjà été détecté par une stratégie technique, validé par un moteur "
            "de calcul déterministe (ADX/RSI/MACD/structure/contexte/multi-timeframe), et une "
            "gestion de risque a proposé un SL/TP. Ton rôle est de donner un second avis "
            "indépendant, en confirmant ou en déconseillant, avec une explication précise.\n\n"
            "Réponds UNIQUEMENT en JSON strict, sans texte autour, format exact:\n"
            '{"score": <entier 0-100>, "avis": "<2-3 phrases: verdict + raisons principales>"}\n\n'
            f"=== SIGNAL ===\n"
            f"Actif: {symbole} | Stratégie: {strategie_nom} | Direction: {signal.get('action')}\n"
            f"R/R prévu: {signal.get('rr')}\n\n"
            f"=== CALCUL DÉTERMINISTE ===\n"
            f"Score: {verdict_calcul.get('score')}% (base avant ajustements: {verdict_calcul.get('score_base','?')}%)\n"
            f"Justifications: {', '.join(verdict_calcul.get('justification', [])[:4])}\n"
            f"RSI H1: {verdict_calcul.get('rsi_val', '?')} | ADX H1: {verdict_calcul.get('adx_val', '?')}\n\n"
            f"=== CONTEXTE MARCHÉ ===\n"
            f"Tendance: {contexte.get('tendance','?')} | Volatilité: {contexte.get('volatilite','?')}\n"
            f"Consolidation: {contexte.get('consolidation','?')} | Proche cassure: {contexte.get('proche_cassure','?')}\n\n"
            f"=== DÉTECTION FAUX SIGNAUX ===\n"
            f"Alertes: {', '.join(raisons_fs) if raisons_fs else 'Aucune alerte détectée'}\n\n"
            f"=== MULTI-TIMEFRAME (M1/M5/M15/H1) ===\n"
            f"Cohérence: {multi_tf.get('score','?')}% | Détail: {multi_tf.get('detail',{})}\n\n"
            f"=== GESTION DU RISQUE PROPOSÉE ===\n"
            f"SL optimisé: {gestion_risque.get('sl_optimise','?')} | "
            f"R:R optimisé: {gestion_risque.get('rr_optimise','?')}\n\n"
            "Sois sévère si le contexte te semble risqué (faux breakout, marché sans "
            "direction claire, divergence, volatilité excessive, incohérence multi-TF). "
            "Réponds uniquement le JSON."
        )
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": 200,
        }
        resp = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                             json=payload, timeout=8)
        if resp.status_code != 200:
            print(f"[Groq] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return {"disponible": False, "score": None, "veto": False, "avis": "Groq indisponible (HTTP)"}
        data = resp.json()
        texte = data["choices"][0]["message"]["content"].strip()
        texte = texte.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(texte)
        score_groq = float(parsed.get("score", 50))
        avis_groq  = str(parsed.get("avis", ""))[:300]
        veto = score_groq < IA_CONFIG["groq_seuil_veto"]
        return {"disponible": True, "score": score_groq, "veto": veto, "avis": avis_groq}
    except Exception as e:
        print(f"[Groq] Erreur: {e}", flush=True)
        return {"disponible": False, "score": None, "veto": False, "avis": "Groq indisponible (erreur)"}

def ia_enregistrer_resultat(symbol, strategie_nom, score, timeframe, win,
                             tp_atteint, sl_atteint, drawdown_pct=0,
                             avis_ia_score=None, sl=None, tp=None,
                             duree_secondes=None, gemini_score=None,
                             contexte_marche=None):
    maintenant = datetime.datetime.utcnow()
    entree = {
        "symbol": symbol, "strategie": strategie_nom, "score": score,
        "timeframe": timeframe, "win": win, "tp_atteint": tp_atteint,
        "sl_atteint": sl_atteint, "drawdown_pct": drawdown_pct, "ts": time.time(),
        "heure_utc": maintenant.hour, "date": maintenant.strftime("%Y-%m-%d"),
        "avis_ia_score": avis_ia_score, "gemini_score": gemini_score,
        "sl": sl, "tp": tp, "duree_secondes": duree_secondes,
        "contexte_marche": contexte_marche,
    }
    ia_historique.append(entree)
    cle = (strategie_nom, symbol)
    trades_cle = [h for h in ia_historique if (h["strategie"], h["symbol"]) == cle]
    if len(trades_cle) < 15:
        return
    winrate = sum(1 for t in trades_cle if t["win"]) / len(trades_cle)
    poids_base = dict(IA_CONFIG["poids"])
    ajustement = 1.15 if winrate < 0.45 else (0.9 if winrate > 0.65 else 1.0)
    criteres_structurels = ("tendance_h1", "adx", "structure_marche", "multi_tf_coherence")
    poids_ajustes = {k: round(v * (ajustement if k in criteres_structurels else 1.0), 2)
                     for k, v in poids_base.items()}
    ia_poids_ajustes[cle] = poids_ajustes
    print(f"[IA Learning] {cle}: winrate={winrate:.0%} sur {len(trades_cle)} trades "
          f"→ poids ajustés (facteur {ajustement})", flush=True)

def _winrate(liste):
    if not liste:
        return None, 0
    return round(sum(1 for x in liste if x["win"]) / len(liste) * 100, 1), len(liste)

def stats_par_strategie():
    groupes = {}
    for h in ia_historique:
        groupes.setdefault(h["strategie"], []).append(h)
    return {k: _winrate(v) for k, v in groupes.items()}

def stats_par_actif():
    groupes = {}
    for h in ia_historique:
        groupes.setdefault(h["symbol"], []).append(h)
    return {k: _winrate(v) for k, v in groupes.items()}

def stats_par_timeframe():
    groupes = {}
    for h in ia_historique:
        groupes.setdefault(h.get("timeframe", "?"), []).append(h)
    return {k: _winrate(v) for k, v in groupes.items()}

def stats_par_tranche_score():
    tranches = {"< 85%": [], "85-90%": [], "90-95%": [], "≥ 95%": []}
    for h in ia_historique:
        s = h.get("score", 0)
        if s < 85: tranches["< 85%"].append(h)
        elif s < 90: tranches["85-90%"].append(h)
        elif s < 95: tranches["90-95%"].append(h)
        else: tranches["≥ 95%"].append(h)
    return {k: _winrate(v) for k, v in tranches.items()}

def stats_par_heure():
    groupes = {}
    for h in ia_historique:
        heure = h.get("heure_utc")
        if heure is None:
            continue
        tranche = f"{(heure // 4) * 4:02d}h-{(heure // 4) * 4 + 4:02d}h"
        groupes.setdefault(tranche, []).append(h)
    return {k: _winrate(v) for k, v in sorted(groupes.items())}

def stats_gemini_vs_sans():
    avec_gemini = [h for h in ia_historique if h.get("gemini_score") is not None]
    sans_gemini = [h for h in ia_historique if h.get("gemini_score") is None]
    return {"avec_gemini": _winrate(avec_gemini), "sans_gemini": _winrate(sans_gemini)}

def stats_par_contexte_marche():
    groupes = {}
    for h in ia_historique:
        ctx = h.get("contexte_marche") or {}
        tendance = ctx.get("tendance", "INCONNU")
        groupes.setdefault(tendance, []).append(h)
    return {k: _winrate(v) for k, v in groupes.items()}

# ==========================================
# CERVEAU PRO TRADER
# ==========================================

def cerveau_pro_trader(symbole):
    signaux_valides = []
    print(f"[DEBUG] === Analyse {symbole} — début cycle ===", flush=True)
    for fn, nom_strategie, emoji_ctx in (
        (analyser_ema_cross, "EMA_CROSS", "📊 CROISEMENT EMA 5/20"),
    ):
        try:
            signal_brut = fn(symbole)
        except Exception as e:
            print(f"[DEBUG] {symbole}/{nom_strategie} EXCEPTION pendant la détection: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        if not signal_brut:
            print(f"[DEBUG] {symbole}/{nom_strategie} → aucune configuration détectée", flush=True)
            continue
        print(f"[DEBUG] {symbole}/{nom_strategie} → SIGNAL BRUT DÉTECTÉ: "
              f"action={signal_brut.get('action')} rr={signal_brut.get('rr')} "
              f"px={signal_brut.get('px')}", flush=True)
        try:
            verdict = moteur_ia_valider_signal(symbole, signal_brut, nom_strategie)
        except Exception as e:
            print(f"[DEBUG] {symbole}/{nom_strategie} EXCEPTION dans moteur_ia_valider_signal: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        print(f"[DEBUG] {symbole}/{nom_strategie} → score calcul = {verdict.get('score')}% "
              f"(seuil actuel = {IA_CONFIG['seuil_acceptation']}%)", flush=True)
        if not verdict["accepte"]:
            print(f"[IA] {symbole}/{nom_strategie} REJETÉ (calcul) — score {verdict['score']}% "
                  f"< seuil {IA_CONFIG['seuil_acceptation']}%", flush=True)
            continue
        try:
            avis_groq = groq_second_avis(symbole, signal_brut, nom_strategie, verdict)
        except Exception as e:
            print(f"[DEBUG] {symbole}/{nom_strategie} EXCEPTION dans groq_second_avis: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        print(f"[DEBUG] {symbole}/{nom_strategie} → Groq disponible={avis_groq.get('disponible')} "
              f"score={avis_groq.get('score')} veto={avis_groq.get('veto')}", flush=True)
        if avis_groq["veto"]:
            print(f"[Groq] {symbole}/{nom_strategie} VETO — score Groq "
                  f"{avis_groq['score']}% < seuil {IA_CONFIG['groq_seuil_veto']}% "
                  f"({avis_groq['avis']})", flush=True)
            continue
        print(f"[DEBUG] {symbole}/{nom_strategie} → ✅✅✅ SIGNAL VALIDÉ DE BOUT EN BOUT", flush=True)
        signal_brut["contexte_detecte"]  = emoji_ctx
        signal_brut["ia_score"]          = verdict["score"]
        signal_brut["ia_score_base"]     = verdict.get("score_base", verdict["score"])
        signal_brut["ia_justification"]  = verdict["justification"]
        signal_brut["ia_accepte"]        = True
        signal_brut["strategie_nom_ia"]  = nom_strategie
        signal_brut["gemini_score"]      = avis_groq["score"]
        signal_brut["gemini_avis"]       = avis_groq["avis"]
        signal_brut["gemini_disponible"] = avis_groq["disponible"]
        signal_brut["contexte_marche"]      = verdict.get("contexte_marche", {})
        signal_brut["risque_faux_signal"]   = verdict.get("risque_faux_signal", False)
        signal_brut["raisons_faux_signal"]  = verdict.get("raisons_faux_signal", [])
        signal_brut["multi_tf"]             = verdict.get("multi_tf", {})
        signal_brut["gestion_risque"]       = verdict.get("gestion_risque", {})
        signaux_valides.append(signal_brut)
    return signaux_valides

# ==========================================
# ✅ /Volatility GRANULAIRE
# ==========================================

@bot.message_handler(commands=['Volatility'])
def gerer_volatility(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin uniquement.")
    parts = message.text.strip().split()
    if len(parts) == 1:
        lignes = ["🔥 *STATUT VOLATILITY PAIRS:*\n━━━━━━━━━━━━━━━━━━"]
        for p, actif in volatility_pairs_active.items():
            lignes.append(f"  {'✅' if actif else '❌'} {p}")
        lignes.append("\n*Commandes:*")
        lignes.append("/Volatility V10 ON/OFF")
        lignes.append("/Volatility ALL ON/OFF")
        return bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")
    if len(parts) < 3:
        return bot.send_message(message.chat.id,
            "Usage: /Volatility V10 ON\n/Volatility ALL OFF", parse_mode="Markdown")
    paire  = parts[1].upper()
    action = parts[2].upper()
    if action not in ("ON","OFF"):
        return bot.send_message(message.chat.id, "Action invalide: ON ou OFF")
    etat = (action == "ON")
    if paire == "ALL":
        for p in volatility_pairs_active:
            volatility_pairs_active[p] = etat
        msg = ("✅ Toutes les paires Volatility *ACTIVÉES*"
               if etat else "⛔ Toutes les paires Volatility *DÉSACTIVÉES*")
        return bot.send_message(message.chat.id, msg, parse_mode="Markdown")
    if paire in volatility_pairs_active:
        volatility_pairs_active[paire] = etat
        msg = (f"✅ {paire} *ACTIVÉ*" if etat else f"⛔ {paire} *DÉSACTIVÉ*")
        return bot.send_message(message.chat.id, msg, parse_mode="Markdown")
    bot.send_message(message.chat.id,
        f"❌ Paire inconnue: {paire}\nValides: V10, V25, V50, V75, V100, ALL")

@bot.message_handler(commands=['Commodities'])
def gerer_commodities(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin uniquement.")
    parts = message.text.strip().split()
    if len(parts) == 1:
        lignes = ["🥇 *STATUT GOLD/ARGENT:*\n━━━━━━━━━━━━━━━━━━"]
        for p, actif in commodity_pairs_active.items():
            lignes.append(f"  {'✅' if actif else '❌'} {p}")
        lignes.append("\n*Commandes:*")
        lignes.append("/Commodities XAUUSD ON/OFF")
        lignes.append("/Commodities ALL ON/OFF")
        return bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")
    if len(parts) < 3:
        return bot.send_message(message.chat.id,
            "Usage: /Commodities XAUUSD ON\n/Commodities ALL OFF", parse_mode="Markdown")
    paire  = parts[1].upper()
    action = parts[2].upper()
    if action not in ("ON","OFF"):
        return bot.send_message(message.chat.id, "Action invalide: ON ou OFF")
    etat = (action == "ON")
    if paire == "ALL":
        for p in commodity_pairs_active:
            commodity_pairs_active[p] = etat
        msg = ("✅ Gold/Argent *ACTIVÉS*" if etat else "⛔ Gold/Argent *DÉSACTIVÉS*")
        return bot.send_message(message.chat.id, msg, parse_mode="Markdown")
    if paire in commodity_pairs_active:
        commodity_pairs_active[paire] = etat
        msg = (f"✅ {paire} *ACTIVÉ*" if etat else f"⛔ {paire} *DÉSACTIVÉ*")
        return bot.send_message(message.chat.id, msg, parse_mode="Markdown")
    bot.send_message(message.chat.id, f"❌ Paire inconnue: {paire}\nValides: XAUUSD, XAGUSD, ALL")

# ==========================================
# /risk
# ==========================================

@bot.message_handler(commands=['risk'])
def gerer_risque(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin uniquement.")
    parts = message.text.strip().split()
    if len(parts) == 1:
        txt = (
            f"⚙️ *PARAMÈTRES DE RISQUE ACTUELS*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Risque/trade : {RISK_CONFIG['risk_per_trade_pct']}%\n"
            f"Limite perte/jour : {RISK_CONFIG['daily_loss_limit_pct']}%\n"
            f"Pertes consécutives max : {RISK_CONFIG['max_consecutive_losses']}\n"
            f"Durée pause anti-tilt : {RISK_CONFIG['pause_duration_minutes']} min\n"
            f"Partial TP : {int(RISK_CONFIG['partial_tp_ratio']*100)}%\n"
            f"Trades max/jour : {RISK_CONFIG['max_trades_per_day']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Usage: /risk <param> <valeur>\n"
            f"Ex: /risk risk_per_trade_pct 1.5"
        )
        return bot.send_message(message.chat.id, txt, parse_mode="Markdown")
    if len(parts) >= 3 and parts[1] in RISK_CONFIG:
        try:
            valeur = float(parts[2])
            RISK_CONFIG[parts[1]] = valeur
            return bot.send_message(message.chat.id,
                f"✅ {parts[1]} = {valeur}", parse_mode="Markdown")
        except ValueError:
            return bot.send_message(message.chat.id, "❌ Valeur invalide.")
    bot.send_message(message.chat.id, "❌ Paramètre inconnu.")

# ==========================================
# /iaconfig
# ==========================================

@bot.message_handler(commands=['iaconfig'])
def gerer_ia_config(message):
    if message.chat.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "❌ Admin uniquement.")
    parts = message.text.strip().split()
    if len(parts) == 1:
        groq_statut = "✅ Actif" if (IA_CONFIG["groq_active"] and GROQ_API_KEY) else \
                     ("⚠️ Activé mais clé absente" if IA_CONFIG["groq_active"] else "❌ Désactivé")
        txt = (
            f"🤖 *PARAMÈTRES MOTEUR IA*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Seuil d'acceptation (calcul) : {IA_CONFIG['seuil_acceptation']}%\n"
            f"Second avis Groq : {groq_statut}\n"
            f"Seuil de veto Groq : {IA_CONFIG['groq_seuil_veto']}%\n"
            f"Trades enregistrés (apprentissage) : {len(ia_historique)}\n"
            f"Couples (stratégie,symbole) ajustés : {len(ia_poids_ajustes)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Usage:\n"
            f"/iaconfig seuil_acceptation 90\n"
            f"/iaconfig groq_active 0 (ou 1)\n"
            f"/iaconfig groq_seuil_veto 35"
        )
        return bot.send_message(message.chat.id, txt, parse_mode="Markdown")
    if len(parts) >= 3 and parts[1] == "seuil_acceptation":
        try:
            valeur = float(parts[2])
            if not (0 <= valeur <= 100):
                return bot.send_message(message.chat.id, "❌ Le seuil doit être entre 0 et 100.")
            IA_CONFIG["seuil_acceptation"] = valeur
            return bot.send_message(message.chat.id,
                f"✅ Seuil d'acceptation IA = {valeur}%", parse_mode="Markdown")
        except ValueError:
            return bot.send_message(message.chat.id, "❌ Valeur invalide.")
    if len(parts) >= 3 and parts[1] == "groq_active":
        IA_CONFIG["groq_active"] = parts[2] in ("1", "true", "on", "True")
        return bot.send_message(message.chat.id,
            f"✅ Second avis Groq : {'activé' if IA_CONFIG['groq_active'] else 'désactivé'}",
            parse_mode="Markdown")
    if len(parts) >= 3 and parts[1] == "groq_seuil_veto":
        try:
            valeur = float(parts[2])
            IA_CONFIG["groq_seuil_veto"] = valeur
            return bot.send_message(message.chat.id,
                f"✅ Seuil de veto Groq = {valeur}%", parse_mode="Markdown")
        except ValueError:
            return bot.send_message(message.chat.id, "❌ Valeur invalide.")
    bot.send_message(message.chat.id, "❌ Paramètre inconnu.")

@bot.message_handler(commands=['testgroq'])
def test_groq_reel(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if not GROQ_API_KEY:
        return bot.send_message(uid,
            "❌ *GROQ_API_KEY absente*\n"
            "Aucune variable d'environnement GROQ_API_KEY configurée sur Render.\n"
            "Le bot tourne en mode dégradé (calcul seul) — c'est normal, pas un bug.",
            parse_mode="Markdown")
    bot.send_message(uid, "🔄 Appel réel en cours vers l'API Groq, patiente...")
    debut = time.time()
    try:
        prompt_test = (
            "Réponds UNIQUEMENT ce JSON exact, rien d'autre: "
            '{"score": 77, "avis": "Ceci est un test de connexion reussi"}'
        )
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt_test}],
            "temperature": 0.1, "max_tokens": 50,
        }
        resp = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                             json=payload, timeout=10)
        duree = round(time.time() - debut, 2)
        rapport = (
            f"🔬 *TEST RÉEL API GROQ*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Endpoint : `{GROQ_URL}`\n"
            f"Modèle : `{GROQ_MODEL}`\n"
            f"Code HTTP : *{resp.status_code}*\n"
            f"Durée : {duree}s\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        if resp.status_code == 200:
            try:
                data = resp.json()
                texte_brut = data["choices"][0]["message"]["content"]
                rapport += (
                    f"✅ *Réponse brute reçue de Groq* :\n"
                    f"`{texte_brut[:300]}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👉 Ceci est la réponse RÉELLE de Groq, pas une simulation.\n"
                    f"Si tu vois du texte cohérent ci-dessus, Groq fonctionne."
                )
            except Exception as parse_err:
                rapport += (
                    f"⚠️ HTTP 200 mais parsing échoué : {parse_err}\n"
                    f"Réponse brute complète :\n`{resp.text[:500]}`"
                )
        else:
            rapport += (
                f"❌ *Échec* — Groq a refusé la requête.\n"
                f"Corps de la réponse :\n`{resp.text[:500]}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Causes possibles : clé invalide, modèle `{GROQ_MODEL}` inexistant/déprécié, "
                f"quota dépassé, compte suspendu."
            )
        bot.send_message(uid, rapport, parse_mode="Markdown")
    except requests.exceptions.Timeout:
        bot.send_message(uid, "❌ *Timeout* — Groq n'a pas répondu en 10s. "
                              "Le bot basculerait en mode dégradé sur un vrai signal.",
                         parse_mode="Markdown")
    except Exception as e:
        bot.send_message(uid, f"❌ *Erreur réseau réelle* : `{type(e).__name__}: {e}`\n"
                              f"Le bot basculerait en mode dégradé sur un vrai signal.",
                         parse_mode="Markdown")

@bot.message_handler(commands=['testderiv'])
def test_deriv_reel(message):
    """
    ✅ Diagnostic honnête, étape par étape, pour la NOUVELLE API Deriv
    (REST → OTP → WebSocket). Ne révèle jamais le token en entier, teste
    chaque étape séparément pour localiser précisément où ça bloque.
    """
    uid = message.chat.id
    if not est_autorise(uid): return

    if not DERIV_API_TOKEN:
        return bot.send_message(uid,
            "❌ *DERIV_API_TOKEN absente*\n"
            "Aucune variable d'environnement DERIV_API_TOKEN configurée sur Render.",
            parse_mode="Markdown")
    if not DERIV_APP_ID:
        return bot.send_message(uid,
            "❌ *DERIV_APP_ID absente*\n"
            "Il faut l'App ID trouvé sur developers.deriv.com → Registered Apps "
            "(ex: 32Oh8ivJRXsJrKUqVgYhR), à mettre dans la variable DERIV_APP_ID sur Render.",
            parse_mode="Markdown")

    longueur = len(DERIV_API_TOKEN)
    apercu = f"{DERIV_API_TOKEN[:3]}...{DERIV_API_TOKEN[-2:]}" if longueur >= 6 else "(trop court)"
    a_espace = DERIV_API_TOKEN != DERIV_API_TOKEN.strip()
    espace_msg = "⚠️ OUI — c'est probablement le bug" if a_espace else "✅ Non"

    bot.send_message(uid,
        f"🔬 *DIAGNOSTIC DERIV — NOUVELLE API*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Token — longueur : {longueur} caractères\n"
        f"Token — aperçu : `{apercu}`\n"
        f"Token — espace parasite : {espace_msg}\n"
        f"App ID : `{DERIV_APP_ID}`\n"
        f"Type de compte visé : {DERIV_ACCOUNT_TYPE}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Étape 1/3 : récupération des comptes (REST)...", parse_mode="Markdown")

    # Étape 1 : GET /trading/v1/options/accounts
    try:
        account_id = deriv_get_account_id(force_refresh=True)
        bot.send_message(uid, f"✅ Étape 1/3 réussie — compte trouvé : `{account_id}`",
                         parse_mode="Markdown")
    except Exception as e:
        return bot.send_message(uid,
            f"❌ *ÉTAPE 1/3 ÉCHOUÉE — récupération des comptes*\n"
            f"`{e}`\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 Vérifie que DERIV_APP_ID correspond bien à l'App ID affiché sur "
            f"developers.deriv.com → Registered Apps, et que le token n'a pas été "
            f"révoqué depuis.",
            parse_mode="Markdown")

    # Étape 2 : POST .../otp
    try:
        ws_url = _deriv_obtenir_ws_url(force_refresh=True)
        bot.send_message(uid, "✅ Étape 2/3 réussie — OTP obtenu, URL WebSocket générée.")
    except Exception as e:
        return bot.send_message(uid,
            f"❌ *ÉTAPE 2/3 ÉCHOUÉE — génération de l'OTP*\n`{e}`",
            parse_mode="Markdown")

    # Étape 3 : connexion WS + requête balance
    try:
        solde = deriv_connecter()
        bot.send_message(uid,
            f"✅ *ÉTAPE 3/3 RÉUSSIE — CONNEXION COMPLÈTE*\n"
            f"Solde : `{solde.get('balance')} {solde.get('currency')}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 Tout fonctionne. Tu peux activer l'auto-trading via /menu.",
            parse_mode="Markdown")
    except Exception as e:
        bot.send_message(uid,
            f"❌ *ÉTAPE 3/3 ÉCHOUÉE — connexion WebSocket*\n`{e}`",
            parse_mode="Markdown")

@bot.message_handler(commands=['iastats'])
def ia_stats(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if not ia_historique:
        return bot.send_message(uid, "📭 Aucune donnée d'apprentissage IA pour le moment.")
    parts = message.text.strip().split()
    vue = parts[1].lower() if len(parts) > 1 else "resume"

    def fmt_stats(d, titre):
        lignes = [f"*{titre}*"]
        for k, (wr, n) in d.items():
            if wr is None:
                continue
            lignes.append(f"  {k} : {wr:.0f}% sur {n} trades")
        return lignes

    if vue == "strategie":
        lignes = ["🤖 *WIN-RATE PAR STRATÉGIE*\n━━━━━━━━━━━━━━━━━━━━━━"]
        lignes += fmt_stats(stats_par_strategie(), "Par stratégie")
    elif vue == "actif":
        lignes = ["🤖 *WIN-RATE PAR ACTIF*\n━━━━━━━━━━━━━━━━━━━━━━"]
        lignes += fmt_stats(stats_par_actif(), "Par actif")
    elif vue == "score":
        lignes = ["🤖 *WIN-RATE PAR TRANCHE DE SCORE*\n━━━━━━━━━━━━━━━━━━━━━━"]
        lignes += fmt_stats(stats_par_tranche_score(), "Par score du calcul déterministe")
    elif vue == "heure":
        lignes = ["🤖 *WIN-RATE PAR HEURE (UTC)*\n━━━━━━━━━━━━━━━━━━━━━━"]
        lignes += fmt_stats(stats_par_heure(), "Par tranche horaire")
    elif vue in ("gemini", "groq"):
        lignes = ["🤖 *WIN-RATE AVEC/SANS GROQ*\n━━━━━━━━━━━━━━━━━━━━━━"]
        g = stats_gemini_vs_sans()
        wr_avec, n_avec = g["avec_gemini"]
        wr_sans, n_sans = g["sans_gemini"]
        lignes.append(f"  Avec Groq consulté : {wr_avec:.0f}% sur {n_avec} trades" if wr_avec is not None
                     else "  Avec Groq consulté : pas assez de données")
        lignes.append(f"  Sans Groq (calcul seul) : {wr_sans:.0f}% sur {n_sans} trades" if wr_sans is not None
                     else "  Sans Groq (calcul seul) : pas assez de données")
    elif vue == "contexte":
        lignes = ["🤖 *WIN-RATE PAR CONTEXTE MARCHÉ*\n━━━━━━━━━━━━━━━━━━━━━━"]
        lignes += fmt_stats(stats_par_contexte_marche(), "Par tendance détectée")
    else:
        par_couple = {}
        for h in ia_historique:
            cle = (h["strategie"], h["symbol"])
            par_couple.setdefault(cle, []).append(h["win"])
        lignes = ["🤖 *STATISTIQUES D'APPRENTISSAGE IA*\n━━━━━━━━━━━━━━━━━━━━━━",
                  f"Total trades enregistrés : {len(ia_historique)}\n"]
        for (strat, sym), resultats in par_couple.items():
            wr = sum(1 for r in resultats if r) / len(resultats) * 100
            ajuste = " (poids ajustés)" if (strat, sym) in ia_poids_ajustes else ""
            lignes.append(f"{strat} / {sym} : {wr:.0f}% sur {len(resultats)} trades{ajuste}")
        lignes.append("\n*Vues détaillées disponibles:*")
        lignes.append("/iastats strategie · /iastats actif · /iastats score")
        lignes.append("/iastats heure · /iastats groq · /iastats contexte")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

# ==========================================
# /rapport
# ==========================================

def generer_rapport_texte(uid):
    stats = init_daily_stats(uid)
    total = stats["trades"]
    winrate = (stats["wins"] / total * 100) if total > 0 else 0
    return (
        f"📊 *RAPPORT DU JOUR* ({stats['date']})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Trades exécutés : {total}/{RISK_CONFIG['max_trades_per_day']}\n"
        f"✅ Gagnés : {stats['wins']}  |  ❌ Perdus : {stats['losses']}\n"
        f"🎯 Win Rate : {winrate:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 P&L du jour : {stats['pnl']:+.2f} USD\n"
        f"🏆 Meilleur trade : {stats['best_trade']:+.2f} USD\n"
        f"💔 Pire trade : {stats['worst_trade']:+.2f} USD\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 P&L total cumulé : {pnl_total.get(uid,0):+.2f} USD\n"
        f"📈 Bilan global : {win_count.get(uid,0)}W / {loss_count.get(uid,0)}L"
    )

@bot.message_handler(commands=['rapport'])
def rapport_quotidien(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    bot.send_message(uid, generer_rapport_texte(uid), parse_mode="Markdown")

def envoyer_rapports_quotidiens_auto():
    dernier_envoi = None
    while True:
        try:
            time.sleep(60)
            now = datetime.datetime.utcnow()
            cle_jour = now.strftime("%Y-%m-%d")
            if now.hour == 22 and dernier_envoi != cle_jour:
                for uid in list(utilisateurs_actifs):
                    try:
                        bot.send_message(uid, "🌙 *Rapport de fin de journée*\n\n" +
                                         generer_rapport_texte(uid), parse_mode="Markdown")
                    except:
                        pass
                dernier_envoi = cle_jour
        except Exception as e:
            print(f"[Rapport Auto] {e}", flush=True)

# ==========================================
# /pause /resume
# ==========================================

@bot.message_handler(commands=['pause'])
def pause_manuelle(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    stats = init_daily_stats(uid)
    stats["paused_until"] = time.time() + (12 * 3600)
    bot.send_message(uid, "⏸️ Trading mis en pause manuellement pour 12h.\n"
                          "Utilise /resume pour reprendre.", parse_mode="Markdown")

@bot.message_handler(commands=['resume'])
def resume_manuel(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    stats = init_daily_stats(uid)
    stats["paused_until"] = None
    stats["consecutive_losses"] = 0
    bot.send_message(uid, "▶️ Trading repris. Bonne chance!", parse_mode="Markdown")

@bot.message_handler(commands=['debloquer'])
def debloquer_manuel(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    parts = message.text.strip().split()
    cible = uid
    if len(parts) > 1 and message.chat.id == ADMIN_ID:
        try:
            cible = int(parts[1])
        except ValueError:
            return bot.send_message(uid, "❌ ID invalide.")
    etait_bloque = cible in trades_actifs
    trades_actifs.pop(cible, None)
    stats = init_daily_stats(cible)
    stats["paused_until"] = None
    if etait_bloque:
        bot.send_message(uid, f"🔓 Utilisateur {cible} débloqué. Trade actif nettoyé.",
                         parse_mode="Markdown")
        if cible != uid:
            try:
                bot.send_message(cible, "🔓 Ton compte a été débloqué par l'admin. "
                                        "Tu peux à nouveau recevoir des signaux.",
                                 parse_mode="Markdown")
            except: pass
    else:
        bot.send_message(uid, f"✅ Aucun blocage détecté pour {cible} — tout est déjà normal.",
                         parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_technique(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    bloque = "🟠 OUI" if uid in trades_actifs else "🟢 NON"
    en_pause, jusqua = utilisateur_en_pause(uid)
    txt = (
        f"🔧 *STATUS TECHNIQUE*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Trade actif en cours : {bloque}\n"
        f"Pause anti-tilt : {'🟠 OUI' if en_pause else '🟢 NON'}\n"
        f"Cycle scanner : ~15s (parallélisé)\n"
        f"Validité signal : {RISK_CONFIG['signal_validity_seconds']}s\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Si tu ne reçois plus de signaux malgré tout, "
        f"utilise /debloquer pour te débloquer immédiatement."
    )
    bot.send_message(uid, txt, parse_mode="Markdown")

# ==========================================
# SCANNER PRINCIPAL
# ==========================================

def _analyser_une_paire(paire):
    try:
        statut, raison = est_symbole_autorise(paire)
        if statut != "AUTORISE":
            print(f"[DEBUG] {paire} BLOQUÉ par le filtre de session: {statut} — {raison}", flush=True)
            return []
        signaux = cerveau_pro_trader(paire)
        if not signaux:
            print(f"[DEBUG] {paire} → cerveau_pro_trader n'a produit aucun signal ce cycle", flush=True)
            return []
        resultats = []
        for res in signaux:
            px = obtenir_prix_broker_realtime(paire) or res["px"]
            if not px:
                print(f"[DEBUG] {paire}/{res.get('strategie_nom_ia','?')} → "
                      f"IMPOSSIBLE d'obtenir un prix broker temps réel", flush=True)
                continue
            valide = valider_prix_avant_signal(paire, px)
            print(f"[DEBUG] {paire}/{res.get('strategie_nom_ia','?')} → "
                  f"validation prix (broker={px}, stratégie={res.get('px')}) = {valide}", flush=True)
            if valide:
                resultats.append((paire, res, px))
        return resultats
    except Exception as e:
        print(f"[Analyse/{paire}] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        return []

def scanner_marche_auto():
    toutes_paires = ELITE_PAIRS_MT5
    while True:
        try:
            time.sleep(8)  # ✅ V57: cycle plus rapide (15→8s), adapté au scalping M1
            libres = [u for u in utilisateurs_actifs if est_autorise(u)]
            if not libres:
                continue

            resultats = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(_analyser_une_paire, p): p for p in toutes_paires}
                for future in as_completed(futures, timeout=25):
                    try:
                        r_list = future.result()
                        resultats.extend(r_list)
                    except Exception as e:
                        print(f"[Scanner Parallel] {e}", flush=True)

            for paire, res, px in resultats:
                cle = f"{paire}_{res.get('strategie_nom_ia', 'PRO')}"
                signaux_cache[cle] = {
                    "time":    time.time(),
                    "action":  res["action"],
                    "mt5_sl":  res["sl"],
                    "mt5_tp1": res.get("tp1", res["tp"]),
                    "mt5_tp":  res["tp"],
                    "mt5_rr":  res["rr"],
                    "force":   res["force"],
                    "msg":     res["msg"],
                    "confiance": res["confiance"],
                    "strategie": res["strategie"],
                    "strategie_nom_ia": res.get("strategie_nom_ia", "?"),
                    "label":   res["label"],
                    "contexte":res.get("contexte_detecte",""),
                    "ia_score": res.get("ia_score", 0),
                    "ia_justification": res.get("ia_justification", []),
                    "gemini_score": res.get("gemini_score"),
                    "gemini_avis": res.get("gemini_avis", ""),
                    "gemini_disponible": res.get("gemini_disponible", False),
                    "extra":   res,
                }
                derniere_alerte_auto[cle] = time.time()

                nom  = NOMS_AFFICHAGE.get(paire, f"{paire[:3]}/{paire[3:]}")
                dir_ = "🟢 BUY" if "BUY" in res["action"] else "🔴 SELL"
                entry_direction = "BUY" if "BUY" in res["action"] else "SELL"

                for uid in libres:
                    if utilisateur_a_trade_actif(uid): continue
                    peut_trader, raison = utilisateur_peut_trader(uid)
                    if not peut_trader: continue

                    # ✅ NOUVEAU : EXÉCUTION 100% AUTOMATIQUE (comme l'EA vidéo)
                    # Si auto-trading est ON, on ouvre directement le trade réel
                    # via Deriv, sans passer par le bouton "Copier".
                    if peut_ouvrir_automatiquement(paire):
                        try:
                            trade_id, sizing = ouvrir_trade(
                                uid, paire, entry_direction, px,
                                res["sl"], res.get("tp1", res["tp"]), res["tp"],
                                res["strategie"], res["confiance"],
                                label=res["label"],
                                strategie_nom_ia=res.get("strategie_nom_ia","?"),
                                ia_score=res.get("ia_score"),
                                gemini_score=res.get("gemini_score"),
                                contexte_marche=res.get("contexte_marche"),
                                executer_reel=True,
                            )
                            txt_auto = (
                                f"🤖 *TRADE OUVERT AUTOMATIQUEMENT*\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"{nom}  {dir_}\n"
                                f"🎯 {res['label']}\n"
                                f"💰 Entrée : {px:.5f}\n"
                                f"🛑 SL : {res['sl']:.5f}  🎯 TP : {res['tp']:.5f}\n"
                                f"⚖️ R/R : {res['rr']}R · 🎖️ Confiance {res['confiance']}%\n"
                                f"🤖 Score IA : {res.get('ia_score','?')}%\n"
                                f"💵 Mise réelle : ${CONTROL_STATE['stake_usd']} x{CONTROL_STATE['multiplier']}\n"
                                f"🆔 {trade_id}"
                            )
                            bot.send_message(uid, txt_auto, parse_mode="Markdown")
                        except Exception as e:
                            print(f"[Auto-Trading] Échec ouverture réelle {paire}: {e}", flush=True)
                            try:
                                bot.send_message(uid, f"⚠️ Échec ouverture automatique {nom} : {e}")
                            except Exception:
                                pass
                        continue

                    # ── Mode manuel classique (auto-trading OFF) : bouton "Copier" ──
                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton(f"⚡ Copier {nom}", callback_data=f"set_{cle}")
                    )

                    ligne_extra = (f"📈 RSI M15 : {res.get('rsi_value','?')} · "
                                   f"ADX H1 : {res.get('adx_value','?')}\n")
                    zones_conf = res.get("zones_confluence", [])
                    if zones_conf:
                        ligne_extra += f"🎯 Confluence : {' + '.join(zones_conf)}\n"
                    ob = res.get("order_block")
                    if ob:
                        ligne_extra += f"📦 Order Block : {ob[0]:.5f} - {ob[1]:.5f}\n"
                    niveau_cle = res.get("niveau_cle")
                    if niveau_cle:
                        ligne_extra += f"📏 Niveau clé : {niveau_cle:.5f}\n"

                    sizing = calculer_position_size(CAPITAL_ACTUEL, RISK_CONFIG["risk_per_trade_pct"],
                                                    px, res["sl"], paire)

                    justif_txt = " · ".join(res.get("ia_justification", [])[:2])
                    if res.get("gemini_disponible"):
                        ligne_gemini = (f"🔮 Groq : {res.get('gemini_score','?')}% — "
                                       f"{res.get('gemini_avis','')}\n")
                    else:
                        ligne_gemini = ""

                    ctx = res.get("contexte_marche", {})
                    if ctx:
                        ligne_contexte_marche = (
                            f"🌍 Marché : {ctx.get('tendance','?')} · "
                            f"Vol. {ctx.get('volatilite','?')} · ADX {ctx.get('adx','?')}\n")
                    else:
                        ligne_contexte_marche = ""

                    if res.get("risque_faux_signal"):
                        alertes = ", ".join(res.get("raisons_faux_signal", [])[:2])
                        ligne_alerte = f"🚨 Vigilance : {alertes}\n"
                    else:
                        ligne_alerte = ""

                    mtf = res.get("multi_tf", {})
                    if mtf.get("score") is not None:
                        ligne_mtf = f"⏱️ Cohérence M1-M5-M15-H1 : {mtf['score']}%\n"
                    else:
                        ligne_mtf = ""

                    gr = res.get("gestion_risque", {})
                    ligne_risque_ia = f"🛡️ {gr.get('note','')}\n" if gr.get("note") else ""

                    txt = (
                        f"💼 *TERMINAL PRIME V55*\n"
                        f"{nom}  {dir_}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Stratégie : *{res['label']}*\n"
                        f"📊 Contexte  : {res.get('contexte_detecte','')}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"☁️ Structure : {res['force']}\n"
                        f"📍 {res['msg']}\n"
                        f"⏰ {nom_killzone()}\n"
                        f"{ligne_extra}"
                        f"{ligne_contexte_marche}"
                        f"{ligne_mtf}"
                        f"{ligne_alerte}"
                        f"{ligne_risque_ia}"
                        f"⚖️ R/R : {res['rr']}R\n"
                        f"🎖️ Confiance stratégie : {res['confiance']}%\n"
                        f"🤖 Score IA (calcul) : *{res.get('ia_score','?')}%* — {justif_txt}\n"
                        f"{ligne_gemini}"
                        f"💰 Prix réel : {px:.5f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💵 Risque calculé : ${sizing['montant_risque']} "
                        f"({RISK_CONFIG['risk_per_trade_pct']}% du capital)\n"
                        f"⏳ Signal valide {RISK_CONFIG['signal_validity_seconds']}s"
                    )
                    try:
                        bot.send_message(uid, txt, reply_markup=markup, parse_mode="Markdown")
                    except:
                        pass
        except Exception as e:
            print(f"[Scanner V55] {e}", flush=True)

# ==========================================
# MONITORING DES TRADES
# ==========================================

def monitorer_trades_actifs():
    while True:
        try:
            time.sleep(5)
            for uid in list(trades_actifs.keys()):
                if uid not in trades_actifs: continue
                trade = trades_actifs[uid]
                symbole      = trade["symbol"]
                prix_current = obtenir_prix_broker_realtime(symbole)
                if not prix_current: continue
                direction = trade["direction"]

                if trade["state"] == TradeState.TRADE_OPEN:
                    hit_tp1 = (direction == "BUY"  and prix_current >= trade["tp1"]) or \
                              (direction == "SELL" and prix_current <= trade["tp1"])
                    hit_sl  = (direction == "BUY"  and prix_current <= trade["sl"]) or \
                              (direction == "SELL" and prix_current >= trade["sl"])
                    if hit_sl:
                        result = fermer_trade_complet(uid, prix_current, win=False)
                        if result:
                            envoyer_message_resultat(uid, trade, result, perte_totale=True)
                        continue
                    if hit_tp1:
                        partiel = fermer_trade_partiel(uid, prix_current)
                        if partiel:
                            envoyer_message_partiel(uid, trade, partiel, prix_current)
                        continue
                elif trade["state"] == TradeState.TRADE_PARTIAL:
                    appliquer_trailing_stop(uid, prix_current)
                    hit_tp_final = (direction == "BUY"  and prix_current >= trade["tp_final"]) or \
                                   (direction == "SELL" and prix_current <= trade["tp_final"])
                    hit_be_sl    = (direction == "BUY"  and prix_current <= trade["sl"]) or \
                                   (direction == "SELL" and prix_current >= trade["sl"])
                    if hit_tp_final:
                        result = fermer_trade_complet(uid, prix_current, win=True)
                        if result:
                            envoyer_message_resultat(uid, trade, result, perte_totale=False,
                                                     partiel_deja_pris=True)
                        continue
                    if hit_be_sl:
                        result = fermer_trade_complet(uid, prix_current, win=True)
                        if result:
                            envoyer_message_resultat(uid, trade, result, perte_totale=False,
                                                     partiel_deja_pris=True, sortie_be=True)
                        continue
        except Exception as e:
            print(f"[Monitor] {e}", flush=True)

def envoyer_message_partiel(uid, trade, partiel, prix_current):
    msg = (
        f"🟡 *TP1 ATTEINT — 85% SÉCURISÉ!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {trade['symbol']}\n"
        f"Entrée : {trade['entry_price']:.5f}\n"
        f"TP1    : {prix_current:.5f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Profit partiel : +{partiel['pnl_partiel']:.2f} USD* (85% fermé)\n"
        f"🛡️ SL déplacé en *Breakeven* : {partiel['nouveau_sl']:.5f}\n"
        f"🏃 15% restant continue vers le TP final, *sans risque*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Technique pro: sécuriser le gain, laisser courir le reste."
    )
    try: bot.send_message(uid, msg, parse_mode="Markdown")
    except: pass

def envoyer_message_resultat(uid, trade, result, perte_totale, partiel_deja_pris=False, sortie_be=False):
    stats = init_daily_stats(uid)
    if perte_totale:
        msg = (
            f"❌ *TRADE PERDU* 😔\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {trade['symbol']}\n"
            f"Entrée : {trade['entry_price']:.5f}\n"
            f"Sortie : {result['pnl']:+.2f} USD (Stop Loss)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💔 *Perte : {result['pnl']:.2f} USD*\n"
            f"⏱️ Durée : {int(result['duration']/60)} min\n"
            f"🎖️ {trade.get('label','')} (Confiance {trade['confiance']}%)\n"
        )
    elif sortie_be:
        msg = (
            f"🛡️ *SORTIE EN BREAKEVEN/TRAILING*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {trade['symbol']}\n"
            f"Le 15% restant est sorti au niveau sécurisé.\n"
            f"💰 Gain sécurisé sur cette portion : {result['pnl']:+.2f} USD\n"
            f"⏱️ Durée totale : {int(result['duration']/60)} min\n"
            f"🎖️ {trade.get('label','')}\n"
        )
    else:
        msg = (
            f"✅ *TP FINAL ATTEINT — TRADE GAGNÉ!* 🎉🎉\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {trade['symbol']}\n"
            f"Entrée : {trade['entry_price']:.5f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Profit (15% final) : +{result['pnl']:.2f} USD*\n"
            f"⏱️ Durée : {int(result['duration']/60)} min\n"
            f"🎖️ {trade.get('label','')} (Confiance {trade['confiance']}%)\n"
        )
    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Bilan du jour : {stats['wins']}W / {stats['losses']}L "
        f"({stats['pnl']:+.2f} USD)\n"
        f"🏦 P&L total : {pnl_total.get(uid,0):+.2f} USD"
    )
    if daily_loss_limit_atteinte(uid):
        msg += (f"\n\n🛑 *LIMITE DE PERTE JOURNALIÈRE ATTEINTE.*\n"
                f"Trading suspendu jusqu'à demain — protection du capital.")
    else:
        en_pause, _ = utilisateur_en_pause(uid)
        if en_pause:
            msg += (f"\n\n⏸️ *PAUSE ANTI-TILT ACTIVÉE* "
                    f"({RISK_CONFIG['max_consecutive_losses']} pertes consécutives).\n"
                    f"Reprise dans {RISK_CONFIG['pause_duration_minutes']} minutes.")
    try: bot.send_message(uid, msg, parse_mode="Markdown")
    except: pass

# ==========================================
# GESTION DES CLÉS VIP
# ==========================================

DUREES_VALIDES = {
    "1s": (7,"1 Semaine"), "2s": (14,"2 Semaines"),
    "1m": (30,"1 Mois"),   "3m": (90,"3 Mois"),
    "6m": (180,"6 Mois"),  "1a": (365,"1 An"),
    "vie": ("LIFETIME","À VIE 👑"),
}

def est_autorise(uid):
    if uid == ADMIN_ID: return True
    if uid in utilisateurs_autorises:
        exp = utilisateurs_autorises[uid]
        if exp == "LIFETIME" or datetime.datetime.now() < exp: return True
        del utilisateurs_autorises[uid]
        try: bot.send_message(uid, "⚠️ Abonnement expiré. Contacte l'admin.")
        except: pass
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(message.chat.id,
            "⚙️ *GÉNÉRATEUR DE CLÉS VIP*\nUsage : /keygen 1m\n"
            "1s / 2s / 1m / 3m / 6m / 1a / vie / <jours>", parse_mode="Markdown")
    arg = parts[1].lower().strip()
    if arg in DUREES_VALIDES:
        jours, label = DUREES_VALIDES[arg]
    else:
        try:
            jours = int(arg)
            label = f"{jours} jours"
        except:
            return bot.send_message(message.chat.id, "❌ Argument invalide.")
    cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    cles_generees[cle] = jours
    bot.send_message(message.chat.id,
        f"✅ *CLÉ VIP GÉNÉRÉE*\n🔑 `{cle}`\n⏳ Durée : {label}\n"
        f"Activation : `/vip {cle}`", parse_mode="Markdown")

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    cid   = message.chat.id
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(cid, "⚠️ Usage : /vip VOTRE-CLÉ")
    cle = parts[1].strip()
    if cle not in cles_generees:
        return bot.send_message(cid, "❌ Clé invalide ou déjà utilisée.")
    jours = cles_generees.pop(cle)
    if jours == "LIFETIME":
        utilisateurs_autorises[cid] = "LIFETIME"; txt = "À VIE 👑"
    else:
        exp = datetime.datetime.now() + datetime.timedelta(days=jours)
        utilisateurs_autorises[cid] = exp; txt = exp.strftime('%d/%m/%Y à %H:%M')
    bot.send_message(cid,
        f"🎉 *ACCÈS DÉVERROUILLÉ !*\n⏳ Expiration : {txt}\n/start pour commencer.",
        parse_mode="Markdown")

@bot.message_handler(commands=['abonnes'])
def lister_abonnes(message):
    if message.chat.id != ADMIN_ID: return
    now = datetime.datetime.now()
    lignes = ["👥 *ABONNÉS ACTIFS :*\n──────────────────"]
    for uid, exp in utilisateurs_autorises.items():
        if uid == ADMIN_ID: continue
        if exp == "LIFETIME":       statut = "👑 À vie"
        elif now < exp:             statut = f"✅ {(exp-now).days}j (exp: {exp.strftime('%d/%m/%Y')})"
        else:                       statut = "❌ Expiré"
        lignes.append(f"• {uid} → {statut}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(commands=['cles'])
def lister_cles(message):
    if message.chat.id != ADMIN_ID: return
    if not cles_generees:
        return bot.send_message(message.chat.id, "Aucune clé en attente.")
    lignes = ["🔑 *CLÉS EN ATTENTE :*\n──────────────────"]
    for cle, jours in cles_generees.items():
        lignes.append(f"`{cle}` → {'À VIE' if jours=='LIFETIME' else f'{jours}j'}")
    bot.send_message(message.chat.id, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(commands=['historique'])
def historique_trades(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    hist = trades_historique.get(uid, [])
    if not hist:
        return bot.send_message(uid, "📭 Aucun trade dans l'historique.")
    lignes = ["📜 *HISTORIQUE (10 derniers trades)*\n━━━━━━━━━━━━━━━━━━━━━━"]
    for t in hist[-10:][::-1]:
        emoji = "✅" if t["win"] else "❌"
        date_str = datetime.datetime.fromtimestamp(t["timestamp"]).strftime("%d/%m %H:%M")
        lignes.append(f"{emoji} {t['symbol']} {t['direction']} | "
                      f"{t['pnl']:+.2f}$ | {date_str}")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

# ==========================================
# ✅ NOUVEAU : PANNEAU DE CONTRÔLE TELEGRAM
# ==========================================

def obtenir_clavier(uid):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    etat_auto = "🟢 AUTO-TRADING: ON" if CONTROL_STATE["auto_trading_active"] else "🔴 AUTO-TRADING: OFF"
    markup.row(KeyboardButton(etat_auto))
    markup.row(KeyboardButton("📊 STATUS LIVE"), KeyboardButton(f"⚙️ MODE ({CONTROL_STATE['mode']})"))
    markup.row(KeyboardButton("📊 CHOISIR UNE CIBLE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("🎯 PAIRES ACTIVES"), KeyboardButton("💰 RISQUE PAR TRADE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"), KeyboardButton("📊 RAPPORT DU JOUR"))
    markup.row(KeyboardButton("📜 HISTORIQUE"))
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
    if uid != ADMIN_ID:
        return bot.send_message(uid, "❌ Réservé à l'admin.")
    if CONTROL_STATE["stop_urgence_actif"] and not CONTROL_STATE["auto_trading_active"]:
        return bot.send_message(uid,
            "🛑 Stop d'urgence encore actif. Utilise '🛑 STOP D'URGENCE' pour le lever avant de relancer.",
            parse_mode="Markdown")
    CONTROL_STATE["auto_trading_active"] = not CONTROL_STATE["auto_trading_active"]
    deriv_ok = True
    if CONTROL_STATE["auto_trading_active"]:
        try:
            deriv_connecter()
        except Exception as e:
            deriv_ok = False
            CONTROL_STATE["auto_trading_active"] = False
    etat = "🟢 ACTIVÉ" if CONTROL_STATE["auto_trading_active"] else "🔴 DÉSACTIVÉ"
    txt = (
        f"⚙️ *Auto-trading : {etat}*\n\n"
        f"{'Le bot ouvre/ferme les trades automatiquement sur les signaux validés (calcul + Groq), sans clic.' if CONTROL_STATE['auto_trading_active'] else 'Retour au mode notification + bouton Copier manuel.'}\n"
        f"Mode actuel : {CONTROL_STATE['mode']}\n"
        f"Mise réelle : ${CONTROL_STATE['stake_usd']} x{CONTROL_STATE['multiplier']}"
    )
    if not deriv_ok:
        txt += "\n\n⚠️ Connexion Deriv échouée — vérifie DERIV_API_TOKEN sur Render."
    bot.send_message(uid, txt, reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("⚙️ MODE"))
def toggle_mode(message):
    uid = message.chat.id
    if uid != ADMIN_ID:
        return bot.send_message(uid, "❌ Réservé à l'admin.")
    CONTROL_STATE["mode"] = "MULTI" if CONTROL_STATE["mode"] == "SOLO" else "SOLO"
    desc = ("Un seul trade actif à la fois, toutes paires confondues (comme l'EA de la vidéo)."
            if CONTROL_STATE["mode"] == "SOLO" else
            "Plusieurs trades en parallèle possibles, un par paire.")
    bot.send_message(uid, f"⚙️ Mode : *{CONTROL_STATE['mode']}*\n{desc}",
                      reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 STATUS LIVE")
def status_live(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    lignes = ["📊 *STATUS LIVE*\n━━━━━━━━━━━━━━━━━━━━━━"]
    lignes.append(f"Auto-trading : {'🟢 ON' if CONTROL_STATE['auto_trading_active'] else '🔴 OFF'}")
    lignes.append(f"Mode : {CONTROL_STATE['mode']}")
    lignes.append(f"Stop d'urgence : {'🛑 ACTIF' if CONTROL_STATE['stop_urgence_actif'] else '✅ inactif'}")
    lignes.append("━━━━━━━━━━━━━━━━━━━━━━")
    try:
        positions = deriv_positions_ouvertes()
        if not positions:
            lignes.append("Aucun contrat ouvert sur Deriv actuellement.")
        else:
            lignes.append(f"*{len(positions)} contrat(s) ouvert(s) sur Deriv :*")
            for p in positions:
                lignes.append(
                    f"  {p.get('symbol')} {p.get('contract_type')} | "
                    f"Mise {p.get('buy_price')}$ | P&L {p.get('profit', 0):+.2f}$"
                )
    except Exception as e:
        lignes.append(f"⚠️ Impossible de récupérer les positions Deriv : {e}")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "💰 RISQUE PAR TRADE")
def risque_menu(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return
    txt = (
        f"💰 *RISQUE PAR TRADE*\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Mise réelle actuelle : ${CONTROL_STATE['stake_usd']}\n"
        f"Multiplicateur : x{CONTROL_STATE['multiplier']}\n"
        f"Risque % configuré : {RISK_CONFIG['risk_per_trade_pct']}%\n\n"
        f"Pour changer la mise : /stake 1.0\n"
        f"Pour changer le multiplicateur : /mult 10\n"
        f"Pour changer le risque % : /risk risk_per_trade_pct 1.0\n\n"
        f"⚠️ Reste petit tant que tu es en démo/tests."
    )
    bot.send_message(uid, txt, parse_mode="Markdown")

@bot.message_handler(commands=['stake'])
def changer_stake(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(uid, "Usage : /stake 1.0")
    try:
        valeur = float(parts[1])
        if valeur <= 0 or valeur > 1000:
            return bot.send_message(uid, "❌ Mise invalide.")
        CONTROL_STATE["stake_usd"] = valeur
        bot.send_message(uid, f"✅ Mise réelle = ${valeur}")
    except ValueError:
        bot.send_message(uid, "❌ Valeur invalide.")

@bot.message_handler(commands=['mult'])
def changer_multiplicateur(message):
    uid = message.chat.id
    if uid != ADMIN_ID: return
    parts = message.text.strip().split()
    if len(parts) < 2:
        return bot.send_message(uid, "Usage : /mult 10")
    try:
        valeur = int(parts[1])
        if valeur <= 0 or valeur > 1000:
            return bot.send_message(uid, "❌ Multiplicateur invalide.")
        CONTROL_STATE["multiplier"] = valeur
        bot.send_message(uid, f"✅ Multiplicateur = x{valeur}")
    except ValueError:
        bot.send_message(uid, "❌ Valeur invalide.")

@bot.message_handler(func=lambda m: m.text == "🎯 PAIRES ACTIVES")
def paires_actives_menu(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    lignes = ["🎯 *PAIRES ACTIVES (Volatility)*\n━━━━━━━━━━━━━━━━━━━━━━"]
    for p, actif in volatility_pairs_active.items():
        lignes.append(f"  {'✅' if actif else '❌'} {p}")
    lignes.append("\nGold/Argent : toujours actifs (soumis aux horaires marché).")
    lignes.append("\nPour changer : /Volatility V50 OFF")
    bot.send_message(uid, "\n".join(lignes), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🛑 STOP D'URGENCE")
def stop_urgence(message):
    uid = message.chat.id
    if uid != ADMIN_ID:
        return bot.send_message(uid, "❌ Réservé à l'admin.")
    if not CONTROL_STATE["stop_urgence_actif"]:
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("⚠️ CONFIRMER — Fermer tout et arrêter", callback_data="confirm_stop_urgence")
        )
        return bot.send_message(uid,
            "🛑 *STOP D'URGENCE*\n\nCeci va :\n"
            "• Fermer TOUS les contrats ouverts sur Deriv\n"
            "• Désactiver l'auto-trading immédiatement\n\n"
            "Confirme :", reply_markup=markup, parse_mode="Markdown")
    else:
        CONTROL_STATE["stop_urgence_actif"] = False
        bot.send_message(uid, "✅ Stop d'urgence levé. Tu peux réactiver l'auto-trading manuellement.",
                          reply_markup=obtenir_clavier(uid))

@bot.callback_query_handler(func=lambda c: c.data == "confirm_stop_urgence")
def confirmer_stop_urgence(call):
    uid = call.message.chat.id
    if uid != ADMIN_ID:
        return
    CONTROL_STATE["auto_trading_active"] = False
    CONTROL_STATE["stop_urgence_actif"] = True
    fermees, erreurs = 0, 0
    try:
        positions = deriv_positions_ouvertes()
        for p in positions:
            try:
                deriv_fermer_contrat(p["contract_id"])
                fermees += 1
            except Exception:
                erreurs += 1
    except Exception as e:
        bot.send_message(uid, f"⚠️ Impossible de lister les positions : {e}")
    try:
        bot.delete_message(uid, call.message.message_id)
    except Exception:
        pass
    bot.send_message(uid,
        f"🛑 *STOP D'URGENCE EXÉCUTÉ*\n"
        f"Positions fermées : {fermees}\n"
        f"Erreurs de fermeture : {erreurs}\n"
        f"Auto-trading : 🔴 DÉSACTIVÉ\n\n"
        f"Utilise à nouveau '🛑 STOP D'URGENCE' pour lever le blocage quand tu es prêt à reprendre.",
        reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

# ==========================================
# INTERFACE TELEGRAM PRINCIPALE
# ==========================================

@bot.message_handler(commands=['start'])
def bienvenue(message):
    uid = message.chat.id
    if not est_autorise(uid):
        return bot.send_message(uid, "🔒 Accès restreint. /vip VOTRE-CLÉ pour activer.")
    utilisateurs_actifs.add(uid)
    init_daily_stats(uid)
    kz  = "🟢 ACTIVE" if dans_killzone() else "🔴 INACTIVE"
    vol = "\n".join([f"  {'✅' if v else '❌'} {p}"
                     for p, v in volatility_pairs_active.items()])
    trade_info = ""
    if uid in trades_actifs:
        t = trades_actifs[uid]
        trade_info = f"\n🟠 *TRADE ACTIF:* {t['symbol']} {t['direction']} @ {t['entry_price']}"
    bot.send_message(uid,
        f"💼 *TERMINAL PRIME V55* — ANALYSTE IA + EXÉCUTION RÉELLE (Deriv)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Auto-trading : {'🟢 ON' if CONTROL_STATE['auto_trading_active'] else '🔴 OFF (démarre toujours désactivé)'}\n"
        f"Mode : {CONTROL_STATE['mode']}\n"
        f"🎯 Scan exclusif : 🥇 Gold · 🥈 Argent · 🔥 Volatility\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Envoie /menu pour ouvrir le panneau de contrôle.\n"
        f"⏰ Killzone : {kz}{trade_info}",
        reply_markup=obtenir_clavier(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires(message):
    kz  = "🟢 EN COURS" if dans_killzone() else "🔴 INACTIVE"
    vol = "\n".join([f"  {'✅' if v else '❌'} {p}"
                     for p, v in volatility_pairs_active.items()])
    bot.send_message(message.chat.id,
        f"🕒 *KILLZONES & CPR JOURNALIER*\n\n"
        f"🌏 Asie    : 00:00 – 07:00 GMT\n"
        f"🇬🇧 Londres : 08:00 – 11:00 GMT\n"
        f"🇺🇸 New York: 14:00 – 17:00 GMT\n\n"
        f"⏰ Statut : {kz}\n"
        f"🔥 Volatility :\n{vol}\n\n"
        f"/Volatility V50 OFF → désactiver V50\n"
        f"/Volatility ALL ON  → tout activer",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 RAPPORT DU JOUR")
def rapport_bouton(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    bot.send_message(uid, generer_rapport_texte(uid), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📜 HISTORIQUE")
def historique_bouton(message):
    historique_trades(message)

@bot.message_handler(func=lambda m: m.text in ["📊 CHOISIR UNE CIBLE", "📊 CHOISIR UNE CIBLE ELITE"])
def devises(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_actifs:
        return bot.send_message(uid,
            "🟠 *TRADE ACTIF EN COURS*\nAttendez la clôture avant d'ouvrir un autre.",
            parse_mode="Markdown")
    peut_trader, raison = utilisateur_peut_trader(uid)
    if not peut_trader:
        return bot.send_message(uid, raison, parse_mode="Markdown")
    markup = InlineKeyboardMarkup(row_width=3)
    btns_vol = [InlineKeyboardButton(NOMS_AFFICHAGE.get(p, p), callback_data=f"set_{p}")
                for p, actif in volatility_pairs_active.items() if actif]
    if btns_vol:
        markup.add(*btns_vol)
    markup.add(InlineKeyboardButton("🥇 GOLD",   callback_data="set_XAUUSD"),
               InlineKeyboardButton("🥈 ARGENT", callback_data="set_XAGUSD"))
    bot.send_message(uid, "🎯 Sélectionne ta cible :", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    uid = message.chat.id
    if not est_autorise(uid): return
    if uid in trades_actifs:
        return bot.send_message(uid, "⚠️ Trade actif en cours.")
    actif = user_prefs.get(uid)
    if not actif:
        return bot.send_message(uid, "⚠️ Choisis d'abord une cible !")
    fake = type("C", (), {
        "data": f"set_{actif}", "message": message,
        "from_user": message.from_user, "id": 0
    })()
    save_devise(fake)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    uid = call.message.chat.id
    if not est_autorise(uid): return
    if uid in trades_actifs:
        try: bot.answer_callback_query(call.id, "🟠 Trade actif! Attendez la clôture.", show_alert=True)
        except: pass
        return
    peut_trader, raison = utilisateur_peut_trader(uid)
    if not peut_trader:
        try: bot.answer_callback_query(call.id, raison, show_alert=True)
        except: pass
        return

    cle_brute = call.data.replace("set_", "")
    try: bot.delete_message(uid, call.message.message_id)
    except: pass

    if cle_brute in signaux_cache:
        cle = cle_brute
        actif = cle_brute.split("_")[0]
    else:
        actif = cle_brute
        candidats = [k for k in signaux_cache if k.startswith(f"{actif}_")]
        if not candidats:
            return bot.send_message(uid,
                f"⏱️ Aucun signal actif sur {NOMS_AFFICHAGE.get(actif, actif)}\n"
                f"Attends le prochain scan automatique.", parse_mode="Markdown")
        cle = max(candidats, key=lambda k: signaux_cache[k]["time"])

    user_prefs[uid] = actif
    cache = signaux_cache.get(cle)
    if not cache or (time.time() - cache["time"]) > RISK_CONFIG["signal_validity_seconds"]:
        return bot.send_message(uid,
            f"⏱️ Signal expiré sur {NOMS_AFFICHAGE.get(actif, actif)}\n"
            f"Attends le prochain scan automatique.", parse_mode="Markdown")

    px  = obtenir_prix_broker_realtime(actif) or 0
    nom = NOMS_AFFICHAGE.get(actif, actif)
    fmt = ".0f" if actif in VOLATILE_PAIRS else ".5f"
    if px <= 0:
        return bot.send_message(uid,
            f"⚠️ Impossible de récupérer le prix actuel de {nom}. Réessaie dans un instant.",
            parse_mode="Markdown")

    entry_direction = "BUY" if "BUY" in cache["action"] else "SELL"
    sl_cache, tp1_cache, tp_final_cache = cache["mt5_sl"], cache["mt5_tp1"], cache["mt5_tp"]

    if entry_direction == "BUY":
        deja_sl  = px <= sl_cache
        deja_tp1 = px >= tp1_cache
    else:
        deja_sl  = px >= sl_cache
        deja_tp1 = px <= tp1_cache

    if deja_sl:
        return bot.send_message(uid,
            f"❌ *Signal annulé* — {nom}\n"
            f"Le marché a déjà atteint le niveau de Stop Loss prévu "
            f"({sl_cache:{fmt}}) pendant le délai d'exécution.\n"
            f"Aucun trade ouvert. Attends le prochain signal.",
            parse_mode="Markdown")

    if deja_tp1:
        return bot.send_message(uid,
            f"❌ *Signal annulé* — {nom}\n"
            f"Le marché a déjà atteint l'objectif TP1 prévu ({tp1_cache:{fmt}}) "
            f"avant que tu n'ouvres la position — entrer maintenant capturerait "
            f"un R/R trop dégradé.\n"
            f"Aucun trade ouvert. Attends le prochain signal.",
            parse_mode="Markdown")

    risque_restant  = abs(px - sl_cache)
    recomp_restante = abs(tp_final_cache - px)
    rr_restant = (recomp_restante / risque_restant) if risque_restant > 0 else 0
    rr_original = cache["mt5_rr"]
    if rr_original > 0:
        degradation_pct = max(0, (1 - (rr_restant / rr_original)) * 100)
    else:
        degradation_pct = 0

    if degradation_pct > RISK_CONFIG["max_rr_degradation_pct"]:
        return bot.send_message(uid,
            f"❌ *Signal annulé* — {nom}\n"
            f"Le R/R restant s'est trop dégradé depuis la détection du signal "
            f"({rr_original:.2f}R → {rr_restant:.2f}R, -{degradation_pct:.0f}%).\n"
            f"Aucun trade ouvert pour protéger la qualité de l'entrée.",
            parse_mode="Markdown")

    # Exécution manuelle : réelle uniquement si auto-trading est ON (cohérence globale)
    executer_reel_manuel = CONTROL_STATE["auto_trading_active"] and not CONTROL_STATE["stop_urgence_actif"]

    trade_id, sizing = ouvrir_trade(uid, actif, entry_direction, px,
                                    sl_cache, tp1_cache, tp_final_cache,
                                    cache["strategie"], cache["confiance"],
                                    label=cache.get("label","SIGNAL"),
                                    strategie_nom_ia=cache.get("strategie_nom_ia","?"),
                                    ia_score=cache.get("ia_score"),
                                    gemini_score=cache.get("gemini_score"),
                                    contexte_marche=cache.get("extra", {}).get("contexte_marche"),
                                    executer_reel=executer_reel_manuel)

    signal = (
        f"💼 *{cache.get('label','SIGNAL')}* — {nom}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 BUY MARKET' if 'BUY' in cache['action'] else '🔴 SELL MARKET'}"
        f"{' (RÉEL — Deriv)' if executer_reel_manuel else ' (simulé)'}\n"
        f"📊 Contexte : {cache.get('contexte','')}\n"
        f"🤖 Score IA validé : {cache.get('ia_score','?')}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entrée  : {px:{fmt}}\n"
        f"🛑 SL      : {sl_cache:{fmt}}\n"
        f"🎯 TP1 (85%): {tp1_cache:{fmt}}\n"
        f"🏁 TP Final (15%): {tp_final_cache:{fmt}}\n"
        f"⚖️ R/R actuel : {rr_restant:.2f}R (prévu {rr_original:.2f}R)\n"
        f"🎖️ Confiance : {cache.get('confiance',0)}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 *Risque réel calculé* : ${sizing['montant_risque']}\n"
        f"   ({RISK_CONFIG['risk_per_trade_pct']}% du capital ${CAPITAL_ACTUEL})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *TRADE OUVERT*\n"
        f"🆔 {trade_id}\n"
        f"📬 Au TP1: 85% fermé + SL→Breakeven automatique\n"
        f"🏃 Au TP Final: 15% restant sécurisé par trailing stop"
    )
    bot.send_message(uid, signal, parse_mode="Markdown")

# ==========================================
# LANCEMENT
# ==========================================

if __name__ == "__main__":
    keep_alive()
    # ✅ Connexion Deriv tentée au démarrage (non bloquante si elle échoue —
    # l'admin pourra réessayer via le bouton "AUTO-TRADING" dans /menu).
    try:
        deriv_connecter()
        print("✅ Deriv connecté au démarrage", flush=True)
    except Exception as e:
        print(f"⚠️ Deriv non connecté au démarrage ({e}) — connexion réessayée à l'activation.", flush=True)

    Thread(target=scanner_marche_auto,            daemon=True).start()
    Thread(target=monitorer_trades_actifs,         daemon=True).start()
    Thread(target=envoyer_rapports_quotidiens_auto,daemon=True).start()
    Thread(target=watchdog_trades_bloques,         daemon=True).start()
    print("💼 TERMINAL PRIME V55 — Deriv Edition ACTIF", flush=True)
    bot.infinity_polling()
