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
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception as e:
    enrich_match_with_bzzoiro = None
    print(f"❌ Import bzzoiro_data: {e}")

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

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

try:
    ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except ValueError:
    ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "10"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")

BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • H2H + Gemini")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

MAX_MATCHES_TO_ANALYZE = int(os.getenv("MAX_MATCHES_TO_ANALYZE", "40"))
MAX_MATCHES_TO_ENRICH = int(os.getenv("MAX_MATCHES_TO_ENRICH", "15"))
GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "20"))

BZZ_MIN_CONFIDENCE = float(os.getenv("BZZ_MIN_CONFIDENCE", "65.0"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

ODDS3_MIN_ODDS = 3.0
ODDS3_MAX_ODDS = 4.0
ODDS3_MIN_PROB_PER_LEG = 0.65
ODDS3_MIN_LEG_ODDS = 1.15
ODDS3_MAX_LEG_ODDS = 1.90
ODDS3_MAX_LEGS = 5

ODDS10_MIN_ODDS = 10.0
ODDS10_MIN_PROB_PER_LEG = 0.55
ODDS10_MIN_LEG_ODDS = 1.35
ODDS10_MAX_LEG_ODDS = 2.60
ODDS10_MAX_LEGS = 10

MTFIN_TOP_N = 3
MTFIN_MIN_PROB = 0.50
MTFIN_SIMULATIONS = 1000
MTFIN_FIRST_HALF_RATIO = 0.45

SCORE_EXACT_TARGETS = [(1,2), (0,2), (0,1), (2,1), (2,0), (1,0)]
SCORE_EXACT_TOP_N = 3
SCORE_EXACT_MIN_PROB = 0.50

KICKOFF_MIN_MINUTES_AHEAD = 30

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

TOP5_SPORT_KEYS = {
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
}

TOP5_LEAGUES_KW = {
    "premier league", "epl", "la liga", "laliga", "serie a",
    "bundesliga", "ligue 1", "ligue1", "champions league", "europa league",
    "uefa champions", "uefa europa",
}

def is_top_league(league: str, sport_key: str = "") -> bool:
    if sport_key and sport_key in TOP5_SPORT_KEYS:
        return True
    if not league:
        return False
    low = league.lower()
    return any(kw in low for kw in TOP5_LEAGUES_KW)

def is_obscure_league(league: str, sport_key: str = "") -> bool:
    if not league and not sport_key:
        return False
    return not is_top_league(league, sport_key)

def today_pretty():
    months = {1:"janvier",2:"février",3:"mars",4:"avril",5:"mai",6:"juin",
              7:"juillet",8:"août",9:"septembre",10:"octobre",11:"novembre",12:"décembre"}
    d = datetime.now(TZ)
    return f"{d.day} {months[d.month]} {d.year}"

def normalize_name(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_dt_safe(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(int(s), tz=ZoneInfo("UTC")).astimezone(TZ)
        except Exception:
            return None
    s = str(s).strip()
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None

def kickoff_local(iso_str) -> str:
    dt = parse_dt_safe(iso_str)
    if not dt:
        return "?"
    return dt.strftime("%d/%m %H:%M")

def is_match_upcoming(kickoff_str, min_minutes: int = KICKOFF_MIN_MINUTES_AHEAD) -> bool:
    if not kickoff_str:
        return True
    dt = parse_dt_safe(kickoff_str)
    if not dt:
        return True
    return dt > datetime.now(TZ) + timedelta(minutes=min_minutes)

def prob_bar(p: float) -> str:
    if p >= 0.80:
        return "🟢🟢🟢"
    if p >= 0.72:
        return "🟢🟢⚪"
    if p >= 0.65:
        return "🟢⚪⚪"
    return "⚪⚪⚪"

def confidence_bar(pct: int) -> str:
    if pct >= 80:
        return "🟢🟢🟢"
    if pct >= 70:
        return "🟢🟢⚪"
    if pct >= 60:
        return "🟢⚪⚪"
    return "⚪⚪⚪"

def split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                parts.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts

def _poisson_sample(lam: float) -> int:
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L and k < 20:
        k += 1
        p *= random.random()
    return k - 1

def _binomial_sample(n: int, p: float) -> int:
    return sum(1 for _ in range(n) if random.random() < p)

def simulate_htft(lambda_home: float, lambda_away: float, n_sims: int = MTFIN_SIMULATIONS) -> dict:
    counts: dict = {}
    for _ in range(n_sims):
        hg = _poisson_sample(lambda_home)
        ag = _poisson_sample(lambda_away)
        ht_hg = _binomial_sample(hg, MTFIN_FIRST_HALF_RATIO)
        ht_ag = _binomial_sample(ag, MTFIN_FIRST_HALF_RATIO)
        ht_res = "V1" if ht_hg > ht_ag else ("X" if ht_hg == ht_ag else "V2")
        ft_res = "V1" if hg > ag else ("X" if hg == ag else "V2")
        key = f"{ht_res}/{ft_res}"
        counts[key] = counts.get(key, 0) + 1
    return {k: round(v / n_sims, 4) for k, v in counts.items()}

def best_htft(probs: dict) -> tuple[str, float]:
    if not probs:
        return ("", 0.0)
    return max(probs.items(), key=lambda x: x[1])

W_GEMINI = 0.35
W_DIXON = 0.25
W_BZZOIRO = 0.40

ALL_MARKETS = [
    "victoire_home", "match_nul", "victoire_away",
    "over_2.5", "under_2.5", "over_3.5",
    "btts_yes", "btts_no", "btts_over_25",
]

def combine_three_sources(gemini_markets: dict, dixon_pred: Optional[dict], bzz_markets: dict) -> dict:
    result = {}
    g = {}
    for k in ALL_MARKETS:
        v = gemini_markets.get(k)
        if v is not None:
            try:
                g[k] = max(0.0, min(1.0, float(v) / 100.0))
            except (ValueError, TypeError):
                pass

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
                try:
                    b[k] = max(0.0, min(1.0, float(v) / 100.0))
                except (ValueError, TypeError):
                    pass

    all_keys = set(g.keys()) | set(d.keys()) | set(b.keys())
    for k in all_keys:
        tw, tv = 0.0, 0.0
        if k in g:
            tv += W_GEMINI * g[k]
            tw += W_GEMINI
        if k in d and d[k] > 0:
            tv += W_DIXON * d[k]
            tw += W_DIXON
        if k in b:
            tv += W_BZZOIRO * b[k]
            tw += W_BZZOIRO
        if tw > 0:
            result[k] = tv / tw

    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = result["victoire_home"] + result["match_nul"]
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = result["victoire_home"] + result["victoire_away"]
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = result["match_nul"] + result["victoire_away"]

    return result

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
    has_context: bool
    bzz_confidence: float
    analysis: str = ""
    advices: list = None
    score_exact_home: int = -1
    score_exact_away: int = -1
    score_exact_prob: float = 0.0
    sport_key: str = ""

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
    real_odd: Optional[float] = None
    analysis: str = ""
    advices: list = None
    has_context: bool = False
    prob_gemini: float = 0.0
    prob_dixon: float = 0.0
    prob_bzzoiro: float = 0.0
    bzz_confidence: float = 0.0
    score_exact_home: int = -1
    score_exact_away: int = -1
    score_exact_prob: float = 0.0
    htft_pick: str = ""
    htft_prob: float = 0.0

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

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_ODDS3 = "🎯 Odds +3"
BTN_ODDS10 = "🔥 Odds +10"
BTN_MTFT = "⏱ MT-Fin"
BTN_SCOREEXACT = "⚽ Score Exact"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"
BTN_ACTIVATE = "🔑 Activer mon abonnement"

USER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_ODDS10)],
        [KeyboardButton(text=BTN_MTFT), KeyboardButton(text=BTN_SCOREEXACT)],
        [KeyboardButton(text=BTN_BILAN), KeyboardButton(text=BTN_ACTIVATE)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choisis un ticket…",
)

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_ODDS10)],
        [KeyboardButton(text=BTN_MTFT), KeyboardButton(text=BTN_SCOREEXACT)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Debug")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Admin: Choisis une action…",
)

STATE = {
    "ready": False, "candidates": [], "coupons": [],
    "last_scan": None, "debug": {}, "tracker": {"coupons": []}, "keys": {}, "users": {},
    "scan_lock": asyncio.Lock(), "dixon_model": None,
}

TARGETS = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try:
            TARGETS.append(int(raw))
        except ValueError:
            TARGETS.append(raw)

def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def is_subscribed(user_id: int) -> bool:
    uid = str(user_id)
    if uid not in STATE["users"]:
        return False
    exp = STATE["users"][uid].get("expires_at")
    if not exp:
        return False
    try:
        return datetime.now(TZ) < datetime.fromisoformat(exp)
    except Exception:
        return False

def get_subscription_message(user_id: int) -> str:
    if is_subscribed(user_id):
        exp = STATE["users"][str(user_id)]["expires_at"]
        return f"✅ Abonnement actif jusqu'au {datetime.fromisoformat(exp).strftime('%d/%m/%Y à %H:%M')}."
    return "❌ Abonnement expiré ou inexistant.\nContactez : @hermann1123"

def load_tracker() -> dict:
    try:
        with open(LOCAL_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"coupons": []}

def save_tracker(data: dict):
    try:
        with open(LOCAL_DB, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def load_used_today() -> set:
    tracker = STATE["tracker"]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    u = tracker.get("used_today", {})
    if u.get("date") != today:
        return set()
    return set(u.get("ids", []))

def save_used_today(ids: set):
    tracker = STATE["tracker"]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    existing = tracker.get("used_today", {})
    merged = set(existing.get("ids", [])) | ids if existing.get("date") == today else ids
    tracker["used_today"] = {"date": today, "ids": list(merged)}
    save_tracker(tracker)

def mark_picks_used(selections: list):
    if selections:
        save_used_today({s.event_id for s in selections})

def record_coupons(coupons: list[Coupon], date_str: str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing:
            continue
        legs_data = []
        for s in c.legs:
            legs_data.append({
                "event_id": s.event_id, "home": s.home, "away": s.away,
                "market": s.market, "pick_label": s.pick_label,
                "odds": s.odds, "kickoff": s.kickoff, "result": "pending",
            })
        tracker["coupons"].append({
            "id": cid, "date": date_str, "name": c.name,
            "combined_odds": c.combined_odds,
            "legs": legs_data, "status": "pending",
        })
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)

async def settle_coupons():
    print("⚠️ settle_coupons désactivé")

async def fetch_odds_api_league(client: httpx.AsyncClient, sport_key: str) -> list[dict]:
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {"apiKey": ODDS_API_KEY, "regions": "eu",
              "markets": "h2h,totals,btts", "oddsFormat": "decimal"}
    try:
        r = await client.get(url, params=params, timeout=30.0)
        if r.status_code >= 400:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []

async def fetch_all_odds_events() -> list[dict]:
    all_events = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for key in SOCCER_KEYS:
            events = await fetch_odds_api_league(client, key)
            all_events.extend(events)
            await asyncio.sleep(0.15)
    print(f"💰 Odds API : {len(all_events)} events")
    if not all_events:
        print("🚨 ALERTE : The Odds API renvoie 0 match !")
    return all_events

def extract_real_odds(ev: dict) -> dict:
    result = {"home": None, "draw": None, "away": None,
              "over_2.5": None, "under_2.5": None,
              "btts_yes": None, "btts_no": None}
    home = ev.get("home_team", "")
    away = ev.get("away_team", "")
    for book in ev.get("bookmakers", []) or []:
        for market in book.get("markets", []) or []:
            mk = market.get("key", "")
            for out in market.get("outcomes", []) or []:
                try:
                    price = float(out.get("price", 0))
                except (ValueError, TypeError):
                    continue
                if price <= 1.01:
                    continue
                name = out.get("name", "")
                if mk == "h2h":
                    if name == home and result["home"] is None:
                        result["home"] = price
                    elif name == away and result["away"] is None:
                        result["away"] = price
                    elif "draw" in name.lower() and result["draw"] is None:
                        result["draw"] = price
                elif mk == "totals" and out.get("point") == 2.5:
                    if "over" in name.lower() and result["over_2.5"] is None:
                        result["over_2.5"] = price
                    elif "under" in name.lower() and result["under_2.5"] is None:
                        result["under_2.5"] = price
                elif mk == "btts":
                    if "yes" in name.lower() and result["btts_yes"] is None:
                        result["btts_yes"] = price
                    elif "no" in name.lower() and result["btts_no"] is None:
                        result["btts_no"] = price
    return result

def build_matches_from_odds(odds_events: list[dict]) -> list[dict]:
    matches = []
    for ev in odds_events:
        h = ev.get("home_team", "")
        a = ev.get("away_team", "")
        kick = ev.get("commence_time", "")
        if not h or not a:
            continue
        if not is_match_upcoming(kick):
            continue
        matches.append({
            "event_id": ev.get("id", ""), "home": h, "away": a,
            "league": ev.get("sport_title", ""), "kickoff": kick,
            "has_odds": True, "real_odds": extract_real_odds(ev),
            "odds_sport_key": ev.get("sport_key", ""),
            "bzz_markets": {}, "bzz_confidence": 0,
        })
    return matches

async def enrich_matches(matches: list[dict]) -> dict:
    to_enrich = matches[:MAX_MATCHES_TO_ENRICH]
    if not to_enrich:
        return {}
    contexts = {}
    for fx in to_enrich:
        try:
            home = fx.get("home", "")
            away = fx.get("away", "")
            dt = parse_dt_safe(fx.get("kickoff", ""))
            date_iso = dt.strftime("%Y-%m-%d") if dt else datetime.now(TZ).strftime("%Y-%m-%d")
            ctx = None
            if enrich_match_with_bzzoiro:
                ctx = await enrich_match_with_bzzoiro(home, away, date_iso)
            if ctx and ctx.get("available"):
                contexts[fx["event_id"]] = ctx
            await asyncio.sleep(0.2)
        except Exception:
            pass
    return contexts

async def build_all_candidates(matches, used_today, contexts=None):
    candidates: list[Candidate] = []
    if not predict_match_probas:
        return candidates
    contexts = contexts or {}
    ordered = [m for m in matches if m.get("has_odds") or m.get("bzz_markets")]

    print(f"🎯 Analyse Gemini forcée (Avis IA garanti) — max {GEMINI_MAX_CALLS_PER_SCAN}")

    async with httpx.AsyncClient(timeout=40.0) as client:
        for idx, fx in enumerate(ordered[:GEMINI_MAX_CALLS_PER_SCAN]):
            try:
                home, away = fx["home"], fx["away"]
                event_id, league = fx["event_id"], fx["league"]
                kickoff = fx["kickoff"]
                sport_key = fx.get("odds_sport_key", "")
                if event_id in used_today:
                    continue

                print(f"  [{idx+1}/{min(len(ordered), GEMINI_MAX_CALLS_PER_SCAN)}] {home} vs {away}")

                ctx = contexts.get(event_id)
                has_ctx = ctx is not None and ctx.get("available", False)
                bzz_markets_direct = fx.get("bzz_markets", {})
                bzz_conf_direct = fx.get("bzz_confidence", 0)

                pred = await predict_match_probas(client, home, away, league, kickoff, context=ctx)
                if not pred or "markets" not in pred:
                    continue

                gemini_markets = pred.get("markets", {})
                analysis = pred.get("analysis", "")
                advices_clean = []
                for a in (pred.get("advices") or [])[:2]:
                    if isinstance(a, dict) and a.get("text"):
                        try:
                            conf = int(a.get("confidence", 0))
                        except (ValueError, TypeError):
                            conf = 0
                        advices_clean.append({"text": str(a.get("text", "")).strip(), "confidence": conf})

                score_home, score_away, score_prob = -1, -1, 0.0
                se = pred.get("score_exact") or {}
                if isinstance(se, dict):
                    try:
                        sh = int(se.get("home", -1))
                        sa = int(se.get("away", -1))
                        sp = float(se.get("prob", 0))
                        if 0 <= sh <= 8 and 0 <= sa <= 8 and sp > 0:
                            score_home, score_away = sh, sa
                            score_prob = sp if sp <= 1.0 else sp / 100.0
                    except (ValueError, TypeError):
                        pass

                dixon_pred = STATE["dixon_model"].predict_all(home, away) if STATE.get("dixon_model") else None
                combined_probs = combine_three_sources(gemini_markets, dixon_pred, bzz_markets_direct)
                real_odds = fx.get("real_odds", {})

                market_map = {
                    "victoire_home": (f"Victoire {home}", "1X2", real_odds.get("home")),
                    "match_nul": ("Match nul", "1X2", real_odds.get("draw")),
                    "victoire_away": (f"Victoire {away}", "1X2", real_odds.get("away")),
                    "double_chance_1X": (f"Double Chance 1X", "Double Chance", None),
                    "double_chance_X2": (f"Double Chance X2", "Double Chance", None),
                    "over_2.5": ("Over 2.5 buts", "Over/Under", real_odds.get("over_2.5")),
                    "under_2.5": ("Under 2.5 buts", "Over/Under", real_odds.get("under_2.5")),
                    "btts_yes": ("BTTS Oui", "BTTS", real_odds.get("btts_yes")),
                    "btts_no": ("BTTS Non", "BTTS", real_odds.get("btts_no")),
                }

                for key, (label, mkt, real_odd) in market_map.items():
                    prob = combined_probs.get(key)
                    if prob is None or prob < 0.40 or prob > 0.95:
                        continue

                    used_odd = real_odd if real_odd else 1.0 / prob
                    if used_odd < 1.05:
                        continue

                    edge = (prob * used_odd) - 1.0 if real_odd else -1.0

                    try:
                        pg = float(gemini_markets.get(key, 0)) / 100.0
                    except (ValueError, TypeError):
                        pg = 0.0
                    pd_ = 0.0
                    if dixon_pred:
                        dk = {"victoire_home": "p_home", "match_nul": "p_draw", "victoire_away": "p_away",
                              "over_2.5": "over_2.5", "under_2.5": "under_2.5",
                              "btts_yes": "btts_yes", "btts_no": "btts_no"}.get(key)
                        if dk:
                            pd_ = dixon_pred.get(dk, 0.0)
                    pb = 0.0
                    if bzz_markets_direct:
                        bv = bzz_markets_direct.get(key)
                        if bv is not None:
                            try:
                                pb = float(bv) / 100.0
                            except (ValueError, TypeError):
                                pass

                    candidates.append(Candidate(
                        event_id=event_id, league=league, home=home, away=away,
                        kickoff=kickoff, market=mkt, pick_label=label,
                        odds=round(used_odd, 2), prob=round(prob, 4), edge=round(edge, 4),
                        real_odd=real_odd,
                        prob_gemini=round(pg, 4), prob_dixon=round(pd_, 4), prob_bzzoiro=round(pb, 4),
                        has_context=has_ctx, bzz_confidence=bzz_conf_direct,
                        analysis=analysis, advices=advices_clean,
                        score_exact_home=score_home, score_exact_away=score_away,
                        score_exact_prob=score_prob,
                        sport_key=sport_key,
                    ))

                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"⚠️ build_all_candidates: {e}")

    print(f"🏠 Candidats totaux : {len(candidates)}")
    return candidates

def candidate_to_selection(c: Candidate) -> Selection:
    return Selection(
        event_id=c.event_id, league=c.league, home=c.home, away=c.away,
        kickoff=c.kickoff, market=c.market, pick_label=c.pick_label,
        odds=c.odds, model_prob=c.prob, edge=c.edge,
        bookmaker="The Odds API" if c.real_odd else "Estimation",
        real_odd=c.real_odd, analysis=c.analysis, advices=c.advices,
        has_context=c.has_context,
        prob_gemini=c.prob_gemini, prob_dixon=c.prob_dixon, prob_bzzoiro=c.prob_bzzoiro,
        bzz_confidence=c.bzz_confidence,
        score_exact_home=c.score_exact_home, score_exact_away=c.score_exact_away,
        score_exact_prob=c.score_exact_prob,
    )

def build_odds3_coupon(candidates: list[Candidate]) -> Optional[Coupon]:
    pool = []
    for c in candidates:
        if not is_top_league(c.league, c.sport_key):
            continue
        if c.real_odd is None:
            continue
        if c.market not in ("Double Chance", "Over/Under", "BTTS", "1X2"):
            continue
        if c.prob < ODDS3_MIN_PROB_PER_LEG:
            continue
        if not (ODDS3_MIN_LEG_ODDS <= c.odds <= ODDS3_MAX_LEG_ODDS):
            continue
        pool.append(c)

    print(f"  → Odds +3 : {len(pool)} candidats (Top 5 + cote réelle)")

    by_event = {}
    for c in pool:
        if c.event_id not in by_event or c.prob > by_event[c.event_id].prob:
            by_event[c.event_id] = c
    pool = sorted(by_event.values(), key=lambda x: x.prob, reverse=True)

    if len(pool) < 2:
        return None

    legs, used = [], set()
    combined_odds, combined_prob = 1.0, 1.0
    for c in pool:
        if len(legs) >= ODDS3_MAX_LEGS:
            break
        if c.event_id in used:
            continue
        next_odds = combined_odds * c.odds
        if next_odds > ODDS3_MAX_ODDS and legs:
            continue
        legs.append(candidate_to_selection(c))
        used.add(c.event_id)
        combined_odds = next_odds
        combined_prob *= c.prob
        if combined_odds >= ODDS3_MIN_ODDS:
            break

    if combined_odds < ODDS3_MIN_ODDS or combined_odds > ODDS3_MAX_ODDS:
        print(f"  → Odds +3 : cote {combined_odds:.2f} hors fourchette")
        return None
    if len(legs) < 2:
        return None

    print(f"  → Odds +3 OK : {len(legs)} legs, cote {combined_odds:.2f}")
    return Coupon("Odds +3", f"Cote {ODDS3_MIN_ODDS}-{ODDS3_MAX_ODDS} • Top 5 + cotes réelles",
                  "🎯", legs, round(combined_odds, 2), round(combined_prob, 4))

def build_odds10_coupon(candidates: list[Candidate]) -> Optional[Coupon]:
    pool = []
    for c in candidates:
        if not is_obscure_league(c.league, c.sport_key):
            continue
        if c.market not in ("Double Chance", "Over/Under", "BTTS", "1X2"):
            continue
        if c.prob < ODDS10_MIN_PROB_PER_LEG:
            continue
        if not (ODDS10_MIN_LEG_ODDS <= c.odds <= ODDS10_MAX_LEG_ODDS):
            continue
        pool.append(c)

    print(f"  → Odds +10 : {len(pool)} candidats (obscures)")

    by_event = {}
    for c in pool:
        if c.event_id not in by_event or c.prob > by_event[c.event_id].prob:
            by_event[c.event_id] = c
    pool = sorted(by_event.values(), key=lambda x: (x.prob, x.edge if x.edge > 0 else 0), reverse=True)

    if len(pool) < 3:
        return None

    legs, used = [], set()
    combined_odds, combined_prob = 1.0, 1.0
    for c in pool:
        if len(legs) >= ODDS10_MAX_LEGS:
            break
        if c.event_id in used:
            continue
        legs.append(candidate_to_selection(c))
        used.add(c.event_id)
        combined_odds *= c.odds
        combined_prob *= c.prob
        if combined_odds >= ODDS10_MIN_ODDS:
            break

    if combined_odds < ODDS10_MIN_ODDS:
        return None
    if len(legs) < 3:
        return None

    print(f"  → Odds +10 OK : {len(legs)} legs, cote {combined_odds:.2f}")
    return Coupon("Odds +10", f"Cote ≥ {ODDS10_MIN_ODDS} • Ligues obscures",
                  "🔥", legs, round(combined_odds, 2), round(combined_prob, 4))

def build_mtft_coupon(matches: list[dict]) -> Optional[Coupon]:
    if not STATE.get("dixon_model"):
        print("  → MT-Fin : Dixon non disponible")
        return None

    print(f"  → MT-Fin : analyse de {len(matches)} matchs")

    picks = []
    for fx in matches:
        home, away = fx["home"], fx["away"]
        league = fx.get("league", "")
        sport_key = fx.get("odds_sport_key", "")
        if not is_top_league(league, sport_key):
            continue

        try:
            dixon = STATE["dixon_model"].predict_all(home, away)
        except Exception:
            dixon = None
        if not dixon:
            continue

        lam_h = dixon.get("lambda_home", 1.3)
        lam_a = dixon.get("lambda_away", 1.1)
        htft_probs = simulate_htft(lam_h, lam_a, MTFIN_SIMULATIONS)
        pick, prob = best_htft(htft_probs)

        if not pick or prob < MTFIN_MIN_PROB:
            continue

        picks.append({
            "home": home, "away": away, "league": league,
            "kickoff": fx.get("kickoff", ""), "event_id": fx.get("event_id", ""),
            "htft_pick": pick, "htft_prob": prob,
        })

    print(f"  → MT-Fin : {len(picks)} matchs ≥ {MTFIN_MIN_PROB*100:.0f}%")

    if len(picks) < MTFIN_TOP_N:
        print(f"  → MT-Fin : besoin de {MTFIN_TOP_N}, seulement {len(picks)}")
        return None

    picks.sort(key=lambda x: x["htft_prob"], reverse=True)
    top = picks[:MTFIN_TOP_N]

    legs = []
    for p in top:
        est_odd = round(1.0 / p["htft_prob"], 2) if p["htft_prob"] > 0 else 3.5
        sel = Selection(
            event_id=p["event_id"], league=p["league"], home=p["home"], away=p["away"],
            kickoff=p["kickoff"], market="MT-Fin",
            pick_label=f"Mi-temps/Fin : {p['htft_pick']}",
            odds=est_odd, model_prob=p["htft_prob"], edge=-1.0,
            bookmaker="Monte Carlo Dixon",
            htft_pick=p["htft_pick"], htft_prob=p["htft_prob"],
        )
        legs.append(sel)
        print(f"     🥇 {p['home']} vs {p['away']} : {p['htft_pick']} ({p['htft_prob']*100:.1f}%)")

    combined_odds = 1.0
    combined_prob = 1.0
    for s in legs:
        combined_odds *= s.odds
        combined_prob *= s.final_prob()

    return Coupon("MT-Fin", "Mi-Temps/Fin • 3 matchs",
                  "⏱", legs, round(combined_odds, 2), round(combined_prob, 4), is_list=True)

def build_score_exact_coupon(matches: list[dict]) -> Optional[Coupon]:
    if not STATE.get("dixon_model"):
        print("  → Score Exact : Dixon non disponible")
        return None

    print(f"  → Score Exact : analyse de {len(matches)} matchs")

    picks = []
    for fx in matches:
        home, away = fx["home"], fx["away"]

        try:
            dixon = STATE["dixon_model"].predict_all(home, away)
        except Exception:
            dixon = None
        if not dixon:
            continue

        lam_h = dixon.get("lambda_home", 1.3)
        lam_a = dixon.get("lambda_away", 1.1)

        def pmf(lam, k):
            return (math.exp(-lam) * lam**k) / math.factorial(k)

        score_probs = {}
        for (h, a) in SCORE_EXACT_TARGETS:
            score_probs[(h, a)] = pmf(lam_h, h) * pmf(lam_a, a)

        cumulative = sum(score_probs.values())
        if cumulative < SCORE_EXACT_MIN_PROB:
            continue

        best_score = max(score_probs.items(), key=lambda x: x[1])
        (bh, ba), bp = best_score

        picks.append({
            "home": home, "away": away, "league": fx.get("league", ""),
            "kickoff": fx.get("kickoff", ""), "event_id": fx.get("event_id", ""),
            "best_score": (bh, ba), "best_score_prob": bp,
            "cumulative_prob": cumulative,
        })

    print(f"  → Score Exact : {len(picks)} matchs ≥ {SCORE_EXACT_MIN_PROB*100:.0f}%")

    if len(picks) < SCORE_EXACT_TOP_N:
        print(f"  → Score Exact : besoin de {SCORE_EXACT_TOP_N}, seulement {len(picks)}")
        return None

    picks.sort(key=lambda x: x["best_score_prob"], reverse=True)
    top = picks[:SCORE_EXACT_TOP_N]

    legs = []
    for p in top:
        bh, ba = p["best_score"]
        est_odd = round(1.0 / p["best_score_prob"], 2) if p["best_score_prob"] > 0 else 10.0
        sel = Selection(
            event_id=p["event_id"], league=p["league"], home=p["home"], away=p["away"],
            kickoff=p["kickoff"], market="Score Exact",
            pick_label=f"Score exact : {bh}-{ba}",
            odds=est_odd, model_prob=p["best_score_prob"], edge=-1.0,
            bookmaker="Dixon Poisson",
            score_exact_home=bh, score_exact_away=ba, score_exact_prob=p["best_score_prob"],
        )
        legs.append(sel)
        print(f"     ⚽ {p['home']} vs {p['away']} : {bh}-{ba} ({p['best_score_prob']*100:.1f}%)")

    combined_odds = 1.0
    combined_prob = 1.0
    for s in legs:
        combined_odds *= s.odds
        combined_prob *= s.final_prob()

    return Coupon("Score Exact", "3 matchs • 6 scores cibles",
                  "⚽", legs, round(combined_odds, 2), round(combined_prob, 4), is_list=True)

def build_all_coupons(candidates: list[Candidate], matches: list[dict] = None) -> list[Coupon]:
    coupons = []
    if c := build_odds3_coupon(candidates):
        coupons.append(c)
    if c := build_odds10_coupon(candidates):
        coupons.append(c)
    if matches:
        if c := build_mtft_coupon(matches):
            coupons.append(c)
        if c := build_score_exact_coupon(matches):
            coupons.append(c)
    return coupons

def format_header():
    return (
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<i>{BRAND_TAGLINE}</i>\n"
        f"<i>📅 {today_pretty()} • 🇨🇮 Côte d’Ivoire</i>"
    )

def format_selection_block(s: Selection, idx: int) -> str:
    prob_pct = s.final_prob() * 100
    ctx_badge = " ✅ <i>H2H</i>" if s.has_context else ""
    bzz_badge = f" 🔮{s.bzz_confidence:.0f}" if s.bzz_confidence > 0 else ""

    lines = [
        f"<b>┌─ #{idx} {s.home} vs {s.away}</b>",
        f"│ 🕐 <b>{kickoff_local(s.kickoff)}</b> • {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>  <i>({s.market})</i>",
        f"│ 📊 Proba : <b>{prob_pct:.1f}%</b> {prob_bar(s.final_prob())}{ctx_badge}{bzz_badge}",
    ]
    src = []
    if s.prob_gemini > 0:
        src.append(f"G:{s.prob_gemini*100:.0f}%")
    if s.prob_dixon > 0:
        src.append(f"D:{s.prob_dixon*100:.0f}%")
    if s.prob_bzzoiro > 0:
        src.append(f"B:{s.prob_bzzoiro*100:.0f}%")
    if src:
        lines.append(f"│    <i>{' × '.join(src)}</i>")

    if s.htft_pick:
        lines.append(f"│ ⏱ <b>MT/Fin</b> : {s.htft_pick}")
    if s.score_exact_home >= 0 and s.score_exact_away >= 0:
        lines.append(f"│ ⚽ <b>Score exact</b> : {s.score_exact_home}-{s.score_exact_away}")

    if s.real_odd:
        lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b> <i>(réelle)</i>")
    else:
        lines.append(f"│ 💰 Cote : <b>{s.odds}</b>")

    if s.analysis:
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>Avis IA</b> :")
        lines.append(f"│ 💬 <i>{s.analysis[:300]}</i>")
    if s.advices:
        lines.append(f"│ 💡 <b>Conseils IA</b> :")
        for a in s.advices:
            txt = a.get("text", "").strip()
            if not txt:
                continue
            conf = a.get("confidence", 0)
            if conf > 0:
                lines.append(f"│    • {txt}  {confidence_bar(conf)} <b>{conf}%</b>")
            else:
                lines.append(f"│    • {txt}")

    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)

def format_coupon(c: Coupon) -> str:
    if c.is_list:
        lines = [
            format_header(), "",
            f"<b>{c.emoji} {c.name.upper()}</b>",
            f"<i>{c.subtitle}</i>", "",
            f"<b>🎯 {len(c.legs)} pronostics</b>", "",
        ]
        for i, s in enumerate(c.legs, 1):
            lines.append(format_selection_block(s, i))
            lines.append("")
        lines.append(f"<i>{PUBLIC_FOOTER}</i>")
        return "\n".join(lines)
    prob_level = "🟢 Faible risque" if c.combined_prob >= 0.40 else "🟡 Risque modéré" if c.combined_prob >= 0.20 else "🔴 Risque élevé"
    lines = [
        format_header(), "",
        f"<b>{c.emoji} {c.name.upper()}</b>",
        f"<i>{c.subtitle}</i>", "",
        f"<b>┏━━━━━━━━━━━━━━━┓</b>",
        f"<b>┃ 📊 RÉCAP TICKET</b>",
        f"<b>┃ 🎫 Cote totale : {c.combined_odds}</b>",
        f"<b>┃ 📈 Proba combinée : {c.combined_prob*100:.1f}%</b>",
        f"<b>┃ 🎯 {prob_level}</b>",
        f"<b>┗━━━━━━━━━━━━━━━┛</b>", "",
    ]
    for i, s in enumerate(c.legs, 1):
        lines.append(format_selection_block(s, i))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)

def format_coupons_ready():
    if not STATE["coupons"]:
        return f"{format_header()}\n\n<b>📭 Aucun coupon aujourd'hui</b>\n<i>Pas assez de paris qualifiés.</i>"
    lignes = []
    for c in STATE["coupons"]:
        n = len(c.legs)
        if c.is_list:
            lignes.append(f"{c.emoji} <b>{c.name}</b>\n   • {n} pronostics")
        else:
            lignes.append(f"{c.emoji} <b>{c.name}</b>\n   • {n} paris • Cote <b>{c.combined_odds}</b>")
    return f"{format_header()}\n\n<b>🔔 COUPONS DU JOUR PRÊTS !</b>\n\n<b>{len(STATE['coupons'])} coupon(s) disponible(s) :</b>\n\n" + "\n\n".join(lignes) + "\n\n<i>👇 Clique sur le bouton du ticket qui t'intéresse</i>"

def format_bilan(tracker):
    coupons = tracker.get("coupons", [])
    if not coupons:
        return f"{format_header()}\n\n<b>📊 Bilan</b>\n<i>Aucun historique.</i>"
    total = len(coupons)
    won = sum(1 for c in coupons if c.get("status") == "won")
    lost = sum(1 for c in coupons if c.get("status") == "lost")
    pending = sum(1 for c in coupons if c.get("status") == "pending")
    settled = won + lost
    wr = (won / settled * 100) if settled > 0 else 0
    return f"{format_header()}\n\n<b>📊 BILAN</b>\n🎫 Total : <b>{total}</b>\n✅ Gagnés : <b>{won}</b>\n❌ Perdus : <b>{lost}</b>\n⏳ En attente : <b>{pending}</b>\n\n📈 Taux réussite : <b>{wr:.1f}%</b>"

async def safe_answer(message, text, reply_markup=None):
    for i, part in enumerate(split_message(text)):
        try:
            if i == 0:
                await message.answer(part, parse_mode="HTML", reply_markup=reply_markup)
            else:
                await message.answer(part, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"⚠️ Answer part {i}: {e}")

async def safe_send(chat_id, text):
    for part in split_message(text):
        try:
            await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"⚠️ Send {chat_id}: {e}")

async def broadcast_after_scan():
    for chat_id in TARGETS:
        await safe_send(chat_id, format_coupons_ready())
        await asyncio.sleep(0.5)
    for user_id_str in STATE["users"]:
        try:
            uid = int(user_id_str)
            if is_subscribed(uid):
                await safe_send(uid, format_coupons_ready())
                await asyncio.sleep(0.3)
        except Exception:
            pass

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
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Usage: /keygen [1m | 1s | 60]")
        return
    duration_str = args[1].lower()
    days = 0
    if duration_str.endswith("m"):
        days = int(duration_str[:-1]) * 30
    elif duration_str.endswith("s"):
        days = int(duration_str[:-1]) * 7
    else:
        days = int(duration_str)
    if days <= 0:
        await message.answer("❌ Durée invalide.")
        return
    key = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    STATE["keys"][key] = {"days": days, "used": False, "created_at": datetime.now(TZ).isoformat()}
    save_json(KEYS_DB, STATE["keys"])
    await message.answer(f"🔑 <b>Clé générée !</b>\n\nClé : <code>{key}</code>\nDurée : <b>{days} jours</b>", parse_mode="HTML")

@dp.message(Command("activate", "vip", "cle", "key"))
async def activate_cmd(message: Message):
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Usage: /activate <votre_clé>")
        return
    await process_activation(message, args[1].strip().upper())

@dp.message(F.text.regexp(r"^[A-Z0-9]{8}$"))
async def raw_key_cmd(message: Message):
    await process_activation(message, message.text.strip().upper())

async def process_activation(message: Message, key: str):
    user_id = message.from_user.id
    if key not in STATE["keys"]:
        await message.answer("❌ Clé invalide.")
        return
    key_data = STATE["keys"][key]
    if key_data.get("used"):
        await message.answer("❌ Clé déjà utilisée.")
        return
    days = key_data["days"]
    expires_at = datetime.now(TZ) + timedelta(days=days)
    STATE["users"][str(user_id)] = {"expires_at": expires_at.isoformat(), "key_used": key}
    key_data["used"] = True
    key_data["used_by"] = str(user_id)
    key_data["used_at"] = datetime.now(TZ).isoformat()
    save_json(KEYS_DB, STATE["keys"])
    save_json(USERS_DB, STATE["users"])
    await message.answer(f"✅ <b>Abonnement activé !</b>\n\nExpiration : <b>{expires_at.strftime('%d/%m/%Y à %H:%M')}</b>", parse_mode="HTML", reply_markup=USER_KEYBOARD)

@dp.message(Command("scan"))
async def scan_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await safe_answer(message, "⏳ Analyse en cours (2-3 min)...")
    await scan()
    await broadcast_after_scan()

@dp.message(Command("regler"))
async def regler_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await safe_answer(message, "⏳ Vérification...")
    await settle_coupons()
    await safe_answer(message, format_bilan(STATE["tracker"]))

@dp.message(Command("debug"))
async def debug_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    d = STATE["debug"]
    txt = (
        f"<b>🔍 DEBUG</b>\n\n"
        f"Dixon : <b>{'✅' if STATE['dixon_model'] else '❌'}</b>\n"
        f"Bzzoiro : <b>{'✅' if BZZOIRO_API_KEY else '❌'}</b>\n"
        f"Odds events : <b>{d.get('odds_events', 0)}</b>\n"
        f"Bzzoiro direct : <b>{d.get('bzz_predictions', 0)}</b>\n"
        f"Matchs analysés : <b>{d.get('matched', 0)}</b>\n"
        f"Candidats : <b>{d.get('candidates', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>"
    )
    await safe_answer(message, txt)

async def send_coupon(message, name):
    c = next((x for x in STATE["coupons"] if x.name == name), None)
    if c:
        await safe_answer(message, format_coupon(c))
    else:
        await safe_answer(message, "Aucun coupon pour ce profil.")

@dp.message(F.text == BTN_ODDS3)
async def btn_odds3(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await send_coupon(message, "Odds +3")

@dp.message(F.text == BTN_ODDS10)
async def btn_odds10(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await send_coupon(message, "Odds +10")

@dp.message(F.text == BTN_MTFT)
async def btn_mtft(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await send_coupon(message, "MT-Fin")

@dp.message(F.text == BTN_SCOREEXACT)
async def btn_scoreexact(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await send_coupon(message, "Score Exact")

@dp.message(F.text == BTN_BILAN)
async def btn_bilan(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await safe_answer(message, format_bilan(STATE["tracker"]))

@dp.message(F.text == BTN_SCAN)
async def btn_scan(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await scan_cmd(message)

@dp.message(F.text == BTN_ACTIVATE)
async def btn_activate(message: Message):
    await message.answer("🔑 <b>Pour activer votre abonnement :</b>\n\nEnvoyez <code>/activate VOTRE_CLE</code>", parse_mode="HTML")

@dp.message(F.text == "⚙️ Debug")
async def btn_debug(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await debug_cmd(message)

async def scan():
    async with STATE["scan_lock"]:
        bzz_predictions = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try:
                bzz_predictions = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except Exception as e:
                print(f"⚠️ Bzzoiro direct crash: {e}")

        odds_events = await fetch_all_odds_events()

        if not odds_events and not bzz_predictions:
            STATE["coupons"] = []
            STATE["candidates"] = []
            print("⚠️ Scan annulé : 0 source.")
            return

        if not odds_events:
            print(f"⚠️ Odds API vide. On continue avec {len(bzz_predictions)} prédictions Bzzoiro.")

        matches = []
        if bzz_predictions:
            for bzz in bzz_predictions:
                matched_odds = None
                h_norm = normalize_name(bzz["home"])
                a_norm = normalize_name(bzz["away"])
                for ev in odds_events:
                    ev_h = normalize_name(ev.get("home_team", ""))
                    ev_a = normalize_name(ev.get("away_team", ""))
                    if (h_norm in ev_h or ev_h in h_norm) and (a_norm in ev_a or a_norm in ev_h):
                        matched_odds = ev
                        break
                bzz_copy = dict(bzz)
                if matched_odds:
                    bzz_copy["real_odds"] = extract_real_odds(matched_odds)
                    bzz_copy["has_odds"] = True
                    bzz_copy["odds_sport_key"] = matched_odds.get("sport_key", "")
                    bzz_copy["league"] = matched_odds.get("sport_title", bzz.get("league", ""))
                matches.append(bzz_copy)

        if not matches:
            matches = build_matches_from_odds(odds_events)

        if not matches:
            print("❌ Aucun match éligible — scan annulé.")
            STATE["coupons"] = []
            STATE["candidates"] = []
            return

        print(f"📋 {len(matches)} matchs éligibles")

        contexts = {}
        try:
            contexts = await enrich_matches(matches)
        except Exception as e:
            print(f"⚠️ Enrichissement : {e}")

        used_today = load_used_today()
        candidates = await build_all_candidates(matches, used_today, contexts)
        STATE["candidates"] = candidates
        STATE["coupons"] = build_all_coupons(candidates, matches)
        mark_picks_used([s for c in STATE["coupons"] for s in c.legs])
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        STATE["debug"] = {
            "odds_events": len(odds_events),
            "bzz_predictions": len(bzz_predictions),
            "matched": len(matches),
            "candidates": len(candidates),
            "coupons": len(STATE["coupons"]),
        }
        print(f"──────── RÉSUMÉ ────────")
        print(f"Candidats : {len(candidates)}")
        print(f"Coupons : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} legs, cote {c.combined_odds}")
        print("────────────────────────")

async def daily_broadcast():
    await scan()
    record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    await broadcast_after_scan()

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    STATE["tracker"] = load_tracker()
    STATE["keys"] = load_json(KEYS_DB)
    STATE["users"] = load_json(USERS_DB)
    STATE["ready"] = True
    print("✅ Bot prêt.")

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ),
                      id="daily_broadcast", replace_existing=True)
    scheduler.start()
    bot_task = asyncio.create_task(dp.start_polling(bot))
    print(f"✅ Bot démarré. Scan auto à {NOTIFY_HOUR:02d}h{NOTIFY_MINUTE:02d}")

    async def background_bootstrap():
        try:
            if download_and_train:
                STATE["dixon_model"] = await download_and_train()
                print(f"✅ Dixon-Coles actif ({len(STATE['dixon_model'].teams) if STATE['dixon_model'] else 0} équipes)")

            await asyncio.sleep(5)
            if NOTIFY_ON_STARTUP:
                for chat_id in TARGETS:
                    await safe_send(chat_id, f"{format_header()}\n\n⏳ <b>Bot démarré...</b>")
            await daily_broadcast()
        except Exception as e:
            print(f"⚠️ bootstrap crash: {e}")

    asyncio.create_task(background_bootstrap())
    yield
    scheduler.shutdown(wait=False)
    bot_task.cancel()
    try:
        await bot.session.close()
    except Exception:
        pass

app = FastAPI(title="Value Bot", lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "ok", "ready": STATE["ready"], "coupons": [c.name for c in STATE["coupons"]]}

@app.get("/debug")
async def debug_http():
    return STATE["debug"]

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
