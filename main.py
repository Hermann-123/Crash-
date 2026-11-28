from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import secrets
import unicodedata
import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from scipy.stats import poisson
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

# --- IMPORTS ---
try:
    from gemini_predictor import predict_match_probas
except Exception:
    predict_match_probas = None

try:
    from gemini_predictor import explain_selected_pick_with_gemini
except Exception:
    explain_selected_pick_with_gemini = None

try:
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception:
    enrich_match_with_bzzoiro = None

try:
    from bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
except Exception:
    fetch_bzzoiro_predictions_direct = None

try:
    from odds_fallback import fetch_all_odds as fetch_infer_odds
except Exception:
    fetch_infer_odds = None

try:
    from ml_engine import train_model_if_needed, predict_ml, fuse_probas
    print("✅ ml_engine chargé")
except Exception as e:
    train_model_if_needed = predict_ml = fuse_probas = None
    print(f"❌ ml_engine: {e}")

try:
    from rapidoddsapi_data import fetch_all_odds as fetch_rapidodds_odds_data
except Exception:
    fetch_rapidodds_odds_data = None

try:
    from dixon_coles import download_and_train
except Exception:
    download_and_train = None

try:
    from weekly_analyzer import analyze_weekly_top_matches
    print("✅ weekly_analyzer chargé")
except Exception as e:
    analyze_weekly_top_matches = None
    print(f"⚠️ weekly_analyzer non chargé : {e}")

# --- VARIABLES & ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "5968288964")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "-1003982738017")
RAPIDODDSAPI_API_KEY = os.getenv("RAPIDODDSAPI_API_KEY", "")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")

try:
    ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except Exception:
    ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")
LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")
COUPONS_CACHE_DB = os.getenv("COUPONS_CACHE_DB", "/tmp/coupons_today.json")

BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • VIP")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Gestion de bankroll recommandée (1-3% par ticket).")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "16"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

WEIGHT_ALGO = float(os.getenv("WEIGHT_ALGO", "0.35"))
WEIGHT_GEMINI = float(os.getenv("WEIGHT_GEMINI", "0.35"))
WEIGHT_BZZOIRO = float(os.getenv("WEIGHT_BZZOIRO", "0.30"))

# ✅ CALIBRAGE PRO GLOBAL — PLAGE OFFICIELLE 2.00 À 3.50
MIN_EDGE = float(os.getenv("MIN_EDGE", "-0.03"))
MIN_COUPON_EV = float(os.getenv("MIN_COUPON_EV", "0.96"))
MIN_REAL_COUPON_EV = float(os.getenv("MIN_REAL_COUPON_EV", "0.97"))

# ✅ CALIBRAGE MOTEUR VIP UNIQUE (HAUTE QUALITÉ, ZÉRO PARI AU RABAIS)
AUTH_PROBA_MIN = float(os.getenv("AUTH_PROBA_MIN", "67.0"))
AUTH_STRAIGHT_WIN_MIN = float(os.getenv("AUTH_STRAIGHT_WIN_MIN", "71.0"))
AUTH_PROBA_MAX = float(os.getenv("AUTH_PROBA_MAX", "88.0"))
AUTH_MIN_COMBINED_PROB = float(os.getenv("AUTH_MIN_COMBINED_PROB", "0.31"))

DOUBLE_SCAN_POOL_SIZE = int(os.getenv("DOUBLE_SCAN_POOL_SIZE", "16"))
DOUBLE_SCAN_STABILITY_DELTA = float(os.getenv("DOUBLE_SCAN_STABILITY_DELTA", "0.17"))
DOUBLE_SCAN_MIN_STABLE = int(os.getenv("DOUBLE_SCAN_MIN_STABLE", "3"))
DOUBLE_SCAN_PAUSE_SECONDS = int(os.getenv("DOUBLE_SCAN_PAUSE_SECONDS", "4"))

MTFIN_MIN_SCORE = 68
MTFIN_MIN_PROB = 0.40
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45

DRAW_MIN_SCORE = 68
DRAW_MAX_ECART = 0.20
DRAW_MIN_PROB = 0.28

ODDS3_AUTH_MIN_COTE = 2.00
ODDS3_AUTH_MAX_COTE = 3.50
ODDS3_MIN_COTE_LEG = 1.18

MATCH_START_BUFFER_MINUTES = 10
RESERVE_KEYWORDS = ["jong", " fc b", " ii", " u21", " u19", " castilla", " reserves", " b team"]

PROTECTIVE_VIP_MARKETS = {
    "double_chance_1X",
    "double_chance_X2",
    "double_chance_12",
    "under_2.5",
    "under_3.5",
    "over_1.5",
}

HARD_TRAP_PHRASES = [
    "effectif remanié",
    "équipe bis",
    "équipe b",
    "déjà éliminé",
    "déjà champion",
    "match amical",
    "coupe secondaire",
    "en pleine crise",
    "démobilisé",
]

SOFT_TRAP_PHRASES = [
    "match piège",
    "risque élevé",
    "nombreux absents",
    "blessures majeures",
    "aucun enjeu",
    "sans enjeu",
    "déjà qualifié",
    "manque de motivation",
]

# ✅ LISTE BLANCHE DES COMPÉTITIONS MAJEURES UNIQUEMENT
TRUSTED_MAJOR_LEAGUE_KEYWORDS = [
    "uefa champions league", "champions league",
    "uefa europa league", "europa league",
    "uefa conference league", "europa conference league", "conference league",
    "uefa nations league", "nations league",
    "premier league", "english premier league",
    "la liga", "laliga", "spanish laliga",
    "serie a", "italian serie a",
    "bundesliga", "german bundesliga",
    "ligue 1", "french ligue 1",
    "eredivisie", "dutch eredivisie",
    "primeira liga", "portuguese primeira liga", "liga portugal",
    "pro league", "jupiler pro league", "belgian pro league",
    "super lig", "süper lig", "turkish super lig",
    "scottish premiership",
    "saudi pro league",
    "major league soccer", "mls",
    "brasileirao", "serie a", "liga profesional", "primera division",
    "copa libertadores", "copa sudamericana",
    "world cup", "coupe du monde", "euro 20", "africa cup", "can ", "copa america",
]

EXCLUDED_LEAGUE_KEYWORDS = [
    "friendly", "amical", "club friendlies", "efl trophy", "checkatrade",
    "premier league cup", "premier league 2", "u21", "u23", "u20", "u19", "u17",
    "youth", "reserve", "oberliga", "regionalliga", "taca da liga", "league cup",
    "efl cup", "carabao", "women", "feminine", "féminine", "femenina", "frauen",
    "national league", "league one", "league two", "championship",
    "2. bundesliga", "3. liga", "serie b", "serie c", "ligue 2", "national",
    "segunda", "challenger", "eerste divisie", "liga 2", "1. lig",
]

HIGH_STAKES_ANALYSIS_PHRASES = [
    "qualification", "qualifier", "course au titre", "titre", "podium",
    "maintien", "relégation", "zone rouge", "top 4", "top 8", "place européenne",
    "europe", "montée", "promotion", "play-off", "barrage", "derby",
    "revanche", "impératif", "victoire obligatoire", "doit gagner", "enjeu majeur",
    "crucial", "décisif", "capital", "sous pression",
]

ESPN_LEAGUE_SLUGS = [
    "eng.1", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1",
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
]

TELEGRAM_MAX_LEN = 3800
TZ = ZoneInfo(NOTIFY_TZ)

if not BOT_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise RuntimeError("Variables d'environnement manquantes")

# =====================================================================
# ⚙️ MOTEURS
# =====================================================================
@dataclass
class WS_MatchData:
    match_id: str; league: str; home_team: str; away_team: str
    home_odds: Optional[float]; draw_odds: Optional[float]; away_odds: Optional[float]
    over_2_5: Optional[float] = None; under_2_5: Optional[float] = None
    btts_yes: Optional[float] = None; btts_no: Optional[float] = None
    commence_time: str = ""

@dataclass
class WS_SimulationResult:
    match_id: str; proba_home: float; proba_draw: float; proba_away: float
    most_likely_score: str; proba_btts: float; proba_over_1_5: float; proba_over_2_5: float; proba_over_3_5: float

class DixonColesEngine:
    def __init__(self): self.max_goals = 6
    def simulate(self, match: WS_MatchData) -> Optional[WS_SimulationResult]:
        if not match.home_odds or not match.away_odds: return None
        lx = (1.0 / match.home_odds) * 1.8 * 1.15; my = (1.0 / match.away_odds) * 1.8
        matrix = np.zeros((self.max_goals, self.max_goals))
        for i in range(self.max_goals):
            for j in range(self.max_goals): matrix[i, j] = poisson.pmf(i, lx) * poisson.pmf(j, my)
        matrix /= np.sum(matrix)
        p_h = float(np.sum(np.tril(matrix, -1))) * 100; p_d = float(np.sum(np.diag(matrix))) * 100; p_a = float(np.sum(np.triu(matrix, 1))) * 100
        best_idx = np.argmax(matrix); sx, sy = np.unravel_index(best_idx, matrix.shape)
        p_btts = float(np.sum(matrix[1:, 1:])) * 100
        p_o15 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 1])) * 100
        p_o25 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 2])) * 100
        p_o35 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 3])) * 100
        return WS_SimulationResult(match.match_id, p_h, p_d, p_a, f"{sx}-{sy}", p_btts, p_o15, p_o25, p_o35)

dixon_ws = DixonColesEngine()

@dataclass
class MatchData:
    event_id: str; league: str; home: str; away: str; kickoff: str
    real_odds: dict; bzz_markets: dict; bzz_confidence: float
    gemini_markets: dict = None; dixon_pred: dict = None; analysis: str = ""; has_h2h: bool = False; stability_flag: str = "unknown"

@dataclass
class Selection:
    event_id: str; league: str; home: str; away: str; kickoff: str; market: str; pick_label: str
    odds: float; model_prob: float; real_odd: Optional[float] = None; value: float = -1.0
    quality: int = 0; analysis: str = ""; htft_pick: str = ""

@dataclass
class Coupon:
    name: str; subtitle: str; emoji: str; legs: list; combined_odds: float; combined_prob: float
    is_list: bool = False; show_analysis: bool = False

bot = Bot(BOT_TOKEN); dp = Dispatcher()
BTN_ODDS3 = "🎯 Odds +3"; BTN_MTFT = "⏱ MT-Fin"; BTN_DRAW = "🤝 Match Nul"
BTN_WEEKLY = "📅 Top Semaine"; BTN_SCAN = "🔄 Actualiser"; BTN_BILAN = "📊 Bilan"

USER_KEYBOARD = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_ODDS3)]], resize_keyboard=True)
ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_MTFT)],
        [KeyboardButton(text=BTN_DRAW), KeyboardButton(text=BTN_WEEKLY)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Debug")],
    ],
    resize_keyboard=True,
)

STATE = {
    "ready": False, "coupons": [], "last_scan": None, "debug": {},
    "tracker": {"coupons": []}, "keys": {}, "users": {},
    "scan_lock": asyncio.Lock(), "verify_lock": asyncio.Lock(),
    "dixon_model": None, "last_scan_date": "", "last_score_check": None,
    "double_scan_status": "", "double_scan_stats": {},
    "rejections": {"value": 0, "ev_coupon": 0, "auth_proba": 0, "pro_trap": 0, "unstable": 0, "no_real_odds": 0, "minor_league": 0},
}

TARGETS = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try: TARGETS.append(int(raw))
        except ValueError: TARGETS.append(raw)

# =====================================================================
# GESTION DES CLÉS VIP & ABONNÉS
# =====================================================================
def generate_vip_key_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    p1 = "".join(secrets.choice(alphabet) for _ in range(4))
    p2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"VIP-{p1}-{p2}"

def create_access_keys(days: int = 30, count: int = 1) -> list[str]:
    created = []
    now_iso = datetime.now(TZ).isoformat()
    for _ in range(count):
        code = generate_vip_key_code()
        while code in STATE["keys"]:
            code = generate_vip_key_code()
        STATE["keys"][code] = {
            "days": days,
            "created_at": now_iso,
            "used": False,
            "used_by": None,
            "used_at": None,
        }
        created.append(code)
    save_json(KEYS_DB, STATE["keys"])
    return created

def redeem_access_key(user, raw_code: str) -> Tuple[bool, str]:
    code = raw_code.strip().upper()
    key_info = STATE["keys"].get(code)
    if not key_info:
        return False, "❌ Clé invalide ou introuvable."
    if key_info.get("used"):
        return False, "❌ Cette clé a déjà été utilisée."

    uid = str(user.id)
    now = datetime.now(TZ)
    days = int(key_info.get("days", 30))

    base_dt = now
    if uid in STATE["users"]:
        try:
            old_exp = datetime.fromisoformat(STATE["users"][uid].get("expires_at", ""))
            if old_exp > now:
                base_dt = old_exp
        except Exception:
            pass

    new_exp = base_dt + timedelta(days=days)
    STATE["users"][uid] = {
        "user_id": user.id,
        "username": user.username or "",
        "full_name": user.full_name or "",
        "expires_at": new_exp.isoformat(),
        "last_key": code,
        "updated_at": now.isoformat(),
    }
    key_info["used"] = True
    key_info["used_by"] = user.id
    key_info["used_at"] = now.isoformat()

    save_json(USERS_DB, STATE["users"])
    save_json(KEYS_DB, STATE["keys"])

    exp_str = new_exp.strftime("%d/%m/%Y à %H:%M")
    return True, f"✅ <b>Accès VIP activé ({days} jours) !</b>\n📅 Valide jusqu'au <b>{exp_str}</b>.\n\nAppuie sur <b>{BTN_ODDS3}</b> pour voir le ticket du jour."

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
        return f"✅ Actif jusqu'au {datetime.fromisoformat(STATE['users'][str(user_id)]['expires_at']).strftime('%d/%m/%Y à %H:%M')}."
    return "❌ Abonnement expiré ou inactif.\n🔑 <i>Colle directement ta clé d'accès dans ce chat pour activer ton VIP, ou contacte @hermann1123.</i>"

# =====================================================================
# UTILITAIRES & CACHE JOURNALIER
# =====================================================================
def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except: pass

def serialize_coupon(c: Coupon) -> dict:
    return {
        "name": c.name,
        "subtitle": c.subtitle,
        "emoji": c.emoji,
        "combined_odds": c.combined_odds,
        "combined_prob": c.combined_prob,
        "is_list": c.is_list,
        "show_analysis": c.show_analysis,
        "legs": [asdict(s) for s in c.legs],
    }

def deserialize_coupon(d: dict) -> Coupon:
    legs = [Selection(**leg) for leg in d.get("legs", [])]
    return Coupon(
        name=d["name"],
        subtitle=d["subtitle"],
        emoji=d["emoji"],
        legs=legs,
        combined_odds=d["combined_odds"],
        combined_prob=d["combined_prob"],
        is_list=d.get("is_list", False),
        show_analysis=d.get("show_analysis", False),
    )

def save_daily_cache(date_str: str):
    payload = {
        "date": date_str,
        "saved_at": datetime.now(TZ).isoformat(),
        "odds_api_mode": STATE.get("odds_api_mode", "unknown"),
        "double_scan_status": STATE.get("double_scan_status", ""),
        "double_scan_stats": STATE.get("double_scan_stats", {}),
        "coupons": [serialize_coupon(c) for c in STATE.get("coupons", [])],
    }
    save_json(COUPONS_CACHE_DB, payload)
    print(f"💾 Cache journalier sauvegardé ({len(payload['coupons'])} coupons figés)")

def load_daily_cache(date_str: str) -> bool:
    data = load_json(COUPONS_CACHE_DB, {})
    if not data or data.get("date") != date_str:
        return False
    try:
        STATE["coupons"] = [deserialize_coupon(c) for c in data.get("coupons", [])]
        STATE["last_scan_date"] = date_str
        STATE["last_scan"] = data.get("saved_at")
        if data.get("odds_api_mode"):
            STATE["odds_api_mode"] = data["odds_api_mode"]
        if data.get("double_scan_status"):
            STATE["double_scan_status"] = data["double_scan_status"]
        if data.get("double_scan_stats"):
            STATE["double_scan_stats"] = data["double_scan_stats"]
        return True
    except Exception as e:
        print(f"⚠️ Erreur lecture cache journalier : {e}")
        return False

def today_pretty():
    d = datetime.now(TZ); months = {1:"janvier",2:"février",3:"mars",4:"avril",5:"mai",6:"juin",7:"juillet",8:"août",9:"septembre",10:"octobre",11:"novembre",12:"décembre"}
    return f"{d.day} {months[d.month]} {d.year}"

def normalize_name(s: str) -> str:
    if not s: return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]: s = s.replace(sfx, "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s)).strip()

def teams_match(name1: str, name2: str) -> bool:
    n1, n2 = normalize_name(name1), normalize_name(name2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    if len(n1) >= 4 and len(n2) >= 4 and (n1 in n2 or n2 in n1):
        return True
    return False

def is_reserve_team(name: str) -> bool: return any(kw in name.lower() for kw in RESERVE_KEYWORDS)

def is_trusted_pro_league(league_name: str) -> bool:
    """N'autorise que les grands championnats et compétitions professionnelles majeures."""
    if not league_name:
        return False
    low = "".join(c for c in unicodedata.normalize("NFD", league_name.lower().strip()) if unicodedata.category(c) != "Mn")
    if any(bad in low for bad in EXCLUDED_LEAGUE_KEYWORDS):
        return False
    if "afc champions" in low or "concacaf champions" in low:
        return False
    return any(good in low for good in TRUSTED_MAJOR_LEAGUE_KEYWORDS)

def compute_stakes_score(league_name: str, analysis_text: str = "") -> int:
    score = 0
    low_lg = (league_name or "").lower()
    low_an = (analysis_text or "").lower()
    if any(k in low_lg for k in ("champions league", "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1")):
        score += 40
    elif is_trusted_pro_league(league_name):
        score += 25
    if any(ph in low_an for ph in HIGH_STAKES_ANALYSIS_PHRASES):
        score += 15
    return score

def has_pro_red_flags(
    analysis_text: str,
    league_name: str = "",
    market_key: str = "",
    proba_pct: float = 0.0,
) -> Tuple[bool, str]:
    if league_name and not is_trusted_pro_league(league_name):
        return True, f"compétition non majeure ({league_name})"
    if not analysis_text:
        return False, ""
    low = analysis_text.lower()
    for phrase in HARD_TRAP_PHRASES:
        if phrase in low:
            return True, phrase
    for phrase in SOFT_TRAP_PHRASES:
        if phrase in low:
            if market_key in PROTECTIVE_VIP_MARKETS and proba_pct >= 69.0:
                return False, ""
            return True, phrase
    return False, ""

def clean_analysis_text(text: str, max_len: int = 560) -> str:
    if not text:
        return ""
    text = re.sub(r"^(Salut les pronos\s*[!,.:-]*\s*|Bonjour\s*[!,.:-]*\s*)", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > 100:
            return cut[:idx + 1]
    return cut.rsplit(" ", 1)[0] + "..."

def parse_dt_safe(s) -> Optional[datetime]:
    if not s: return None
    try:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except: return None

def format_kickoff(kickoff_str) -> str:
    if not kickoff_str: return "Heure non conf."
    dt = parse_dt_safe(kickoff_str)
    if dt: return dt.strftime("%d/%m %H:%M")
    return "Heure non conf."

def is_match_not_started(kickoff_str) -> bool:
    if not kickoff_str: return True
    dt = parse_dt_safe(kickoff_str)
    if not dt or (dt.hour == 0 and dt.minute == 0): return True
    return dt > (datetime.now(TZ) + timedelta(minutes=MATCH_START_BUFFER_MINUTES))

def is_match_today(kickoff_str) -> bool:
    if not kickoff_str:
        return False
    dt = parse_dt_safe(kickoff_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    if dt.date() != now.date():
        return False
    if dt.hour == 0 and dt.minute == 0:
        return True
    return dt > (now + timedelta(minutes=MATCH_START_BUFFER_MINUTES))

def calibrated_odd(prob: float, real_odd: Optional[float]) -> float:
    if real_odd and float(real_odd) > 1.01:
        return round(float(real_odd), 2)
    return 0.0

def prob_bar(p: float) -> str:
    if p >= 0.74: return "🟢🟢🟢"
    if p >= 0.67: return "🟢🟢⚪"
    if p >= 0.62: return "🟢⚪⚪"
    return "⚪⚪⚪"

def split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= max_len: return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len: parts.append(current.rstrip()); current = line + "\n"
        else: current += line + "\n"
    if current.strip(): parts.append(current.rstrip())
    return parts

def record_coupons(coupons, date_str):
    tracker = STATE["tracker"]; existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing: continue
        legs = [{"event_id": s.event_id, "home": s.home, "away": s.away, "market": s.market, "pick_label": s.pick_label, "odds": s.odds, "kickoff": s.kickoff, "result": "pending", "score": ""} for s in c.legs]
        tracker["coupons"].append({"id": cid, "date": date_str, "name": c.name, "combined_odds": c.combined_odds, "legs": legs, "status": "pending"})
    tracker["coupons"] = tracker["coupons"][-500:]; save_json(LOCAL_DB, tracker)

# =====================================================================
# ✅ VÉRIFICATION AUTOMATIQUE DES SCORES (BZZOIRO + SECOURS ESPN GRATUIT)
# =====================================================================
def evaluate_pick_outcome(pick_label: str, ft_h: int, ft_a: int, ht_h: Optional[int] = None, ht_a: Optional[int] = None) -> Optional[str]:
    lbl = (pick_label or "").lower().strip()
    total_goals = ft_h + ft_a

    if "mt/fin" in lbl or "v1/v1" in lbl or "v2/v2" in lbl:
        if ht_h is None or ht_a is None:
            return None
        ht_res = "v1" if ht_h > ht_a else ("x" if ht_h == ht_a else "v2")
        ft_res = "v1" if ft_h > ft_a else ("x" if ft_h == ft_a else "v2")
        actual = f"{ht_res}/{ft_res}"
        if "v1/v1" in lbl:
            return "won" if actual == "v1/v1" else "lost"
        if "v2/v2" in lbl:
            return "won" if actual == "v2/v2" else "lost"
        return None

    if "(1x)" in lbl or "1x" in lbl:
        return "won" if ft_h >= ft_a else "lost"
    if "(x2)" in lbl or "x2" in lbl:
        return "won" if ft_a >= ft_h else "lost"
    if "double chance 12" in lbl or "(12)" in lbl or "une équipe gagne" in lbl:
        return "won" if ft_h != ft_a else "lost"

    if "match nul" in lbl or "(x)" in lbl:
        return "won" if ft_h == ft_a else "lost"

    if lbl.endswith("(1)") or ("victoire" in lbl and "(1)" in lbl):
        return "won" if ft_h > ft_a else "lost"
    if lbl.endswith("(2)") or ("victoire" in lbl and "(2)" in lbl):
        return "won" if ft_a > ft_h else "lost"

    if "plus de 1.5" in lbl or "over 1.5" in lbl:
        return "won" if total_goals >= 2 else "lost"
    if "moins de 1.5" in lbl or "under 1.5" in lbl:
        return "won" if total_goals <= 1 else "lost"
    if "plus de 2.5" in lbl or "over 2.5" in lbl:
        return "won" if total_goals >= 3 else "lost"
    if "moins de 2.5" in lbl or "under 2.5" in lbl:
        return "won" if total_goals <= 2 else "lost"
    if "plus de 3.5" in lbl or "over 3.5" in lbl:
        return "won" if total_goals >= 4 else "lost"
    if "moins de 3.5" in lbl or "under 3.5" in lbl:
        return "won" if total_goals <= 3 else "lost"

    if "btts oui" in lbl or "2 équipes marquent" in lbl:
        return "won" if (ft_h > 0 and ft_a > 0) else "lost"
    if "btts non" in lbl:
        return "won" if (ft_h == 0 or ft_a == 0) else "lost"

    return None

def _extract_bzzoiro_score(ev: dict) -> Optional[dict]:
    h = ev.get("home_team") or ev.get("home") or (ev.get("home_team_obj") or {}).get("name") or ""
    a = ev.get("away_team") or ev.get("away") or (ev.get("away_team_obj") or {}).get("name") or ""
    if isinstance(h, dict): h = h.get("name", "")
    if isinstance(a, dict): a = a.get("name", "")
    if not h or not a:
        return None

    raw_st = str(ev.get("status") or ev.get("event_status") or "").lower()
    ft_h = ev.get("home_score") if ev.get("home_score") is not None else ev.get("home_goals")
    ft_a = ev.get("away_score") if ev.get("away_score") is not None else ev.get("away_goals")
    ht_h = ev.get("home_score_ht") if ev.get("home_score_ht") is not None else ev.get("ht_home_score")
    ht_a = ev.get("away_score_ht") if ev.get("away_score_ht") is not None else ev.get("ht_away_score")

    if any(k in raw_st for k in ("postponed", "cancel", "abandon", "pst", "canc")):
        return {"home": h, "away": a, "status": "VOID", "ft_h": None, "ft_a": None, "ht_h": None, "ht_a": None}

    if any(k in raw_st for k in ("fin", "ft", "ended", "complet", "full")) and ft_h is not None and ft_a is not None:
        return {
            "home": h, "away": a, "status": "FT",
            "ft_h": int(ft_h), "ft_a": int(ft_a),
            "ht_h": int(ht_h) if ht_h is not None else None,
            "ht_a": int(ht_a) if ht_a is not None else None,
        }
    return None

async def fetch_bzzoiro_finished_scores(date_iso: str) -> list[dict]:
    if not BZZOIRO_API_KEY:
        return []
    results = []
    headers = {"Authorization": f"Token {BZZOIRO_API_KEY}"}
    urls = [
        ("https://sports.bzzoiro.com/api/events/", {"date": date_iso}),
        ("https://sports.bzzoiro.com/api/events/", {"date_from": date_iso, "date_to": date_iso}),
    ]
    async with httpx.AsyncClient() as client:
        for url, params in urls:
            try:
                r = await client.get(url, headers=headers, params=params, timeout=15.0)
                if r.status_code != 200:
                    continue
                payload = r.json()
                items = payload.get("results", payload) if isinstance(payload, dict) else payload
                if not isinstance(items, list):
                    continue
                for ev in items:
                    parsed = _extract_bzzoiro_score(ev)
                    if parsed:
                        results.append(parsed)
                if results:
                    break
            except Exception as e:
                print(f"⚠️ Bzzoiro scores ({date_iso}) : {e}")
    return results

async def fetch_espn_finished_scores(date_iso: str) -> list[dict]:
    date_compact = date_iso.replace("-", "")
    results = []
    async with httpx.AsyncClient() as client:
        for slug in ESPN_LEAGUE_SLUGS:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
                r = await client.get(url, params={"dates": date_compact}, timeout=10.0)
                if r.status_code != 200:
                    continue
                for ev in r.json().get("events", []):
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    st_obj = comp.get("status", {}).get("type", {})
                    st_name = (st_obj.get("name") or "").upper()
                    completed = bool(st_obj.get("completed"))

                    competitors = comp.get("competitors", [])
                    home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
                    away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
                    if not home_c or not away_c:
                        continue

                    h_name = home_c.get("team", {}).get("displayName") or home_c.get("team", {}).get("name") or ""
                    a_name = away_c.get("team", {}).get("displayName") or away_c.get("team", {}).get("name") or ""
                    if not h_name or not a_name:
                        continue

                    if "POSTPONED" in st_name or "CANCELED" in st_name:
                        results.append({"home": h_name, "away": a_name, "status": "VOID", "ft_h": None, "ft_a": None, "ht_h": None, "ht_a": None})
                    elif completed:
                        try:
                            ft_h = int(float(home_c.get("score", 0)))
                            ft_a = int(float(away_c.get("score", 0)))
                            results.append({"home": h_name, "away": a_name, "status": "FT", "ft_h": ft_h, "ft_a": ft_a, "ht_h": None, "ht_a": None})
                        except Exception:
                            pass
            except Exception:
                continue
    return results

async def verify_pending_scores(notify: bool = True) -> dict:
    async with STATE["verify_lock"]:
        coupons = STATE["tracker"].get("coupons", [])
        pending_coupons = [c for c in coupons if c.get("status") == "pending"]
        if not pending_coupons:
            STATE["last_score_check"] = datetime.now(TZ).isoformat()
            return {"checked": 0, "updated_legs": 0, "settled_coupons": 0}

        dates_to_fetch = set()
        for c in pending_coupons:
            d = c.get("date")
            if d:
                dates_to_fetch.add(d)
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    dates_to_fetch.add((dt + timedelta(days=1)).strftime("%Y-%m-%d"))
                except Exception:
                    pass

        all_results = []
        for d in sorted(dates_to_fetch):
            bzz_res = await fetch_bzzoiro_finished_scores(d)
            espn_res = await fetch_espn_finished_scores(d)
            all_results.extend(bzz_res)
            all_results.extend(espn_res)
            await asyncio.sleep(0.3)

        if not all_results:
            STATE["last_score_check"] = datetime.now(TZ).isoformat()
            return {"checked": len(pending_coupons), "updated_legs": 0, "settled_coupons": 0}

        updated_legs = 0
        newly_settled = []

        for c in pending_coupons:
            legs = c.get("legs", [])
            changed = False

            for leg in legs:
                if leg.get("result") != "pending":
                    continue
                lh, la = leg.get("home", ""), leg.get("away", "")
                matched_res = None
                for item in all_results:
                    if teams_match(lh, item["home"]) and teams_match(la, item["away"]):
                        matched_res = item
                        if item.get("ht_h") is not None:
                            break

                if not matched_res:
                    continue

                if matched_res["status"] == "FT":
                    ft_h, ft_a = matched_res["ft_h"], matched_res["ft_a"]
                    ht_h, ht_a = matched_res.get("ht_h"), matched_res.get("ht_a")
                    outcome = evaluate_pick_outcome(leg.get("pick_label", ""), ft_h, ft_a, ht_h, ht_a)
                    if outcome:
                        leg["result"] = outcome
                        score_txt = f"{ft_h}-{ft_a}"
                        if ht_h is not None and ht_a is not None and "mt/fin" in leg.get("pick_label", "").lower():
                            score_txt += f" (MT {ht_h}-{ht_a})"
                        leg["score"] = score_txt
                        updated_legs += 1
                        changed = True

                elif matched_res["status"] == "VOID":
                    leg["result"] = "void"
                    leg["odds"] = 1.0
                    leg["score"] = "Reporté/Annulé"
                    updated_legs += 1
                    changed = True

            if changed:
                results = [l.get("result") for l in legs]
                if "lost" in results:
                    c["status"] = "lost"
                    c["settled_at"] = datetime.now(TZ).isoformat()
                    newly_settled.append(c)
                elif all(r in ("won", "void") for r in results):
                    eff_odds = round(math.prod(float(l.get("odds", 1.0)) for l in legs), 2)
                    c["combined_odds"] = eff_odds
                    c["status"] = "void" if all(r == "void" for r in results) else "won"
                    c["settled_at"] = datetime.now(TZ).isoformat()
                    newly_settled.append(c)

        if updated_legs > 0 or newly_settled:
            save_json(LOCAL_DB, STATE["tracker"])

        STATE["last_score_check"] = datetime.now(TZ).isoformat()

        if notify and newly_settled:
            for c in newly_settled:
                await notify_settled_coupon(c)

        return {"checked": len(pending_coupons), "updated_legs": updated_legs, "settled_coupons": len(newly_settled)}

async def notify_settled_coupon(c: dict):
    status = c.get("status")
    if status not in ("won", "lost"):
        return
    is_win = status == "won"
    header = "✅ <b>TICKET VIP VALIDÉ !</b>" if is_win else "❌ <b>BILAN TICKET</b>"
    lines = [
        header,
        f"🎫 <b>{c.get('name')}</b> ({c.get('date')}) • Cote <b>{c.get('combined_odds')}</b>",
        "",
    ]
    for l in c.get("legs", []):
        r = l.get("result")
        ico = "✅" if r == "won" else ("❌" if r == "lost" else "⚪")
        sc = f" <b>[{l.get('score')}]</b>" if l.get("score") else ""
        lines.append(f"{ico} {l.get('home')} vs {l.get('away')} — <i>{l.get('pick_label')}</i>{sc}")

    msg = "\n".join(lines)
    if is_win and c.get("name") == "Odds +3 AUTH":
        for cid in TARGETS:
            await safe_send(cid, msg)
    elif ADMIN_ID:
        await safe_send(ADMIN_ID, msg)

def compute_roi_stats(coupons: list) -> dict:
    won = [c for c in coupons if c.get("status") == "won"]
    lost = [c for c in coupons if c.get("status") == "lost"]
    pending = [c for c in coupons if c.get("status") == "pending"]
    settled = len(won) + len(lost)
    win_rate = (len(won) / settled * 100.0) if settled > 0 else 0.0
    profit = sum(float(c.get("combined_odds", 0)) - 1.0 for c in won) - float(len(lost))
    roi = (profit / settled * 100.0) if settled > 0 else 0.0
    avg_win_odd = (sum(float(c.get("combined_odds", 0)) for c in won) / len(won)) if won else 0.0
    return {
        "won": len(won),
        "lost": len(lost),
        "pending": len(pending),
        "settled": settled,
        "win_rate": win_rate,
        "profit": profit,
        "roi": roi,
        "avg_win_odd": avg_win_odd,
    }

def format_bilan_report() -> str:
    all_c = STATE["tracker"].get("coupons", [])
    vip_c = [c for c in all_c if c.get("name") == "Odds +3 AUTH"]

    s_vip = compute_roi_stats(vip_c)
    s_all = compute_roi_stats(all_c)

    lines = [
        f"<b>📊 BILAN PERFORMANCE & ROI</b>",
        f"<i>Mise fixe : 1.00 unité / ticket</i>",
        "",
        f"<b>🛡️ VIP — ODDS +3 AUTH</b>",
        f"• Terminés : <b>{s_vip['settled']}</b> (✅ {s_vip['won']} / ❌ {s_vip['lost']} / ⏳ {s_vip['pending']})",
        f"• Taux de réussite : <b>{s_vip['win_rate']:.1f}%</b>",
        f"• Cote moyenne gagnante : <b>{s_vip['avg_win_odd']:.2f}</b>",
        f"• Profit Net : <b>{s_vip['profit']:+.2f} u</b>",
        f"• ROI Réel : <b>{s_vip['roi']:+.1f}%</b>",
        "",
        f"<b>📈 GLOBAL (TOUS COUPONS)</b>",
        f"• Terminés : <b>{s_all['settled']}</b> (✅ {s_all['won']} / ❌ {s_all['lost']} / ⏳ {s_all['pending']})",
        f"• Réussite : <b>{s_all['win_rate']:.1f}%</b> • Profit : <b>{s_all['profit']:+.2f} u</b> (ROI <b>{s_all['roi']:+.1f}%</b>)",
    ]

    recent = list(reversed(all_c[-5:]))
    if recent:
        lines.append("\n<b>🕒 5 DERNIERS TICKETS :</b>")
        for c in recent:
            st = c.get("status")
            badge = "✅" if st == "won" else ("❌" if st == "lost" else ("⚪" if st == "void" else "⏳"))
            lines.append(f"\n{badge} <b>{c.get('name')}</b> ({c.get('date')}) • Cote <b>{c.get('combined_odds')}</b>")
            for l in c.get("legs", []):
                lr = l.get("result")
                lico = "✅" if lr == "won" else ("❌" if lr == "lost" else ("⚪" if lr == "void" else "⏳"))
                sc = f" [{l.get('score')}]" if l.get("score") else ""
                lines.append(f"   {lico} {l.get('home')} - {l.get('away')} ({l.get('pick_label')}){sc}")

    return "\n".join(lines)

def prescore_match(md: MatchData) -> int:
    """Classe uniquement les grandes affiches professionnelles par prestige, enjeu et clarté des cotes."""
    if not is_trusted_pro_league(md.league):
        return -999
    stakes_bonus = compute_stakes_score(md.league, md.analysis)
    conf_score = int(min(82.0, md.bzz_confidence or 68.0))
    real_bonus = 35 if (md.real_odds.get("home") and md.real_odds.get("away")) else (15 if md.real_odds else 0)
    bzz_bonus = 12 if md.bzz_markets else 0
    dixon_bonus = 20 if md.dixon_pred else 0
    h2h_bonus = 8 if md.has_h2h else 0

    odds_sweetspot = 0
    h_o = float(md.real_odds.get("home") or 0)
    a_o = float(md.real_odds.get("away") or 0)
    if (1.30 <= h_o <= 1.95) or (1.30 <= a_o <= 1.95):
        odds_sweetspot = 24
    elif (1.18 <= h_o < 1.30) or (1.18 <= a_o < 1.30):
        odds_sweetspot = 14

    clarity_bonus = 0
    if md.dixon_pred:
        lh = float(md.dixon_pred.get("lambda_home", 1.3))
        la = float(md.dixon_pred.get("lambda_away", 1.1))
        if abs(lh - la) >= 0.40 or (lh + la) <= 1.95 or (lh + la) >= 2.95:
            clarity_bonus = 12

    return stakes_bonus + odds_sweetspot + conf_score + real_bonus + bzz_bonus + dixon_bonus + h2h_bonus + clarity_bonus

def _ensure_dixon_pred(md: MatchData):
    if not md.dixon_pred:
        if STATE.get("dixon_model"):
            try:
                md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
            except Exception:
                pass
        if not md.dixon_pred and md.real_odds.get("home") and md.real_odds.get("away"):
            try:
                h_o = float(md.real_odds["home"])
                a_o = float(md.real_odds["away"])
                d_o = float(md.real_odds["draw"]) if md.real_odds.get("draw") else None
                sim = dixon_ws.simulate(WS_MatchData(md.event_id, md.league, md.home, md.away, h_o, d_o, a_o))
                if sim:
                    lx = round((1.0 / h_o) * 1.8 * 1.15, 2)
                    my = round((1.0 / a_o) * 1.8, 2)
                    md.dixon_pred = {
                        "lambda_home": lx,
                        "lambda_away": my,
                        "p_home": round(sim.proba_home / 100.0, 4),
                        "p_draw": round(sim.proba_draw / 100.0, 4),
                        "p_away": round(sim.proba_away / 100.0, 4),
                        "over_1.5": round(sim.proba_over_1_5 / 100.0, 4),
                        "over_2.5": round(sim.proba_over_2_5 / 100.0, 4),
                        "under_2.5": round(max(0.01, 1.0 - sim.proba_over_2_5 / 100.0), 4),
                        "over_3.5": round(sim.proba_over_3_5 / 100.0, 4),
                        "under_3.5": round(max(0.01, 1.0 - sim.proba_over_3_5 / 100.0), 4),
                        "btts_yes": round(sim.proba_btts / 100.0, 4),
                        "btts_no": round(max(0.01, 1.0 - sim.proba_btts / 100.0), 4),
                    }
            except Exception:
                pass

    if md.real_odds.get("home") and md.real_odds.get("draw") and not md.real_odds.get("dc_1x"):
        inv_1x = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["draw"]))
        if inv_1x > 0:
            md.real_odds["dc_1x"] = round(max(1.04, (1.0 / inv_1x) * 0.97), 2)
    if md.real_odds.get("away") and md.real_odds.get("draw") and not md.real_odds.get("dc_x2"):
        inv_x2 = (1.0 / float(md.real_odds["away"])) + (1.0 / float(md.real_odds["draw"]))
        if inv_x2 > 0:
            md.real_odds["dc_x2"] = round(max(1.04, (1.0 / inv_x2) * 0.97), 2)
    if md.real_odds.get("home") and md.real_odds.get("away") and not md.real_odds.get("dc_12"):
        inv_12 = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["away"]))
        if inv_12 > 0:
            md.real_odds["dc_12"] = round(max(1.04, (1.0 / inv_12) * 0.97), 2)

    if md.dixon_pred and (md.real_odds.get("over_2.5") or md.real_odds.get("home")):
        p_o15 = float(md.dixon_pred.get("over_1.5") or 0)
        p_u35 = float(md.dixon_pred.get("under_3.5") or (1.0 - float(md.dixon_pred.get("over_3.5") or 0.25)))
        if p_o15 > 0.55 and not md.real_odds.get("over_1.5"):
            md.real_odds["over_1.5"] = round(max(1.08, (1.0 / p_o15) * 0.95), 2)
        if p_u35 > 0.55 and not md.real_odds.get("under_3.5"):
            md.real_odds["under_3.5"] = round(max(1.08, (1.0 / p_u35) * 0.95), 2)

# =====================================================================
# CALCUL PROBABILITÉ (Dixon + ML + Gemini + Bzzoiro + Double Chance)
# =====================================================================
def compute_probability(md, market_key):
    _ensure_dixon_pred(md)
    dixon_pred = md.dixon_pred if md.dixon_pred else {}
    ml_probas = predict_ml(dixon_pred, md.bzz_confidence) if predict_ml else None
    fused = fuse_probas(dixon_pred, ml_probas) if fuse_probas and ml_probas else dixon_pred
    gem = dix = bzz = 0.0; gem_avail = dix_avail = bzz_avail = False

    if md.gemini_markets:
        if md.gemini_markets.get(market_key) is not None:
            try: gem = float(md.gemini_markets[market_key]) / 100.0; gem_avail = True
            except: pass
        elif market_key == "double_chance_1X" and md.gemini_markets.get("victoire_home") is not None and md.gemini_markets.get("match_nul") is not None:
            try: gem = (float(md.gemini_markets["victoire_home"]) + float(md.gemini_markets["match_nul"])) / 100.0; gem_avail = True
            except: pass
        elif market_key == "double_chance_X2" and md.gemini_markets.get("victoire_away") is not None and md.gemini_markets.get("match_nul") is not None:
            try: gem = (float(md.gemini_markets["victoire_away"]) + float(md.gemini_markets["match_nul"])) / 100.0; gem_avail = True
            except: pass
        elif market_key == "double_chance_12" and md.gemini_markets.get("victoire_home") is not None and md.gemini_markets.get("victoire_away") is not None:
            try: gem = (float(md.gemini_markets["victoire_home"]) + float(md.gemini_markets["victoire_away"])) / 100.0; gem_avail = True
            except: pass
        elif market_key == "under_3.5" and md.gemini_markets.get("over_3.5") is not None:
            try: gem = max(0.05, 1.0 - float(md.gemini_markets["over_3.5"]) / 100.0); gem_avail = True
            except: pass

    if fused:
        if market_key == "double_chance_1X" and fused.get("p_home") is not None and fused.get("p_draw") is not None:
            try: dix = float(fused["p_home"]) + float(fused["p_draw"]); dix_avail = True
            except: pass
        elif market_key == "double_chance_X2" and fused.get("p_away") is not None and fused.get("p_draw") is not None:
            try: dix = float(fused["p_away"]) + float(fused["p_draw"]); dix_avail = True
            except: pass
        elif market_key == "double_chance_12" and fused.get("p_home") is not None and fused.get("p_away") is not None:
            try: dix = float(fused["p_home"]) + float(fused["p_away"]); dix_avail = True
            except: pass
        elif market_key == "under_3.5":
            try:
                if fused.get("under_3.5") is not None:
                    dix = float(fused["under_3.5"]); dix_avail = True
                elif fused.get("over_3.5") is not None:
                    dix = max(0.05, 1.0 - float(fused["over_3.5"])); dix_avail = True
            except: pass
        else:
            dk = {"victoire_home":"p_home","match_nul":"p_draw","victoire_away":"p_away","over_2.5":"over_2.5","under_2.5":"under_2.5","over_3.5":"over_3.5","over_1.5":"over_1.5","under_1.5":"under_1.5","btts_yes":"btts_yes","btts_no":"btts_no"}.get(market_key)
            if dk and fused.get(dk) is not None:
                try: dix = float(fused.get(dk)); dix_avail = True
                except: pass

    if md.bzz_markets and md.bzz_markets.get(market_key) is not None:
        try: bzz = float(md.bzz_markets[market_key]) / 100.0; bzz_avail = True
        except: pass

    total_w = 0.0; weighted_sum = 0.0
    if dix_avail: total_w += WEIGHT_ALGO; weighted_sum += dix * WEIGHT_ALGO
    if gem_avail: total_w += WEIGHT_GEMINI; weighted_sum += gem * WEIGHT_GEMINI
    if bzz_avail: total_w += WEIGHT_BZZOIRO; weighted_sum += bzz * WEIGHT_BZZOIRO
    if total_w <= 0: return 0.0, gem, dix, bzz, ""
    final = min(0.95, weighted_sum / total_w)
    sources = [x for x in (gem, dix, bzz) if x > 0]
    emoji = ""
    if len(sources) >= 2:
        delta = max(sources) - min(sources)
        emoji = "🤝🤝" if len(sources) == 3 and delta <= 0.14 else "🤝" if delta <= 0.17 else "⚠️" if delta >= 0.24 else ""
    return final, gem, dix, bzz, emoji

def passes_value_filter(proba, odd, is_real, consensus_emoji=""):
    if proba <= 0 or odd <= 0:
        return False
    if consensus_emoji == "⚠️":
        STATE["rejections"]["value"] += 1
        return False
    if is_real:
        ok = (proba * odd) >= (1.0 + MIN_EDGE)
        if not ok:
            STATE["rejections"]["value"] += 1
        return ok
    ok = (proba * odd) >= 0.96
    if not ok:
        STATE["rejections"]["value"] += 1
    return ok

def coupon_passes_ev(odds, prob, has_real_odds: bool = False):
    if odds <= 0 or prob <= 0:
        return False
    min_ev = MIN_REAL_COUPON_EV if has_real_odds else MIN_COUPON_EV
    ok = (odds * prob) >= min_ev
    if not ok:
        STATE["rejections"]["ev_coupon"] += 1
    return ok

def passes_auth_proba_filter(proba, market_key: str = ""):
    if proba <= 0:
        return False
    min_req = AUTH_STRAIGHT_WIN_MIN if market_key in ("victoire_home", "victoire_away") else AUTH_PROBA_MIN
    ok = min_req <= proba <= AUTH_PROBA_MAX
    if not ok:
        STATE["rejections"]["auth_proba"] += 1
    return ok

# =====================================================================
# APIS COTES (ESPN + Bzzoiro + The Odds API + RapidOddsAPI)
# =====================================================================
def _american_to_decimal(ml) -> Optional[float]:
    if ml is None:
        return None
    try:
        s = str(ml).strip().upper()
        if s in ("EVEN", "EV", "PK"):
            return 2.00
        val = float(s.replace("+", ""))
        if val == 0:
            return None
        if val > 0:
            dec = 1.0 + (val / 100.0)
        else:
            dec = 1.0 + (100.0 / abs(val))
        return round(dec, 2) if dec > 1.01 else None
    except Exception:
        return None

def _parse_espn_odds_list(odds_list: list) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    h_odd = d_odd = a_odd = o25 = u25 = None
    for od in (odds_list or []):
        if not isinstance(od, dict):
            continue
        h_ml = (od.get("homeTeamOdds") or {}).get("moneyLine")
        a_ml = (od.get("awayTeamOdds") or {}).get("moneyLine")
        d_ml = (od.get("drawOdds") or {}).get("moneyLine")
        if h_ml is not None and not h_odd:
            h_odd = _american_to_decimal(h_ml)
        if a_ml is not None and not a_odd:
            a_odd = _american_to_decimal(a_ml)
        if d_ml is not None and not d_odd:
            d_odd = _american_to_decimal(d_ml)

        ou_line = od.get("overUnder")
        if ou_line is not None and abs(float(ou_line) - 2.5) < 0.01:
            ov_dec = _american_to_decimal(od.get("overOdds"))
            un_dec = _american_to_decimal(od.get("underOdds"))
            if ov_dec and not o25:
                o25 = ov_dec
            if un_dec and not u25:
                u25 = un_dec

        if h_odd and a_odd and d_odd:
            break
    return h_odd, d_odd, a_odd, o25, u25

ESPN_ODDS_SLUGS = [
    "all",
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
    "eng.1", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1",
    "conmebol.libertadores", "usa.1", "bra.1", "arg.1",
]

async def _fetch_espn_slug_date(client: httpx.AsyncClient, slug: str, d_str: str) -> list[dict]:
    items = []
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
        r = await client.get(url, params={"dates": d_str, "limit": 200})
        if r.status_code != 200:
            return []
        data = r.json()
        default_league = (data.get("leagues") or [{}])[0].get("name") or slug
        for ev in data.get("events", []):
            comps = ev.get("competitions", [])
            if not comps:
                continue
            comp = comps[0]
            st_type = comp.get("status", {}).get("type", {})
            if st_type.get("completed") or st_type.get("state") in ("in", "post"):
                continue
            competitors = comp.get("competitors", [])
            home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home_c or not away_c:
                continue
            home_name = home_c.get("team", {}).get("displayName") or home_c.get("team", {}).get("name") or ""
            away_name = away_c.get("team", {}).get("displayName") or away_c.get("team", {}).get("name") or ""
            if not home_name or not away_name:
                continue
            lg_name = (ev.get("league") or {}).get("name") or default_league
            items.append({
                "raw_id": str(ev.get("id", "")),
                "slug": slug,
                "league": lg_name,
                "home": home_name,
                "away": away_name,
                "date": ev.get("date", ""),
                "odds": comp.get("odds") or [],
            })
    except Exception:
        pass
    return items

async def _fetch_espn_event_summary_odds(client: httpx.AsyncClient, slug: str, raw_id: str) -> list:
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary"
        r = await client.get(url, params={"event": raw_id})
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("pickcenter") or data.get("odds") or []
    except Exception:
        return []

async def fetch_espn_prematch_odds(days_ahead: int = 3) -> list[WS_MatchData]:
    now = datetime.now(TZ)
    date_strings = [(now + timedelta(days=d)).strftime("%Y%m%d") for d in range(max(1, days_ahead + 1))]
    ws_map = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
        tasks = [_fetch_espn_slug_date(client, slug, d_str) for slug in ESPN_ODDS_SLUGS for d_str in date_strings]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

        candidates_without_odds = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                ev_id = f"espn_{item['raw_id']}"
                if ev_id in ws_map:
                    continue
                h_odd, d_odd, a_odd, o25, u25 = _parse_espn_odds_list(item["odds"])
                if h_odd and a_odd:
                    ws_map[ev_id] = WS_MatchData(
                        match_id=ev_id,
                        league=item["league"],
                        home_team=item["home"],
                        away_team=item["away"],
                        home_odds=h_odd,
                        draw_odds=d_odd,
                        away_odds=a_odd,
                        over_2_5=o25,
                        under_2_5=u25,
                        commence_time=item["date"],
                    )
                elif item["raw_id"] and item["slug"] != "all":
                    candidates_without_odds.append(item)

        if candidates_without_odds:
            seen_raw = set()
            unique_missing = []
            for it in candidates_without_odds:
                if it["raw_id"] not in seen_raw and f"espn_{it['raw_id']}" not in ws_map:
                    seen_raw.add(it["raw_id"])
                    unique_missing.append(it)
            summary_tasks = [_fetch_espn_event_summary_odds(client, it["slug"], it["raw_id"]) for it in unique_missing[:35]]
            summary_results = await asyncio.gather(*summary_tasks, return_exceptions=True)
            for it, s_odds in zip(unique_missing[:35], summary_results):
                if not isinstance(s_odds, list) or not s_odds:
                    continue
                h_odd, d_odd, a_odd, o25, u25 = _parse_espn_odds_list(s_odds)
                if h_odd and a_odd:
                    ev_id = f"espn_{it['raw_id']}"
                    ws_map[ev_id] = WS_MatchData(
                        match_id=ev_id,
                        league=it["league"],
                        home_team=it["home"],
                        away_team=it["away"],
                        home_odds=h_odd,
                        draw_odds=d_odd,
                        away_odds=a_odd,
                        over_2_5=o25,
                        under_2_5=u25,
                        commence_time=it["date"],
                    )

    res = list(ws_map.values())
    print(f"🆓 ESPN Vraies Cotes Gratuites : {len(res)} matchs récupérés (0 €)")
    return res

async def fetch_bzzoiro_free_odds(days_ahead: int = 3) -> list[WS_MatchData]:
    if not BZZOIRO_API_KEY:
        return []
    now = datetime.now(TZ)
    d_from = now.strftime("%Y-%m-%d")
    d_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"Token {BZZOIRO_API_KEY}"}
    ws_map = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for endpoint in ("https://sports.bzzoiro.com/api/events/", "https://sports.bzzoiro.com/api/predictions/"):
            try:
                r = await client.get(endpoint, headers=headers, params={"date_from": d_from, "date_to": d_to, "limit": 250})
                if r.status_code != 200:
                    continue
                payload = r.json()
                items = payload.get("results", payload) if isinstance(payload, dict) else payload
                if not isinstance(items, list):
                    continue
                for raw in items:
                    ev = raw.get("event") if isinstance(raw.get("event"), dict) else raw
                    h = ev.get("home_team") or raw.get("home_team") or ""
                    a = ev.get("away_team") or raw.get("away_team") or ""
                    if isinstance(h, dict): h = h.get("name", "")
                    if isinstance(a, dict): a = a.get("name", "")
                    if not h or not a:
                        continue
                    lg = ev.get("league") or raw.get("league") or ""
                    if isinstance(lg, dict): lg = lg.get("name", "")

                    def _f(v):
                        try:
                            fv = float(v)
                            return round(fv, 2) if fv > 1.01 else None
                        except Exception:
                            return None

                    h_odd = _f(ev.get("odds_home") or raw.get("odds_home") or ev.get("home_odds"))
                    d_odd = _f(ev.get("odds_draw") or raw.get("odds_draw") or ev.get("draw_odds"))
                    a_odd = _f(ev.get("odds_away") or raw.get("odds_away") or ev.get("away_odds"))
                    o25 = _f(ev.get("odds_over_25") or raw.get("odds_over_25") or ev.get("over_2_5_odds"))
                    u25 = _f(ev.get("odds_under_25") or raw.get("odds_under_25") or ev.get("under_2_5_odds"))
                    b_yes = _f(ev.get("odds_btts_yes") or raw.get("odds_btts_yes"))
                    b_no = _f(ev.get("odds_btts_no") or raw.get("odds_btts_no"))

                    if h_odd and a_odd:
                        nk = f"{normalize_name(h)}__{normalize_name(a)}"
                        if nk not in ws_map:
                            ws_map[nk] = WS_MatchData(
                                match_id=str(ev.get("id") or raw.get("id") or nk),
                                league=str(lg),
                                home_team=str(h),
                                away_team=str(a),
                                home_odds=h_odd,
                                draw_odds=d_odd,
                                away_odds=a_odd,
                                over_2_5=o25,
                                under_2_5=u25,
                                btts_yes=b_yes,
                                btts_no=b_no,
                                commence_time=str(ev.get("event_date") or ev.get("commence_time") or ""),
                            )
            except Exception:
                continue

    res = list(ws_map.values())
    print(f"🆓 Bzzoiro Vraies Cotes Gratuites : {len(res)} matchs récupérés (0 €)")
    return res

async def fetch_rapidodds_odds() -> Tuple[list, str]:
    ws_by_key = {}

    try:
        espn_ws = await fetch_espn_prematch_odds(days_ahead=1)
        for item in espn_ws:
            nk = f"{normalize_name(item.home_team)}__{normalize_name(item.away_team)}"
            ws_by_key[nk] = item
    except Exception as e:
        print(f"⚠️ ESPN Odds erreur : {e}")

    try:
        bzz_ws = await fetch_bzzoiro_free_odds(days_ahead=1)
        for item in bzz_ws:
            nk = f"{normalize_name(item.home_team)}__{normalize_name(item.away_team)}"
            existing = ws_by_key.get(nk)
            if existing:
                if not existing.draw_odds and item.draw_odds: existing.draw_odds = item.draw_odds
                if not existing.over_2_5 and item.over_2_5: existing.over_2_5 = item.over_2_5
                if not existing.under_2_5 and item.under_2_5: existing.under_2_5 = item.under_2_5
                if not existing.btts_yes and item.btts_yes: existing.btts_yes = item.btts_yes
                if not existing.btts_no and item.btts_no: existing.btts_no = item.btts_no
            else:
                ws_by_key[nk] = item
    except Exception as e:
        print(f"⚠️ Bzzoiro Odds erreur : {e}")

    if THE_ODDS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    "https://api.the-odds-api.com/v4/sports/upcoming/odds",
                    params={
                        "apiKey": THE_ODDS_API_KEY,
                        "regions": "eu,uk",
                        "markets": "h2h,totals",
                        "oddsFormat": "decimal",
                    },
                )
                if r.status_code == 200:
                    rem = r.headers.get("x-requests-remaining", "?")
                    added = 0
                    for ev in r.json():
                        if not str(ev.get("sport_key", "")).startswith("soccer_"):
                            continue
                        home = ev.get("home_team", "")
                        away = ev.get("away_team", "")
                        h_odd = d_odd = a_odd = o25 = u25 = None

                        for bk in ev.get("bookmakers", []):
                            for mkt in bk.get("markets", []):
                                if mkt.get("key") == "h2h":
                                    for out in mkt.get("outcomes", []):
                                        if out.get("name") == home and not h_odd:
                                            h_odd = float(out["price"])
                                        elif out.get("name") == away and not a_odd:
                                            a_odd = float(out["price"])
                                        elif out.get("name") == "Draw" and not d_odd:
                                            d_odd = float(out["price"])
                                elif mkt.get("key") == "totals":
                                    for out in mkt.get("outcomes", []):
                                        if float(out.get("point", 0)) == 2.5:
                                            if out.get("name") == "Over" and not o25:
                                                o25 = float(out["price"])
                                            elif out.get("name") == "Under" and not u25:
                                                u25 = float(out["price"])
                            if h_odd and a_odd and d_odd and o25 and u25:
                                break

                        if h_odd and a_odd:
                            nk = f"{normalize_name(home)}__{normalize_name(away)}"
                            existing = ws_by_key.get(nk)
                            if existing:
                                if not existing.draw_odds and d_odd: existing.draw_odds = d_odd
                                if not existing.over_2_5 and o25: existing.over_2_5 = o25
                                if not existing.under_2_5 and u25: existing.under_2_5 = u25
                            else:
                                ws_by_key[nk] = WS_MatchData(
                                    match_id=str(ev.get("id", "")),
                                    league=ev.get("sport_title", ""),
                                    home_team=home,
                                    away_team=away,
                                    home_odds=h_odd,
                                    draw_odds=d_odd,
                                    away_odds=a_odd,
                                    over_2_5=o25,
                                    under_2_5=u25,
                                    commence_time=ev.get("commence_time", ""),
                                )
                                added += 1
                    print(f"✅ The Odds API (1 req/jour) : +{added} matchs (crédits restants : {rem})")
        except Exception as e:
            print(f"⚠️ The Odds API erreur : {e}")

    if RAPIDODDSAPI_API_KEY and fetch_rapidodds_odds_data:
        try:
            events = await fetch_rapidodds_odds_data()
            for e in (events or []):
                nk = f"{normalize_name(e['home_team'])}__{normalize_name(e['away_team'])}"
                if nk not in ws_by_key and e.get("home_odds") and e.get("away_odds"):
                    ws_by_key[nk] = WS_MatchData(e["match_id"], e.get("league", ""), e["home_team"], e["away_team"], e.get("home_odds"), e.get("draw_odds"), e.get("away_odds"), e.get("over_2_5"), e.get("under_2_5"), e.get("btts_yes"), e.get("btts_no"), e.get("commence_time", ""))
        except Exception as e:
            print(f"⚠️ RapidOddsAPI erreur : {e}")

    ws = list(ws_by_key.values())
    return ws, ("live" if ws else "fallback")

def inject_all_real_odds(pool, ws_matches):
    matched = 0
    for md in pool:
        had_initial_odds = bool(md.real_odds)
        for ws in ws_matches:
            if teams_match(md.home, ws.home_team) and teams_match(md.away, ws.away_team):
                if ws.home_odds: md.real_odds["home"] = ws.home_odds
                if ws.draw_odds: md.real_odds["draw"] = ws.draw_odds
                if ws.away_odds: md.real_odds["away"] = ws.away_odds
                if ws.over_2_5: md.real_odds["over_2.5"] = ws.over_2_5
                if ws.under_2_5: md.real_odds["under_2.5"] = ws.under_2_5
                if ws.btts_yes: md.real_odds["btts_yes"] = ws.btts_yes
                if ws.btts_no: md.real_odds["btts_no"] = ws.btts_no
                if ws.commence_time: md.kickoff = ws.commence_time
                break
        _ensure_dixon_pred(md)
        if bool(md.real_odds) or had_initial_odds:
            matched += 1
    return matched

# =====================================================================
# DOUBLE SCAN & ENRICHISSEMENT IA COMPLET
# =====================================================================
def _build_quant_analysis(md: MatchData, pick_label: str, proba_pct: float, odd: float) -> str:
    _ensure_dixon_pred(md)
    dp = md.dixon_pred or {}
    lh = dp.get("lambda_home")
    la = dp.get("lambda_away")
    ev = round((proba_pct / 100.0) * odd, 2)
    parts = []
    if lh is not None and la is not None:
        xg_tot = round(float(lh) + float(la), 2)
        if float(lh) >= float(la) + 0.35:
            parts.append(f"{md.home} affiche un avantage statistique net à domicile (xG {float(lh):.2f} contre {float(la):.2f} pour {md.away}, total attendu {xg_tot:.2f} buts).")
        elif float(la) >= float(lh) + 0.35:
            parts.append(f"{md.away} présente une supériorité offensive marquée en déplacement (xG {float(la):.2f} contre {float(lh):.2f} pour {md.home}, total attendu {xg_tot:.2f} buts).")
        else:
            parts.append(f"Duel équilibré sur les projections Dixon-Coles avec un rythme attendu de {xg_tot:.2f} buts (xG {float(lh):.2f} - {float(la):.2f}) en {md.league}.")
    else:
        parts.append(f"Analyse croisée Dixon-Coles et Bzzoiro sur cette affiche de {md.league} entre {md.home} et {md.away}.")

    bzz_note = "avec convergence des modèles Bzzoiro" if md.bzz_markets else "selon la modélisation Poisson et les cotes du marché"
    h2h_note = " et l'historique des confrontations directes" if md.has_h2h else ""
    parts.append(f"La probabilité estimée à {proba_pct:.1f}% {bzz_note}{h2h_note} confirme la solidité du scénario.")
    parts.append(f"À une cote bookmaker de {odd:.2f} (EV {ev:.2f}), l'option {pick_label} offre une value statistique cohérente.")
    return " ".join(parts)

async def _fetch_gemini_analysis_for_match(client: httpx.AsyncClient, md: MatchData) -> Optional[dict]:
    _ensure_dixon_pred(md)
    ctx = {}
    if enrich_match_with_bzzoiro:
        try:
            date_iso = parse_dt_safe(md.kickoff).strftime("%Y-%m-%d") if parse_dt_safe(md.kickoff) else datetime.now(TZ).strftime("%Y-%m-%d")
            bzz_ctx = await enrich_match_with_bzzoiro(md.home, md.away, date_iso)
            if isinstance(bzz_ctx, dict):
                ctx.update(bzz_ctx)
                if bzz_ctx.get("available"):
                    md.has_h2h = True
        except Exception:
            pass
    if md.dixon_pred:
        ctx["dixon_coles"] = md.dixon_pred
    if md.real_odds:
        ctx["bookmaker_odds"] = md.real_odds
    if md.bzz_markets:
        ctx["bzzoiro_probas"] = md.bzz_markets

    if predict_match_probas:
        pred = await predict_match_probas(client, md.home, md.away, md.league, md.kickoff, context=ctx)
        if pred and isinstance(pred, dict) and ("markets" in pred or pred.get("analysis")):
            return {"markets": pred.get("markets", {}), "analysis": str(pred.get("analysis") or "").strip()}
    return None

async def _enrich_selected_legs_ai(combo: tuple | list) -> bool:
    async with httpx.AsyncClient(timeout=55.0) as client:
        for c in combo:
            md: Optional[MatchData] = c.get("_md") or c.get("match")
            if not md:
                continue
            pick_label = c.get("type", "")
            mk = c.get("mk", "")
            proba_pct = float(c.get("proba", 70.0))
            odd = float(c.get("odds", 1.35))

            if explain_selected_pick_with_gemini:
                try:
                    _ensure_dixon_pred(md)
                    ctx = {}
                    if md.dixon_pred:
                        ctx["dixon_coles"] = md.dixon_pred
                    if md.real_odds:
                        ctx["bookmaker_odds"] = md.real_odds
                    if md.bzz_markets:
                        ctx["bzzoiro_probas"] = md.bzz_markets

                    enriched = await explain_selected_pick_with_gemini(
                        client=client,
                        home=md.home,
                        away=md.away,
                        league=md.league,
                        kickoff=md.kickoff,
                        pick_label=pick_label,
                        proba_pct=proba_pct,
                        odd=odd,
                        context=ctx,
                    )
                    if enriched:
                        if not enriched.get("is_safe", True) and mk not in PROTECTIVE_VIP_MARKETS:
                            print(f"  🚩 Gemini rejette le pari '{pick_label}' sur {md.home} vs {md.away}")
                            return False
                        analysis_txt = enriched.get("analysis", "").strip()
                        if has_pro_red_flags(analysis_txt, md.league, mk, proba_pct)[0]:
                            print(f"  🚩 Signal rouge dans l'analyse finale sur {md.home} vs {md.away}")
                            return False
                        if len(analysis_txt) >= 80:
                            c["ai"] = analysis_txt
                            print(f"🧠 Analyse Gemini ciblée validée : {md.home} vs {md.away} -> {pick_label}")
                            await asyncio.sleep(1.5)
                            continue
                except Exception as e:
                    print(f"⚠️ Enrichissement ciblé ({md.home} vs {md.away}) : {e}")

            if c.get("ai") and len(str(c.get("ai")).strip()) >= 60:
                if has_pro_red_flags(str(c.get("ai")), md.league, mk, proba_pct)[0]:
                    return False
                continue

            if md.analysis and len(md.analysis.strip()) >= 60:
                if has_pro_red_flags(md.analysis, md.league, mk, proba_pct)[0]:
                    return False
                c["ai"] = md.analysis.strip()
                continue

            c["ai"] = _build_quant_analysis(md, pick_label, proba_pct, odd)
    return True

async def _enrich_single_pass(targets, pass_num):
    results = {}
    errors = 0
    async with httpx.AsyncClient(timeout=70.0) as client:
        for md in targets:
            try:
                pred = await _fetch_gemini_analysis_for_match(client, md)
                if pred:
                    results[md.event_id] = pred
                    errors = 0
                else:
                    errors += 1
                await asyncio.sleep(2.0)
            except Exception as e:
                errors += 1
                print(f"⚠️ Gemini Pass {pass_num} ({md.home} vs {md.away}) : {e}")
                if errors >= 3:
                    print(f"🛑 Gemini Pass {pass_num} : arrêt anticipé (erreurs consécutives).")
                    break
    print(f"🤖 Gemini Pass {pass_num} : {len(results)}/{len(targets)} matchs analysés")
    return results

def _compare_two_passes(targets, r1, r2):
    stable_list, enriched_list, stats = [], [], {"stable": 0, "unstable": 0, "single": 0, "none": 0}
    for md in targets:
        p1, p2 = r1.get(md.event_id), r2.get(md.event_id)
        if not p1 and not p2: stats["none"] += 1; md.stability_flag = "no_gemini"; continue
        if p1 and not p2: md.gemini_markets = p1["markets"]; md.analysis = p1["analysis"]; md.stability_flag = "single"; stats["single"] += 1; enriched_list.append(md); continue
        if p2 and not p1: md.gemini_markets = p2["markets"]; md.analysis = p2["analysis"]; md.stability_flag = "single"; stats["single"] += 1; enriched_list.append(md); continue
        is_stable, merged = True, {}
        for k in set(p1["markets"].keys()) | set(p2["markets"].keys()):
            v1, v2 = p1["markets"].get(k), p2["markets"].get(k)
            if v1 is None and v2 is not None: merged[k] = v2; is_stable = False; continue
            if v2 is None and v1 is not None: merged[k] = v1; is_stable = False; continue
            if v1 is None and v2 is None: continue
            try: f1, f2 = float(v1), float(v2)
            except: merged[k] = v1; continue
            if abs(f1 - f2) / 100.0 > DOUBLE_SCAN_STABILITY_DELTA: merged[k] = min(f1, f2); is_stable = False
            else: merged[k] = (f1 + f2) / 2.0
        md.gemini_markets = merged
        a1, a2 = p1.get("analysis", "") or "", p2.get("analysis", "") or ""
        base = a1 if len(a1) >= len(a2) else a2
        if is_stable: md.analysis = base; md.stability_flag = "stable"; stats["stable"] += 1; stable_list.append(md)
        else: md.analysis = f"⚠️ Variable. {base[:280]}"; md.stability_flag = "unstable"; stats["unstable"] += 1
        enriched_list.append(md)
    return stable_list, enriched_list, stats

async def enrich_with_double_pass(pool):
    if not pool: STATE["double_scan_status"] = "EMPTY"; return pool
    targets = pool[:min(len(pool), DOUBLE_SCAN_POOL_SIZE)]
    r1 = await _enrich_single_pass(targets, 1)
    await asyncio.sleep(DOUBLE_SCAN_PAUSE_SECONDS)
    r2 = await _enrich_single_pass(targets, 2)
    stable, enriched, stats = _compare_two_passes(targets, r1, r2)
    STATE["double_scan_stats"] = stats
    print(f"🔬 Double Scan Stats : {stats}")
    if len(stable) >= DOUBLE_SCAN_MIN_STABLE:
        STATE["double_scan_status"] = f"STRICT ({len(stable)})"
        return stable
    if enriched:
        STATE["double_scan_status"] = f"FALLBACK ({len(stable)}/{len(enriched)})"
        return enriched
    STATE["double_scan_status"] = "GEMINI_KO"
    return targets

# =====================================================================
# MOTEUR EXCLUSIF VIP UNIQUE : GRANDS MATCHS & SCÉNARIOS SÛRS (2.00 - 3.50)
# =====================================================================
def _build_auth_coupon(combo, subtitle, quality) -> Coupon:
    sorted_combo = sorted(combo, key=lambda c: parse_dt_safe(c.get("kickoff")) or datetime.max.replace(tzinfo=TZ))
    total_odds = math.prod(c["odds"] for c in sorted_combo)
    total_prob = math.prod(c["proba"] / 100.0 for c in sorted_combo)
    legs = [
        Selection(
            event_id=c["event_id"],
            league=c["league"],
            home=c["home"],
            away=c["away"],
            kickoff=c["kickoff"],
            market=c["type"],
            pick_label=c["type"],
            odds=c["odds"],
            model_prob=c["proba"] / 100.0,
            real_odd=c["real_odd"],
            quality=quality,
            analysis=clean_analysis_text(c["ai"], 560),
        )
        for c in sorted_combo
    ]
    return Coupon("Odds +3 AUTH", f"{subtitle} • {len(legs)} sélections", "🛡️", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)

def _determine_match_scenario_pick(md: MatchData) -> Optional[dict]:
    """
    Évalue les options les plus solides sur les grandes affiches professionnelles
    et choisit le pari sûr le mieux aligné avec le scénario du match.
    """
    if not is_trusted_pro_league(md.league):
        return None
    _ensure_dixon_pred(md)
    dp = md.dixon_pred or {}
    lh = float(dp.get("lambda_home", 1.35))
    la = float(dp.get("lambda_away", 1.15))
    xg_total = lh + la

    p_home, g_h, _, _, em_h = compute_probability(md, "victoire_home")
    p_away, g_a, _, _, em_a = compute_probability(md, "victoire_away")
    p_draw, _, _, _, _ = compute_probability(md, "match_nul")
    p_1x, _, _, _, em_1x = compute_probability(md, "double_chance_1X")
    p_x2, _, _, _, em_x2 = compute_probability(md, "double_chance_X2")
    p_12, _, _, _, em_12 = compute_probability(md, "double_chance_12")
    p_u25, _, _, _, em_u25 = compute_probability(md, "under_2.5")
    p_o25, _, _, _, em_o25 = compute_probability(md, "over_2.5")
    p_o15, _, _, _, em_o15 = compute_probability(md, "over_1.5")
    p_u35, _, _, _, em_u35 = compute_probability(md, "under_3.5")

    scenario_candidates = []

    # 1. Domination à domicile -> Victoire 1 (>=71%) ou Double Chance 1X
    if p_home >= 0.71 and (p_home - p_away) >= 0.38 and em_h != "⚠️":
        scenario_candidates.append(("victoire_home", "home", "result", f"Victoire {md.home} (1)", p_home, em_h, 3))
    if p_home >= p_away and (g_a == 0 or g_h >= g_a - 0.04):
        scenario_candidates.append(("double_chance_1X", "dc_1x", "dc", f"Double Chance {md.home} ou Nul (1X)", p_1x, em_1x, 3))

    # 2. Supériorité à l'extérieur -> Victoire 2 (>=72%) ou Double Chance X2
    if p_away >= 0.72 and (p_away - p_home) >= 0.40 and em_a != "⚠️":
        scenario_candidates.append(("victoire_away", "away", "result", f"Victoire {md.away} (2)", p_away, em_a, 3))
    if p_away > p_home and (g_h == 0 or g_a >= g_h - 0.04):
        scenario_candidates.append(("double_chance_X2", "dc_x2", "dc", f"Double Chance Nul ou {md.away} (X2)", p_x2, em_x2, 3))

    # 3. Match fermé -> Moins de 2.5 buts ou Moins de 3.5 buts
    if xg_total <= 2.25 or p_u25 >= 0.67:
        scenario_candidates.append(("under_2.5", "under_2.5", "goals", "Moins de 2.5 buts", p_u25, em_u25, 3))
    if xg_total <= 2.45 or p_u35 >= 0.75:
        scenario_candidates.append(("under_3.5", "under_3.5", "goals", "Moins de 3.5 buts", p_u35, em_u35, 2))

    # 4. Match ouvert -> Plus de 2.5 buts ou Plus de 1.5 buts
    if xg_total >= 2.80 or p_o25 >= 0.67:
        scenario_candidates.append(("over_2.5", "over_2.5", "goals", "Plus de 2.5 buts", p_o25, em_o25, 2))
    if xg_total >= 2.55 or p_o15 >= 0.75:
        scenario_candidates.append(("over_1.5", "over_1.5", "goals", "Plus de 1.5 buts", p_o15, em_o15, 2))

    # 5. Match engagé sans nul -> Double Chance 12
    if p_draw <= 0.25 and p_12 >= 0.73:
        scenario_candidates.append(("double_chance_12", "dc_12", "dc12", "Une équipe gagne (12)", p_12, em_12, 2))

    best_pick = None
    best_score = (-1, -1.0)

    for mk, odd_key, family, label, prob, emoji, scenario_fit in scenario_candidates:
        proba_pct = prob * 100.0
        if emoji == "⚠️":
            continue
        if not passes_auth_proba_filter(proba_pct, mk):
            continue

        is_trap, trap_reason = has_pro_red_flags(md.analysis, md.league, mk, proba_pct)
        if is_trap:
            STATE["rejections"]["pro_trap"] += 1
            print(f"  🚩 Pro-Filter rejet ({md.home} vs {md.away} • {label}) : '{trap_reason}'")
            continue

        real_odd = md.real_odds.get(odd_key)
        if not real_odd or float(real_odd) <= 1.01:
            STATE["rejections"]["no_real_odds"] += 1
            continue

        odd = round(float(real_odd), 2)
        if odd < ODDS3_MIN_COTE_LEG:
            continue
        if not passes_value_filter(prob, odd, True, emoji):
            continue

        consensus_val = 2 if emoji == "🤝🤝" else (1 if emoji == "🤝" else 0)
        odd_fit = 1 if 1.24 <= odd <= 1.68 else 0
        score = (scenario_fit + consensus_val + odd_fit, round(proba_pct, 1))
        if score > best_score:
            best_score = score
            best_pick = {
                "event_id": md.event_id,
                "norm_key": f"{normalize_name(md.home)}__{normalize_name(md.away)}",
                "league": md.league,
                "home": md.home,
                "away": md.away,
                "kickoff": md.kickoff,
                "mk": mk,
                "type": label,
                "family": family,
                "odds": odd,
                "proba": proba_pct,
                "real_odd": odd,
                "consensus": emoji or "🤝",
                "ai": (md.analysis or "").strip(),
                "_md": md,
            }

    return best_pick

def _valid_pro_combo(combo) -> bool:
    if len({c["norm_key"] for c in combo}) != len(combo):
        return False
    if sum(1 for c in combo if c["family"] == "goals") > 1:
        return False
    if sum(1 for c in combo if c.get("mk") == "victoire_away") > 1:
        return False
    if not any(c["proba"] >= 71.0 for c in combo):
        return False
    return True

async def build_exclusive_vip_auth_coupon(enriched_pool: List[MatchData]) -> Tuple[Optional[Coupon], set]:
    vip_candidates = []
    for md in enriched_pool:
        if not md.home or not md.away or not is_match_today(md.kickoff):
            continue
        if not is_trusted_pro_league(md.league):
            continue
        if is_reserve_team(md.home) or is_reserve_team(md.away):
            continue
        if getattr(md, "stability_flag", "") == "unstable":
            STATE["rejections"]["unstable"] += 1
            continue

        safe_pick = _determine_match_scenario_pick(md)
        if safe_pick:
            vip_candidates.append(safe_pick)

    vip_candidates.sort(
        key=lambda x: (compute_stakes_score(x["league"], x["ai"]), x["proba"]),
        reverse=True,
    )
    top_pool = vip_candidates[:20]

    valid_combos = []
    for r in (2, 3):
        if len(top_pool) < r:
            continue
        for combo in itertools.combinations(top_pool, r):
            if not _valid_pro_combo(combo):
                continue
            tot_o = round(math.prod(c["odds"] for c in combo), 2)
            tot_p = math.prod(c["proba"] / 100.0 for c in combo)
            if ODDS3_AUTH_MIN_COTE <= tot_o <= ODDS3_AUTH_MAX_COTE and tot_p >= AUTH_MIN_COMBINED_PROB:
                if not coupon_passes_ev(tot_o, tot_p, has_real_odds=True):
                    continue
                avg_conf = sum(c["proba"] for c in combo) / len(combo)
                safety_rank = (tot_p, avg_conf)
                valid_combos.append((safety_rank, combo))

    valid_combos.sort(key=lambda item: item[0], reverse=True)
    for _, combo in valid_combos:
        is_clean = await _enrich_selected_legs_ai(combo)
        if not is_clean:
            continue
        locked_keys = {c["norm_key"] for c in combo}
        label = "Duo Haute Confiance" if len(combo) == 2 else "Pyramide Sécurisée"
        print(f"✅ VIP AUTH généré ({label}) : cote {round(math.prod(c['odds'] for c in combo), 2)}")
        return _build_auth_coupon(combo, f"Sélection Pro VIP • {label}", 96), locked_keys

    print("  ℹ️ AUTH VIP : Aucune combinaison haute confiance trouvée dans la plage 2.00 - 3.50 aujourd'hui.")
    return None, set()

# =====================================================================
# MOTEURS SECONDAIRES ADMIN (MT-FIN & MATCH NUL SUR GRANDS CHAMPIONNATS)
# =====================================================================
def _poisson_sample(lam):
    if lam <= 0: return 0
    L = math.exp(-lam); k, p = 0, 1.0
    while p > L and k < 20: k += 1; p *= random.random()
    return k - 1

def _binomial_sample(n, p): return sum(1 for _ in range(n) if random.random() < p)

def simulate_htft(l_h, l_a, n_sims=2000):
    counts = {}
    for _ in range(n_sims):
        hg, ag = _poisson_sample(l_h), _poisson_sample(l_a)
        ht_hg, ht_ag = _binomial_sample(hg, 0.45), _binomial_sample(ag, 0.45)
        key = f"{'V1' if ht_hg > ht_ag else ('X' if ht_hg == ht_ag else 'V2')}/{'V1' if hg > ag else ('X' if hg == ag else 'V2')}"
        counts[key] = counts.get(key, 0) + 1
    return {k: round(v / n_sims, 4) for k, v in counts.items()}

def build_mtft_coupon(pool, locked_keys: set):
    picks = []
    for md in pool:
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        if nkey in locked_keys: continue
        if not md.home or not md.away or not md.dixon_pred or not is_match_today(md.kickoff): continue
        if not is_trusted_pro_league(md.league) or is_reserve_team(md.home) or is_reserve_team(md.away): continue
        is_trap, _ = has_pro_red_flags(md.analysis, md.league)
        if is_trap: continue

        htft = simulate_htft(md.dixon_pred.get("lambda_home", 1.3), md.dixon_pred.get("lambda_away", 1.1))
        best_p, best_prob = None, 0.0
        for c in ("V1/V1", "V2/V2"):
            if htft.get(c, 0) > best_prob: best_prob, best_p = htft.get(c, 0), c
        if not best_p or best_prob < MTFIN_MIN_PROB: continue

        fav_mk = "victoire_home" if best_p == "V1/V1" else "victoire_away"
        fav_consensus, _, _, _, emoji = compute_probability(md, fav_mk)
        if emoji == "⚠️" or fav_consensus < 0.60:
            continue

        odd = round(max(1.55, (1.0 / best_prob) * 0.93), 2)
        pts = 15 + (10 if md.has_h2h else 0) + 10 + (30 if best_prob >= 0.55 else 20) + 15 + 10
        if pts < MTFIN_MIN_SCORE: continue
        picks.append({"md": md, "pick": best_p, "prob": best_prob, "score": pts, "odd": odd})

    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    top3 = picks[:3]
    if not top3:
        return None
    legs = [Selection(p["md"].event_id, p["md"].league, p["md"].home, p["md"].away, p["md"].kickoff, "MT-Fin", f"MT/Fin : {p['pick']}", p["odd"], p["prob"], None, -1.0, p["score"], htft_pick=p["pick"]) for p in top3]
    co = round(math.prod(s.odds for s in legs), 2)
    cp = round(math.prod(s.model_prob for s in legs), 4)
    return Coupon("MT-Fin", f"Sélection Pro • {len(legs)} matchs", "⏱", legs, co, cp, is_list=True)

def build_draw_coupon(pool, locked_keys: set):
    candidates = []
    for md in pool:
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        if nkey in locked_keys: continue
        if not md.home or not md.away or not is_match_today(md.kickoff): continue
        if not is_trusted_pro_league(md.league) or is_reserve_team(md.home) or is_reserve_team(md.away): continue
        is_trap, _ = has_pro_red_flags(md.analysis, md.league)
        if is_trap: continue

        prob, g, d, b, emoji = compute_probability(md, "match_nul")
        if prob < DRAW_MIN_PROB or (g > 0 and g < 0.25) or emoji == "⚠️": continue
        draw_odd = md.real_odds.get("draw")
        if not draw_odd or float(draw_odd) < 2.95:
            STATE["rejections"]["no_real_odds"] += 1
            continue
        draw_odd = round(float(draw_odd), 2)
        if (md.real_odds.get("home") and md.real_odds.get("home") < 1.85) or (md.real_odds.get("away") and md.real_odds.get("away") < 1.85): continue
        if not passes_value_filter(prob, draw_odd, True, emoji): continue
        pts = 30 + (25 if prob >= 0.31 else 18) + (20 if g >= 0.28 else 12) + 10 + (5 if md.has_h2h else 0) + 10
        if pts >= DRAW_MIN_SCORE: candidates.append(Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "Match Nul", "Match Nul (X)", draw_odd, prob, draw_odd, (prob*draw_odd)-1, pts))

    candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    top3 = candidates[:3]
    if not top3:
        return None
    co = round(math.prod(s.odds for s in top3), 2)
    cp = round(math.prod(s.model_prob for s in top3), 4)
    return Coupon("Match Nul", f"Sélection Pro • {len(top3)} nuls", "🤝", top3, co, cp, is_list=True)

# =====================================================================
# SCAN GLOBAL (DOUBLE SCAN + CONTRÔLE QUALITÉ + CACHE JOURNALIER)
# =====================================================================
async def scan(force: bool = False) -> bool:
    async with STATE["scan_lock"]:
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        if not force and load_daily_cache(today_str):
            print(f"📦 Cache journalier actif ({today_str}) : {len(STATE['coupons'])} coupons chargés sans rescan.")
            return False

        ws_matches, odds_status = await fetch_rapidodds_odds()
        
        bzz_preds = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try: bzz_preds = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except Exception as e: print(f"⚠️ Bzzoiro erreur : {e}")
        
        infer_odds = {}
        if bzz_preds and fetch_infer_odds:
            try: infer_odds = await fetch_infer_odds([{"event_id": b["event_id"], "home": b["home"], "away": b["away"], "kickoff": b.get("kickoff", "")} for b in bzz_preds])
            except: pass

        base_pool = []
        seen_pool_keys = set()
        for bz in (bzz_preds or []):
            base_odds = dict(infer_odds.get(bz["event_id"], {}))
            base_odds.update(bz.get("real_odds", {}))
            md = MatchData(bz["event_id"], bz.get("league", ""), bz["home"], bz["away"], bz.get("kickoff", ""), base_odds, bz.get("bzz_markets", {}), bz.get("bzz_confidence", 0))
            _ensure_dixon_pred(md)
            base_pool.append(md)
            seen_pool_keys.add(f"{normalize_name(md.home)}__{normalize_name(md.away)}")

        for ws in ws_matches:
            nk = f"{normalize_name(ws.home_team)}__{normalize_name(ws.away_team)}"
            if nk in seen_pool_keys:
                continue
            r_odds = {}
            if ws.home_odds: r_odds["home"] = ws.home_odds
            if ws.draw_odds: r_odds["draw"] = ws.draw_odds
            if ws.away_odds: r_odds["away"] = ws.away_odds
            if ws.over_2_5: r_odds["over_2.5"] = ws.over_2_5
            if ws.under_2_5: r_odds["under_2.5"] = ws.under_2_5
            md = MatchData(ws.match_id, ws.league, ws.home_team, ws.away_team, ws.commence_time, r_odds, {}, 70.0)
            _ensure_dixon_pred(md)
            base_pool.append(md)
            seen_pool_keys.add(nk)

        matched = 0
        if base_pool:
            matched = inject_all_real_odds(base_pool, ws_matches)
            print(f"⏰ Total matchs avec vraies cotes : {matched}/{len(base_pool)}")
            major_pool = []
            minor_dropped = 0
            for md in base_pool:
                if not is_match_today(md.kickoff) or not bool(md.real_odds):
                    continue
                if not is_trusted_pro_league(md.league):
                    minor_dropped += 1
                    continue
                major_pool.append(md)
            base_pool = major_pool
            print(f"🏆 Matchs retenus (Grands Championnats + Vraies Cotes) : {len(base_pool)} (écartés mineurs : {minor_dropped})")
            base_pool.sort(key=prescore_match, reverse=True)

        if ws_matches or matched > 0:
            odds_status = "live"
        STATE["odds_api_mode"] = odds_status

        for scan_attempt in range(2):
            STATE["rejections"] = {"value": 0, "ev_coupon": 0, "auth_proba": 0, "pro_trap": 0, "unstable": 0, "no_real_odds": 0, "minor_league": 0}
            enriched_pool = []
            if base_pool:
                print(f"🎯 Top {min(len(base_pool), DOUBLE_SCAN_POOL_SIZE)} grandes affiches sélectionnées pour Double Analyse Gemini (passe {scan_attempt + 1})")
                enriched_pool = await enrich_with_double_pass(base_pool[:GEMINI_MAX_CALLS_PER_SCAN])

            coupon_auth, locked_vip_keys = await build_exclusive_vip_auth_coupon(enriched_pool)
            if locked_vip_keys:
                print(f"🔒 Matchs verrouillés pour VIP AUTH : {locked_vip_keys}")

            ds_stats = STATE.get("double_scan_stats", {})
            degraded = (ds_stats.get("single", 0) + ds_stats.get("none", 0)) > 4 and ds_stats.get("stable", 0) < 4
            if scan_attempt == 0 and (degraded or not coupon_auth) and len(base_pool) >= 2:
                print("⏳ Scan automatique incomplet -> nouvelle passe propre dans 10s...")
                await asyncio.sleep(10)
                continue

            coupon_mtft = build_mtft_coupon(enriched_pool, locked_vip_keys) if enriched_pool else None
            coupon_draw = build_draw_coupon(enriched_pool, locked_vip_keys) if enriched_pool else None

            coupons = []
            if coupon_auth: coupons.append(coupon_auth)
            if coupon_mtft: coupons.append(coupon_mtft)
            if coupon_draw: coupons.append(coupon_draw)

            STATE["coupons"] = coupons
            STATE["last_scan_date"] = today_str
            STATE["last_scan"] = datetime.now(TZ).isoformat()
            STATE["debug"]["mode"] = odds_status
            if coupons:
                save_daily_cache(today_str)
            else:
                print("⚠️ Cache journalier non figé (0 coupon généré) : prochain /scan pourra réanalyser.")
            break

        print(f"──────── RÉSUMÉ ────────")
        print(f"Rejets filtres : {STATE['rejections']}")
        print(f"Coupons validés : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} matchs, cote {c.combined_odds}, proba {c.combined_prob*100:.1f}%, EV {c.combined_odds*c.combined_prob:.2f}")
        print("────────────────────────")
        return True

# =====================================================================
# FORMATAGE & HANDLERS
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
    mode = STATE.get("odds_api_mode", "unknown")
    mode_label = "🟢 LIVE" if mode == "live" else "🟡 IA CALIBRÉ" if mode == "fallback" else "⚪ ?"
    vip_coupons = [c for c in STATE["coupons"] if c.name == "Odds +3 AUTH"]
    if not vip_coupons:
        msg = f"<b>📭 Aucun coupon VIP fiable aujourd'hui</b>\n<i>(Critères Pro stricts : grands championnats & haute confiance)</i>"
    else:
        c = vip_coupons[0]
        msg = f"<b>🔔 TICKET VIP DU JOUR !</b>\n<i>Mode : {mode_label}</i>\n\n{c.emoji} <b>{c.name}</b> • {len(c.legs)} paris • Cote <b>{c.combined_odds}</b>"
    for cid in TARGETS:
        await safe_send(cid, msg)

def format_header():
    mode = STATE.get("odds_api_mode", "unknown")
    mode_icon = "🟢" if mode == "live" else "🟡" if mode == "fallback" else "⚪"
    mode_txt = "LIVE" if mode == "live" else "FALLBACK" if mode == "fallback" else "?"
    return f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n<i>{BRAND_TAGLINE}</i>\n<i>📅 {today_pretty()}</i>\n<i>{mode_icon} Mode : {mode_txt}</i>"

def format_selection_block(s, idx, show_analysis):
    lines = [f"<b>┌─ #{idx} {s.home} vs {s.away}</b>", f"│ 📅 <b>{format_kickoff(s.kickoff)}</b> • {s.league}", f"│ 🎯 <b>{s.pick_label}</b>", f"│ 📊 Confiance : <b>{s.model_prob*100:.1f}%</b> {prob_bar(s.model_prob)}"]
    if s.real_odd: lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b>")
    else: lines.append(f"│ 💰 Cote : <b>{s.odds}</b>")
    if show_analysis and s.analysis: lines.append(f"│ 🧠 <i>{clean_analysis_text(s.analysis, 560)}</i>")
    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)

def format_coupon(c):
    active_legs = [s for s in c.legs if is_match_not_started(s.kickoff)]
    if not active_legs:
        return "\n".join([
            format_header(), "",
            f"<b>{c.emoji} {c.name.upper()}</b>",
            f"<i>{c.subtitle}</i>", "",
            "⏳ <i>Tous les matchs de ce coupon ont déjà démarré.</i>", "",
            f"<i>{PUBLIC_FOOTER}</i>",
        ])

    if len(active_legs) < len(c.legs):
        disp_odds = round(math.prod(s.odds for s in active_legs), 2)
        disp_prob = round(math.prod(s.model_prob for s in active_legs), 4)
        disp_sub = re.sub(r"\d+\s+(sélections|matchs|nuls)", f"{len(active_legs)} \\1", c.subtitle)
    else:
        disp_odds = c.combined_odds
        disp_prob = c.combined_prob
        disp_sub = c.subtitle

    if c.is_list:
        lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{disp_sub}</i>", ""]
        for i, s in enumerate(active_legs, 1): lines.append(format_selection_block(s, i, False) + "\n")
    else:
        ev = disp_odds * disp_prob if (disp_odds > 0 and disp_prob > 0) else 0
        ev_badge = f"<b>┃ 💎 EV : {ev:.2f}</b>" if ev > 0 else ""
        lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{disp_sub}</i>", "", f"<b>┏━━━━━━━━━━━━━━━┓</b>", f"<b>┃ 🎫 Cote : {disp_odds}</b>", f"<b>┃ 📈 Réussite est. : {disp_prob*100:.1f}%</b>", ev_badge, f"<b>┗━━━━━━━━━━━━━━━┛</b>", ""]
        for i, s in enumerate(active_legs, 1): lines.append(format_selection_block(s, i, True) + "\n")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)

def _parse_date_range_args(parts: list[str]) -> tuple[Optional[datetime], Optional[datetime], int, int, str]:
    now = datetime.now(TZ)
    days = 3
    limit = 15
    start_dt = None
    end_dt = None

    def _make_dt(day_s: str, month_s: str, end_of_day: bool = False) -> datetime:
        d, mo = int(day_s), int(month_s)
        yr = now.year + (1 if mo < now.month and (now.month - mo) > 6 else 0)
        if end_of_day:
            return datetime(yr, mo, d, 23, 59, 59, tzinfo=TZ)
        return datetime(yr, mo, d, 0, 0, 0, tzinfo=TZ)

    args = parts[1:]
    if args:
        m_range = re.match(r"^(\d{1,2})/(\d{1,2})\s*[-–]\s*(\d{1,2})/(\d{1,2})$", args[0])
        if m_range:
            start_dt = _make_dt(m_range.group(1), m_range.group(2), end_of_day=False)
            end_dt = _make_dt(m_range.group(3), m_range.group(4), end_of_day=True)
            days = max(1, (end_dt.date() - start_dt.date()).days + 1)
            period_label = f"Du {start_dt.strftime('%d/%m')} au {end_dt.strftime('%d/%m')}"
            if len(args) >= 2 and args[1].isdigit():
                limit = max(3, min(30, int(args[1])))
            return start_dt, end_dt, days, limit, period_label

        if len(args) >= 2:
            m1 = re.match(r"^(\d{1,2})/(\d{1,2})$", args[0])
            m2 = re.match(r"^(\d{1,2})/(\d{1,2})$", args[1])
            if m1 and m2:
                start_dt = _make_dt(m1.group(1), m1.group(2), end_of_day=False)
                end_dt = _make_dt(m2.group(1), m2.group(2), end_of_day=True)
                days = max(1, (end_dt.date() - start_dt.date()).days + 1)
                period_label = f"Du {start_dt.strftime('%d/%m')} au {end_dt.strftime('%d/%m')}"
                if len(args) >= 3 and args[2].isdigit():
                    limit = max(3, min(30, int(args[2])))
                return start_dt, end_dt, days, limit, period_label

        m_single = re.match(r"^(\d{1,2})/(\d{1,2})$", args[0])
        if m_single:
            start_dt = _make_dt(m_single.group(1), m_single.group(2), end_of_day=False)
            end_dt = _make_dt(m_single.group(1), m_single.group(2), end_of_day=True)
            days = 1
            period_label = f"Le {start_dt.strftime('%d/%m')}"
            if len(args) >= 2 and args[1].isdigit():
                limit = max(3, min(30, int(args[1])))
            return start_dt, end_dt, days, limit, period_label

        if args[0].isdigit():
            val0 = int(args[0])
            if len(args) == 1 and val0 >= 8:
                limit = max(3, min(30, val0))
            else:
                days = max(1, min(7, val0))
        if len(args) >= 2 and args[1].isdigit():
            limit = max(3, min(30, int(args[1])))

    return None, None, days, limit, f"{days} jours"

# --- COMMANDES UTILISATEUR & ADMIN ---
@dp.message(Command("start"))
async def start_cmd(m: Message):
    kb = ADMIN_KEYBOARD if m.from_user.id == ADMIN_ID else USER_KEYBOARD
    if m.from_user.id == ADMIN_ID:
        msg = (
            f"{format_header()}\n\n<b>👋 Mode Admin</b>\n"
            f"• /semaine <code>24/11-25/11 15</code> — Top matchs sur dates précises\n"
            f"• /semaine <code>24/11 10</code> — Top matchs sur une date unique\n"
            f"• /semaine <code>[jours] [matchs]</code> — Top matchs sur N jours\n"
            f"• /genkey <code>[jours] [nb]</code> — Créer clé(s) VIP\n"
            f"• /vip — Liste des abonnés & clés\n"
            f"• /delvip <code>ID</code> — Révoquer un abonné\n"
            f"• /bilan — Vérifier scores & voir le ROI\n"
            f"• /scan • /rescan"
        )
    elif is_subscribed(m.from_user.id):
        msg = f"{format_header()}\n\n<b>🎉 Bienvenue VIP !</b>\n\n{get_subscription_message(m.from_user.id)}\n\nAppuie sur <b>{BTN_ODDS3}</b> ci-dessous."
    else:
        msg = f"{format_header()}\n\n<b>🔒 Accès VIP requis</b>\n\n{get_subscription_message(m.from_user.id)}"
    await safe_answer(m, msg, reply_markup=kb)

@dp.message(F.text == BTN_WEEKLY)
@dp.message(Command("semaine", "week", "top"))
async def semaine_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    if not analyze_weekly_top_matches:
        await safe_answer(m, "⚠️ Module <code>weekly_analyzer.py</code> introuvable sur le serveur.")
        return

    parts = (m.text or "").split()
    start_dt, end_dt, days, limit, period_label = _parse_date_range_args(parts)

    await safe_answer(
        m,
        f"⏳ <b>Analyse Pro Multi-Jours en cours...</b>\n"
        f"📅 Période : <b>{period_label}</b> • 🎯 Objectif : <b>Top {limit} matchs</b>\n"
        f"<i>Croisement Dixon-Coles + XGBoost + Bzzoiro + Gemini (patiente ~30-45 sec)...</i>",
    )

    picks = await analyze_weekly_top_matches(
        days=days,
        limit=limit,
        match_cls=MatchData,
        dixon_model=STATE.get("dixon_model"),
        fetch_odds_fn=fetch_rapidodds_odds,
        inject_odds_fn=inject_all_real_odds,
        prescore_fn=prescore_match,
        enrich_pass_fn=_enrich_single_pass,
        compute_prob_fn=compute_probability,
        fallback_bzz_fn=fetch_bzzoiro_predictions_direct,
        start_dt=start_dt,
        end_dt=end_dt,
    )

    filtered_picks = []
    for p in (picks or []):
        if not is_trusted_pro_league(p.get("league", "")):
            continue
        prob_pct = float(p.get("proba", 0))
        is_trap, _ = has_pro_red_flags(p.get("ai", ""), p.get("league", ""), "", prob_pct)
        if is_trap:
            continue
        prob = prob_pct / 100.0
        is_real = bool(p.get("is_real"))
        if not is_real:
            continue
        odd = round(float(p.get("odd", 0)), 2)
        if passes_value_filter(prob, odd, True):
            p["odd"] = odd
            p["_stakes"] = compute_stakes_score(p.get("league", ""), p.get("ai", ""))
            filtered_picks.append(p)
    filtered_picks.sort(key=lambda x: (x.get("_stakes", 0), x.get("proba", 0)), reverse=True)

    if not filtered_picks:
        await safe_answer(m, f"📭 Aucun match fiable des grands championnats trouvé pour la période <b>{period_label}</b>.")
        return

    total_odd = round(math.prod(p["odd"] for p in filtered_picks), 2)
    total_prob = round(math.prod(p["proba"] / 100.0 for p in filtered_picks), 4)
    total_ev = total_odd * total_prob
    lines = [
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>",
        f"<b>📅 TOP {len(filtered_picks)} MATCHS • {period_label.upper()}</b>",
        f"<b>🎫 COTE COMBINÉE : {total_odd} • 💎 EV : {total_ev:.2f}</b>",
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>",
        f"<i>Grands championnats uniquement • Haute confiance</i>\n",
    ]
    for i, p in enumerate(filtered_picks, 1):
        odd_tag = f"<b>{p['odd']}</b>"
        lines.append(
            f"<b>#{i} {p['home']} vs {p['away']}</b>\n"
            f"📅 {format_kickoff(p['kickoff'])} • {p['league']}\n"
            f"🎯 <b>{p['pick']}</b> | 📊 <b>{p['proba']:.1f}%</b> | 💰 {odd_tag}\n"
            f"🧠 <i>{p['ai']}</i>\n"
        )
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    await safe_answer(m, "\n".join(lines))

@dp.message(Command("genkey", "gen", "newkey"))
async def genkey_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    parts = (m.text or "").split()
    days = 30
    count = 1
    try:
        if len(parts) >= 2:
            days = max(1, min(3650, int(parts[1])))
        if len(parts) >= 3:
            count = max(1, min(50, int(parts[2])))
    except ValueError:
        await safe_answer(m, "⚠️ Format : <code>/genkey 30</code> ou <code>/genkey 30 5</code>")
        return

    keys = create_access_keys(days=days, count=count)
    keys_fmt = "\n".join(f"• <code>{k}</code>" for k in keys)
    await safe_answer(
        m,
        f"🔑 <b>{count} clé(s) VIP générée(s) ({days} jours) :</b>\n\n{keys_fmt}\n\n"
        f"<i>L'abonné n'a qu'à coller la clé dans le bot pour activer son accès.</i>",
    )

@dp.message(Command("vip", "users", "keys"))
async def vip_list_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    now = datetime.now(TZ)
    active_lines = []
    for uid, info in STATE["users"].items():
        try:
            exp = datetime.fromisoformat(info.get("expires_at", ""))
            if exp > now:
                uname = f"@{info['username']}" if info.get("username") else info.get("full_name", "Sans nom")
                active_lines.append(f"• <b>{uname}</b> (<code>{uid}</code>) → {exp.strftime('%d/%m/%Y %H:%M')}")
        except Exception:
            pass

    unused_keys = [
        f"• <code>{k}</code> ({v.get('days', 30)}j)"
        for k, v in STATE["keys"].items()
        if not v.get("used")
    ]

    txt = f"<b>👥 ABONNÉS VIP ACTIFS ({len(active_lines)}) :</b>\n"
    txt += ("\n".join(active_lines) if active_lines else "<i>Aucun abonné actif.</i>")
    txt += f"\n\n<b>🔑 CLÉS DISPONIBLES ({len(unused_keys)}) :</b>\n"
    txt += ("\n".join(unused_keys[:25]) if unused_keys else "<i>Aucune clé libre (/genkey pour en créer).</i>")
    await safe_answer(m, txt)

@dp.message(Command("delvip", "revoke"))
async def delvip_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    parts = (m.text or "").split()
    if len(parts) < 2:
        await safe_answer(m, "⚠️ Format : <code>/delvip ID_TELEGRAM</code>")
        return
    uid = parts[1].strip()
    if uid in STATE["users"]:
        del STATE["users"][uid]
        save_json(USERS_DB, STATE["users"])
        await safe_answer(m, f"🗑️ Abonnement révoqué pour <code>{uid}</code>.")
    else:
        await safe_answer(m, f"❌ Utilisateur <code>{uid}</code> introuvable.")

@dp.message(F.text == BTN_ODDS3)
async def b_odds3(m: Message):
    if m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await safe_answer(
            m,
            f"<b>🔒 Accès VIP requis</b>\n\n{get_subscription_message(m.from_user.id)}",
            reply_markup=USER_KEYBOARD,
        )
        return
    c = next((x for x in STATE["coupons"] if x.name == "Odds +3 AUTH"), None)
    if c:
        await safe_answer(m, format_coupon(c))
    else:
        await safe_answer(m, "📭 Aucun ticket VIP aujourd'hui : aucun match des grands championnats ne passe tous nos filtres de sécurité.")

@dp.message(F.text == BTN_MTFT)
async def b_mtft(m: Message):
    if m.from_user.id == ADMIN_ID:
        c = next((x for x in STATE["coupons"] if x.name == "MT-Fin"), None)
        if c: await safe_answer(m, format_coupon(c))
        else: await safe_answer(m, "📭 Aucun coupon MT-Fin disponible.")

@dp.message(F.text == BTN_DRAW)
async def b_draw(m: Message):
    if m.from_user.id == ADMIN_ID:
        c = next((x for x in STATE["coupons"] if x.name == "Match Nul"), None)
        if c: await safe_answer(m, format_coupon(c))
        else: await safe_answer(m, "📭 Aucun coupon Match Nul disponible.")

@dp.message(F.text == BTN_BILAN)
@dp.message(Command("bilan", "roi", "checkscores"))
async def b_bilan(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    await safe_answer(m, "⏳ <i>Vérification des scores (Bzzoiro + ESPN) en cours...</i>")
    await verify_pending_scores(notify=False)
    await safe_answer(m, format_bilan_report())

@dp.message(F.text == "⚙️ Debug")
async def b_debug(m: Message):
    if m.from_user.id == ADMIN_ID:
        await safe_answer(
            m,
            f"⚙️ <b>Debug</b>\n"
            f"• Mode cotes : <code>{STATE.get('odds_api_mode', '?')}</code>\n"
            f"• Double Scan : <code>{STATE.get('double_scan_status', '?')}</code>\n"
            f"• Stats : <code>{STATE.get('double_scan_stats', {})}</code>\n"
            f"• Rejets : <code>{STATE.get('rejections', {})}</code>\n"
            f"• Dernier check scores : <code>{STATE.get('last_score_check') or 'N/A'}</code>\n"
            f"• Clés libres : <b>{sum(1 for v in STATE['keys'].values() if not v.get('used'))}</b>",
        )

@dp.message(F.text == BTN_SCAN)
async def b_scan(m: Message):
    if m.from_user.id == ADMIN_ID:
        await safe_answer(m, "⏳ Vérification des coupons...")
        ran_fresh = await scan(force=False)
        if ran_fresh:
            record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
            await broadcast_after_scan()
        else:
            await safe_answer(m, f"📦 <b>Cache journalier actif</b> ({len(STATE['coupons'])} coupon(s) figé(s) pour aujourd'hui).\n<i>Utilise /rescan pour forcer une nouvelle double analyse.</i>")

@dp.message(Command("scan"))
async def scan_cmd(m: Message):
    if m.from_user.id == ADMIN_ID:
        await safe_answer(m, "⏳ Vérification des coupons...")
        ran_fresh = await scan(force=False)
        if ran_fresh:
            record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
            await broadcast_after_scan()
        else:
            await safe_answer(m, f"📦 <b>Cache journalier actif</b> ({len(STATE['coupons'])} coupon(s) figé(s) pour aujourd'hui).\n<i>Utilise /rescan pour forcer une nouvelle double analyse.</i>")

@dp.message(Command("rescan"))
async def rescan_cmd(m: Message):
    if m.from_user.id == ADMIN_ID:
        await safe_answer(m, "🔄 <b>Rescan forcé en cours</b> (Analyse Pro + mise à jour du cache)...")
        await scan(force=True)
        record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
        await broadcast_after_scan()

# --- ACTIVATION AUTOMATIQUE QUAND UN ABONNÉ COLLE SA CLÉ ---
@dp.message(F.text)
async def handle_key_redeem(m: Message):
    raw = (m.text or "").strip()
    if not raw or raw.startswith("/"):
        return
    candidate = raw.upper()
    if candidate in STATE["keys"] or candidate.startswith("VIP-"):
        ok, reply = redeem_access_key(m.from_user, candidate)
        kb = ADMIN_KEYBOARD if m.from_user.id == ADMIN_ID else USER_KEYBOARD
        await safe_answer(m, reply, reply_markup=kb)
        if ok and ADMIN_ID and m.from_user.id != ADMIN_ID:
            uname = f"@{m.from_user.username}" if m.from_user.username else m.from_user.full_name
            await safe_send(
                ADMIN_ID,
                f"🔔 <b>Nouvelle activation VIP !</b>\n👤 {uname} (<code>{m.from_user.id}</code>)\n🔑 Clé : <code>{candidate}</code>",
            )
    elif m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await safe_answer(
            m,
            f"<b>🔒 Accès VIP requis</b>\n\n{get_subscription_message(m.from_user.id)}",
            reply_markup=USER_KEYBOARD,
        )

async def daily_broadcast():
    try:
        await verify_pending_scores(notify=True)
        await scan(force=True)
        record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"❌ Erreur daily_broadcast : {e}")
    await broadcast_after_scan()

async def periodic_score_check():
    try:
        res = await verify_pending_scores(notify=True)
        if res.get("updated_legs"):
            print(f"⚽ Score check : {res['updated_legs']} match(s) mis à jour, {res['settled_coupons']} coupon(s) réglé(s)")
    except Exception as e:
        print(f"⚠️ Erreur periodic_score_check : {e}")

@asynccontextmanager
async def lifespan(app):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        print(f"🤖 Bot Telegram connecté : @{me.username} (ID: {me.id})")
    except Exception as e:
        print(f"❌ ERREUR CONNEXION TELEGRAM : {e}")

    STATE["tracker"] = load_json(LOCAL_DB, {"coupons": []})
    STATE["keys"] = load_json(KEYS_DB, {})
    STATE["users"] = load_json(USERS_DB, {})
    STATE["ready"] = True
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    if load_daily_cache(today_str):
        print(f"📦 Cache du jour restauré au démarrage ({len(STATE['coupons'])} coupons)")
    if train_model_if_needed:
        asyncio.create_task(asyncio.to_thread(train_model_if_needed))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ), id="daily")
    scheduler.add_job(periodic_score_check, IntervalTrigger(minutes=45), id="score_check")
    scheduler.start()

    async def run_polling():
        try:
            print("📡 Démarrage de l'écoute Telegram (polling)...")
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ CRASH POLLING TELEGRAM : {e}")

    bot_task = asyncio.create_task(run_polling())

    async def bootstrap():
        try:
            if download_and_train:
                STATE["dixon_model"] = await download_and_train()
                print("✅ Dixon chargé")
            await asyncio.sleep(5)
            await periodic_score_check()
            curr_today = datetime.now(TZ).strftime("%Y-%m-%d")
            if not load_daily_cache(curr_today):
                await daily_broadcast()
                STATE["last_scan_date"] = curr_today
        except Exception as e:
            print(f"⚠️ Erreur bootstrap : {e}")

    asyncio.create_task(bootstrap())
    yield
    scheduler.shutdown(wait=False)
    bot_task.cancel()

app = FastAPI(title="Value Bot", lifespan=lifespan)

@app.get("/")
async def health():
    return {"status": "ok", "bot": BRAND_NAME, "cached_date": STATE.get("last_scan_date", "")}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
