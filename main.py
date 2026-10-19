from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import string
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

try:
    from gemini_predictor import predict_match_probas
except Exception as e:
    predict_match_probas = None
    print(f"❌ Import gemini_predictor: {e}")

try:
    from football_data import get_fixture_result
except Exception as e:
    get_fixture_result = None
    print(f"⚠️ Import get_fixture_result: {e}")

try:
    from api_football import find_team_id, get_team_btts_stats
    _API_FOOTBALL_BTTS_AVAILABLE = True
except Exception as e:
    find_team_id = None
    get_team_btts_stats = None
    _API_FOOTBALL_BTTS_AVAILABLE = False
    print(f"⚠️ Import api_football (stats BTTS): {e}")

fetch_all_today_fixtures = None
get_corners_cards = None
get_full_context_api_football = None

try:
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception as e:
    enrich_match_with_bzzoiro = None
    print(f"❌ Import bzzoiro_data: {e}")

try:
    from bzzoiro_predictions import get_bzzoiro_best_matches
except Exception as e:
    get_bzzoiro_best_matches = None
    print(f"❌ Import bzzoiro_predictions: {e}")

try:
    from bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
except Exception as e:
    fetch_bzzoiro_predictions_direct = None
    print(f"❌ Import bzzoiro_predictions_direct: {e}")

try:
    from dixon_coles import download_and_train
except Exception as e:
    download_and_train = None
    print(f"❌ Import dixon_coles: {e}")

compute_corners_probs = None
compute_cards_probs = None

# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

try:
    ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except ValueError:
    ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "14"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")

BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • H2H + Gemini")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

MAX_MATCHES_TO_ANALYZE = int(os.getenv("MAX_MATCHES_TO_ANALYZE", "50"))
MAX_MATCHES_TO_ENRICH = int(os.getenv("MAX_MATCHES_TO_ENRICH", "20"))

BZZ_MIN_CONFIDENCE = float(os.getenv("BZZ_MIN_CONFIDENCE", "75.0"))
# === CORRECTION : Bzzoiro confiance baissée à 65% ===
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))
BZZ_MAX_MATCHES = int(os.getenv("BZZ_MAX_MATCHES", "15"))

# === TICKET SÉCURISÉ ===
SAFE_MIN_PROB = float(os.getenv("SAFE_MIN_PROB", "0.75"))
SAFE_MAX_PROB = float(os.getenv("SAFE_MAX_PROB", "0.92"))
SAFE_MIN_LEG_ODDS = float(os.getenv("SAFE_MIN_LEG_ODDS", "1.10"))
SAFE_MAX_LEG_ODDS = float(os.getenv("SAFE_MAX_LEG_ODDS", "1.35"))
SAFE_MIN_TOTAL = 2.0
SAFE_MAX_TOTAL = 2.5
SAFE_MAX_LEGS = 6

# === TICKET ÉQUILIBRÉ ===
BAL_MIN_PROB = float(os.getenv("BAL_MIN_PROB", "0.60"))
BAL_MAX_PROB = float(os.getenv("BAL_MAX_PROB", "0.88"))
BAL_MIN_LEG_ODDS = float(os.getenv("BAL_MIN_LEG_ODDS", "1.30"))
BAL_MAX_LEG_ODDS = float(os.getenv("BAL_MAX_LEG_ODDS", "2.00"))
BAL_MIN_TOTAL = 2.0
BAL_MAX_TOTAL = 6.0

# === TICKET VALUE ===
VALUE_MIN_PROB = float(os.getenv("VALUE_MIN_PROB", "0.85"))
VALUE_MAX_PROB = float(os.getenv("VALUE_MAX_PROB", "0.92"))
VALUE_FALLBACK_MIN_PROB = float(os.getenv("VALUE_FALLBACK_MIN_PROB", "0.80"))
VALUE_MIN_LEG_ODDS = float(os.getenv("VALUE_MIN_LEG_ODDS", "1.10"))
VALUE_MAX_LEG_ODDS = float(os.getenv("VALUE_MAX_LEG_ODDS", "1.35"))
VALUE_TOP_N = int(os.getenv("VALUE_TOP_N", "20"))

# === TICKET BBTS+2.5 ===
BTTS_TOP_N = 3
BTTS_MIN_PROB = 0.50          # Seuil à 50% (compromis)
BTTS_MIN_DIXON = 0.15         # Dixon optionnel si ligue inconnue
BTTS_STATS_MIN_TEAM = 0.50
BTTS_STATS_MIN_AVG = 0.55
BTTS_STATS_MIN_CONCEDED = 0.60

TELEGRAM_MAX_LEN = 3800

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY manquant")
if not ADMIN_ID:
    raise RuntimeError("TELEGRAM_ADMIN_ID manquant ou invalide")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

SOCCER_KEYS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_efl_champ", "soccer_efl_league_one", "soccer_efl_league_two",
    "soccer_spain_la_liga2", "soccer_italy_serie_b",
    "soccer_germany_bundesliga2", "soccer_france_ligue_two",
    "soccer_netherlands_eerste_divisie",
    "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga",
    "soccer_belgium_first_div", "soccer_turkey_super_league",
    "soccer_greece_super_league", "soccer_scotland_premiership",
    "soccer_denmark_superliga", "soccer_norway_eliteserien",
    "soccer_sweden_allsvenskan", "soccer_poland_ekstraklasa",
    "soccer_switzerland_superleague", "soccer_austria_bundesliga",
    "soccer_romania_liga_i", "soccer_ukraine_premier_league",
    "soccer_brazil_campeonato", "soccer_brazil_serie_b",
    "soccer_argentina_primera_division",
    "soccer_chile_campeonato", "soccer_colombia_primera_a",
    "soccer_usa_mls", "soccer_mexico_ligamx",
    "soccer_ecuador_liga_pro", "soccer_peru_primera_division",
    "soccer_uruguay_primera_division",
    "soccer_japan_j_league", "soccer_korea_kleague1",
    "soccer_australia_aleague",
]

# =========================================================
# FILTRE LIGUES OBSCURES
# =========================================================
TOP_LEAGUES_KEYWORDS = {
    "premier league", "epl", "champions league", "europa league",
    "conference league", "uefa champions", "uefa europa",
    "uefa conference", "fa cup", "carabao cup",
    "la liga", "laliga", "primera division spain", "copa del rey",
    "serie a", "coppa italia",
    "bundesliga", "dfb pokal",
    "ligue 1", "ligue1", "coupe de france",
}

def is_obscure_league(league_name: str) -> bool:
    if not league_name: return True
    low = league_name.lower().strip()
    for kw in TOP_LEAGUES_KEYWORDS:
        if kw in low: return False
    return True

# =========================================================
# MATCH QUALITY SCORING
# =========================================================
WEIGHT_LEAGUE = 10.0
WEIGHT_ODDS_COUNT = 2.0
WEIGHT_FAVORITE_CLARITY = 8.0
WEIGHT_TIME_PROXIMITY = 3.0
MIN_MATCH_SCORE = 3.0

def get_league_score(league_name: str) -> int:
    return 5

def calculate_match_score(fx: dict) -> float:
    score = 0.0
    real_odds = fx.get("real_odds", {}) or {}
    has_odds = fx.get("has_odds", False)
    score += get_league_score(fx.get("league", "")) * (WEIGHT_LEAGUE / 10.0)
    if not has_odds:
        return -1.0
    markets_count = sum(1 for v in real_odds.values() if v is not None)
    score += markets_count * WEIGHT_ODDS_COUNT
    home = real_odds.get("home"); draw = real_odds.get("draw"); away = real_odds.get("away")
    if home and away:
        min_odd = min(home, away, draw or 999)
        if min_odd < 1.60: score += WEIGHT_FAVORITE_CLARITY
        elif min_odd < 1.90: score += WEIGHT_FAVORITE_CLARITY * 0.6
        elif min_odd < 2.30: score += WEIGHT_FAVORITE_CLARITY * 0.3
    dt = parse_dt_safe(fx.get("kickoff", ""))
    if dt:
        hours_until = (dt - datetime.now(TZ)).total_seconds() / 3600
        if 1 <= hours_until <= 6: score += WEIGHT_TIME_PROXIMITY
        elif hours_until <= 12: score += WEIGHT_TIME_PROXIMITY * 0.7
        elif hours_until <= 24: score += WEIGHT_TIME_PROXIMITY * 0.3
    if STATE.get("dixon_model"):
        try:
            h_key = STATE["dixon_model"]._find_team(fx.get("home", ""))
            a_key = STATE["dixon_model"]._find_team(fx.get("away", ""))
            if h_key and a_key:
                score += 15.0; fx["dixon_available"] = True
            elif h_key or a_key:
                score += 5.0; fx["dixon_available"] = False
            else:
                fx["dixon_available"] = False
        except Exception:
            fx["dixon_available"] = False
    bzz_conf = fx.get("bzz_confidence", 0)
    if bzz_conf > 0: score += bzz_conf * 0.3
    return score

def filter_and_rank_matches(matches: list[dict]) -> list[dict]:
    scored = []
    for fx in matches:
        s = calculate_match_score(fx)
        fx["_score"] = s
        if s >= MIN_MATCH_SCORE:
            scored.append(fx)
    scored.sort(key=lambda m: m["_score"], reverse=True)
    return scored

# =========================================================
# FUSION SOURCES
# =========================================================
W_GEMINI = 0.35; W_DIXON = 0.25; W_BZZOIRO = 0.40
W_GEMINI_BTTS = 0.20; W_DIXON_BTTS = 0.25; W_BZZOIRO_BTTS = 0.30; W_STATS_BTTS = 0.25

ALL_MARKETS = [
    "victoire_home", "match_nul", "victoire_away",
    "over_2.5", "under_2.5", "over_3.5",
    "btts_yes", "btts_no", "btts_over_25",
    "over_8.5_corners", "over_9.5_corners", "over_10.5_corners",
    "over_2.5_cartons", "over_3.5_cartons",
]

def combine_three_sources(gemini_markets: dict, dixon_pred: Optional[dict], bzz_markets: dict) -> dict:
    result = {}
    g = {}
    for k in ALL_MARKETS:
        v = gemini_markets.get(k)
        if v is not None:
            try: g[k] = max(0.0, min(1.0, float(v) / 100.0))
            except (ValueError, TypeError): pass
    d = {}
    if dixon_pred:
        d = {
            "victoire_home": dixon_pred.get("p_home", 0),
            "match_nul": dixon_pred.get("p_draw", 0),
            "victoire_away": dixon_pred.get("p_away", 0),
            "over_2.5": dixon_pred.get("over_2.5", 0),
            "under_2.5": dixon_pred.get("under_2.5", 0),
            "over_3.5": dixon_pred.get("over_3.5", 0),
            "btts_yes": dixon_pred.get("btts_yes", 0),
            "btts_no": dixon_pred.get("btts_no", 0),
            "btts_over_25": dixon_pred.get("btts_over_25", 0),
        }
    b = {}
    if bzz_markets:
        for k in ALL_MARKETS:
            v = bzz_markets.get(k)
            if v is not None:
                try: b[k] = max(0.0, min(1.0, float(v) / 100.0))
                except (ValueError, TypeError): pass
    all_keys = set(g.keys()) | set(d.keys()) | set(b.keys())
    for k in all_keys:
        total_weight = 0.0; total_value = 0.0
        if k in g: total_value += W_GEMINI * g[k]; total_weight += W_GEMINI
        if k in d and d[k] > 0: total_value += W_DIXON * d[k]; total_weight += W_DIXON
        if k in b: total_value += W_BZZOIRO * b[k]; total_weight += W_BZZOIRO
        if total_weight > 0: result[k] = total_value / total_weight
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = result["victoire_home"] + result["match_nul"]
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = result["victoire_home"] + result["victoire_away"]
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = result["match_nul"] + result["victoire_away"]
    return result

def compute_stats_btts_prob(home_stats: dict, away_stats: dict) -> float:
    if not home_stats or not away_stats:
        return 0.0
    h = home_stats.get("btts_over_25_pct", 0)
    a = away_stats.get("btts_over_25_pct", 0)
    if h <= 0 or a <= 0: return 0.0
    base = (h + a) / 2.0
    h_conc = home_stats.get("conceded_pct", 0)
    a_conc = away_stats.get("conceded_pct", 0)
    if h_conc >= 0.7 and a_conc >= 0.7: base += 0.03
    h_score = home_stats.get("scored_pct", 0)
    a_score = away_stats.get("scored_pct", 0)
    if h_score >= 0.7 and a_score >= 0.7: base += 0.03
    return min(0.95, round(base, 4))

def compute_agreement_three(gemini_markets: dict, dixon_pred: Optional[dict], bzz_markets: dict, market_key: str) -> tuple[str, float]:
    map_g = {k: k for k in ALL_MARKETS}
    map_d = {
        "victoire_home": "p_home", "match_nul": "p_draw", "victoire_away": "p_away",
        "over_2.5": "over_2.5", "under_2.5": "under_2.5", "over_3.5": "over_3.5",
        "btts_yes": "btts_yes", "btts_no": "btts_no", "btts_over_25": "btts_over_25",
    }
    probs = {}
    gk = map_g.get(market_key)
    if gk:
        gv = gemini_markets.get(gk)
        if gv is not None:
            try: probs["g"] = float(gv) / 100.0
            except (ValueError, TypeError): pass
    if dixon_pred:
        dk = map_d.get(market_key)
        if dk:
            dv = dixon_pred.get(dk, 0)
            if dv > 0: probs["d"] = dv
    if bzz_markets:
        bv = bzz_markets.get(market_key)
        if bv is not None:
            try: probs["b"] = float(bv) / 100.0
            except (ValueError, TypeError): pass
    if len(probs) < 2: return ("", 0.0)
    values = list(probs.values())
    delta = max(values) - min(values)
    if len(probs) == 3 and delta <= 0.10: return ("🤝🤝", delta)
    if delta <= 0.10: return ("🤝", delta)
    if delta >= 0.15: return ("⚠️", delta)
    return ("", delta)

# =========================================================
# MODELS
# =========================================================
@dataclass
class Candidate:
    event_id: str
    league: str
    home: str
    away: str
    kickoff: str
    market: str
    pick_label: str
    odds: float
    prob: float
    edge: float
    real_odd: Optional[float]
    prob_gemini: float
    prob_dixon: float
    prob_bzzoiro: float
    agreement_emoji: str
    agreement_delta: float
    has_context: bool
    bzz_confidence: float
    analysis: str = ""
    advice: str = ""
    advice_confidence: int = 0
    advices: list = None
    score_exact_home: int = -1
    score_exact_away: int = -1
    score_exact_prob: float = 0.0
    score_exact_source: str = ""
    prob_stats: float = 0.0
    stats_home: dict = None
    stats_away: dict = None

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
    edge: float
    bookmaker: str
    source: str = "gemini"
    real_odd: Optional[float] = None
    analysis: str = ""
    advice: str = ""
    advice_confidence: int = 0
    advices: list = None
    has_context: bool = False
    prob_gemini: float = 0.0
    prob_dixon: float = 0.0
    prob_bzzoiro: float = 0.0
    agreement_emoji: str = ""
    agreement_delta: float = 0.0
    bzz_confidence: float = 0.0
    score_exact_home: int = -1
    score_exact_away: int = -1
    score_exact_prob: float = 0.0
    score_exact_source: str = ""
    prob_stats: float = 0.0
    stats_home: dict = None
    stats_away: dict = None

    def final_prob(self) -> float:
        return self.model_prob

@dataclass
class Coupon:
    name: str
    subtitle: str
    emoji: str
    legs: list[Selection]
    combined_odds: float
    combined_prob: float
    is_list: bool = False

# =========================================================
# GLOBALS & KEYBOARDS
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_SAFE = "🔒 Sécurisé"
BTN_BAL = "⚖️ Équilibré"
BTN_VAR = "💎 Value"
BTN_BTTS = "🎯 BBTS+2.5"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"
BTN_ACTIVATE = "🔑 Activer mon abonnement"

USER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_VAR), KeyboardButton(text=BTN_BTTS)],
        [KeyboardButton(text=BTN_BILAN), KeyboardButton(text=BTN_ACTIVATE)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choisis un ticket…",
)

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_VAR), KeyboardButton(text=BTN_BTTS)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Debug")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Admin: Choisis une action…",
)

STATE = {
    "ready": False, "candidates": [], "selections": [], "coupons": [],
    "last_scan": None, "debug": {}, "tracker": {"coupons": []}, "keys": {}, "users": {},
    "scan_lock": asyncio.Lock(), "dixon_model": None,
}

TARGETS = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try: TARGETS.append(int(raw))
        except ValueError: TARGETS.append(raw)

# =========================================================
# HELPERS
# =========================================================
def today_pretty():
    months = {1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
              7: "juillet", 8: "août", 9: "septembre", 10: "octobre",
              11: "novembre", 12: "décembre"}
    d = datetime.now(TZ)
    return f"{d.day} {months[d.month]} {d.year}"

def normalize_name(s: str) -> str:
    if not s: return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_dt_safe(s) -> Optional[datetime]:
    if not s: return None
    if isinstance(s, (int, float)):
        try: return datetime.fromtimestamp(int(s), tz=ZoneInfo("UTC")).astimezone(TZ)
        except Exception: return None
    s = str(s).strip()
    s = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception: return None

def kickoff_local(iso_str) -> str:
    dt = parse_dt_safe(iso_str)
    return dt.strftime("%H:%M") if dt else "?"

def is_today_upcoming(iso_str) -> bool:
    dt = parse_dt_safe(iso_str)
    if not dt: return False
    now = datetime.now(TZ)
    if dt.date() != now.date(): return False
    return dt > now + timedelta(minutes=5)

def prob_bar(p: float) -> str:
    if p >= 0.80: return "🟢🟢🟢"
    if p >= 0.72: return "🟢🟢⚪"
    if p >= 0.65: return "🟢⚪⚪"
    return "⚪⚪⚪"

def confidence_bar(pct: int) -> str:
    if pct >= 80: return "🟢🟢🟢"
    if pct >= 70: return "🟢🟢⚪"
    if pct >= 60: return "🟢⚪⚪"
    return "⚪⚪⚪"

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

# =========================================================
# CLÉS / ABONNEMENTS
# =========================================================
def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except Exception: pass

def is_subscribed(user_id: int) -> bool:
    uid = str(user_id)
    if uid not in STATE["users"]: return False
    exp = STATE["users"][uid].get("expires_at")
    if not exp: return False
    try: return datetime.now(TZ) < datetime.fromisoformat(exp)
    except Exception: return False

def get_subscription_message(user_id: int) -> str:
    if is_subscribed(user_id):
        exp = STATE["users"][str(user_id)]["expires_at"]
        return f"✅ Abonnement actif jusqu'au {datetime.fromisoformat(exp).strftime('%d/%m/%Y à %H:%M')}."
    return "❌ Abonnement expiré ou inexistant.\nContactez : @hermann1123"

# =========================================================
# TRACKER
# =========================================================
def load_tracker() -> dict:
    try:
        with open(LOCAL_DB, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {"coupons": []}

def save_tracker(data: dict):
    try:
        with open(LOCAL_DB, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except Exception: pass

def load_used_today() -> set:
    tracker = STATE["tracker"]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    u = tracker.get("used_today", {})
    if u.get("date") != today: return set()
    return set(u.get("ids", []))

def save_used_today(ids: set):
    tracker = STATE["tracker"]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    existing = tracker.get("used_today", {})
    merged = set(existing.get("ids", [])) | ids if existing.get("date") == today else ids
    tracker["used_today"] = {"date": today, "ids": list(merged)}
    save_tracker(tracker)

def mark_picks_used(selections: list):
    if selections: save_used_today({s.event_id for s in selections})

def record_coupons(coupons: list[Coupon], date_str: str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing: continue
        legs_data = [{"event_id": s.event_id, "home": s.home, "away": s.away,
                      "market": s.market, "pick_label": s.pick_label,
                      "odds": s.odds, "kickoff": s.kickoff, "result": "pending"} for s in c.legs]
        tracker["coupons"].append({"id": cid, "date": date_str, "name": c.name,
                                    "combined_odds": c.combined_odds,
                                    "legs": legs_data, "status": "pending"})
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)

async def settle_coupons():
    print("⚠️ settle_coupons désactivé")

# =========================================================
# THE ODDS API
# =========================================================
async def fetch_odds_api_league(client: httpx.AsyncClient, sport_key: str) -> list[dict]:
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {"apiKey": ODDS_API_KEY, "regions": "eu",
              "markets": "h2h,totals", "oddsFormat": "decimal"}
    try:
        r = await client.get(url, params=params, timeout=30.0)
        if r.status_code >= 400: return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []

async def fetch_all_odds_events() -> list[dict]:
    all_events: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for key in SOCCER_KEYS:
            events = await fetch_odds_api_league(client, key)
            all_events.extend(events)
            await asyncio.sleep(0.2)
    print(f"💰 Odds API : {len(all_events)} events ({len(SOCCER_KEYS)} ligues)")
    return all_events

def extract_real_odds(ev: dict) -> dict:
    result = {"home": None, "draw": None, "away": None,
              "over_2.5": None, "under_2.5": None,
              "btts_yes": None, "btts_no": None}
    home = ev.get("home_team", ""); away = ev.get("away_team", "")
    for book in ev.get("bookmakers", []) or []:
        for market in book.get("markets", []) or []:
            mk = market.get("key", "")
            for out in market.get("outcomes", []) or []:
                try: price = float(out.get("price", 0))
                except (ValueError, TypeError): continue
                if price <= 1.01: continue
                name = out.get("name", "")
                if mk == "h2h":
                    if name == home and result["home"] is None: result["home"] = price
                    elif name == away and result["away"] is None: result["away"] = price
                    elif "draw" in name.lower() and result["draw"] is None: result["draw"] = price
                elif mk == "totals" and out.get("point") == 2.5:
                    if "over" in name.lower() and result["over_2.5"] is None: result["over_2.5"] = price
                    elif "under" in name.lower() and result["under_2.5"] is None: result["under_2.5"] = price
                elif mk == "btts":
                    if "yes" in name.lower() and result["btts_yes"] is None: result["btts_yes"] = price
                    elif "no" in name.lower() and result["btts_no"] is None: result["btts_no"] = price
    return result

def build_matches_from_odds(odds_events: list[dict]) -> list[dict]:
    matches = []
    for ev in odds_events:
        h = ev.get("home_team", ""); a = ev.get("away_team", ""); kick = ev.get("commence_time", "")
        if not h or not a: continue
        if not is_today_upcoming(kick): continue
        matches.append({
            "event_id": ev.get("id", ""), "home": h, "away": a,
            "league": ev.get("sport_title", ""), "kickoff": kick,
            "has_odds": True, "real_odds": extract_real_odds(ev),
            "odds_sport_key": ev.get("sport_key", ""),
        })
    return matches

# =========================================================
# ENRICHISSEMENT
# =========================================================
async def enrich_matches(matches: list[dict]) -> dict:
    to_enrich = matches[:MAX_MATCHES_TO_ENRICH]
    if not to_enrich: return {}
    contexts = {}
    for fx in to_enrich:
        try:
            home = fx.get("home", ""); away = fx.get("away", "")
            dt = parse_dt_safe(fx.get("kickoff", ""))
            date_iso = dt.strftime("%Y-%m-%d") if dt else datetime.now(TZ).strftime("%Y-%m-%d")
            ctx = None
            if enrich_match_with_bzzoiro:
                ctx = await enrich_match_with_bzzoiro(home, away, date_iso)
            if ctx and ctx.get("available"):
                contexts[fx["event_id"]] = ctx
                print(f"  ✅ {home} vs {away} — Bzzoiro • H2H:{ctx.get('h2h', {}).get('total_matches', 0)}")
            await asyncio.sleep(0.3)
        except Exception:
            pass
    return contexts

async def enrich_btts_stats(matches: list[dict]) -> dict:
    if not _API_FOOTBALL_BTTS_AVAILABLE or not API_FOOTBALL_KEY:
        return {}
    prefiltered = []
    if STATE.get("dixon_model"):
        for fx in matches:
            try:
                pred = STATE["dixon_model"].predict_all(fx["home"], fx["away"])
                if pred and pred.get("btts_over_25", 0) >= 0.40:
                    prefiltered.append(fx)
            except Exception:
                pass
    print(f"📊 Pré-filtre BBTS+2.5 (Dixon ≥ 40%) : {len(prefiltered)}/{len(matches)} matchs à enrichir")
    if not prefiltered:
        return {}
    stats_by_event = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for fx in prefiltered:
            try:
                home = fx["home"]; away = fx["away"]
                home_id = await find_team_id(client, home)
                away_id = await find_team_id(client, away)
                if not home_id or not away_id: continue
                home_stats = await get_team_btts_stats(home_id, last=10)
                away_stats = await get_team_btts_stats(away_id, last=10)
                if home_stats and away_stats:
                    stats_by_event[fx["event_id"]] = {"home": home_stats, "away": away_stats}
                    print(f"  📈 {home} ({home_stats.get('btts_over_25_pct',0)*100:.0f}%) vs {away} ({away_stats.get('btts_over_25_pct',0)*100:.0f}%)")
                await asyncio.sleep(0.4)
            except Exception as e:
                print(f"  ⚠️ stats crash {fx.get('home')}: {e}")
    print(f"📊 Stats BTTS+O2.5 : {len(stats_by_event)}/{len(prefiltered)} matchs enrichis")
    return stats_by_event

# =========================================================
# BUILD CANDIDATES
# =========================================================
async def build_all_candidates(matches, used_today, contexts=None, btts_stats_map=None):
    candidates: list[Candidate] = []
    if not predict_match_probas:
        return candidates
    contexts = contexts or {}
    btts_stats_map = btts_stats_map or {}
    ordered = [m for m in matches if m.get("has_odds") or m.get("bzz_markets")]

    async with httpx.AsyncClient(timeout=40.0) as client:
        for fx in ordered[:MAX_MATCHES_TO_ANALYZE]:
            try:
                home, away = fx["home"], fx["away"]
                event_id, league = fx["event_id"], fx["league"]
                kickoff = fx["kickoff"]
                if event_id in used_today: continue

                ctx = contexts.get(event_id)
                has_ctx = ctx is not None and ctx.get("available", False)
                bzz_markets_direct = fx.get("bzz_markets", {})
                bzz_conf_direct = fx.get("bzz_confidence", 0)
                stats_ctx = btts_stats_map.get(event_id, {})
                stats_home = stats_ctx.get("home", {})
                stats_away = stats_ctx.get("away", {})
                stats_p_btts = compute_stats_btts_prob(stats_home, stats_away)

                score_home, score_away, score_prob, score_source = -1, -1, 0.0, ""

                if bzz_markets_direct:
                    gemini_markets = bzz_markets_direct
                    analysis, advices_clean, advice, advice_conf = "", [], "", 0
                else:
                    pred = await predict_match_probas(client, home, away, league, kickoff, context=ctx)
                    if not pred or "markets" not in pred: continue
                    gemini_markets = pred.get("markets", {})
                    analysis = pred.get("analysis", "")
                    advice = pred.get("advice", "")
                    advice_conf = pred.get("advice_confidence", 0)
                    try:
                        advice_conf = int(advice_conf)
                        if advice_conf < 0 or advice_conf > 100: advice_conf = 0
                    except (ValueError, TypeError): advice_conf = 0
                    raw_advices = pred.get("advices", [])
                    advices_clean = []
                    if isinstance(raw_advices, list):
                        for a in raw_advices[:2]:
                            if isinstance(a, dict) and a.get("text"):
                                try:
                                    conf = int(a.get("confidence", 0))
                                    if conf < 0 or conf > 100: conf = 0
                                except (ValueError, TypeError): conf = 0
                                advices_clean.append({"text": str(a.get("text", "")).strip(), "confidence": conf})
                    se = pred.get("score_exact") or {}
                    if isinstance(se, dict):
                        try:
                            sh, sa, sp = int(se.get("home", -1)), int(se.get("away", -1)), float(se.get("prob", 0))
                            if 0 <= sh <= 8 and 0 <= sa <= 8 and sp > 0:
                                score_home, score_away = sh, sa
                                score_prob = sp if sp <= 1.0 else sp / 100.0
                                score_source = "gemini"
                        except (ValueError, TypeError): pass

                if score_source == "":
                    bzz_se = fx.get("bzz_score_exact") or {}
                    if isinstance(bzz_se, dict):
                        try:
                            sh, sa, sp = int(bzz_se.get("home", -1)), int(bzz_se.get("away", -1)), float(bzz_se.get("prob", 0))
                            if 0 <= sh <= 8 and 0 <= sa <= 8:
                                score_home, score_away = sh, sa
                                score_prob = sp if sp <= 1.0 else sp / 100.0
                                score_source = "bzzoiro"
                        except (ValueError, TypeError): pass

                dixon_pred = STATE["dixon_model"].predict_all(home, away) if STATE.get("dixon_model") else None
                combined_probs = combine_three_sources(gemini_markets, dixon_pred, bzz_markets_direct)

                if stats_p_btts > 0:
                    g_p = float(gemini_markets.get("btts_over_25", 0)) / 100.0 if gemini_markets.get("btts_over_25") else 0.0
                    d_p = dixon_pred.get("btts_over_25", 0) if dixon_pred else 0.0
                    b_p = float(bzz_markets_direct.get("btts_over_25", 0)) / 100.0 if bzz_markets_direct.get("btts_over_25") else 0.0
                    weights = []; values = []
                    if g_p > 0: weights.append(W_GEMINI_BTTS); values.append(g_p)
                    if d_p > 0: weights.append(W_DIXON_BTTS); values.append(d_p)
                    if b_p > 0: weights.append(W_BZZOIRO_BTTS); values.append(b_p)
                    weights.append(W_STATS_BTTS); values.append(stats_p_btts)
                    if weights:
                        combined_probs["btts_over_25"] = sum(w * v for w, v in zip(weights, values)) / sum(weights)

                real_odds = fx.get("real_odds", {})

                market_map = {
                    "victoire_home": (f"Victoire {home}", "1X2"),
                    "match_nul": ("Match nul", "1X2"),
                    "victoire_away": (f"Victoire {away}", "1X2"),
                    "double_chance_1X": (f"Double Chance 1X ({home} ou nul)", "Double Chance"),
                    "double_chance_12": ("Double Chance 12 (Pas de nul)", "Double Chance"),
                    "double_chance_X2": (f"Double Chance X2 (Nul ou {away})", "Double Chance"),
                    "over_2.5": ("Over 2.5 buts", "Over/Under"),
                    "under_2.5": ("Under 2.5 buts", "Over/Under"),
                    "over_3.5": ("Over 3.5 buts", "Over/Under"),
                    "btts_yes": ("Les 2 marquent : Oui", "BTTS"),
                    "btts_no": ("Les 2 marquent : Non", "BTTS"),
                    "btts_over_25": ("BTTS + Plus de 2.5 buts", "BTTS+O2.5"),
                    "over_8.5_corners": ("Plus de 8.5 corners", "Corners"),
                    "over_9.5_corners": ("Plus de 9.5 corners", "Corners"),
                    "over_10.5_corners": ("Plus de 10.5 corners", "Corners"),
                    "over_2.5_cartons": ("Plus de 2.5 cartons", "Cartons"),
                    "over_3.5_cartons": ("Plus de 3.5 cartons", "Cartons"),
                }

                for key, (label, mkt) in market_map.items():
                    prob = combined_probs.get(key)
                    if prob is None or prob < 0.40 or prob > 0.95: continue
                    if mkt in ("Corners", "Cartons") and key not in bzz_markets_direct: continue

                    real_odd = None
                    if mkt == "1X2":
                        if "victoire" in label.lower() and home.lower() in label.lower(): real_odd = real_odds.get("home")
                        elif "victoire" in label.lower() and away.lower() in label.lower(): real_odd = real_odds.get("away")
                        elif "nul" in label.lower(): real_odd = real_odds.get("draw")
                    elif mkt == "Over/Under":
                        if "over 2.5" in label.lower(): real_odd = real_odds.get("over_2.5")
                        elif "under 2.5" in label.lower(): real_odd = real_odds.get("under_2.5")
                    elif mkt == "BTTS":
                        if "oui" in label.lower(): real_odd = real_odds.get("btts_yes")
                        elif "non" in label.lower(): real_odd = real_odds.get("btts_no")

                    used_odd = real_odd if real_odd else 1.0 / prob
                    if used_odd < 1.05: continue

                    edge = -1.0
                    if real_odd and real_odd > 1.01: edge = (prob * real_odd) - 1.0

                    pg_raw = gemini_markets.get(key)
                    try: prob_gemini = float(pg_raw) / 100.0 if pg_raw is not None else 0.0
                    except (ValueError, TypeError): prob_gemini = 0.0
                    prob_dixon = 0.0
                    if dixon_pred:
                        dk = {"victoire_home": "p_home", "match_nul": "p_draw", "victoire_away": "p_away", "over_2.5": "over_2.5", "under_2.5": "under_2.5", "over_3.5": "over_3.5", "btts_yes": "btts_yes", "btts_no": "btts_no", "btts_over_25": "btts_over_25"}.get(key)
                        if dk: prob_dixon = dixon_pred.get(dk, 0.0)
                    prob_bzzoiro = 0.0
                    if bzz_markets_direct:
                        bv = bzz_markets_direct.get(key)
                        if bv is not None:
                            try: prob_bzzoiro = float(bv) / 100.0
                            except (ValueError, TypeError): pass

                    agr_emoji, agr_delta = compute_agreement_three(gemini_markets, dixon_pred, bzz_markets_direct, key)
                    if agr_emoji == "⚠️": continue

                    candidates.append(Candidate(
                        event_id=event_id, league=league, home=home, away=away,
                        kickoff=kickoff, market=mkt, pick_label=label,
                        odds=round(used_odd, 2), prob=round(prob, 4),
                        edge=round(edge, 4), real_odd=real_odd,
                        prob_gemini=round(prob_gemini, 4), prob_dixon=round(prob_dixon, 4), prob_bzzoiro=round(prob_bzzoiro, 4),
                        agreement_emoji=agr_emoji, agreement_delta=round(agr_delta, 4),
                        has_context=has_ctx, bzz_confidence=bzz_conf_direct,
                        analysis=analysis, advice=advice, advice_confidence=advice_conf, advices=advices_clean,
                        score_exact_home=score_home, score_exact_away=score_away, score_exact_prob=score_prob, score_exact_source=score_source,
                        prob_stats=round(stats_p_btts, 4) if key == "btts_over_25" else 0.0,
                        stats_home=stats_home if key == "btts_over_25" else None,
                        stats_away=stats_away if key == "btts_over_25" else None,
                    ))

                await asyncio.sleep(2.0)
            except Exception as e:
                print(f"⚠️ build_all_candidates: {e}")
    return candidates

# =========================================================
# CONSTRUCTION DES TICKETS
# =========================================================
def candidate_to_selection(c: Candidate) -> Selection:
    return Selection(
        event_id=c.event_id, league=c.league, home=c.home, away=c.away,
        kickoff=c.kickoff, market=c.market, pick_label=c.pick_label,
        odds=c.odds, model_prob=c.prob, edge=c.edge,
        bookmaker="The Odds API" if c.real_odd else "Triple Fusion",
        real_odd=c.real_odd, analysis=c.analysis, advice=c.advice,
        advice_confidence=c.advice_confidence, advices=c.advices,
        has_context=c.has_context, prob_gemini=c.prob_gemini, prob_dixon=c.prob_dixon, prob_bzzoiro=c.prob_bzzoiro,
        agreement_emoji=c.agreement_emoji, agreement_delta=c.agreement_delta, bzz_confidence=c.bzz_confidence,
        score_exact_home=c.score_exact_home, score_exact_away=c.score_exact_away, score_exact_prob=c.score_exact_prob, score_exact_source=c.score_exact_source,
        prob_stats=c.prob_stats, stats_home=c.stats_home, stats_away=c.stats_away,
    )

def build_safe_coupon(candidates: list[Candidate]) -> Optional[Coupon]:
    pool = [c for c in candidates if c.market == "Double Chance" and SAFE_MIN_PROB <= c.prob <= SAFE_MAX_PROB and SAFE_MIN_LEG_ODDS <= c.odds <= SAFE_MAX_LEG_ODDS]
    by_event = {}
    for c in pool:
        if c.event_id not in by_event or c.prob > by_event[c.event_id].prob:
            by_event[c.event_id] = c
    pool = list(by_event.values()); pool.sort(key=lambda x: x.prob, reverse=True)
    if not pool: return None
    legs, used_events, used_leagues, combined_odds, combined_prob = [], set(), set(), 1.0, 1.0
    for c in pool:
        if len(legs) >= SAFE_MAX_LEGS: break
        if c.event_id in used_events: continue
        if c.league in used_leagues and len(legs) >= 2: continue
        next_odds = combined_odds * c.odds
        if next_odds > SAFE_MAX_TOTAL * 1.15 and legs: continue
        legs.append(candidate_to_selection(c))
        used_events.add(c.event_id); used_leagues.add(c.league)
        combined_odds = next_odds; combined_prob *= c.prob
        if combined_odds >= SAFE_MIN_TOTAL: break
    if not legs or combined_odds < SAFE_MIN_TOTAL or combined_odds > SAFE_MAX_TOTAL * 1.25: return None
    return Coupon("Ticket Sécurisé", f"Cote {SAFE_MIN_TOTAL} - {SAFE_MAX_TOTAL} • DC sûrs", "🔒", legs, round(combined_odds, 2), round(combined_prob, 4))

def build_balanced_coupon(candidates: list[Candidate]) -> Optional[Coupon]:
    market_types = ["Over/Under", "BTTS", "Double Chance", "1X2"]
    pool_all = [c for c in candidates if c.market in market_types and BAL_MIN_PROB <= c.prob <= BAL_MAX_PROB and BAL_MIN_LEG_ODDS <= c.odds <= BAL_MAX_LEG_ODDS]
    print(f"  → Ticket Équilibré: {len(pool_all)} candidates initiales")
    if not pool_all:
        print("  → Ticket Équilibré: Aucun candidat dans les critères")
        return None
    legs, used_events, used_leagues = [], set(), set()
    for mkt in market_types:
        pool_mkt = [c for c in pool_all if c.market == mkt]
        by_event = {}
        for c in pool_mkt:
            if c.event_id not in by_event or c.prob > by_event[c.event_id].prob: by_event[c.event_id] = c
        pool_mkt = list(by_event.values()); pool_mkt.sort(key=lambda x: (x.prob, x.edge), reverse=True)
        added = 0
        for c in pool_mkt:
            if added >= 2: break
            if c.event_id in used_events: continue
            if c.league in used_leagues and added >= 1: continue
            legs.append(candidate_to_selection(c))
            used_events.add(c.event_id); used_leagues.add(c.league); added += 1
    print(f"  → Ticket Équilibré: {len(legs)} legs préliminaires")
    if not legs: return None
    combined_odds, combined_prob = 1.0, 1.0
    for s in legs: combined_odds *= s.odds; combined_prob *= s.final_prob()
    while combined_odds > BAL_MAX_TOTAL * 1.25 and len(legs) > 3:
        removed = legs.pop(); combined_odds /= removed.odds; combined_prob /= removed.final_prob()
    print(f"  → Ticket Équilibré: {len(legs)} legs, cote {combined_odds:.2f}")
    if combined_odds < 1.70:
        print(f"  → Ticket Équilibré: REJETÉ (cote {combined_odds:.2f} < 1.70)")
        return None
    if combined_odds > BAL_MAX_TOTAL * 1.25: return None
    return Coupon("Ticket Équilibré", f"Cote 2.0 - 6.0 • Multi-marchés", "⚖️", legs, round(combined_odds, 2), round(combined_prob, 4))

def build_value_coupon(candidates: list[Candidate]) -> Optional[Coupon]:
    pool = [c for c in candidates if is_obscure_league(c.league)]
    pool = [c for c in pool if c.market in ("Double Chance", "1X2", "Over/Under", "BTTS")]
    pool = [c for c in pool if VALUE_MIN_LEG_ODDS <= c.odds <= VALUE_MAX_LEG_ODDS]
    print(f"  → Value après filtres marché/cote : {len(pool)}")
    def qualifies(c: Candidate) -> bool:
        if c.real_odd is not None: return c.edge >= 0.01
        return c.prob >= 0.80
    pool = [c for c in pool if qualifies(c)]
    def n_sources(c: Candidate) -> int:
        return sum([1 for x in (c.has_context, c.prob_dixon > 0, c.prob_bzzoiro > 0, c.prob_gemini > 0) if x])
    pool = [c for c in pool if n_sources(c) >= 2]
    strict = [c for c in pool if VALUE_MIN_PROB <= c.prob <= VALUE_MAX_PROB]
    def dedup(pool_list):
        by_ev = {}
        for c in pool_list:
            if c.event_id not in by_ev or c.prob > by_ev[c.event_id].prob: by_ev[c.event_id] = c
        return list(by_ev.values())
    strict = dedup(strict)
    def score(c):
        edge_score = c.edge if c.edge > 0 else 0.0
        return (c.prob, edge_score, c.bzz_confidence * 0.001)
    strict.sort(key=score, reverse=True)
    selected = strict[:VALUE_TOP_N]
    if len(selected) < VALUE_TOP_N:
        used_ids = {c.event_id for c in selected}
        fallback = [c for c in pool if VALUE_FALLBACK_MIN_PROB <= c.prob < VALUE_MIN_PROB and c.event_id not in used_ids]
        fallback = dedup(fallback)
        fallback.sort(key=score, reverse=True)
        for c in fallback:
            if len(selected) >= VALUE_TOP_N: break
            selected.append(c)
    print(f"  → Ticket Value FINAL : {len(selected)} paris retenus")
    if not selected: return None
    combined_odds, combined_prob = 1.0, 1.0
    for c in selected: combined_odds *= c.odds; combined_prob *= c.prob
    return Coupon("Ticket Value", f"Top {len(selected)} • Ligues obscures", "💎", [candidate_to_selection(c) for c in selected], round(combined_odds, 2), round(combined_prob, 4), is_list=True)

def build_btts_over_coupon(candidates: list[Candidate]) -> Optional[Coupon]:
    pool = [c for c in candidates if c.market == "BTTS+O2.5"]
    print(f"  → BBTS+2.5 : {len(pool)} candidats bruts")

    pool = [c for c in pool if c.prob >= BTTS_MIN_PROB]
    print(f"  → BBTS+2.5 après seuil {BTTS_MIN_PROB*100:.0f}% : {len(pool)}")

    filtered = []
    for c in pool:
        if c.score_exact_home >= 0 and c.score_exact_away >= 0:
            if c.score_exact_home == 0 or c.score_exact_away == 0: continue
            if c.score_exact_home + c.score_exact_away < 3: continue
        filtered.append(c)
    pool = filtered
    print(f"  → BBTS+2.5 après score cohérent : {len(pool)}")

    def n_sources(c: Candidate) -> int:
        return sum([1 for x in (c.has_context, c.prob_dixon > 0,
                                c.prob_bzzoiro > 0, c.prob_gemini > 0,
                                c.prob_stats > 0) if x])
    pool = [c for c in pool if n_sources(c) >= 2]
    print(f"  → BBTS+2.5 après 2 sources : {len(pool)}")

    def stats_ok(c: Candidate) -> bool:
        if not c.stats_home or not c.stats_away: return True
        h_pct = c.stats_home.get("btts_over_25_pct", 0)
        a_pct = c.stats_away.get("btts_over_25_pct", 0)
        return h_pct >= BTTS_STATS_MIN_TEAM and a_pct >= BTTS_STATS_MIN_TEAM
    pool = [c for c in pool if stats_ok(c)]
    print(f"  → BBTS+2.5 après filtre stats : {len(pool)}")

    by_ev = {}
    for c in pool:
        if c.event_id not in by_ev or c.prob > by_ev[c.event_id].prob: by_ev[c.event_id] = c
    pool = list(by_ev.values())

    pool.sort(key=lambda x: x.prob, reverse=True)
    selected = pool[:BTTS_TOP_N]
    print(f"  → BBTS+2.5 FINAL : {len(selected)} matchs retenus")

    if not selected: return None
    combined_odds, combined_prob = 1.0, 1.0
    for c in selected: combined_odds *= c.odds; combined_prob *= c.prob
    return Coupon("BBTS+2.5", f"Top {len(selected)} matchs BTTS+O2.5", "🎯", [candidate_to_selection(c) for c in selected], round(combined_odds, 2), round(combined_prob, 4), is_list=True)

def build_all_coupons(candidates: list[Candidate]) -> list[Coupon]:
    coupons = []
    if c := build_safe_coupon(candidates): coupons.append(c)
    if c := build_balanced_coupon(candidates): coupons.append(c)
    if c := build_value_coupon(candidates): coupons.append(c)
    if c := build_btts_over_coupon(candidates): coupons.append(c)
    return coupons

# =========================================================
# FORMAT
# =========================================================
def format_header():
    return (
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<i>{BRAND_TAGLINE}</i>\n"
        f"<i>📅 {today_pretty()} • 🇨🇮 Côte d’Ivoire</i>"
    )

def format_selection_block(s: Selection, idx: int, compact: bool = False, hide_analysis: bool = False, hide_advices: bool = False):
    dt = parse_dt_safe(s.kickoff)
    day_tag = ""
    if dt:
        today = datetime.now(TZ).date()
        if dt.date() == today: day_tag = "📍 <b>Aujourd'hui</b> "
        elif dt.date() == today + timedelta(days=1): day_tag = "📅 <b>Demain</b> "
    prob_pct = s.final_prob() * 100
    ctx_badge = " ✅ <i>H2H</i>" if s.has_context else ""
    agreement = ""
    if s.agreement_emoji == "🤝🤝": agreement = " 🤝🤝 <b>TRIPLE CONSENSUS</b>"
    elif s.agreement_emoji == "🤝": agreement = " 🤝 <b>consensus</b>"
    elif s.agreement_emoji == "⚠️": agreement = " ⚠️"
    bzz_badge = f" 🔮{s.bzz_confidence:.0f}" if s.bzz_confidence > 0 else ""

    lines = [
        f"<b>┌─ #{idx} {s.home} vs {s.away}</b>",
        f"│ {day_tag}🕐 <b>{kickoff_local(s.kickoff)}</b> • {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>  <i>({s.market})</i>",
        f"│ 📊 Proba : <b>{prob_pct:.1f}%</b> {prob_bar(s.final_prob())}{ctx_badge}{bzz_badge}{agreement}",
    ]
    src_parts = []
    if s.prob_gemini > 0: src_parts.append(f"G:{s.prob_gemini*100:.0f}%")
    if s.prob_dixon > 0: src_parts.append(f"D:{s.prob_dixon*100:.0f}%")
    if s.prob_bzzoiro > 0: src_parts.append(f"B:{s.prob_bzzoiro*100:.0f}%")
    if s.prob_stats > 0: src_parts.append(f"S:{s.prob_stats*100:.0f}%")
    if src_parts:
        lines.append(f"│    <i>{' × '.join(src_parts)}</i>")

    if s.stats_home and s.stats_away:
        h_pct = s.stats_home.get("btts_over_25_pct", 0) * 100
        a_pct = s.stats_away.get("btts_over_25_pct", 0) * 100
        h_gf = s.stats_home.get("goals_for_avg", 0)
        a_gf = s.stats_away.get("goals_for_avg", 0)
        h_conc = s.stats_home.get("conceded_pct", 0) * 100
        a_conc = s.stats_away.get("conceded_pct", 0) * 100
        lines.append(f"│ 📈 Stats {s.home[:14]} : {h_gf:.1f}b/m • BTTS+O2.5 {h_pct:.0f}% • encaisse {h_conc:.0f}%")
        lines.append(f"│ 📈 Stats {s.away[:14]} : {a_gf:.1f}b/m • BTTS+O2.5 {a_pct:.0f}% • encaisse {a_conc:.0f}%")

    if s.score_exact_home >= 0 and s.score_exact_away >= 0:
        prob_txt = f" ({s.score_exact_prob*100:.1f}%)" if s.score_exact_prob > 0 else ""
        lines.append(f"│ ⚽ <b>Score exact</b> : {s.score_exact_home}-{s.score_exact_away}{prob_txt}")
    if s.real_odd: lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b> <i>(réelle)</i>")
    else: lines.append(f"│ 💰 Cote : <b>{s.odds}</b> <i>(estimée)</i>")
    if s.edge > 0.05: lines.append(f"│ 💎 <b>Value</b> : +{s.edge*100:.1f}%")

    if not compact and (s.analysis or s.advices or s.advice):
        if not hide_analysis and s.analysis:
            lines.append(f"│"); lines.append(f"│ 🧠 <b>Avis IA</b> :"); lines.append(f"│ 💬 <i>{s.analysis}</i>")
        if not hide_advices:
            if s.advices:
                lines.append(f"│ 💡 <b>Conseils IA</b> :")
                for a in s.advices:
                    txt = a.get("text", "").strip()
                    if not txt: continue
                    conf = a.get("confidence", 0)
                    if conf > 0: lines.append(f"│    • {txt}  {confidence_bar(conf)} <b>{conf}%</b>")
                    else: lines.append(f"│    • {txt}")
            elif s.advice:
                if s.advice_confidence > 0: lines.append(f"│ 💡 <b>Conseil IA</b> ({confidence_bar(s.advice_confidence)} <b>{s.advice_confidence}%</b>) :\n│    {s.advice}")
                else: lines.append(f"│ 💡 <b>Conseil IA</b> : {s.advice}")
    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)

def format_coupon(c: Coupon) -> str:
    if c.is_list:
        lines = [
            format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{c.subtitle}</i>", "",
            f"<b>🎯 {len(c.legs)} pronostics (valeur)</b>", "",
        ]
        for i, s in enumerate(c.legs, 1):
            hide_ana = (c.name == "Ticket Value")
            lines.append(format_selection_block(s, i, compact=False, hide_analysis=hide_ana, hide_advices=False))
            lines.append("")
        lines.append(f"<i>{PUBLIC_FOOTER}</i>")
        return "\n".join(lines)
    prob_level = "🟢 Faible risque" if c.combined_prob >= 0.40 else "🟡 Risque modéré" if c.combined_prob >= 0.20 else "🔴 Risque élevé"
    lines = [
        format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{c.subtitle}</i>", "",
        f"<b>┏━━━━━━━━━━━━━━━┓</b>", f"<b>┃ 📊 RÉCAP TICKET</b>",
        f"<b>┃ 🎫 Cote totale : {c.combined_odds}</b>",
        f"<b>┃ 📈 Proba combinée : {c.combined_prob*100:.1f}%</b>",
        f"<b>┃ 🎯 {prob_level}</b>", f"<b>┗━━━━━━━━━━━━━━━┛</b>", "",
    ]
    for i, s in enumerate(c.legs, 1):
        lines.append(format_selection_block(s, i, compact=False))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)

def format_coupons_ready():
    if not STATE["coupons"]:
        return f"{format_header()}\n\n<b>📭 Aucun coupon aujourd'hui</b>\n<i>Pas assez de paris qualifiés.</i>"
    lignes = []
    for c in STATE["coupons"]:
        n = len(c.legs)
        if c.is_list: lignes.append(f"{c.emoji} <b>{c.name}</b>\n   • {n} pronostics (liste)")
        else: lignes.append(f"{c.emoji} <b>{c.name}</b>\n   • {n} paris • Cote <b>{c.combined_odds}</b> • Proba <b>{c.combined_prob*100:.0f}%</b>")
    return f"{format_header()}\n\n<b>🔔 COUPONS DU JOUR PRÊTS !</b>\n\n<b>{len(STATE['coupons'])} coupon(s) disponible(s) :</b>\n\n" + "\n\n".join(lignes) + "\n\n<i>👇 Clique sur le bouton du ticket qui t'intéresse</i>"

def format_bilan(tracker):
    coupons = tracker.get("coupons", [])
    if not coupons: return f"{format_header()}\n\n<b>📊 Bilan</b>\n<i>Aucun historique.</i>"
    total, won, lost, pending = len(coupons), sum(1 for c in coupons if c.get("status") == "won"), sum(1 for c in coupons if c.get("status") == "lost"), sum(1 for c in coupons if c.get("status") == "pending")
    settled = won + lost; wr = (won / settled * 100) if settled > 0 else 0
    return f"{format_header()}\n\n<b>📊 BILAN</b>\n🎫 Total : <b>{total}</b>\n✅ Gagnés : <b>{won}</b>\n❌ Perdus : <b>{lost}</b>\n⏳ En attente : <b>{pending}</b>\n\n📈 Taux réussite : <b>{wr:.1f}%</b>"

# =========================================================
# TELEGRAM
# =========================================================
async def safe_answer(message, text, reply_markup=None):
    for i, part in enumerate(split_message(text)):
        try:
            if i == 0: await message.answer(part, parse_mode="HTML", reply_markup=reply_markup)
            else: await message.answer(part, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e: print(f"⚠️ Answer part {i}: {e}")

async def safe_send(chat_id, text):
    for part in split_message(text):
        try:
            await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e: print(f"⚠️ Send {chat_id}: {e}")

async def broadcast_after_scan():
    for chat_id in TARGETS:
        await safe_send(chat_id, format_coupons_ready()); await asyncio.sleep(0.5)
    for user_id_str in STATE["users"]:
        try:
            uid = int(user_id_str)
            if is_subscribed(uid): await safe_send(uid, format_coupons_ready()); await asyncio.sleep(0.3)
        except Exception: pass

@dp.message(Command("start"))
async def start_cmd(message: Message):
    if message.from_user.id == ADMIN_ID:
        await safe_answer(message, f"{format_header()}\n\n<b>👋 Bienvenue Admin !</b>\n\nUtilise /scan pour lancer une analyse.", reply_markup=ADMIN_KEYBOARD)
    else:
        status = get_subscription_message(message.from_user.id)
        msg = f"{format_header()}\n\n<b>👋 Bienvenue !</b>\n\n{status}\n\n<b>🔑 Pour activer :</b>\n<code>/activate VOTRE_CLE</code>"
        await safe_answer(message, msg, reply_markup=USER_KEYBOARD)

@dp.message(Command("keygen"))
async def keygen_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 2: await message.answer("Usage: /keygen [1m | 1s | 60]"); return
    duration_str, days = args[1].lower(), 0
    if duration_str.endswith("m"): days = int(duration_str[:-1]) * 30
    elif duration_str.endswith("s"): days = int(duration_str[:-1]) * 7
    else: days = int(duration_str)
    if days <= 0: await message.answer("❌ Durée invalide."); return
    key = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    STATE["keys"][key] = {"days": days, "used": False, "created_at": datetime.now(TZ).isoformat()}
    save_json(KEYS_DB, STATE["keys"])
    await message.answer(f"🔑 <b>Clé générée !</b>\n\nClé : <code>{key}</code>\nDurée : <b>{days} jours</b>", parse_mode="HTML")

@dp.message(Command("activate", "vip", "cle", "key"))
async def activate_cmd(message: Message):
    args = message.text.split()
    if len(args) != 2: await message.answer("Usage: /activate <votre_clé>"); return
    await process_activation(message, args[1].strip().upper())

@dp.message(F.text.regexp(r"^[A-Z0-9]{8}$"))
async def raw_key_cmd(message: Message):
    await process_activation(message, message.text.strip().upper())

async def process_activation(message: Message, key: str):
    user_id = message.from_user.id
    if key not in STATE["keys"]: await message.answer("❌ Clé invalide."); return
    key_data = STATE["keys"][key]
    if key_data.get("used"): await message.answer("❌ Clé déjà utilisée."); return
    days = key_data["days"]
    expires_at = datetime.now(TZ) + timedelta(days=days)
    STATE["users"][str(user_id)] = {"expires_at": expires_at.isoformat(), "key_used": key}
    key_data["used"] = True; key_data["used_by"] = str(user_id); key_data["used_at"] = datetime.now(TZ).isoformat()
    save_json(KEYS_DB, STATE["keys"]); save_json(USERS_DB, STATE["users"])
    await message.answer(f"✅ <b>Abonnement activé !</b>\n\nExpiration : <b>{expires_at.strftime('%d/%m/%Y à %H:%M')}</b>", parse_mode="HTML", reply_markup=USER_KEYBOARD)

@dp.message(Command("scan"))
async def scan_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await safe_answer(message, "⏳ Analyse en cours..."); await scan(); await broadcast_after_scan()

@dp.message(Command("regler"))
async def regler_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await safe_answer(message, "⏳ Vérification..."); await settle_coupons(); await safe_answer(message, format_bilan(STATE["tracker"]))

@dp.message(Command("debug"))
async def debug_cmd(message: Message):
    if message.from_user.id != ADMIN_ID: return
    d = STATE["debug"]
    txt = f"<b>🔍 DEBUG</b>\n\nDixon : <b>{'✅' if STATE['dixon_model'] else '❌'}</b>\nBzzoiro : <b>{'✅' if BZZOIRO_API_KEY else '❌'}</b>\nAPI-Football : <b>{'✅' if API_FOOTBALL_KEY else '❌'}</b>\nOdds events : <b>{d.get('odds_events', 0)}</b>\nBzzoiro direct : <b>{d.get('bzz_predictions', 0)}</b>\nStats BTTS : <b>{d.get('btts_stats', 0)}</b>\nMatchs finaux : <b>{d.get('matched', 0)}</b>\nCandidats : <b>{d.get('candidates', 0)}</b>\nCoupons : <b>{d.get('coupons', 0)}</b>"
    await safe_answer(message, txt)

async def send_coupon(message, name):
    c = next((x for x in STATE["coupons"] if x.name == name), None)
    if c: await safe_answer(message, format_coupon(c))
    else: await safe_answer(message, "Aucun coupon pour ce profil.")

@dp.message(F.text == BTN_SAFE)
async def btn_safe(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}"); return
    await send_coupon(message, "Ticket Sécurisé")

@dp.message(F.text == BTN_BAL)
async def btn_bal(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}"); return
    await send_coupon(message, "Ticket Équilibré")

@dp.message(F.text == BTN_VAR)
async def btn_var(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}"); return
    await send_coupon(message, "Ticket Value")

@dp.message(F.text == BTN_BTTS)
async def btn_btts(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}"); return
    await send_coupon(message, "BBTS+2.5")

@dp.message(F.text == BTN_BILAN)
async def btn_bilan(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}"); return
    await safe_answer(message, format_bilan(STATE["tracker"]))

@dp.message(F.text == BTN_SCAN)
async def btn_scan(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await scan_cmd(message)

@dp.message(F.text == BTN_ACTIVATE)
async def btn_activate(message: Message):
    await message.answer("🔑 <b>Pour activer votre abonnement :</b>\n\nEnvoyez <code>/activate VOTRE_CLE</code>", parse_mode="HTML")

@dp.message(F.text == "⚙️ Debug")
async def btn_debug(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await debug_cmd(message)

# =========================================================
# SCAN
# =========================================================
async def scan():
    async with STATE["scan_lock"]:
        bzz_predictions = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try:
                bzz_predictions = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except Exception as e: print(f"⚠️ Bzzoiro direct crash: {e}")

        odds_events = await fetch_all_odds_events()
        matches = []
        if bzz_predictions:
            for bzz in bzz_predictions:
                matched_odds = None
                h_norm, a_norm = normalize_name(bzz["home"]), normalize_name(bzz["away"])
                for ev in odds_events:
                    ev_h, ev_a = normalize_name(ev.get("home_team", "")), normalize_name(ev.get("away_team", ""))
                    if (h_norm in ev_h or ev_h in h_norm) and (a_norm in ev_a or a_norm in ev_h):
                        matched_odds = ev; break
                bzz_copy = dict(bzz)
                if matched_odds:
                    bzz_copy["real_odds"] = extract_real_odds(matched_odds); bzz_copy["has_odds"] = True; bzz_copy["odds_sport_key"] = matched_odds.get("sport_key", "")
                matches.append(bzz_copy)

        if not matches:
            odds_matches = build_matches_from_odds(odds_events)
            if not odds_matches:
                STATE["coupons"] = []; STATE["candidates"] = []; return
            if get_bzzoiro_best_matches and BZZOIRO_API_KEY:
                try:
                    matches = await get_bzzoiro_best_matches(odds_matches, min_confidence=BZZ_MIN_CONFIDENCE)
                    matches = matches[:BZZ_MAX_MATCHES]
                except Exception:
                    matches = odds_matches[:MAX_MATCHES_TO_ANALYZE]
            else:
                matches = odds_matches[:MAX_MATCHES_TO_ANALYZE]

        if not matches:
            STATE["coupons"] = []; STATE["candidates"] = []; return

        matches = filter_and_rank_matches(matches)
        contexts = {}
        try:
            contexts = await enrich_matches(matches)
        except Exception as e: print(f"⚠️ Enrichissement Bzzoiro : {e}")

        btts_stats_map = {}
        try:
            btts_stats_map = await enrich_btts_stats(matches)
        except Exception as e: print(f"⚠️ Enrichissement stats BTTS : {e}")

        used_today = load_used_today()
        candidates = await build_all_candidates(matches, used_today, contexts, btts_stats_map)
        STATE["candidates"] = candidates
        STATE["coupons"] = build_all_coupons(candidates)
        mark_picks_used([s for c in STATE["coupons"] for s in c.legs])
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        STATE["debug"] = {
            "odds_events": len(odds_events),
            "bzz_predictions": len(bzz_predictions),
            "btts_stats": len(btts_stats_map),
            "matched": len(matches),
            "candidates": len(candidates),
            "coupons": len(STATE["coupons"]),
        }

        print(f"──────── RÉSUMÉ ────────\nCandidats : {len(candidates)}\nCoupons : {len(STATE['coupons'])}\n────────────────────────")

# =========================================================
# LIFESPAN
# =========================================================
async def daily_broadcast():
    await scan()
    record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    await broadcast_after_scan()

@asynccontextmanager
async def lifespan(app: FastAPI):
    try: await bot.delete_webhook(drop_pending_updates=True)
    except Exception: pass

    STATE["tracker"] = load_tracker(); STATE["keys"] = load_json(KEYS_DB); STATE["users"] = load_json(USERS_DB); STATE["ready"] = True
    print("✅ Bot prêt.")
    if not API_FOOTBALL_KEY:
        print("⚠️ API_FOOTBALL_KEY manquante → le filtre stats BTTS+O2.5 sera DÉSACTIVÉ")

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ), id="daily_broadcast", replace_existing=True)
    scheduler.start()
    bot_task = asyncio.create_task(dp.start_polling(bot))
    print("✅ Bot Telegram démarré.")

    async def background_bootstrap():
        try:
            if download_and_train:
                STATE["dixon_model"] = await download_and_train()
                print(f"✅ Dixon-Coles actif ({len(STATE['dixon_model'].teams) if STATE['dixon_model'] else 0} équipes)")
            await asyncio.sleep(5)
            if NOTIFY_ON_STARTUP:
                for chat_id in TARGETS: await safe_send(chat_id, f"{format_header()}\n\n⏳ <b>Bot démarré...</b>")
            await scan(); record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d")); await broadcast_after_scan()
        except Exception as e: print(f"⚠️ bootstrap crash: {e}")

    asyncio.create_task(background_bootstrap())
    yield
    scheduler.shutdown(wait=False); bot_task.cancel()
    try: await bot.session.close()
    except Exception: pass

app = FastAPI(title="Value Bot", lifespan=lifespan)

@app.get("/")
async def root(): return {"status": "ok", "ready": STATE["ready"], "coupons": [c.name for c in STATE["coupons"]]}

@app.get("/debug")
async def debug_http(): return STATE["debug"]

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
