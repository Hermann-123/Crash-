from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import secrets
import string
import unicodedata
import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from zoneinfo import ZoneInfo
from collections import defaultdict

import httpx
import numpy as np
from scipy.stats import poisson
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

# --- IMPORTS ---
try:
    from gemini_predictor import predict_match_probas
except Exception as e:
    predict_match_probas = None
    print(f"❌ gemini_predictor: {e}")

try:
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception as e:
    enrich_match_with_bzzoiro = None
    print(f"❌ bzzoiro_data: {e}")

try:
    from bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
except Exception as e:
    fetch_bzzoiro_predictions_direct = None
    print(f"❌ bzzoiro_predictions_direct: {e}")

try:
    from odds_fallback import fetch_all_odds as fetch_infer_odds
    print("✅ odds_fallback chargé")
except Exception as e:
    fetch_infer_odds = None
    print(f"❌ odds_fallback: {e}")

# NOUVEAU : Import du moteur ML
try:
    from ml_engine import train_model_if_needed, predict_ml, fuse_probas
    print("✅ ml_engine chargé")
except Exception as e:
    train_model_if_needed = None
    predict_ml = None
    fuse_probas = None
    print(f"❌ ml_engine: {e}")

try:
    from rapidoddsapi_data import fetch_all_odds as fetch_rapidodds_odds_data
    print("✅ rapidoddsapi_data chargé")
except Exception as e:
    fetch_rapidodds_odds_data = None
    print(f"❌ rapidoddsapi_data: {e}")

try:
    from dixon_coles import download_and_train
except Exception as e:
    download_and_train = None
    print(f"❌ dixon_coles: {e}")

# --- VARIABLES & ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
INFERSPORTS_API_KEY = os.getenv("INFERSPORTS_API_KEY", "")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "99f9731b68429ed4aaf0383cd7ca8cd4")

TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "5968288964")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "-1003982738017")

RAPIDODDSAPI_API_KEY = os.getenv("RAPIDODDSAPI_API_KEY", "")

try: ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except ValueError: ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")

BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • VIP")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "50"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

# Nouvelle pondération (L'Algo Dixon+ML est plus fiable)
WEIGHT_ALGO    = float(os.getenv("WEIGHT_ALGO",   "0.40"))
WEIGHT_GEMINI  = float(os.getenv("WEIGHT_GEMINI", "0.30"))
WEIGHT_BZZOIRO = float(os.getenv("WEIGHT_BZZOIRO","0.30"))

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))
MIN_COUPON_EV = float(os.getenv("MIN_COUPON_EV", "0.95"))

AUTH_PROBA_MIN = float(os.getenv("AUTH_PROBA_MIN", "70.0"))
AUTH_PROBA_MAX = float(os.getenv("AUTH_PROBA_MAX", "85.0"))
AUTH_MAX_LEGS  = int(os.getenv("AUTH_MAX_LEGS", "5"))

DOUBLE_SCAN_POOL_SIZE       = int(os.getenv("DOUBLE_SCAN_POOL_SIZE", "25"))
DOUBLE_SCAN_STABILITY_DELTA = float(os.getenv("DOUBLE_SCAN_STABILITY_DELTA", "0.15"))
DOUBLE_SCAN_MIN_STABLE      = int(os.getenv("DOUBLE_SCAN_MIN_STABLE", "5"))
DOUBLE_SCAN_PAUSE_SECONDS   = int(os.getenv("DOUBLE_SCAN_PAUSE_SECONDS", "5"))

MTFIN_MIN_SCORE = 70
MTFIN_MIN_PROB = 0.35
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45

DRAW_MIN_SCORE = 70
DRAW_MAX_ECART = 0.20
DRAW_MIN_PROB = 0.28

ODDS3_AUTH_MIN_COTE = 2.5
ODDS3_AUTH_MAX_COTE = 3.0
ODDS3_HYB_MIN_COTE = 3.0
ODDS3_HYB_MAX_COTE = 5.5
ODDS3_MIN_COTE_LEG = 1.30

MATCH_START_BUFFER_MINUTES = 10
RESERVE_KEYWORDS = ["jong", " fc b", " ii", " u21", " u19", " castilla", " reserves", " b team"]

TELEGRAM_MAX_LEN = 3800
TZ = ZoneInfo(NOTIFY_TZ)

if not BOT_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise RuntimeError("Variables d'environnement manquantes")

# =====================================================================
# ⚙️ MOTEUR WALLSTREET
# =====================================================================
@dataclass
class WS_MatchData:
    match_id: str
    league: str
    home_team: str
    away_team: str
    home_odds: Optional[float]
    draw_odds: Optional[float]
    away_odds: Optional[float]
    over_2_5: Optional[float] = None
    under_2_5: Optional[float] = None
    btts_yes: Optional[float] = None
    btts_no: Optional[float] = None
    commence_time: str = ""

@dataclass
class WS_SimulationResult:
    match_id: str
    proba_home: float
    proba_draw: float
    proba_away: float
    most_likely_score: str
    proba_btts: float
    proba_over_1_5: float
    proba_over_2_5: float
    proba_over_3_5: float

class DixonColesEngine:
    def __init__(self, rho: float = -0.15, home_advantage: float = 1.15):
        self.rho = rho
        self.home_advantage = home_advantage
        self.max_goals = 6

    def simulate(self, match: WS_MatchData) -> Optional[WS_SimulationResult]:
        if not match.home_odds or not match.away_odds: return None
        lambda_x = (1.0 / match.home_odds) * 1.8 * self.home_advantage
        mu_y = (1.0 / match.away_odds) * 1.8
        matrix = np.zeros((self.max_goals, self.max_goals))

        for i in range(self.max_goals):
            for j in range(self.max_goals):
                matrix[i, j] = poisson.pmf(i, lambda_x) * poisson.pmf(j, mu_y)

        matrix /= np.sum(matrix)
        p_home = float(np.sum(np.tril(matrix, -1))) * 100
        p_draw = float(np.sum(np.diag(matrix))) * 100
        p_away = float(np.sum(np.triu(matrix, 1))) * 100

        best_idx = np.argmax(matrix)
        score_x, score_y = np.unravel_index(best_idx, matrix.shape)

        p_btts = float(np.sum(matrix[1:, 1:])) * 100
        p_o15 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 1])) * 100
        p_o25 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 2])) * 100
        p_o35 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 3])) * 100

        return WS_SimulationResult(
            match_id=match.match_id, proba_home=p_home, proba_draw=p_draw, proba_away=p_away,
            most_likely_score=f"{score_x}-{score_y}", proba_btts=p_btts,
            proba_over_1_5=p_o15, proba_over_2_5=p_o25, proba_over_3_5=p_o35
        )

dixon_ws = DixonColesEngine()

# =====================================================================
# 🎫 ARCHITECTURE
# =====================================================================
@dataclass
class MatchData:
    event_id: str
    league: str
    home: str
    away: str
    kickoff: str
    real_odds: dict
    bzz_markets: dict
    bzz_confidence: float
    gemini_markets: dict = None
    dixon_pred: dict = None
    analysis: str = ""
    has_h2h: bool = False
    stability_flag: str = "unknown"

@dataclass
class Selection:
    event_id: str
    league: str
    home: str
    away: str
    kickoff: str
    market: str
    pick_label: str
    odds: float
    model_prob: float
    real_odd: Optional[float] = None
    value: float = -1.0
    quality: int = 0
    analysis: str = ""
    htft_pick: str = ""

@dataclass
class Coupon:
    name: str
    subtitle: str
    emoji: str
    legs: list
    combined_odds: float
    combined_prob: float
    is_list: bool = False
    show_analysis: bool = False

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_ODDS3 = "🎯 Odds +3"
BTN_MTFT = "⏱ MT-Fin"
BTN_DRAW = "🤝 Match Nul"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"
BTN_ACTIVATE = "🔑 Activer mon abonnement"

USER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3)],
        [KeyboardButton(text=BTN_ACTIVATE)],
    ], resize_keyboard=True, input_field_placeholder="🎯 Ton coupon du jour")

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_MTFT)],
        [KeyboardButton(text=BTN_DRAW)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Debug")],
    ], resize_keyboard=True, input_field_placeholder="Admin…")

STATE = {
    "ready": False, "coupons": [], "last_scan": None, "debug": {},
    "tracker": {"coupons": []}, "keys": {}, "users": {},
    "scan_lock": asyncio.Lock(), "dixon_model": None, "last_scan_date": "",
    "double_scan_status": "", "double_scan_stats": {},
    "rejections": {"value": 0, "ev_coupon": 0, "auth_proba": 0},
}

TARGETS = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try: TARGETS.append(int(raw))
        except ValueError: TARGETS.append(raw)

# =====================================================================
# 🔐 SYSTÈME D'ABONNEMENT
# =====================================================================
def is_subscribed(user_id):
    if user_id == ADMIN_ID: return True
    uid = str(user_id)
    if uid not in STATE["users"]: return False
    exp = STATE["users"][uid].get("expires_at")
    if not exp: return False
    try: return datetime.now(TZ) < datetime.fromisoformat(exp)
    except: return False

def get_subscription_message(user_id):
    if user_id == ADMIN_ID: return "✅ Admin"
    if is_subscribed(user_id):
        exp = STATE["users"][str(user_id)]["expires_at"]
        return f"✅ Actif jusqu'au {datetime.fromisoformat(exp).strftime('%d/%m/%Y à %H:%M')}."
    return "❌ Abonnement expiré ou inexistant.\nContactez @hermann1123 pour activer votre clé."

def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except: pass

def load_tracker(): return load_json(LOCAL_DB, {"coupons": []})
def save_tracker(data): save_json(LOCAL_DB, data)

# =====================================================================
# 🛠️ UTILITAIRES
# =====================================================================
def today_pretty():
    d = datetime.now(TZ)
    months = {1:"janvier",2:"février",3:"mars",4:"avril",5:"mai",6:"juin",7:"juillet",8:"août",9:"septembre",10:"octobre",11:"novembre",12:"décembre"}
    return f"{d.day} {months[d.month]} {d.year}"

def normalize_name(s: str) -> str:
    if not s: return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def is_reserve_team(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in RESERVE_KEYWORDS)

def parse_dt_safe(s) -> Optional[datetime]:
    if not s: return None
    try:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except: return None

def format_kickoff(kickoff_str) -> str:
    if not kickoff_str: return "Heure non confirmée"
    dt = parse_dt_safe(kickoff_str)
    if dt:
        if dt.hour == 0 and dt.minute == 0: return dt.strftime("%d/%m") + " (heure non confirmée)"
        return dt.strftime("%d/%m %H:%M")
    return "Heure non confirmée"

def is_match_not_started(kickoff_str) -> bool:
    if not kickoff_str: return True
    dt = parse_dt_safe(kickoff_str)
    if not dt: return True
    if dt.hour == 0 and dt.minute == 0: return True
    now = datetime.now(TZ)
    buffer = timedelta(minutes=MATCH_START_BUFFER_MINUTES)
    return dt > (now + buffer)

def prob_bar(p: float) -> str:
    if p >= 0.80: return "🟢🟢🟢"
    if p >= 0.72: return "🟢🟢⚪"
    if p >= 0.65: return "🟢⚪⚪"
    return "⚪⚪⚪"

def quality_bar(score: int) -> str:
    if score >= 90: return "⭐⭐⭐⭐⭐"
    if score >= 85: return "⭐⭐⭐⭐"
    if score >= 80: return "⭐⭐⭐"
    if score >= 75: return "⭐⭐"
    return "⭐"

def split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= max_len: return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current: parts.append(current.rstrip())
            current = line + "\n"
        else: current += line + "\n"
    if current.strip(): parts.append(current.rstrip())
    return parts

def record_coupons(coupons, date_str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing: continue
        legs = [{"event_id": s.event_id, "home": s.home, "away": s.away, "market": s.market, "pick_label": s.pick_label, "odds": s.odds, "kickoff": s.kickoff, "result": "pending"} for s in c.legs]
        tracker["coupons"].append({"id": cid, "date": date_str, "name": c.name, "combined_odds": c.combined_odds, "legs": legs, "status": "pending"})
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)

def prescore_match(md) -> int:
    score = 0
    if md.real_odds: score += 25
    if md.bzz_markets: score += 15
    if md.dixon_pred: score += 15
    if md.has_h2h: score += 10
    return score

# ═══════════════════════════════════════════════════════════════════
# 🎯 MOTEUR HYBRIDE (Dixon + ML)
# ═══════════════════════════════════════════════════════════════════
def compute_probability(md, market_key):
    # 1. On récupère les infos de Dixon
    dixon_pred = md.dixon_pred if md.dixon_pred else {}
    
    # 2. NOUVEAU : On demande au ML d'affiner les probas Dixon
    ml_probas = None
    if predict_ml:
        ml_probas = predict_ml(dixon_pred, md.bzz_confidence)
        
    # 3. On fusionne Dixon et ML (40/60)
    fused = fuse_probas(dixon_pred, ml_probas) if fuse_probas and ml_probas else dixon_pred

    # 4. On extrait la proba finale
    gem = dix = bzz = 0.0
    gem_avail = dix_avail = bzz_avail = False

    # Gemini
    if md.gemini_markets and md.gemini_markets.get(market_key) is not None:
        try:
            gem = float(md.gemini_markets[market_key]) / 100.0
            gem_avail = True
        except: pass

    # Algo (Dixon + ML fusionnés)
    dk = {"victoire_home":"p_home","match_nul":"p_draw","victoire_away":"p_away",
          "over_2.5":"over_2.5","under_2.5":"under_2.5","over_3.5":"over_3.5",
          "over_1.5":"over_1.5","under_1.5":"under_1.5",
          "btts_yes":"btts_yes","btts_no":"btts_no"}.get(market_key)
    
    if dk and fused and fused.get(dk) is not None:
        try:
            dix = float(fused.get(dk))
            dix_avail = True
        except: pass

    # Bzzoiro
    if md.bzz_markets and md.bzz_markets.get(market_key) is not None:
        try:
            bzz = float(md.bzz_markets[market_key]) / 100.0
            bzz_avail = True
        except: pass

    # 5. Pondération finale (40% Algo / 30% Gemini / 30% Bzzoiro)
    total_w = 0.0
    weighted_sum = 0.0
    
    if dix_avail:
        total_w += WEIGHT_ALGO
        weighted_sum += dix * WEIGHT_ALGO
    if gem_avail:
        total_w += WEIGHT_GEMINI
        weighted_sum += gem * WEIGHT_GEMINI
    if bzz_avail:
        total_w += WEIGHT_BZZOIRO
        weighted_sum += bzz * WEIGHT_BZZOIRO

    if total_w <= 0:
        return 0.0, gem, dix, bzz, ""

    final = weighted_sum / total_w

    sources = []
    if gem_avail: sources.append(gem)
    if dix_avail: sources.append(dix)
    if bzz_avail: sources.append(bzz)

    emoji = ""
    if len(sources) >= 2:
        delta = max(sources) - min(sources)
        if len(sources) == 3 and delta <= 0.12: emoji = "🤝🤝"
        elif delta <= 0.15: emoji = "🤝"
        elif delta >= 0.20: emoji = "⚠️"

    return final, gem, dix, bzz, emoji

def passes_value_filter(proba_0_1: float, odd: float, is_real_odd: bool) -> bool:
    if proba_0_1 <= 0 or odd <= 0: return False
    if not is_real_odd: return True
    ev = proba_0_1 * odd
    return ev >= (1.0 + MIN_EDGE)

def coupon_passes_ev(combined_odds: float, combined_prob: float) -> bool:
    if combined_odds <= 0 or combined_prob <= 0: return False
    return (combined_odds * combined_prob) >= MIN_COUPON_EV

def passes_auth_proba_filter(proba_0_100: float) -> bool:
    if proba_0_100 <= 0: return False
    return AUTH_PROBA_MIN <= proba_0_100 <= AUTH_PROBA_MAX

# =====================================================================
# 🔑 RAPIDODDSAPI (inchangé)
# =====================================================================
async def fetch_rapidodds_odds() -> Tuple[list, str]:
    if not RAPIDODDSAPI_API_KEY or not fetch_rapidodds_odds_data:
        return [], "fallback"
    try:
        events = await fetch_rapidodds_odds_data()
        if not events: return [], "fallback"
        ws_matches = [WS_MatchData(
            match_id=e["match_id"], league=e.get("league", ""),
            home_team=e["home_team"], away_team=e["away_team"],
            home_odds=e.get("home_odds"), draw_odds=e.get("draw_odds"),
            away_odds=e.get("away_odds"), over_2_5=e.get("over_2_5"),
            under_2_5=e.get("under_2_5"), btts_yes=e.get("btts_yes"),
            btts_no=e.get("btts_no"), commence_time=e.get("commence_time", ""),
        ) for e in events]
        return ws_matches, "live"
    except Exception as e:
        print(f"⚠️ RapidOddsAPI crash: {e}")
        return [], "fallback"

async def fetch_api_football_odds(today_iso: str) -> list:
    if not API_FOOTBALL_KEY: return []
    url = f"https://v3.football.api-sports.io/odds"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    results = []
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers, params={"date": today_iso}, timeout=15.0)
            if r.status_code == 200:
                data = r.json().get("response", [])
                for item in data:
                    fixture_id = item.get("fixture", {}).get("id")
                    home = item.get("teams", {}).get("home", {}).get("name")
                    away = item.get("teams", {}).get("away", {}).get("name")
                    if not fixture_id or not home or not away: continue
                    match_data = WS_MatchData(
                        match_id=f"af_{fixture_id}", league=item.get("league", {}).get("name", ""),
                        home_team=home, away_team=away, commence_time=item.get("fixture", {}).get("date", ""),
                        home_odds=None, draw_odds=None, away_odds=None
                    )
                    for bk in item.get("bookmakers", []):
                        for bet in bk.get("bets", []):
                            bname = (bet.get("name") or "").lower()
                            if "match winner" in bname:
                                for v in bet.get("values", []):
                                    val = (v.get("value") or "").lower()
                                    try: odd = float(v.get("odd"))
                                    except: continue
                                    if val == "home" and not match_data.home_odds: match_data.home_odds = odd
                                    elif val == "draw" and not match_data.draw_odds: match_data.draw_odds = odd
                                    elif val == "away" and not match_data.away_odds: match_data.away_odds = odd
                        if match_data.home_odds and match_data.draw_odds and match_data.away_odds: break
                    if match_data.home_odds: results.append(match_data)
        except: pass
    return results

def inject_all_real_odds(pool: list, ws_matches: list, af_matches: list) -> int:
    matched = 0
    for md in pool:
        hn, an = normalize_name(md.home), normalize_name(md.away)
        for ws in ws_matches:
            whn, wan = normalize_name(ws.home_team), normalize_name(ws.away_team)
            if (hn in whn or whn in hn) and (an in wan or wan in an):
                if ws.home_odds: md.real_odds["home"] = ws.home_odds
                if ws.draw_odds: md.real_odds["draw"] = ws.draw_odds
                if ws.away_odds: md.real_odds["away"] = ws.away_odds
                if ws.over_2_5: md.real_odds["over_2.5"] = ws.over_2_5
                if ws.under_2_5: md.real_odds["under_2.5"] = ws.under_2_5
                if ws.btts_yes: md.real_odds["btts_yes"] = ws.btts_yes
                if ws.btts_no: md.real_odds["btts_no"] = ws.btts_no
                if ws.commence_time: md.kickoff = ws.commence_time
                matched += 1
                break
        for af in af_matches:
            afhn, afan = normalize_name(af.home_team), normalize_name(af.away_team)
            if (hn in afhn or afhn in hn) and (an in afan or afan in an):
                if af.home_odds and not md.real_odds.get("home"): md.real_odds["home"] = af.home_odds
                if af.draw_odds and not md.real_odds.get("draw"): md.real_odds["draw"] = af.draw_odds
                if af.away_odds and not md.real_odds.get("away"): md.real_odds["away"] = af.away_odds
                break
    return matched

# =====================================================================
# 🔁 DOUBLE SCAN (inchangé)
# =====================================================================
async def _enrich_single_pass(targets: list, pass_num: int) -> dict:
    results = {}
    async with httpx.AsyncClient(timeout=70.0) as client:
        for idx, md in enumerate(targets):
            try:
                ctx = None
                if enrich_match_with_bzzoiro:
                    dt = parse_dt_safe(md.kickoff)
                    date_iso = dt.strftime("%Y-%m-%d") if dt else datetime.now(TZ).strftime("%Y-%m-%d")
                    ctx = await enrich_match_with_bzzoiro(md.home, md.away, date_iso)
                    if ctx and ctx.get("available"): md.has_h2h = True
                if predict_match_probas:
                    pred = await predict_match_probas(client, md.home, md.away, md.league, md.kickoff, context=ctx)
                    if pred and "markets" in pred:
                        results[md.event_id] = {"markets": pred.get("markets", {}), "analysis": pred.get("analysis", "")}
                await asyncio.sleep(2.0)
            except: pass
    return results

def _compare_two_passes(targets: list, results_1: dict, results_2: dict) -> tuple:
    stable_list = []
    enriched_list = []
    stats = {"stable": 0, "unstable": 0, "single": 0, "none": 0}

    for md in targets:
        r1 = results_1.get(md.event_id)
        r2 = results_2.get(md.event_id)

        if not r1 and not r2:
            stats["none"] += 1
            md.stability_flag = "no_gemini"
            continue

        if r1 and not r2:
            md.gemini_markets = r1["markets"]; md.analysis = r1["analysis"]; md.stability_flag = "single"; stats["single"] += 1; enriched_list.append(md); continue
        if r2 and not r1:
            md.gemini_markets = r2["markets"]; md.analysis = r2["analysis"]; md.stability_flag = "single"; stats["single"] += 1; enriched_list.append(md); continue

        m1, m2 = r1["markets"], r2["markets"]
        is_stable = True
        merged = {}

        for key in set(m1.keys()) | set(m2.keys()):
            v1 = m1.get(key); v2 = m2.get(key)
            if v1 is None and v2 is not None: merged[key] = v2; is_stable = False; continue
            if v2 is None and v1 is not None: merged[key] = v1; is_stable = False; continue
            if v1 is None and v2 is None: continue
            try: f1, f2 = float(v1), float(v2)
            except: merged[key] = v1; continue
            delta = abs(f1 - f2) / 100.0
            if delta > DOUBLE_SCAN_STABILITY_DELTA: merged[key] = min(f1, f2); is_stable = False
            else: merged[key] = (f1 + f2) / 2.0

        md.gemini_markets = merged
        a1 = r1.get("analysis", "") or ""; a2 = r2.get("analysis", "") or ""
        base_analysis = a1 if len(a1) >= len(a2) else a2

        if is_stable: md.analysis = base_analysis; md.stability_flag = "stable"; stats["stable"] += 1; stable_list.append(md)
        else: md.analysis = f"⚠️ Prédiction variable. {base_analysis[:200]}"; md.stability_flag = "unstable"; stats["unstable"] += 1
        enriched_list.append(md)

    return stable_list, enriched_list, stats

async def enrich_with_double_pass(pool: list) -> list:
    if not pool: STATE["double_scan_status"] = "EMPTY"; return pool
    targets = pool[:DOUBLE_SCAN_POOL_SIZE]
    results_1 = await _enrich_single_pass(targets, pass_num=1)
    await asyncio.sleep(DOUBLE_SCAN_PAUSE_SECONDS)
    results_2 = await _enrich_single_pass(targets, pass_num=2)
    stable_list, enriched_list, stats = _compare_two_passes(targets, results_1, results_2)
    STATE["double_scan_stats"] = stats

    if len(stable_list) >= DOUBLE_SCAN_MIN_STABLE: STATE["double_scan_status"] = f"STRICT ({len(stable_list)} stables)"; return stable_list
    if enriched_list: STATE["double_scan_status"] = f"FALLBACK ({len(stable_list)} stables / {len(enriched_list)} total)"; return enriched_list
    
    for md in targets: md.stability_flag = "no_gemini"
    STATE["double_scan_status"] = "GEMINI_KO"
    return targets

# =====================================================================
# 🎯 MOTEURS DE TICKETS (inchangés)
# =====================================================================
def _poisson_sample(lam: float) -> int:
    if lam <= 0: return 0
    L = math.exp(-lam); k, p = 0, 1.0
    while p > L and k < 20: k += 1; p *= random.random()
    return k - 1

def _binomial_sample(n: int, p: float) -> int:
    return sum(1 for _ in range(n) if random.random() < p)

def simulate_htft(lambda_home: float, lambda_away: float, n_sims: int = MTFIN_SIMULATIONS) -> dict:
    counts = {}
    for _ in range(n_sims):
        hg, ag = _poisson_sample(lambda_home), _poisson_sample(lambda_away)
        ht_hg, ht_ag = _binomial_sample(hg, MTFIN_FIRST_HALF_RATIO), _binomial_sample(ag, MTFIN_FIRST_HALF_RATIO)
        ht_res = "V1" if ht_hg > ht_ag else ("X" if ht_hg == ht_ag else "V2")
        ft_res = "V1" if hg > ag else ("X" if hg == ag else "V2")
        key = f"{ht_res}/{ft_res}"; counts[key] = counts.get(key, 0) + 1
    return {k: round(v / n_sims, 4) for k, v in counts.items()}

def build_mtft_coupon_and_bonus(pool):
    picks, bonus_pool = [], []
    for md in pool:
        if not md.home or not md.away or not md.dixon_pred: continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        lam_h, lam_a = md.dixon_pred.get("lambda_home", 1.3), md.dixon_pred.get("lambda_away", 1.1)
        htft_probs = simulate_htft(lam_h, lam_a)
        if not htft_probs: continue
        best_pick, best_prob = None, 0.0
        for combo in ("V1/V1", "V2/V2"):
            p = htft_probs.get(combo, 0)
            if p > best_prob: best_prob = p; best_pick = combo
        if not best_pick or best_prob < MTFIN_MIN_PROB: continue
        pts = 15 + (10 if md.has_h2h else 0) + 10 + (30 if best_prob >= 0.65 else 20) + 15 + 10
        if pts < MTFIN_MIN_SCORE: continue
        est_odd = round(1.0 / best_prob, 2) if best_prob > 0 else 3.5
        picks.append({"md": md, "pick": best_pick, "prob": best_prob, "score": pts, "odd": est_odd, "htft_probs": htft_probs})

    coupon = None
    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    top3 = picks[:3]
    if len(top3) > 0:
        legs = [Selection(p["md"].event_id, p["md"].league, p["md"].home, p["md"].away, p["md"].kickoff, "MT-Fin", f"MT/Fin : {p['pick']}", p["odd"], p["prob"], None, -1.0, p["score"], htft_pick=p["pick"]) for p in top3]
        co, cp = 1.0, 1.0
        for s in legs: co *= s.odds; cp *= s.model_prob
        subtitle = f"{len(legs)} matchs Bookmaker Blind" if len(legs) < 3 else "3 matchs Bookmaker Blind"
        coupon = Coupon("MT-Fin", subtitle, "⏱", legs, round(co, 2), round(cp, 4), is_list=True)

    for p in top3:
        md = p["md"]
        fav_market = f"Victoire {md.home} (1)" if p["pick"] == "V1/V1" else f"Victoire {md.away} (2)"
        real_odd = md.real_odds.get("home") if p["pick"] == "V1/V1" else md.real_odds.get("away")
        ft_res = p["pick"].split("/")[1]
        fav_ft_prob = sum(p["htft_probs"].get(f"{ht}/{ft_res}", 0) for ht in ("V1", "V2", "X"))
        is_est = False
        if not real_odd: real_odd = max(1.30, round(1.0 / fav_ft_prob, 2)) if fav_ft_prob > 0 else 2.0; is_est = True
        if real_odd < ODDS3_MIN_COTE_LEG: continue
        if not passes_value_filter(fav_ft_prob, real_odd, is_real_odd=not is_est): continue
        bonus_pool.append({"match": md, "type": fav_market, "odds": real_odd, "proba": fav_ft_prob * 100, "ai": md.analysis, "real_odd": real_odd if not is_est else None, "source": "MT-FIN", "kickoff": md.kickoff})
    return coupon, bonus_pool

def build_draw_coupon_and_bonus(pool):
    candidates = []
    for md in pool:
        if not md.home or not md.away: continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        prob_draw, g, d, b, emoji = compute_probability(md, "match_nul")
        if prob_draw < DRAW_MIN_PROB or (g > 0 and g < 0.22) or emoji == "⚠️": continue
        home_odd, away_odd, draw_odd = md.real_odds.get("home"), md.real_odds.get("away"), md.real_odds.get("draw")
        is_draw_real = draw_odd is not None
        if not draw_odd: draw_odd = round(1.0 / prob_draw, 2) if prob_draw > 0 else 3.20
        if draw_odd < 3.10: continue
        if (home_odd and home_odd < 1.80) or (away_odd and away_odd < 1.80): continue
        if not passes_value_filter(prob_draw, draw_odd, is_real_odd=is_draw_real): continue
        pts = 30 + (25 if prob_draw >= 0.33 else 18) + (20 if g >= 0.30 else 12) + 10 + (5 if md.has_h2h else 0) + 10
        if pts < DRAW_MIN_SCORE: continue
        candidates.append(Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "Match Nul", "Match Nul (X)", draw_odd, prob_draw, draw_odd if is_draw_real else None, (prob_draw*draw_odd)-1, pts))

    coupon = None
    candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    top3 = candidates[:3]
    if len(top3) > 0:
        co, cp = 1.0, 1.0
        for s in top3: co *= s.odds; cp *= s.model_prob
        subtitle = f"{len(top3)} nuls combinés" if len(top3) < 3 else "3 nuls combinés"
        coupon = Coupon("Match Nul", subtitle, "🤝", top3, round(co, 2), round(cp, 4), is_list=True)

    candidates.sort(key=lambda x: x.model_prob, reverse=False)
    bottom_candidates = candidates[:3]
    bonus_pool = []
    for c in bottom_candidates:
        md = next((m for m in pool if m.event_id == c.event_id), None)
        if not md: continue
        prob_home, _, _, _, _ = compute_probability(md, "victoire_home")
        prob_away, _, _, _, _ = compute_probability(md, "victoire_away")
        prob_dc_12 = prob_home + prob_away
        dc12_odd = round(1.0 / prob_dc_12, 2) if prob_dc_12 > 0 else 1.30
        if dc12_odd < ODDS3_MIN_COTE_LEG: continue
        if not passes_value_filter(prob_dc_12, dc12_odd, is_real_odd=False): continue
        bonus_pool.append({"match": md, "type": "Double Chance 12 (Pas de Nul)", "odds": dc12_odd, "proba": prob_dc_12 * 100, "ai": md.analysis, "real_odd": None, "source": "MATCH-NUL", "kickoff": md.kickoff})
    return coupon, bonus_pool

def build_third_pillar(pool) -> list:
    pool_result = []
    for md in pool:
        if not md.home or not md.away or not is_match_not_started(md.kickoff): continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        best_candidate = None
        for market_key, label in {"btts_yes": "BTTS Oui", "btts_no": "BTTS Non", "over_1.5": "Over 1.5", "under_2.5": "Under 2.5", "over_2.5": "Over 2.5"}.items():
            prob, g, d, b, emoji = compute_probability(md, market_key)
            if prob < 0.55 or emoji == "⚠️": continue
            real_odd = md.real_odds.get(market_key); is_est = False
            if not real_odd: real_odd = max(1.30, round(1.0 / prob, 2)); is_est = True
            if real_odd < ODDS3_MIN_COTE_LEG: continue
            if not passes_value_filter(prob, real_odd, is_real_odd=not is_est): continue
            if not best_candidate or prob > best_candidate['proba']:
                best_candidate = {"match": md, "type": label, "odds": real_odd, "proba": prob * 100, "ai": md.analysis, "real_odd": real_odd if not is_est else None, "source": "BZZOIRO", "kickoff": md.kickoff}
        if best_candidate: pool_result.append(best_candidate)
    pool_result.sort(key=lambda x: (x['odds'] * x['proba'] / 100.0, x['proba']), reverse=True)
    return pool_result[:3]

def _build_coupon_from_combo(combo, subtitle_prefix: str, quality: int, source_label: str):
    total_odds, total_prob = 1.0, 1.0
    for c in combo: total_odds *= c['odds']; total_prob *= (c['proba'] / 100.0)
    if not coupon_passes_ev(total_odds, total_prob): STATE["rejections"]["ev_coupon"] += 1; return None
    legs = [Selection(c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id, c['match'].league, c['match'].home if hasattr(c['match'], 'home') else c['match'].home_team, c['match'].away if hasattr(c['match'], 'away') else c['match'].away_team, c.get('kickoff', ''), c['type'], c['type'], c['odds'], c['proba']/100.0, c['real_odd'], quality=quality, analysis=c['ai']) for c in combo]
    return Coupon("Odds +3 AUTH", f"{subtitle_prefix} • {len(combo)} jambes", "🛡️", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)

def build_odds3_auth_coupon(bonus_mtft, bonus_draw, third_pillar, ws_matches) -> Optional[Coupon]:
    if ws_matches:
        pool_ws = []
        for match in ws_matches:
            if not is_match_not_started(match.commence_time) or is_reserve_team(match.home_team) or is_reserve_team(match.away_team): continue
            if (match.home_odds and match.home_odds < 1.30) or (match.away_odds and match.away_odds < 1.30): continue
            sim = dixon_ws.simulate(match)
            if not sim: continue
            for odd, proba, label, ai in [(match.home_odds, sim.proba_home, f"Victoire {match.home_team}", "Dixon."), (match.away_odds, sim.proba_away, f"Victoire {match.away_team}", "Dixon."), (match.over_2_5, sim.proba_over_2_5, "Plus de 2.5", "Dixon.")]:
                if odd and passes_auth_proba_filter(proba): pool_ws.append({"match": match, "type": label, "odds": odd, "proba": proba, "ai": ai, "real_odd": odd, "kickoff": match.commence_time})
        if len(pool_ws) >= 2:
            pool_ws.sort(key=lambda x: (x['odds'] * x['proba'] / 100.0, x['proba']), reverse=True)
            for num_legs in range(2, AUTH_MAX_LEGS + 1):
                for combo in itertools.combinations(pool_ws[:15], num_legs):
                    if len(set([c['match'].match_id for c in combo])) != num_legs: continue
                    total_odds = 1.0
                    for x in combo: total_odds *= x['odds']
                    if ODDS3_AUTH_MIN_COTE <= total_odds <= ODDS3_AUTH_MAX_COTE:
                        coupon = _build_coupon_from_combo(combo, "RapidOddsAPI", 95, "WS")
                        if coupon: return coupon

    combined = bonus_mtft + bonus_draw + third_pillar
    if not combined: return None
    filtered = [c for c in combined if passes_auth_proba_filter(c.get('proba', 0))]
    if not filtered: return None
    filtered.sort(key=lambda x: (x['odds'] * x['proba'] / 100.0, x['proba']), reverse=True)
    for num_legs in range(2, AUTH_MAX_LEGS + 1):
        for combo in itertools.combinations(filtered[:12], num_legs):
            if len(set([c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id for c in combo])) != num_legs: continue
            total_odds = 1.0
            for x in combo: total_odds *= x['odds']
            if ODDS3_AUTH_MIN_COTE <= total_odds <= ODDS3_AUTH_MAX_COTE:
                coupon = _build_coupon_from_combo(combo, "Fallback", 90, "FB")
                if coupon: return coupon
    return None

def build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar) -> Optional[Coupon]:
    combined = bonus_mtft + bonus_draw + third_pillar
    if not combined: return None
    if bonus_mtft and bonus_draw and third_pillar:
        for r in range(3, min(len(combined), 5) + 1):
            for combo in itertools.combinations(combined, r):
                has_mtft = any(c.get('source') == 'MT-FIN' for c in combo); has_draw = any(c.get('source') == 'MATCH-NUL' for c in combo); has_third = any(c.get('source') == 'BZZOIRO' for c in combo)
                if not (has_mtft and has_draw and has_third): continue
                total_odds = 1.0
                for x in combo: total_odds *= x['odds']
                if ODDS3_HYB_MIN_COTE <= total_odds <= ODDS3_HYB_MAX_COTE:
                    legs = [Selection(c['match'].event_id, c['match'].league, c['match'].home, c['match'].away, c.get('kickoff', ''), c['type'], c['type'], c['odds'], c['proba']/100.0, c['real_odd'], quality=90, analysis=c['ai']) for c in combo]
                    total_prob = 1.0
                    for c in combo: total_prob *= (c['proba'] / 100.0)
                    if not coupon_passes_ev(total_odds, total_prob): continue
                    return Coupon("Odds +3 HYBRIDE", "Mélange 3 sources", "🎯", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)

    combined.sort(key=lambda x: (x['odds'] * x['proba'] / 100.0, x['proba']), reverse=True)
    for r in range(3, min(len(combined), 5) + 1):
        for combo in itertools.combinations(combined[:15], r):
            total_odds = 1.0
            for x in combo: total_odds *= x['odds']
            if ODDS3_HYB_MIN_COTE <= total_odds <= ODDS3_HYB_MAX_COTE:
                legs = [Selection(c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id, c['match'].league, c['match'].home if hasattr(c['match'], 'home') else c['match'].home_team, c['match'].away if hasattr(c['match'], 'away') else c['match'].away_team, c.get('kickoff', ''), c['type'], c['type'], c['odds'], c['proba']/100.0, c['real_odd'], quality=90, analysis=c['ai']) for c in combo]
                total_prob = 1.0
                for c in combo: total_prob *= (c['proba'] / 100.0)
                if not coupon_passes_ev(total_odds, total_prob): continue
                return Coupon("Odds +3 HYBRIDE", "Mélange tout sources", "🎯", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)
    return None

# =====================================================================
# 🔧 ASSEMBLAGE FINAL
# =====================================================================
async def scan():
    async with STATE["scan_lock"]:
        STATE["rejections"] = {"value": 0, "ev_coupon": 0, "auth_proba": 0}
        ws_matches, odds_status = await fetch_rapidodds_odds()
        STATE["odds_api_mode"] = odds_status
        af_matches = await fetch_api_football_odds(datetime.now(TZ).strftime("%Y-%m-%d"))

        bzz_preds = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try: bzz_preds = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except: pass

        infer_odds = {}
        if bzz_preds and fetch_infer_odds:
            matches_for_infer = [{"event_id": b["event_id"], "home": b["home"], "away": b["away"], "kickoff": b.get("kickoff", "")} for b in bzz_preds]
            try: infer_odds = await fetch_infer_odds(matches_for_infer)
            except: pass

        enriched_pool = []
        if bzz_preds:
            pool = []
            for bz in bzz_preds:
                md = MatchData(event_id=bz["event_id"], league=bz.get("league", ""), home=bz["home"], away=bz["away"], kickoff=bz.get("kickoff", ""), real_odds=infer_odds.get(bz["event_id"], {}), bzz_markets=bz.get("bzz_markets", {}), bzz_confidence=bz.get("bzz_confidence", 0))
                if STATE.get("dixon_model"):
                    try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                    except: pass
                pool.append(md)

            matched = inject_all_real_odds(pool, ws_matches, af_matches)
            pool = [md for md in pool if is_match_not_started(md.kickoff)]
            pool.sort(key=prescore_match, reverse=True)
            enriched_pool = await enrich_with_double_pass(pool[:GEMINI_MAX_CALLS_PER_SCAN])

        coupon_mtft, bonus_mtft = build_mtft_coupon_and_bonus(enriched_pool) if enriched_pool else (None, [])
        coupon_draw, bonus_draw = build_draw_coupon_and_bonus(enriched_pool) if enriched_pool else (None, [])
        third_pillar = build_third_pillar(enriched_pool) if enriched_pool else []

        coupons = []
        if coupon_mtft: coupons.append(coupon_mtft)
        if coupon_draw: coupons.append(coupon_draw)

        coupon_auth = build_odds3_auth_coupon(bonus_mtft, bonus_draw, third_pillar, ws_matches)
        if coupon_auth: coupons.insert(0, coupon_auth)

        coupon_hybrid = build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar)
        if coupon_hybrid: coupons.insert(1, coupon_hybrid)

        STATE["coupons"] = coupons
        STATE["last_scan_date"] = datetime.now(TZ).strftime("%Y-%m-%d")

# =====================================================================
# 🛡️ FORMATAGE & HANDLERS
# =====================================================================
async def safe_answer(msg, txt, reply_markup=None):
    for i, p in enumerate(split_message(txt)):
        try:
            if i == 0: await msg.answer(p, parse_mode="HTML", reply_markup=reply_markup)
            else: await msg.answer(p, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except: pass

async def safe_send(cid, txt):
    for p in split_message(txt):
        try:
            await bot.send_message(chat_id=cid, text=p, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except: pass

async def broadcast_after_scan():
    if not STATE["coupons"]: msg = "<b>📭 Aucun coupon aujourd'hui</b>"
    else: msg = "<b>🔔 COUPONS DU JOUR !</b>\n\n" + "\n".join([f"{c.emoji} <b>{c.name}</b> • {len(c.legs)} paris • Cote <b>{c.combined_odds}</b>" for c in STATE["coupons"]])
    for cid in TARGETS: await safe_send(cid, msg)

def format_header():
    return f"<b>⚡ {BRAND_NAME.upper()} ⚡</b>\n<i>📅 {today_pretty()}</i>"

def format_selection_block(s, idx, show_analysis):
    lines = [f"<b>┌─ #{idx} {s.home} vs {s.away}</b>", f"│ 📅 <b>{format_kickoff(s.kickoff)}</b> • {s.league}", f"│ 🎯 <b>{s.pick_label}</b>", f"│ 📊 Proba : <b>{s.model_prob*100:.1f}%</b> {prob_bar(s.model_prob)}"]
    if s.real_odd: lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b>")
    else: lines.append(f"│ 💰 Cote : <b>{s.odds}</b> <i>(estimée)</i>")
    if show_analysis and s.analysis: lines.append(f"│ 🧠 <i>{s.analysis[:250]}</i>")
    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)

def format_coupon(c):
    if c.is_list:
        lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{c.subtitle}</i>", ""] + [format_selection_block(s, i, False) + "\n" for i, s in enumerate(c.legs, 1)]
    else:
        lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{c.subtitle}</i>", "", f"<b>┏━━━━━━━━━━━━━━━┓</b>", f"<b>┃ 🎫 Cote : {c.combined_odds}</b>", f"<b>┃ 📈 Proba : {c.combined_prob*100:.1f}%</b>", f"<b>┗━━━━━━━━━━━━━━━┛</b>", ""] + [format_selection_block(s, i, True) + "\n" for i, s in enumerate(c.legs, 1)]
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)

# --- HANDLERS ---
@dp.message(Command("start"))
async def start_cmd(m):
    kb = ADMIN_KEYBOARD if m.from_user.id == ADMIN_ID else USER_KEYBOARD
    await safe_answer(m, f"{format_header()}\n\nBienvenue !", reply_markup=kb)

@dp.message(F.text == BTN_ODDS3)
async def b_odds3(m):
    c_auth = next((x for x in STATE["coupons"] if x.name == "Odds +3 AUTH"), None)
    if c_auth: await safe_answer(m, format_coupon(c_auth))
    else: await safe_answer(m, "📭 Aucun coupon AUTHENTIQUE disponible.")
    if m.from_user.id == ADMIN_ID:
        c_hyb = next((x for x in STATE["coupons"] if x.name == "Odds +3 HYBRIDE"), None)
        if c_hyb: await safe_answer(m, format_coupon(c_hyb))

@dp.message(F.text == BTN_SCAN)
async def b_scan(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, "⏳ Scan en cours...")
    await scan()
    await broadcast_after_scan()

@dp.message(F.text == "⚙️ Debug")
async def b_debug(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, f"Mode : {STATE.get('odds_api_mode')}\nCoupons : {len(STATE['coupons'])}")

async def daily_broadcast():
    await scan()
    await broadcast_after_scan()

@asynccontextmanager
async def lifespan(app):
    try: await bot.delete_webhook(drop_pending_updates=True)
    except: pass
    
    STATE["tracker"] = load_tracker(); STATE["keys"] = load_json(KEYS_DB); STATE["users"] = load_json(USERS_DB)
    
    # NOUVEAU : On lance l'entraînement ML au démarrage en arrière-plan
    if train_model_if_needed:
        asyncio.create_task(asyncio.to_thread(train_model_if_needed))

    if download_and_train:
        try: STATE["dixon_model"] = await download_and_train()
        except: pass

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ), id="daily")
    scheduler.start()
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    yield
    scheduler.shutdown(wait=False); bot_task.cancel()

app = FastAPI(title="Value Bot", lifespan=lifespan)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
