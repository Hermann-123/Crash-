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
try: from gemini_predictor import predict_match_probas
except: predict_match_probas = None

try: from bzzoiro_data import enrich_match_with_bzzoiro
except: enrich_match_with_bzzoiro = None

try: from bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
except: fetch_bzzoiro_predictions_direct = None

try: from odds_fallback import fetch_all_odds as fetch_infer_odds
except: fetch_infer_odds = None

try:
    from ml_engine import train_model_if_needed, predict_ml, fuse_probas
    print("✅ ml_engine chargé")
except Exception as e:
    train_model_if_needed = predict_ml = fuse_probas = None
    print(f"❌ ml_engine: {e}")

try: from rapidoddsapi_data import fetch_all_odds as fetch_rapidodds_odds_data
except: fetch_rapidodds_odds_data = None

try: from dixon_coles import download_and_train
except: download_and_train = None

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

try: ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except: ADMIN_ID = 0

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

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "20"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

WEIGHT_ALGO    = float(os.getenv("WEIGHT_ALGO",   "0.35"))
WEIGHT_GEMINI  = float(os.getenv("WEIGHT_GEMINI", "0.35"))
WEIGHT_BZZOIRO = float(os.getenv("WEIGHT_BZZOIRO","0.30"))

# ✅ CALIBRAGE PRO GLOBAL — ZÉRO EV NÉGATIVE SUR TOUTES LES CATÉGORIES
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.01"))
MIN_COUPON_EV = float(os.getenv("MIN_COUPON_EV", "1.00"))
MIN_REAL_COUPON_EV = float(os.getenv("MIN_REAL_COUPON_EV", "1.02"))

# ✅ CALIBRAGE MOTEUR DU 22 NOVEMBRE (PYRAMIDE SÉCURISÉE >= 71%, EV >= 1.00)
AUTH_PROBA_MIN = float(os.getenv("AUTH_PROBA_MIN", "68.0"))
AUTH_PROBA_MAX = float(os.getenv("AUTH_PROBA_MAX", "88.0"))
AUTH_MIN_COMBINED_PROB = 0.31

DOUBLE_SCAN_POOL_SIZE       = int(os.getenv("DOUBLE_SCAN_POOL_SIZE", "12"))
DOUBLE_SCAN_STABILITY_DELTA = float(os.getenv("DOUBLE_SCAN_STABILITY_DELTA", "0.16"))
DOUBLE_SCAN_MIN_STABLE      = int(os.getenv("DOUBLE_SCAN_MIN_STABLE", "3"))
DOUBLE_SCAN_PAUSE_SECONDS   = int(os.getenv("DOUBLE_SCAN_PAUSE_SECONDS", "3"))

MTFIN_MIN_SCORE = 70
MTFIN_MIN_PROB = 0.42
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45

DRAW_MIN_SCORE = 70
DRAW_MAX_ECART = 0.20
DRAW_MIN_PROB = 0.30

ODDS3_AUTH_MIN_COTE = 2.00
ODDS3_AUTH_MAX_COTE = 2.65
ODDS3_HYB_MIN_COTE = 3.20
ODDS3_HYB_MAX_COTE = 5.50
ODDS3_MIN_COTE_LEG = 1.15

MATCH_START_BUFFER_MINUTES = 10
RESERVE_KEYWORDS = ["jong", " fc b", " ii", " u21", " u19", " castilla", " reserves", " b team"]

PRO_TRAP_PHRASES = [
    "match piège",
    "risque élevé",
    "effectif remanié",
    "équipe bis",
    "équipe b",
    "nombreux absents",
    "blessures majeures",
    "aucun enjeu",
    "sans enjeu",
    "déjà qualifié",
    "déjà éliminé",
    "déjà champion",
    "manque de motivation",
    "match amical",
    "coupe secondaire",
    "en pleine crise",
    "démobilisé",
]

LOW_STAKES_LEAGUE_KEYWORDS = [
    "friendly", "amical", "club friendlies", "efl trophy", "checkatrade",
    "premier league cup", "premier league 2", "u21", "u23", "u19", "youth",
    "reserve", "oberliga", "regionalliga", "taca da liga", "league cup",
]

HIGH_STAKES_LEAGUE_KEYWORDS = [
    "champions league", "europa league", "conference league",
    "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1",
    "championship", "eredivisie", "primeira liga", "pro league", "super lig",
    "world cup", "coupe du monde", "euro", "can", "africa cup", "copa america",
    "libertadores", "sudamericana", "playoff", "barrage",
]

HIGH_STAKES_ANALYSIS_PHRASES = [
    "qualification", "qualifier", "course au titre", "titre", "podium",
    "maintien", "relégation", "zone rouge", "top 4", "top 8", "place européenne",
    "europe", "montée", "promotion", "play-off", "barrage", "derby",
    "revanche", "impératif", "victoire obligatoire", "doit gagner", "enjeu majeur",
    "crucial", "décisif", "capital", "sous pression",
]

ESPN_LEAGUE_SLUGS = [
    "eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1", "gre.1",
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
        return WS_SimulationResult(match.match_id, p_h, p_d, p_a, f"{sx}-{sy}", p_btts, p_o15, p_o25, 0)

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
    "rejections": {"value": 0, "ev_coupon": 0, "auth_proba": 0, "pro_trap": 0, "unstable": 0, "no_real_odds": 0},
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

def is_low_stakes_competition(league_name: str) -> bool:
    if not league_name:
        return False
    low = league_name.lower()
    return any(kw in low for kw in LOW_STAKES_LEAGUE_KEYWORDS)

def compute_stakes_score(league_name: str, analysis_text: str = "") -> int:
    """Évalue l'enjeu sportif réel du match (bonus positif si fort enjeu, pénalité si faible)."""
    score = 0
    low_lg = (league_name or "").lower()
    low_an = (analysis_text or "").lower()
    if any(kw in low_lg for kw in HIGH_STAKES_LEAGUE_KEYWORDS):
        score += 20
    if any(ph in low_an for ph in HIGH_STAKES_ANALYSIS_PHRASES):
        score += 15
    return score

def has_pro_red_flags(analysis_text: str, league_name: str = "") -> Tuple[bool, str]:
    if is_low_stakes_competition(league_name):
        return True, f"compétition à faible enjeu ({league_name})"
    if not analysis_text:
        return False, ""
    low = analysis_text.lower()
    for phrase in PRO_TRAP_PHRASES:
        if phrase in low:
            return True, phrase
    return False, ""

def clean_analysis_text(text: str, max_len: int = 320) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > 80:
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
    """Vérifie strictement que le match se joue aujourd'hui (dans le fuseau TZ) et n'a pas encore commencé."""
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
    """Strictement vraies cotes bookmaker uniquement — zéro estimation artificielle."""
    if real_odd and float(real_odd) > 1.01:
        return round(float(real_odd), 2)
    return 0.0

def prob_bar(p: float) -> str:
    if p >= 0.75: return "🟢🟢🟢"
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
    if "double chance 12" in lbl or "(12)" in lbl:
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
    if is_low_stakes_competition(md.league):
        return -999
    conf_score = int(md.bzz_confidence or 0)
    real_bonus = 40 if (md.real_odds.get("home") or md.real_odds.get("away")) else (20 if md.real_odds else 0)
    bzz_bonus = 15 if md.bzz_markets else 0
    dixon_bonus = 25 if md.dixon_pred else 0
    h2h_bonus = 10 if md.has_h2h else 0
    stakes_bonus = compute_stakes_score(md.league, md.analysis)
    return conf_score + real_bonus + bzz_bonus + dixon_bonus + h2h_bonus + stakes_bonus

def _get_match_id(item: dict) -> str:
    m = item["match"]
    return str(m.event_id if hasattr(m, "event_id") else m.match_id)

# =====================================================================
# CALCUL PROBABILITÉ (Dixon + ML + Gemini + Bzzoiro + Double Chance)
# =====================================================================
def compute_probability(md, market_key):
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
        emoji = "🤝🤝" if len(sources) == 3 and delta <= 0.12 else "🤝" if delta <= 0.15 else "⚠️" if delta >= 0.20 else ""
    return final, gem, dix, bzz, emoji

def passes_value_filter(proba, odd, is_real, consensus_emoji=""):
    """Verrou global : aucune EV négative sur aucune catégorie."""
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
    # En mode estimé (cote = round(1/prob, 2)), tolérance arrondi 0.995 pour garantir EV >= 1.00
    ok = (proba * odd) >= 0.995
    if not ok:
        STATE["rejections"]["value"] += 1
    return ok

def coupon_passes_ev(odds, prob, has_real_odds: bool = False):
    """Verrou global coupon : EV >= 1.05 si cotes réelles, EV >= 1.00 sinon."""
    if odds <= 0 or prob <= 0:
        return False
    min_ev = MIN_REAL_COUPON_EV if has_real_odds else MIN_COUPON_EV
    ok = (odds * prob) >= min_ev
    if not ok:
        STATE["rejections"]["ev_coupon"] += 1
    return ok

def passes_auth_proba_filter(proba):
    if proba <= 0: return False
    ok = AUTH_PROBA_MIN <= proba <= AUTH_PROBA_MAX
    if not ok: STATE["rejections"]["auth_proba"] += 1
    return ok

# =====================================================================
# APIS COTES (The Odds API LIVE + Secours RapidOddsAPI)
# =====================================================================
def _american_to_decimal(ml) -> Optional[float]:
    """Convertit une cote américaine ESPN (+150 / -200 / EVEN) en cote décimale européenne."""
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
    "eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1",
    "conmebol.libertadores", "usa.1", "mex.1", "bra.1", "arg.1",
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
    """Récupère gratuitement les vraies cotes bookmaker d'avant-match via ESPN (scoreboard + pickcenter, 0 €)."""
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

        # Fallback pickcenter (résumé d'avant-match ESPN) sur les matchs majeurs où scoreboard n'affiche pas encore les cotes
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
    """Récupère les vraies cotes bookmaker gratuites déjà incluses dans l'API Bzzoiro (/api/events/ et /api/predictions/)."""
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
    """Fusionne ESPN (scoreboard + pickcenter) + Bzzoiro gratuit + The Odds API gratuit (1 crédit/jour)."""
    ws_by_key = {}

    # 1. ESPN Public Odds (Scoreboard + Pickcenter)
    try:
        espn_ws = await fetch_espn_prematch_odds(days_ahead=1)
        for item in espn_ws:
            nk = f"{normalize_name(item.home_team)}__{normalize_name(item.away_team)}"
            ws_by_key[nk] = item
    except Exception as e:
        print(f"⚠️ ESPN Odds erreur : {e}")

    # 2. Bzzoiro Free Odds (/api/events/ & /api/predictions/)
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

    # 3. Complément gratuit : The Odds API (1 requête/jour sur /sports/upcoming/odds)
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

    # 4. Secours optionnel RapidOddsAPI si configuré
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
        if bool(md.real_odds) or had_initial_odds:
            matched += 1
    return matched

# =====================================================================
# DOUBLE SCAN
# =====================================================================
async def _enrich_single_pass(targets, pass_num):
    if not predict_match_probas:
        print("⚠️ gemini_predictor.py non disponible.")
        return {}
    results = {}
    errors = 0
    async with httpx.AsyncClient(timeout=70.0) as client:
        for md in targets:
            try:
                ctx = {}
                if enrich_match_with_bzzoiro:
                    date_iso = parse_dt_safe(md.kickoff).strftime("%Y-%m-%d") if parse_dt_safe(md.kickoff) else datetime.now(TZ).strftime("%Y-%m-%d")
                    bzz_ctx = await enrich_match_with_bzzoiro(md.home, md.away, date_iso)
                    if isinstance(bzz_ctx, dict):
                        ctx.update(bzz_ctx)
                        if bzz_ctx.get("available"): md.has_h2h = True
                if md.dixon_pred:
                    ctx["dixon_coles"] = md.dixon_pred
                if md.real_odds:
                    ctx["bookmaker_odds"] = md.real_odds
                if md.bzz_markets:
                    ctx["bzzoiro_probas"] = md.bzz_markets

                pred = await predict_match_probas(client, md.home, md.away, md.league, md.kickoff, context=ctx)
                if pred and "markets" in pred:
                    results[md.event_id] = {"markets": pred.get("markets", {}), "analysis": pred.get("analysis", "")}
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
        else: md.analysis = f"⚠️ Variable. {base[:200]}"; md.stability_flag = "unstable"; stats["unstable"] += 1
        enriched_list.append(md)
    return stable_list, enriched_list, stats

async def enrich_with_double_pass(pool):
    if not pool: STATE["double_scan_status"] = "EMPTY"; return pool
    targets = pool[:max(DOUBLE_SCAN_POOL_SIZE, min(len(pool), GEMINI_MAX_CALLS_PER_SCAN))]
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
# MOTEUR EXCLUSIF VIP : ODDS +3 AUTH (CALIBRAGE 22 NOVEMBRE — ZÉRO EV NÉGATIVE)
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
            analysis=clean_analysis_text(c["ai"], 520),
        )
        for c in sorted_combo
    ]
    return Coupon("Odds +3 AUTH", f"{subtitle} • {len(legs)} sélections", "🛡️", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)

def _valid_pro_combo(combo, require_anchor: bool = True) -> bool:
    if len({c["norm_key"] for c in combo}) != len(combo):
        return False
    if sum(1 for c in combo if c["family"] == "goals") > 1:
        return False
    if sum(1 for c in combo if c["family"] == "btts") > 1:
        return False
    if sum(1 for c in combo if c.get("mk") == "victoire_away") > 1:
        return False
    if require_anchor and not any(c["proba"] >= 71.0 or (c["family"] == "result" and c["proba"] >= 66.0) for c in combo):
        return False
    return True

def build_exclusive_vip_auth_coupon(enriched_pool: List[MatchData], ws_matches: List[WS_MatchData]) -> Tuple[Optional[Coupon], set]:
    vip_candidates = []

    markets_map = [
        ("victoire_home", "home", "result", lambda m: f"Victoire {m.home} (1)"),
        ("victoire_away", "away", "result", lambda m: f"Victoire {m.away} (2)"),
        ("double_chance_12", "dc_12", "dc12", lambda m: "Une équipe gagne (12)"),
        ("double_chance_1X", "dc_1x", "dc", lambda m: f"Double Chance {m.home} ou Nul (1X)"),
        ("double_chance_X2", "dc_x2", "dc", lambda m: f"Double Chance Nul ou {m.away} (X2)"),
        ("over_2.5", "over_2.5", "goals", lambda m: "Plus de 2.5 buts"),
        ("over_1.5", "over_1.5", "goals", lambda m: "Plus de 1.5 buts"),
        ("under_2.5", "under_2.5", "goals", lambda m: "Moins de 2.5 buts"),
        ("btts_yes", "btts_yes", "btts", lambda m: "Les 2 équipes marquent (BTTS Oui)"),
    ]

    for md in enriched_pool:
        if not md.home or not md.away or not is_match_today(md.kickoff):
            continue
        if is_reserve_team(md.home) or is_reserve_team(md.away):
            continue
        if not md.dixon_pred and not md.gemini_markets and not md.bzz_markets and not md.real_odds:
            continue

        if getattr(md, "stability_flag", "") == "unstable":
            STATE["rejections"]["unstable"] += 1
            print(f"  ⚠️ Double-Scan rejet ({md.home} vs {md.away}) : analyse instable (>16% d'écart)")
            continue

        is_trap, trap_reason = has_pro_red_flags(md.analysis, md.league)
        if is_trap:
            STATE["rejections"]["pro_trap"] += 1
            print(f"  🚩 Pro-Filter rejet ({md.home} vs {md.away}) : signal '{trap_reason}'")
            continue

        # Calcul des cotes Double Chance réelles à partir des cotes 1X2 réelles si disponibles
        # (avec facteur de marge bookmaker 0.96 pour coller aux cotes réelles type 1xBet/Betclic)
        if md.real_odds.get("home") and md.real_odds.get("draw") and not md.real_odds.get("dc_1x"):
            inv_1x = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["draw"]))
            if inv_1x > 0:
                md.real_odds["dc_1x"] = round(max(1.03, (1.0 / inv_1x) * 0.96), 2)
        if md.real_odds.get("away") and md.real_odds.get("draw") and not md.real_odds.get("dc_x2"):
            inv_x2 = (1.0 / float(md.real_odds["away"])) + (1.0 / float(md.real_odds["draw"]))
            if inv_x2 > 0:
                md.real_odds["dc_x2"] = round(max(1.03, (1.0 / inv_x2) * 0.96), 2)
        if md.real_odds.get("home") and md.real_odds.get("away") and not md.real_odds.get("dc_12"):
            inv_12 = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["away"]))
            if inv_12 > 0:
                md.real_odds["dc_12"] = round(max(1.03, (1.0 / inv_12) * 0.96), 2)

        p_home_est, g_h, _, _, em_h = compute_probability(md, "victoire_home")
        p_away_est, g_a, _, _, em_a = compute_probability(md, "victoire_away")
        one_sided_match = (p_home_est >= 0.72) or (p_away_est >= 0.72)

        # Règle Pro "Sans hésitation" : quand l'issue 1 ou 2 est claire, on joue la victoire sèche (1 ou 2)
        # et on interdit la Double Chance frileuse (1.05 - 1.16) qui plombe la cote du coupon !
        clear_home_win = (p_home_est >= 0.62 and (p_home_est - p_away_est) >= 0.28 and em_h != "⚠️")
        clear_away_win = (p_away_est >= 0.63 and (p_away_est - p_home_est) >= 0.30 and em_a != "⚠️")

        match_options = []
        for mk, odd_key, family, label_fn in markets_map:
            if mk == "btts_yes" and one_sided_match:
                continue

            if mk == "double_chance_1X":
                if clear_home_win:
                    continue  # Pas d'hésitation : Victoire (1) sèche privilégiée !
                if g_a > 0 and g_h > 0 and g_a > (g_h + 0.06):
                    continue
                if p_away_est > (p_home_est + 0.08):
                    continue
            elif mk == "double_chance_X2":
                if clear_away_win:
                    continue  # Pas d'hésitation : Victoire (2) sèche privilégiée !
                if g_h > 0 and g_a > 0 and g_h > (g_a + 0.06):
                    continue
                if p_home_est > (p_away_est + 0.08):
                    continue
            elif mk == "double_chance_12":
                if clear_home_win or clear_away_win:
                    continue  # Pas d'hésitation : Victoire sèche (1 ou 2) privilégiée !
                p_draw_est, _, _, _, _ = compute_probability(md, "match_nul")
                if p_draw_est >= 0.27:
                    continue  # Match trop fermé / risque de nul trop élevé pour 12

            prob, g, d, b, emoji = compute_probability(md, mk)
            proba_pct = prob * 100.0

            if emoji == "⚠️":
                continue

            # Pour une victoire sèche (1 ou 2) où l'issue est claire, on autorise dès 64% (cote ~1.40 - 1.75)
            # Pour les autres marchés (DC, Over/Under, BTTS), on exige >= 71%
            if mk in ("victoire_home", "victoire_away") and (clear_home_win or clear_away_win):
                if proba_pct < 62.0 or proba_pct > AUTH_PROBA_MAX:
                    STATE["rejections"]["auth_proba"] += 1
                    continue
            else:
                if not passes_auth_proba_filter(proba_pct):
                    continue

            if mk == "victoire_away" and p_away_est < 0.65:
                continue

            real_odd = md.real_odds.get(odd_key)
            if not real_odd or float(real_odd) <= 1.01:
                STATE["rejections"]["no_real_odds"] += 1
                continue
            odd = round(float(real_odd), 2)
            is_real = True

            if odd < ODDS3_MIN_COTE_LEG:
                continue
            if not passes_value_filter(prob, odd, is_real, emoji):
                continue

            consensus_bonus = 1 if emoji in ("🤝", "🤝🤝") else 0
            stakes_bonus = compute_stakes_score(md.league, md.analysis)
            # Priorité absolue à la victoire sèche (3) devant la Double Chance (2) et les buts (1), pondérée par l'enjeu réel
            market_priority = 3 if family == "result" else (2 if family in ("dc", "goals", "btts") else 1)
            rank_score = (market_priority, stakes_bonus, consensus_bonus, round(proba_pct, 1))

            match_options.append({
                "event_id": md.event_id,
                "norm_key": f"{normalize_name(md.home)}__{normalize_name(md.away)}",
                "league": md.league,
                "home": md.home,
                "away": md.away,
                "kickoff": md.kickoff,
                "mk": mk,
                "type": label_fn(md),
                "family": family,
                "odds": odd,
                "proba": proba_pct,
                "real_odd": real_odd,
                "consensus": emoji or "🤝",
                "ai": md.analysis or f"Consensus multi-modèles ({proba_pct:.1f}%).",
                "_rank": rank_score,
            })
        # On garde la meilleure option principale par match, et une option secondaire uniquement
        # si elle n'est pas un '12' forcé et si sa cote/proba complète le combinateur quand l'occasion se présente.
        match_options.sort(key=lambda x: x["_rank"], reverse=True)
        kept = 0
        for opt in match_options:
            if opt.get("mk") == "double_chance_12" and kept >= 1:
                continue
            opt.pop("_rank", None)
            vip_candidates.append(opt)
            kept += 1
            if kept >= 2:
                break

    vip_candidates.sort(key=lambda x: (3 if x["family"] == "result" else (2 if x["family"] in ("dc", "goals", "btts") else 1), compute_stakes_score(x["league"], x["ai"]), x["proba"]), reverse=True)
    top_pool = vip_candidates[:28]

    # Priorité exacte du moteur 22/11 : Pyramide Sécurisée (3 sélections), puis Duo (2), puis Quatuor (4)
    for r in (3, 2, 4):
        if len(top_pool) < r:
            continue
        best_combo = None
        best_combo_prob = 0.0
        for combo in itertools.combinations(top_pool, r):
            if not _valid_pro_combo(combo, require_anchor=True):
                continue
            tot_o = math.prod(c["odds"] for c in combo)
            tot_p = math.prod(c["proba"] / 100.0 for c in combo)
            has_real = any(c["real_odd"] is not None for c in combo)
            if ODDS3_AUTH_MIN_COTE <= tot_o <= ODDS3_AUTH_MAX_COTE and tot_p >= AUTH_MIN_COMBINED_PROB:
                if not coupon_passes_ev(tot_o, tot_p, has_real_odds=has_real):
                    continue
                if tot_p > best_combo_prob:
                    best_combo = combo
                    best_combo_prob = tot_p
        if best_combo:
            locked_keys = {c["norm_key"] for c in best_combo}
            label = "Pyramide Sécurisée" if r == 3 else ("Duo Haute Confiance" if r == 2 else "Quatuor Haute Confiance")
            return _build_auth_coupon(best_combo, f"Sélection Pro VIP • {label}", 96), locked_keys

    print("  ℹ️ AUTH VIP : Aucune combinaison >= 71% avec EV >= 1.00 aujourd'hui (protection bankroll).")
    return None, set()
# =====================================================================
# MOTEURS SECONDAIRES (ALIGNÉS SUR LA MÊME RIGUEUR PRO — ZÉRO EV NÉGATIVE)
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

def build_mtft_coupon_and_bonus(pool, locked_keys: set):
    picks, bonus_pool = [], []
    for md in pool:
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        if nkey in locked_keys: continue
        if not md.home or not md.away or not md.dixon_pred or not is_match_today(md.kickoff): continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        is_trap, _ = has_pro_red_flags(md.analysis, md.league)
        if is_trap: continue

        htft = simulate_htft(md.dixon_pred.get("lambda_home", 1.3), md.dixon_pred.get("lambda_away", 1.1))
        best_p, best_prob = None, 0.0
        for c in ("V1/V1", "V2/V2"):
            if htft.get(c, 0) > best_prob: best_prob, best_p = htft.get(c, 0), c
        if not best_p or best_prob < MTFIN_MIN_PROB: continue

        fav_mk = "victoire_home" if best_p == "V1/V1" else "victoire_away"
        fav_consensus, _, _, _, emoji = compute_probability(md, fav_mk)
        if emoji == "⚠️" or fav_consensus < 0.62:
            continue

        odd = calibrated_odd(best_prob, None)
        if not passes_value_filter(best_prob, odd, False, emoji):
            continue

        pts = 15 + (10 if md.has_h2h else 0) + 10 + (30 if best_prob >= 0.65 else 20) + 15 + 10
        if pts < MTFIN_MIN_SCORE: continue
        picks.append({"md": md, "pick": best_p, "prob": best_prob, "score": pts, "odd": odd, "htft": htft})

    coupon = None
    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    top3 = picks[:3]
    if top3:
        legs = [Selection(p["md"].event_id, p["md"].league, p["md"].home, p["md"].away, p["md"].kickoff, "MT-Fin", f"MT/Fin : {p['pick']}", p["odd"], p["prob"], None, -1.0, p["score"], htft_pick=p["pick"]) for p in top3]
        co = round(math.prod(s.odds for s in legs), 2)
        cp = round(math.prod(s.model_prob for s in legs), 4)
        if coupon_passes_ev(co, cp, has_real_odds=False):
            coupon = Coupon("MT-Fin", f"Sélection Pro • {len(legs)} matchs", "⏱", legs, co, cp, is_list=True)

    for p in top3:
        md = p["md"]
        fav_market = f"Victoire {md.home} (1)" if p["pick"] == "V1/V1" else f"Victoire {md.away} (2)"
        real_odd = md.real_odds.get("home") if p["pick"] == "V1/V1" else md.real_odds.get("away")
        ft_res = p["pick"].split("/")[1]
        fav_prob = sum(p["htft"].get(f"{ht}/{ft_res}", 0) for ht in ("V1", "V2", "X"))
        if real_odd and float(real_odd) >= ODDS3_MIN_COTE_LEG and passes_value_filter(fav_prob, float(real_odd), True):
            r_val = round(float(real_odd), 2)
            bonus_pool.append({"match": md, "type": fav_market, "odds": r_val, "proba": fav_prob * 100, "ai": md.analysis, "real_odd": r_val, "source": "MT-FIN", "kickoff": md.kickoff})
    return coupon, bonus_pool

def build_draw_coupon_and_bonus(pool, locked_keys: set):
    candidates, bonus_pool = [], []
    for md in pool:
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        if nkey in locked_keys: continue
        if not md.home or not md.away or not is_match_today(md.kickoff): continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        is_trap, _ = has_pro_red_flags(md.analysis, md.league)
        if is_trap: continue

        prob, g, d, b, emoji = compute_probability(md, "match_nul")
        if prob < DRAW_MIN_PROB or (g > 0 and g < 0.25) or emoji == "⚠️": continue
        draw_odd = md.real_odds.get("draw")
        if not draw_odd or float(draw_odd) < 2.95:
            STATE["rejections"]["no_real_odds"] += 1
            continue
        draw_odd = round(float(draw_odd), 2)
        if (md.real_odds.get("home") and md.real_odds.get("home") < 1.80) or (md.real_odds.get("away") and md.real_odds.get("away") < 1.80): continue
        if not passes_value_filter(prob, draw_odd, True, emoji): continue
        pts = 30 + (25 if prob >= 0.33 else 18) + (20 if g >= 0.30 else 12) + 10 + (5 if md.has_h2h else 0) + 10
        if pts >= DRAW_MIN_SCORE: candidates.append(Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "Match Nul", "Match Nul (X)", draw_odd, prob, draw_odd, (prob*draw_odd)-1, pts))

    coupon = None
    candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    top3 = candidates[:3]
    if top3:
        co = round(math.prod(s.odds for s in top3), 2)
        cp = round(math.prod(s.model_prob for s in top3), 4)
        if coupon_passes_ev(co, cp, has_real_odds=True):
            coupon = Coupon("Match Nul", f"Sélection Pro • {len(top3)} nuls", "🤝", top3, co, cp, is_list=True)

    candidates.sort(key=lambda x: x.model_prob)
    for c in candidates[:3]:
        md = next((m for m in pool if m.event_id == c.event_id), None)
        if not md or not md.real_odds.get("home") or not md.real_odds.get("away"): continue
        p_h, _, _, _, _ = compute_probability(md, "victoire_home"); p_a, _, _, _, _ = compute_probability(md, "victoire_away")
        p_12 = p_h + p_a
        inv_12 = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["away"]))
        odd_12 = round(max(1.03, (1.0 / inv_12) * 0.96), 2) if inv_12 > 0 else 0.0
        if odd_12 >= ODDS3_MIN_COTE_LEG and passes_value_filter(p_12, odd_12, True):
            bonus_pool.append({"match": md, "type": "Double Chance 12", "odds": odd_12, "proba": p_12 * 100, "ai": md.analysis, "real_odd": odd_12, "source": "MATCH-NUL", "kickoff": md.kickoff})
    return coupon, bonus_pool

def build_third_pillar(pool, locked_keys: set):
    pool_result = []
    for md in pool:
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        if nkey in locked_keys: continue
        if not md.home or not md.away or not is_match_today(md.kickoff) or is_reserve_team(md.home) or is_reserve_team(md.away): continue
        is_trap, _ = has_pro_red_flags(md.analysis, md.league)
        if is_trap: continue

        best = None
        for mk, lbl in {"btts_yes": "BTTS Oui", "btts_no": "BTTS Non", "over_1.5": "Over 1.5", "under_2.5": "Under 2.5", "over_2.5": "Over 2.5"}.items():
            prob, g, d, b, emoji = compute_probability(md, mk)
            if prob < 0.62 or emoji == "⚠️": continue
            real_odd = md.real_odds.get(mk)
            if not real_odd or float(real_odd) <= 1.01:
                continue
            odd = round(float(real_odd), 2)
            if odd >= ODDS3_MIN_COTE_LEG and passes_value_filter(prob, odd, True, emoji):
                score = (1, prob * odd, prob)
                if not best or score > best["_score"]:
                    best = {"match": md, "type": lbl, "odds": odd, "proba": prob * 100, "ai": md.analysis, "real_odd": odd, "source": "BZZOIRO", "kickoff": md.kickoff, "_score": score}
        if best:
            best.pop("_score", None)
            pool_result.append(best)
    pool_result.sort(key=lambda x: (1 if x["real_odd"] else 0, x["odds"] * x["proba"] / 100.0, x["proba"]), reverse=True)
    return pool_result[:5]

def build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar, locked_keys: set):
    combined = [c for c in (bonus_mtft + bonus_draw + third_pillar) if f"{normalize_name(c['match'].home)}__{normalize_name(c['match'].away)}" not in locked_keys]
    if not combined: return None
    combined.sort(key=lambda x: (1 if x.get('real_odd') else 0, x['odds'] * x['proba'] / 100.0, x['proba']), reverse=True)

    if bonus_mtft and bonus_draw and third_pillar:
        for r in range(3, min(len(combined), 5) + 1):
            for combo in itertools.combinations(combined[:15], r):
                ids = [_get_match_id(c) for c in combo]
                if len(set(ids)) != len(ids): continue
                if not (any(c.get('source') == 'MT-FIN' for c in combo) and any(c.get('source') == 'MATCH-NUL' for c in combo) and any(c.get('source') == 'BZZOIRO' for c in combo)): continue
                if not all(is_match_today(c.get('kickoff', '')) for c in combo): continue
                tot_o = math.prod(x['odds'] for x in combo)
                if ODDS3_HYB_MIN_COTE <= tot_o <= ODDS3_HYB_MAX_COTE:
                    tot_p = math.prod(c['proba'] / 100.0 for c in combo)
                    has_real = any(c.get('real_odd') is not None for c in combo)
                    if not coupon_passes_ev(tot_o, tot_p, has_real_odds=has_real): continue
                    legs = [Selection(_get_match_id(c), c['match'].league, c['match'].home, c['match'].away, c.get('kickoff', ''), c['type'], c['type'], c['odds'], c['proba']/100.0, c['real_odd'], 90, analysis=clean_analysis_text(c['ai'], 260)) for c in combo]
                    return Coupon("Odds +3 HYBRIDE", f"Mélange 3 sources • {len(legs)} sélections", "🎯", legs, round(tot_o, 2), round(tot_p, 4), show_analysis=True)

    for r in (3, 4):
        if len(combined) < r: break
        for combo in itertools.combinations(combined[:12], r):
            ids = [_get_match_id(c) for c in combo]
            if len(set(ids)) != len(ids): continue
            if not all(is_match_today(c.get('kickoff', '')) for c in combo): continue
            tot_o = math.prod(x['odds'] for x in combo)
            if ODDS3_HYB_MIN_COTE <= tot_o <= ODDS3_HYB_MAX_COTE:
                tot_p = math.prod(c['proba'] / 100.0 for c in combo)
                has_real = any(c.get('real_odd') is not None for c in combo)
                if not coupon_passes_ev(tot_o, tot_p, has_real_odds=has_real): continue
                legs = [Selection(_get_match_id(c), c['match'].league, c['match'].home, c['match'].away, c.get('kickoff', ''), c['type'], c['type'], c['odds'], c['proba']/100.0, c['real_odd'], 85, analysis=clean_analysis_text(c['ai'], 260)) for c in combo]
                return Coupon("Odds +3 HYBRIDE", f"Top Value Multi-IA • {len(legs)} sélections", "🎯", legs, round(tot_o, 2), round(tot_p, 4), show_analysis=True)
    return None

# =====================================================================
# SCAN GLOBAL (DOUBLE SCAN + CACHE JOURNALIER)
# =====================================================================
async def scan(force: bool = False) -> bool:
    async with STATE["scan_lock"]:
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        if not force and load_daily_cache(today_str):
            print(f"📦 Cache journalier actif ({today_str}) : {len(STATE['coupons'])} coupons chargés sans rescan.")
            return False

        STATE["rejections"] = {"value": 0, "ev_coupon": 0, "auth_proba": 0, "pro_trap": 0, "unstable": 0, "no_real_odds": 0}
        ws_matches, odds_status = await fetch_rapidodds_odds()
        
        bzz_preds = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try: bzz_preds = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except Exception as e: print(f"⚠️ Bzzoiro erreur : {e}")
        
        infer_odds = {}
        if bzz_preds and fetch_infer_odds:
            try: infer_odds = await fetch_infer_odds([{"event_id": b["event_id"], "home": b["home"], "away": b["away"], "kickoff": b.get("kickoff", "")} for b in bzz_preds])
            except: pass

        enriched_pool = []
        matched = 0
        pool = []
        seen_pool_keys = set()
        for bz in (bzz_preds or []):
            base_odds = dict(infer_odds.get(bz["event_id"], {}))
            base_odds.update(bz.get("real_odds", {}))
            md = MatchData(bz["event_id"], bz.get("league", ""), bz["home"], bz["away"], bz.get("kickoff", ""), base_odds, bz.get("bzz_markets", {}), bz.get("bzz_confidence", 0))
            if STATE.get("dixon_model"):
                try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                except: pass
            pool.append(md)
            seen_pool_keys.add(f"{normalize_name(md.home)}__{normalize_name(md.away)}")

        # Ajout des matchs ESPN cotés non encore présents dans Bzzoiro
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
            if STATE.get("dixon_model"):
                try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                except: pass
            pool.append(md)
            seen_pool_keys.add(nk)

        if pool:
            matched = inject_all_real_odds(pool, ws_matches)
            print(f"⏰ Total matchs avec vraies cotes : {matched}/{len(pool)}")
            pool = [
                md for md in pool
                if is_match_today(md.kickoff)
                and not is_low_stakes_competition(md.league)
                and bool(md.real_odds)
            ]
            print(f"💰 Matchs retenus après verrou VRAIES COTES uniquement : {len(pool)}")
            pool.sort(key=prescore_match, reverse=True)
            print(f"🎯 Top {min(len(pool), DOUBLE_SCAN_POOL_SIZE)} matchs sélectionnés pour Double Analyse Gemini")
            enriched_pool = await enrich_with_double_pass(pool[:GEMINI_MAX_CALLS_PER_SCAN])

        if ws_matches or matched > 0:
            odds_status = "live"
        STATE["odds_api_mode"] = odds_status

        coupon_auth, locked_vip_keys = build_exclusive_vip_auth_coupon(enriched_pool, ws_matches)
        if locked_vip_keys:
            print(f"🔒 Matchs verrouillés pour VIP AUTH : {locked_vip_keys}")

        coupon_mtft, bonus_mtft = build_mtft_coupon_and_bonus(enriched_pool, locked_vip_keys) if enriched_pool else (None, [])
        coupon_draw, bonus_draw = build_draw_coupon_and_bonus(enriched_pool, locked_vip_keys) if enriched_pool else (None, [])
        third_pillar = build_third_pillar(enriched_pool, locked_vip_keys) if enriched_pool else []
        coupon_hybrid = build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar, locked_vip_keys)

        coupons = []
        if coupon_auth: coupons.append(coupon_auth)
        if coupon_hybrid: coupons.append(coupon_hybrid)
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

        print(f"──────── RÉSUMÉ ────────")
        print(f"Rejets filtres : {STATE['rejections']}")
        print(f"Coupons validés : {len(STATE['coupons'])}")
        for c in STATE["coupons"]: print(f"  • {c.name} : {len(c.legs)} matchs, cote {c.combined_odds}, proba {c.combined_prob*100:.1f}%, EV {c.combined_odds*c.combined_prob:.2f}")
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
    if not STATE["coupons"]: msg = f"<b>📭 Aucun coupon fiable aujourd'hui</b>\n<i>(Critères Pro stricts : protection de bankroll)</i>"
    else:
        lignes = [f"{c.emoji} <b>{c.name}</b> • {len(c.legs)} paris • Cote <b>{c.combined_odds}</b>" for c in STATE["coupons"]]
        msg = f"<b>🔔 COUPONS DU JOUR !</b>\n<i>Mode : {mode_label}</i>\n\n" + "\n\n".join(lignes)
    for cid in TARGETS: await safe_send(cid, msg)

def format_header():
    mode = STATE.get("odds_api_mode", "unknown")
    mode_icon = "🟢" if mode == "live" else "🟡" if mode == "fallback" else "⚪"
    mode_txt = "LIVE" if mode == "live" else "FALLBACK" if mode == "fallback" else "?"
    return f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n<i>{BRAND_TAGLINE}</i>\n<i>📅 {today_pretty()}</i>\n<i>{mode_icon} Mode : {mode_txt}</i>"

def format_selection_block(s, idx, show_analysis):
    lines = [f"<b>┌─ #{idx} {s.home} vs {s.away}</b>", f"│ 📅 <b>{format_kickoff(s.kickoff)}</b> • {s.league}", f"│ 🎯 <b>{s.pick_label}</b>", f"│ 📊 Confiance : <b>{s.model_prob*100:.1f}%</b> {prob_bar(s.model_prob)}"]
    if s.real_odd: lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b>")
    else: lines.append(f"│ 💰 Cote : <b>{s.odds}</b>")
    if show_analysis and s.analysis: lines.append(f"│ 🧠 <i>{clean_analysis_text(s.analysis, 520)}</i>")
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
    """Parse '/semaine 24/11-25/11 15', '/semaine 24/11 25/11 15', '/semaine 24/11 15', '/semaine 15' ou '/semaine 3 15'."""
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

    # Filtre strict Pro appliqué aussi sur Top Semaine (aucune EV négative + enjeu réel obligatoire)
    filtered_picks = []
    for p in (picks or []):
        is_trap, _ = has_pro_red_flags(p.get("ai", ""), p.get("league", ""))
        if is_trap:
            continue
        prob = float(p.get("proba", 0)) / 100.0
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
        await safe_answer(m, f"📭 Aucun match fiable à EV positive trouvé pour la période <b>{period_label}</b>.")
        return

    total_odd = round(math.prod(p["odd"] for p in filtered_picks), 2)
    total_prob = round(math.prod(p["proba"] / 100.0 for p in filtered_picks), 4)
    total_ev = total_odd * total_prob
    lines = [
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>",
        f"<b>📅 TOP {len(filtered_picks)} MATCHS • {period_label.upper()}</b>",
        f"<b>🎫 COTE COMBINÉE : {total_odd} • 💎 EV : {total_ev:.2f}</b>",
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>",
        f"<i>Stratégie Pro (20 ans) : Fidélité au scénario & haute confiance</i>\n",
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
        await safe_answer(m, "📭 Aucun ticket VIP aujourd'hui : aucun match ne passe tous nos filtres de sécurité pro (protection de bankroll).")
    if m.from_user.id == ADMIN_ID:
        c_hyb = next((x for x in STATE["coupons"] if x.name == "Odds +3 HYBRIDE"), None)
        if c_hyb:
            await safe_answer(m, format_coupon(c_hyb))

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
