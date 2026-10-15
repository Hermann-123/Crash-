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

# === API-Football DÉSACTIVÉ (comptes suspendus) ===
try:
    from football_data import get_fixture_result
except Exception as e:
    get_fixture_result = None
    print(f"⚠️ Import get_fixture_result: {e}")

fetch_all_today_fixtures = None
get_corners_cards = None
get_full_context_api_football = None

try:
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception as e:
    enrich_match_with_bzzoiro = None
    print(f"❌ Import bzzoiro_data: {e}")

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
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

try:
    ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except ValueError:
    ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")

BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • H2H + Gemini")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

MIN_PROB = float(os.getenv("MIN_PROB", "0.70"))
MIN_ODD = float(os.getenv("MIN_ODD", "1.45"))
MAX_MATCHES_TO_ANALYZE = int(os.getenv("MAX_MATCHES_TO_ANALYZE", "40"))
MAX_MATCHES_TO_ENRICH = int(os.getenv("MAX_MATCHES_TO_ENRICH", "15"))

SAFE_MIN_ODDS = 2.0
SAFE_MAX_ODDS = 2.5
BAL_MIN_ODDS = 2.5
BAL_MAX_ODDS = 5.0
BAL_MIN_LEG_ODDS = 1.35
VALUE_TOP_N = 20
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
    "soccer_efl_champ", "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
]


# =========================================================
# MATCH QUALITY SCORING SYSTEM
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
    home = real_odds.get("home")
    draw = real_odds.get("draw")
    away = real_odds.get("away")
    if home and away:
        min_odd = min(home, away, draw or 999)
        if min_odd < 1.60:
            score += WEIGHT_FAVORITE_CLARITY
        elif min_odd < 1.90:
            score += WEIGHT_FAVORITE_CLARITY * 0.6
        elif min_odd < 2.30:
            score += WEIGHT_FAVORITE_CLARITY * 0.3
    dt = parse_dt_safe(fx.get("kickoff", ""))
    if dt:
        hours_until = (dt - datetime.now(TZ)).total_seconds() / 3600
        if 1 <= hours_until <= 6:
            score += WEIGHT_TIME_PROXIMITY
        elif hours_until <= 12:
            score += WEIGHT_TIME_PROXIMITY * 0.7
        elif hours_until <= 24:
            score += WEIGHT_TIME_PROXIMITY * 0.3

    if STATE.get("dixon_model"):
        try:
            h_key = STATE["dixon_model"]._find_team(fx.get("home", ""))
            a_key = STATE["dixon_model"]._find_team(fx.get("away", ""))
            if h_key and a_key:
                score += 15.0
                fx["dixon_available"] = True
            elif h_key or a_key:
                score += 5.0
                fx["dixon_available"] = False
            else:
                fx["dixon_available"] = False
        except Exception:
            fx["dixon_available"] = False

    return score


def filter_and_rank_matches(matches: list[dict]) -> list[dict]:
    scored = []
    for fx in matches:
        s = calculate_match_score(fx)
        fx["_score"] = s
        if s >= MIN_MATCH_SCORE:
            scored.append(fx)
    scored.sort(key=lambda m: m["_score"], reverse=True)
    print(f"🎯 Matchs qualifiés : {len(scored)}/{len(matches)} (seuil {MIN_MATCH_SCORE})")
    for fx in scored[:10]:
        dixon_tag = "✅D" if fx.get("dixon_available") else "  "
        print(f"  ⭐ [{fx['_score']:.1f}] {dixon_tag} {fx.get('league','')[:30]} | {fx['home']} vs {fx['away']}")
    return scored


# =========================================================
# FUSION GEMINI × DIXON-COLES
# =========================================================
W_GEMINI = 0.55
W_DIXON = 0.45


def combine_gemini_dixon(gemini_markets: dict, dixon_pred: Optional[dict]) -> dict:
    result = {}
    g = {}
    for k in ["victoire_home", "match_nul", "victoire_away",
              "over_2.5", "under_2.5", "btts_yes", "btts_no"]:
        v = gemini_markets.get(k)
        if v is not None:
            try:
                g[k] = max(0.0, min(1.0, float(v) / 100.0))
            except (ValueError, TypeError):
                pass
    if not dixon_pred:
        result.update(g)
    else:
        d = {
            "victoire_home": dixon_pred.get("p_home", 0),
            "match_nul": dixon_pred.get("p_draw", 0),
            "victoire_away": dixon_pred.get("p_away", 0),
            "over_2.5": dixon_pred.get("over_2.5", 0),
            "under_2.5": dixon_pred.get("under_2.5", 0),
            "btts_yes": dixon_pred.get("btts_yes", 0),
            "btts_no": dixon_pred.get("btts_no", 0),
        }
        for k in g:
            if k in d:
                result[k] = W_GEMINI * g[k] + W_DIXON * d[k]
            else:
                result[k] = g[k]
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = result["victoire_home"] + result["match_nul"]
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = result["victoire_home"] + result["victoire_away"]
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = result["match_nul"] + result["victoire_away"]
    return result


def compute_agreement(gemini_markets: dict, dixon_pred: Optional[dict], market_key: str) -> tuple[str, float]:
    if not dixon_pred:
        return ("", 0.0)
    map_g = {
        "victoire_home": "victoire_home", "match_nul": "match_nul", "victoire_away": "victoire_away",
        "over_2.5": "over_2.5", "under_2.5": "under_2.5",
        "btts_yes": "btts_yes", "btts_no": "btts_no",
    }
    map_d = {
        "victoire_home": "p_home", "match_nul": "p_draw", "victoire_away": "p_away",
        "over_2.5": "over_2.5", "under_2.5": "under_2.5",
        "btts_yes": "btts_yes", "btts_no": "btts_no",
    }
    gk = map_g.get(market_key)
    dk = map_d.get(market_key)
    if not gk or not dk:
        return ("", 0.0)
    gv = gemini_markets.get(gk)
    if gv is None:
        return ("", 0.0)
    try:
        g_prob = float(gv) / 100.0
    except (ValueError, TypeError):
        return ("", 0.0)
    d_prob = dixon_pred.get(dk, 0)
    delta = abs(g_prob - d_prob)
    if delta <= 0.10:
        return ("🤝", delta)
    elif delta >= 0.15:
        return ("⚠️", delta)
    return ("", delta)


# =========================================================
# MODELS
# =========================================================
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
    agreement_emoji: str = ""
    agreement_delta: float = 0.0

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
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"
BTN_ACTIVATE = "🔑 Activer mon abonnement"

USER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_VAR), KeyboardButton(text=BTN_BILAN)],
        [KeyboardButton(text=BTN_ACTIVATE)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choisis un ticket…",
)

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_VAR), KeyboardButton(text=BTN_BILAN)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text="⚙️ Debug")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Admin: Choisis une action…",
)

STATE = {
    "ready": False,
    "selections": [],
    "coupons": [],
    "last_scan": None,
    "debug": {},
    "tracker": {"coupons": []},
    "keys": {},
    "users": {},
    "scan_lock": asyncio.Lock(),
    "dixon_model": None,
}

TARGETS = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try:
            TARGETS.append(int(raw))
        except ValueError:
            TARGETS.append(raw)


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
    return dt.strftime("%H:%M") if dt else "?"


def is_today_upcoming(iso_str) -> bool:
    dt = parse_dt_safe(iso_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    if dt.date() != now.date():
        return False
    return dt > now + timedelta(minutes=5)


def prob_bar(p: float) -> str:
    if p >= 0.80:
        return "🟢🟢🟢"
    if p >= 0.75:
        return "🟢🟢⚪"
    if p >= 0.70:
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


# =========================================================
# GESTION DES CLÉS ET ABONNEMENTS
# =========================================================
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
        exp_dt = datetime.fromisoformat(exp)
        return datetime.now(TZ) < exp_dt
    except Exception:
        return False


def get_subscription_message(user_id: int) -> str:
    if is_subscribed(user_id):
        exp = STATE["users"][str(user_id)]["expires_at"]
        exp_dt = datetime.fromisoformat(exp)
        return f"✅ Votre abonnement est actif jusqu'au {exp_dt.strftime('%d/%m/%Y à %H:%M')}."
    else:
        return "❌ Votre abonnement est expiré ou inexistant.\nContactez l'administrateur : @hermann1123"


# =========================================================
# TRACKER
# =========================================================
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
    if existing.get("date") == today:
        merged = set(existing.get("ids", [])) | ids
    else:
        merged = ids
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
    print("⚠️ settle_coupons désactivé (API-Football suspendu)")
    return


# =========================================================
# THE ODDS API
# =========================================================
async def fetch_odds_api_league(client: httpx.AsyncClient, sport_key: str) -> list[dict]:
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {"apiKey": ODDS_API_KEY, "regions": "eu",
              "markets": "h2h,totals", "oddsFormat": "decimal"}
    try:
        r = await client.get(url, params=params, timeout=30.0)
        if r.status_code >= 400:
            return []
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
            await asyncio.sleep(0.3)
    print(f"💰 Odds API : {len(all_events)} events")
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
        if not is_today_upcoming(kick):
            continue
        matches.append({
            "event_id": ev.get("id", ""),
            "home": h, "away": a,
            "league": ev.get("sport_title", ""),
            "kickoff": kick,
            "has_odds": True,
            "real_odds": extract_real_odds(ev),
            "odds_sport_key": ev.get("sport_key", ""),
        })
    print(f"📊 Matchs construits depuis Odds API : {len(matches)}")
    return matches


# =========================================================
# ENRICHISSEMENT (Bzzoiro SEUL - OPTIONNEL)
# =========================================================
async def enrich_matches(matches: list[dict]) -> dict:
    to_enrich = matches[:MAX_MATCHES_TO_ENRICH]
    if not to_enrich:
        return {}
    print(f"🌐 Enrichissement Bzzoiro (max {MAX_MATCHES_TO_ENRICH})...")
    contexts = {}
    for fx in to_enrich:
        try:
            home = fx.get("home", "")
            away = fx.get("away", "")
            kickoff = fx.get("kickoff", "")
            dt = parse_dt_safe(kickoff)
            date_iso = dt.strftime("%Y-%m-%d") if dt else datetime.now(TZ).strftime("%Y-%m-%d")

            ctx = None
            if enrich_match_with_bzzoiro:
                ctx = await enrich_match_with_bzzoiro(home, away, date_iso)

            if ctx and ctx.get("available"):
                contexts[fx["event_id"]] = ctx
                h2h_total = ctx.get("h2h", {}).get("total_matches", 0)
                print(f"  ✅ {home} vs {away} — Bzzoiro • H2H:{h2h_total}")
            else:
                print(f"  ⚠️ {home} vs {away} : pas d'enrichissement (optionnel)")

            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ Enrich crash {fx.get('home')}: {e}")
    print(f"✅ Contextes enrichis : {len(contexts)}/{len(to_enrich)}")
    return contexts


# =========================================================
# BUILD PICKS
# =========================================================
async def build_gemini_picks(matches, used_today, contexts=None):
    selections: list[Selection] = []
    seen = set()
    stats = {"ok": 0, "fail": 0, "kept": 0, "with_ctx": 0, "without_ctx": 0,
             "rejected_low_prob": 0}
    if not predict_match_probas:
        print("❌ predict_match_probas non disponible")
        return selections
    contexts = contexts or {}
    ordered = [m for m in matches if m.get("has_odds")]

    async with httpx.AsyncClient(timeout=40.0) as client:
        for fx in ordered[:MAX_MATCHES_TO_ANALYZE]:
            try:
                home, away = fx["home"], fx["away"]
                event_id, league = fx["event_id"], fx["league"]
                kickoff = fx["kickoff"]
                if event_id in used_today:
                    continue

                # === CONTEXTE OPTIONNEL (bonus si présent) ===
                ctx = contexts.get(event_id)
                has_ctx = ctx is not None and ctx.get("available", False)
                if has_ctx:
                    stats["with_ctx"] += 1
                else:
                    stats["without_ctx"] += 1

                pred = await predict_match_probas(client, home, away, league, kickoff, context=ctx)
                if not pred or "markets" not in pred:
                    stats["fail"] += 1
                    continue
                stats["ok"] += 1

                gemini_markets = pred.get("markets", {})
                analysis = pred.get("analysis", "")
                advice = pred.get("advice", "")
                advice_conf = pred.get("advice_confidence", 0)
                try:
                    advice_conf = int(advice_conf)
                    if advice_conf < 0 or advice_conf > 100:
                        advice_conf = 0
                except (ValueError, TypeError):
                    advice_conf = 0

                raw_advices = pred.get("advices", [])
                advices_clean = []
                if isinstance(raw_advices, list):
                    for a in raw_advices[:2]:
                        if isinstance(a, dict) and a.get("text"):
                            try:
                                conf = int(a.get("confidence", 0))
                                if conf < 0 or conf > 100:
                                    conf = 0
                            except (ValueError, TypeError):
                                conf = 0
                            advices_clean.append({
                                "text": str(a.get("text", "")).strip(),
                                "confidence": conf,
                            })

                dixon_pred = None
                if STATE["dixon_model"]:
                    try:
                        dixon_pred = STATE["dixon_model"].predict_all(home, away)
                    except Exception as e:
                        print(f"  ⚠️ Dixon crash {home}-{away}: {e}")

                combined_probs = combine_gemini_dixon(gemini_markets, dixon_pred)
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
                }

                candidates = []
                for key, (label, mkt) in market_map.items():
                    prob = combined_probs.get(key)
                    if prob is None:
                        continue
                    if prob < 0.70:
                        stats["rejected_low_prob"] += 1
                        continue
                    if prob > 0.90:
                        continue

                    real_odd = None
                    if mkt == "1X2":
                        if "victoire" in label.lower() and home.lower() in label.lower():
                            real_odd = real_odds.get("home")
                        elif "victoire" in label.lower() and away.lower() in label.lower():
                            real_odd = real_odds.get("away")
                        elif "nul" in label.lower():
                            real_odd = real_odds.get("draw")
                    elif mkt == "Over/Under":
                        if "over 2.5" in label.lower():
                            real_odd = real_odds.get("over_2.5")
                        elif "under 2.5" in label.lower():
                            real_odd = real_odds.get("under_2.5")
                    elif mkt == "BTTS":
                        if "oui" in label.lower():
                            real_odd = real_odds.get("btts_yes")
                        elif "non" in label.lower():
                            real_odd = real_odds.get("btts_no")

                    used_odd = real_odd if real_odd else 1.0 / prob
                    if used_odd < MIN_ODD:
                        continue
                    edge = 0.0
                    if real_odd and real_odd > 1.01:
                        edge = (prob * real_odd) - 1.0

                    pg_raw = gemini_markets.get(key)
                    try:
                        prob_gemini = float(pg_raw) / 100.0 if pg_raw is not None else 0.0
                    except (ValueError, TypeError):
                        prob_gemini = 0.0
                    prob_dixon = 0.0
                    if dixon_pred:
                        dk_map = {
                            "victoire_home": "p_home", "match_nul": "p_draw", "victoire_away": "p_away",
                            "over_2.5": "over_2.5", "under_2.5": "under_2.5",
                            "btts_yes": "btts_yes", "btts_no": "btts_no",
                        }
                        dk = dk_map.get(key)
                        if dk:
                            prob_dixon = dixon_pred.get(dk, 0.0)
                    agr_emoji, agr_delta = compute_agreement(gemini_markets, dixon_pred, key)

                    score = prob
                    if real_odd:
                        score += 0.5
                    if has_ctx:
                        score += 0.3
                    if edge > 0.05:
                        score += 0.4

                    if agr_emoji == "🤝":
                        score += 0.8
                    elif agr_emoji == "⚠️":
                        score -= 0.6
                    elif prob_dixon > 0:
                        score += 0.4
                    elif prob_dixon == 0 and prob_gemini > 0:
                        score -= 0.3

                    candidates.append({
                        "score": score, "label": label, "mkt": mkt,
                        "used_odd": used_odd, "real_odd": real_odd, "prob": prob,
                        "edge": edge, "prob_gemini": prob_gemini, "prob_dixon": prob_dixon,
                        "agr_emoji": agr_emoji, "agr_delta": agr_delta,
                    })

                candidates.sort(reverse=True, key=lambda x: x["score"])

                # === 1 SEUL PICK PAR MATCH ===
                if candidates:
                    c = candidates[0]
                    dedup = f"{event_id}|{c['mkt']}|{c['label']}"
                    if dedup not in seen:
                        seen.add(dedup)
                        selections.append(Selection(
                            event_id=event_id, league=league, home=home, away=away,
                            kickoff=kickoff, market=c["mkt"], pick_label=c["label"],
                            odds=round(c["used_odd"], 2), model_prob=round(c["prob"], 4),
                            edge=round(c["edge"], 4),
                            bookmaker="The Odds API" if c["real_odd"] else "Gemini+Dixon",
                            real_odd=c["real_odd"],
                            analysis=analysis,
                            advice=advice,
                            advice_confidence=advice_conf,
                            advices=advices_clean,
                            has_context=has_ctx,
                            prob_gemini=round(c["prob_gemini"], 4),
                            prob_dixon=round(c["prob_dixon"], 4),
                            agreement_emoji=c["agr_emoji"],
                            agreement_delta=round(c["agr_delta"], 4),
                        ))
                        stats["kept"] += 1

                await asyncio.sleep(2.5)
            except Exception as e:
                print(f"⚠️ build_gemini_picks: {e}")
    print(f"🏠 Picks : ok={stats['ok']} échec={stats['fail']} "
          f"gardés={stats['kept']} avec_ctx={stats['with_ctx']} "
          f"sans_ctx={stats['without_ctx']} rejetés_proba={stats['rejected_low_prob']}")
    return selections


# =========================================================
# COUPONS
# =========================================================
def build_coupon_target(name, subtitle, emoji, pool, target_min, target_max,
                        max_legs, globally_used, min_leg_odds=1.01, strict=True):
    pool = [s for s in pool
            if s.odds >= min_leg_odds
            and s.event_id not in globally_used]
    print(f"  → {name}: {len(pool)} candidates")
    if not pool:
        return None
    pool.sort(key=lambda s: s.final_prob(), reverse=True)
    legs = []
    used_events = set()
    used_markets = set()
    combined_odds = 1.0
    combined_prob = 1.0
    for s in pool:
        if len(legs) >= max_legs:
            break
        if s.event_id in used_events:
            continue
        market_count = sum(1 for leg in legs if leg.market == s.market)
        if market_count >= 3:
            continue
        next_odds = combined_odds * s.odds
        if next_odds > target_max * 1.15 and legs:
            continue
        legs.append(s)
        used_events.add(s.event_id)
        used_markets.add(s.market)
        combined_odds = next_odds
        combined_prob *= s.final_prob()
        if combined_odds >= target_min:
            break
    if combined_odds < target_min and len(legs) < max_legs:
        for s in pool:
            if len(legs) >= max_legs:
                break
            if s.event_id in used_events:
                continue
            market_count = sum(1 for leg in legs if leg.market == s.market)
            if market_count >= 3:
                continue
            next_odds = combined_odds * s.odds
            if next_odds > target_max * 1.3 and legs:
                continue
            legs.append(s)
            used_events.add(s.event_id)
            combined_odds = next_odds
            combined_prob *= s.final_prob()
            if combined_odds >= target_min:
                break
    print(f"  → {name}: {len(legs)} legs, cote {combined_odds:.2f}")
    if not legs:
        return None
    if strict:
        if combined_odds < target_min:
            print(f"  ⛔ {name} rejeté : cote {combined_odds:.2f} < {target_min}")
            return None
    else:
        if combined_odds < target_min * 0.85:
            return None
    if combined_odds > target_max * 1.25:
        return None
    legs.sort(key=lambda s: s.odds)
    for s in legs:
        globally_used.add(s.event_id)
    return Coupon(name, subtitle, emoji, legs,
                  round(combined_odds, 2), round(combined_prob, 4))


def build_value_list(selections, top_n=20, globally_used=None):
    if globally_used is None:
        globally_used = set()
    pool = [s for s in selections
            if s.event_id not in globally_used and s.odds >= MIN_ODD]
    if not pool:
        return None

    def value_score(s):
        score = s.final_prob()
        if s.agreement_emoji == "🤝":
            score += 0.20
        elif s.prob_dixon > 0:
            score += 0.10
        elif s.prob_dixon == 0 and s.prob_gemini > 0:
            score -= 0.15
        if s.edge > 0.05:
            score += 0.05
        if s.edge > 0.10:
            score += 0.05
        if s.has_context:
            score += 0.05
        return score

    pool.sort(key=value_score, reverse=True)
    top_list = []
    seen_events = set()
    for s in pool:
        if s.event_id in seen_events:
            continue
        top_list.append(s)
        seen_events.add(s.event_id)
        if len(top_list) >= top_n:
            break
    if not top_list:
        return None
    combined_odds = 1.0
    combined_prob = 1.0
    for s in top_list:
        combined_odds *= s.odds
        combined_prob *= s.final_prob()
    for s in top_list:
        globally_used.add(s.event_id)
    return Coupon(
        "Ticket Value", f"Top {len(top_list)} pronostics (fiabilité)",
        "💎", top_list,
        round(combined_odds, 2), round(combined_prob, 4),
        is_list=True,
    )


def build_all_coupons(selections):
    coupons = []
    used = set()
    pool_safe = [s for s in selections
                 if s.final_prob() >= 0.70
                 and s.market in ("1X2", "Double Chance", "Over/Under", "BTTS")]
    c = build_coupon_target(
        "Ticket Sécurisé", "Cote 2.0 - 2.5 • Proba > 70%",
        "🔒", pool_safe, SAFE_MIN_ODDS, SAFE_MAX_ODDS, 6, used,
        min_leg_odds=1.35, strict=True,
    )
    if c:
        coupons.append(c)
    pool_bal = [s for s in selections
                if s.final_prob() >= 0.70
                and s.market not in ("Corners", "Cartons")]
    c = build_coupon_target(
        "Ticket Équilibré", "Cote 2.5 - 5.0 • Proba > 70%",
        "⚖️", pool_bal, BAL_MIN_ODDS, BAL_MAX_ODDS, 15, used,
        min_leg_odds=BAL_MIN_LEG_ODDS, strict=False,
    )
    if c:
        coupons.append(c)
    c = build_value_list(selections, top_n=VALUE_TOP_N, globally_used=set())
    if c:
        coupons.append(c)
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


def format_selection_block(s: Selection, idx: int, compact: bool = False):
    dt = parse_dt_safe(s.kickoff)
    day_tag = ""
    if dt:
        today = datetime.now(TZ).date()
        if dt.date() == today:
            day_tag = "📍 <b>Aujourd'hui</b> "
        elif dt.date() == today + timedelta(days=1):
            day_tag = "📅 <b>Demain</b> "
    prob_pct = s.final_prob() * 100
    ctx_badge = " ✅ <i>H2H réel</i>" if s.has_context else ""
    agreement = f" {s.agreement_emoji}" if s.agreement_emoji else ""
    lines = [
        f"<b>┌─ #{idx} {s.home} vs {s.away}</b>",
        f"│ {day_tag}🕐 <b>{kickoff_local(s.kickoff)}</b> • {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>  <i>({s.market})</i>",
        f"│ 📊 Proba : <b>{prob_pct:.1f}%</b> {prob_bar(s.final_prob())}{ctx_badge}{agreement}",
    ]
    if s.prob_dixon > 0 or s.prob_gemini > 0:
        g_pct = s.prob_gemini * 100
        d_pct = s.prob_dixon * 100
        lines.append(f"│    <i>G:{g_pct:.0f}% × D:{d_pct:.0f}%</i>")
    if s.real_odd:
        lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b> <i>(réelle)</i>")
    else:
        lines.append(f"│ 💰 Cote : <b>{s.odds}</b> <i>(estimée)</i>")
    if s.edge > 0.05:
        lines.append(f"│ 💎 <b>Value</b> : +{s.edge*100:.1f}%")
    if not compact and s.analysis:
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>Avis IA</b> :")
        lines.append(f"│ 💬 <i>{s.analysis}</i>")
        if s.advices:
            lines.append(f"│ 💡 <b>Conseils IA</b> :")
            for a in s.advices:
                txt = a.get("text", "").strip()
                conf = a.get("confidence", 0)
                if not txt:
                    continue
                if conf > 0:
                    conf_bar = confidence_bar(conf)
                    lines.append(f"│    • {txt}  {conf_bar} <b>{conf}%</b>")
                else:
                    lines.append(f"│    • {txt}")
        elif s.advice:
            if s.advice_confidence > 0:
                conf_bar = confidence_bar(s.advice_confidence)
                lines.append(f"│ 💡 <b>Conseil IA</b> ({conf_bar} <b>{s.advice_confidence}%</b>) :")
                lines.append(f"│    {s.advice}")
            else:
                lines.append(f"│ 💡 <b>Conseil IA</b> : {s.advice}")
    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)


def format_coupon(c: Coupon) -> str:
    if c.is_list:
        lines = [
            format_header(), "",
            f"<b>{c.emoji} {c.name.upper()}</b>",
            f"<i>{c.subtitle}</i>", "",
            f"<b>🎯 {len(c.legs)} pronostics classés par fiabilité</b>",
            "",
        ]
        for i, s in enumerate(c.legs, 1):
            lines.append(format_selection_block(s, i, compact=True))
            lines.append("")
        lines.append(f"<i>{PUBLIC_FOOTER}</i>")
        return "\n".join(lines)
    prob_level = "🟢 Faible risque" if c.combined_prob >= 0.40 else \
                 "🟡 Risque modéré" if c.combined_prob >= 0.20 else "🔴 Risque élevé"
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
        lines.append(format_selection_block(s, i, compact=False))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_coupons_ready():
    if not STATE["coupons"]:
        return (f"{format_header()}\n\n<b>📭 Aucun coupon aujourd'hui</b>\n"
                f"<i>Pas assez de paris qualifiés (proba > 70%).</i>")
    lignes = []
    for c in STATE["coupons"]:
        n = len(c.legs)
        if c.is_list:
            lignes.append(f"{c.emoji} <b>{c.name}</b>\n   • {n} pronostics (liste)")
        else:
            lignes.append(
                f"{c.emoji} <b>{c.name}</b>\n"
                f"   • {n} paris • Cote <b>{c.combined_odds}</b> • Proba <b>{c.combined_prob*100:.0f}%</b>"
            )
    return (
        f"{format_header()}\n\n"
        f"<b>🔔 COUPONS DU JOUR PRÊTS !</b>\n\n"
        f"<b>{len(STATE['coupons'])} coupon(s) disponible(s) :</b>\n\n"
        + "\n\n".join(lignes)
        + "\n\n<i>👇 Clique sur le bouton du ticket qui t'intéresse</i>"
    )


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
    return (
        f"{format_header()}\n\n"
        f"<b>📊 BILAN</b>\n"
        f"🎫 Total : <b>{total}</b>\n"
        f"✅ Gagnés : <b>{won}</b>\n"
        f"❌ Perdus : <b>{lost}</b>\n"
        f"⏳ En attente : <b>{pending}</b>\n\n"
        f"📈 Taux réussite : <b>{wr:.1f}%</b>"
    )


# =========================================================
# TELEGRAM
# =========================================================
async def safe_answer(message, text, reply_markup=None):
    parts = split_message(text)
    for i, part in enumerate(parts):
        try:
            if i == 0:
                await message.answer(part, parse_mode="HTML", reply_markup=reply_markup)
            else:
                await message.answer(part, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"⚠️ Answer part {i}: {e}")


async def safe_send(chat_id, text):
    parts = split_message(text)
    for part in parts:
        try:
            await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"⚠️ Send {chat_id}: {e}")


async def broadcast_after_scan():
    for chat_id in TARGETS:
        await safe_send(chat_id, format_coupons_ready())
        await asyncio.sleep(0.5)
    for user_id_str, data in STATE["users"].items():
        try:
            uid = int(user_id_str)
            if is_subscribed(uid):
                await safe_send(uid, format_coupons_ready())
                await asyncio.sleep(0.3)
        except Exception as e:
            print(f"⚠️ Erreur envoi abonné {user_id_str}: {e}")


@dp.message(Command("start"))
async def start_cmd(message: Message):
    if message.from_user.id == ADMIN_ID:
        await safe_answer(message, f"{format_header()}\n\n<b>👋 Bienvenue Admin !</b>\n\nUtilise /scan pour lancer une analyse.", reply_markup=ADMIN_KEYBOARD)
    else:
        status = get_subscription_message(message.from_user.id)
        msg = (
            f"{format_header()}\n\n"
            f"<b>👋 Bienvenue sur le bot de pronostics !</b>\n\n"
            f"{status}\n\n"
            f"<b>🔑 Pour activer votre accès :</b>\n"
            f"Envoyez simplement la commande <code>/activate VOTRE_CLE</code>\n"
            f"<i>ou cliquez sur le bouton ci-dessous et envoyez votre clé.</i>"
        )
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
        try:
            days = int(duration_str[:-1]) * 30
        except ValueError:
            pass
    elif duration_str.endswith("s"):
        try:
            days = int(duration_str[:-1]) * 7
        except ValueError:
            pass
    else:
        try:
            days = int(duration_str)
        except ValueError:
            pass
    if days <= 0:
        await message.answer("❌ Durée invalide. Utilisez 1m, 1s, ou un nombre de jours (ex: 60).")
        return
    key = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    STATE["keys"][key] = {"days": days, "used": False, "created_at": datetime.now(TZ).isoformat()}
    save_json(KEYS_DB, STATE["keys"])
    await message.answer(f"🔑 <b>Clé générée avec succès !</b>\n\n"
                         f"Clé : <code>{key}</code>\n"
                         f"Durée : <b>{days} jours</b>\n\n"
                         f"Envoyez cette clé à l'abonné.", parse_mode="HTML")


@dp.message(Command("activate", "vip", "cle", "key"))
async def activate_cmd(message: Message):
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Usage: /activate <votre_clé>\nExemple: /activate MTJ8XWUZ")
        return
    key = args[1].strip().upper()
    await process_activation(message, key)


@dp.message(F.text.regexp(r"^[A-Z0-9]{8}$"))
async def raw_key_cmd(message: Message):
    key = message.text.strip().upper()
    await process_activation(message, key)


async def process_activation(message: Message, key: str):
    user_id = message.from_user.id
    if key not in STATE["keys"]:
        await message.answer("❌ Clé invalide. Vérifiez que vous avez bien copié la clé à 8 caractères.")
        return
    key_data = STATE["keys"][key]
    if key_data.get("used"):
        await message.answer("❌ Cette clé a déjà été utilisée. Contactez l'administrateur : @hermann1123")
        return
    days = key_data["days"]
    expires_at = datetime.now(TZ) + timedelta(days=days)
    STATE["users"][str(user_id)] = {
        "expires_at": expires_at.isoformat(),
        "key_used": key
    }
    key_data["used"] = True
    key_data["used_by"] = str(user_id)
    key_data["used_at"] = datetime.now(TZ).isoformat()
    save_json(KEYS_DB, STATE["keys"])
    save_json(USERS_DB, STATE["users"])
    await message.answer(f"✅ <b>Abonnement activé avec succès !</b>\n\n"
                         f"Vous avez accès au bot pendant <b>{days} jours</b>.\n"
                         f"Expiration : <b>{expires_at.strftime('%d/%m/%Y à %H:%M')}</b>\n\n"
                         f"👇 Utilisez les boutons ci-dessous pour voir les tickets.",
                         parse_mode="HTML", reply_markup=USER_KEYBOARD)


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
    dixon_status = "✅ actif" if STATE["dixon_model"] else "❌ inactif"
    txt = (
        f"<b>🔍 DEBUG</b>\n\n"
        f"Dixon-Coles : <b>{dixon_status}</b>\n"
        f"Events Odds API : <b>{d.get('odds_events', 0)}</b>\n"
        f"Matchs construits : <b>{d.get('matched', 0)}</b>\n"
        f"Contextes Bzzoiro : <b>{d.get('enriched', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>"
    )
    await safe_answer(message, txt)


async def send_coupon(message, name):
    c = next((x for x in STATE["coupons"] if x.name == name), None)
    if c:
        await safe_answer(message, format_coupon(c))
    else:
        await safe_answer(message, "Aucun coupon pour ce profil.")


@dp.message(F.text == BTN_SAFE)
async def btn_safe(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await send_coupon(message, "Ticket Sécurisé")


@dp.message(F.text == BTN_BAL)
async def btn_bal(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await send_coupon(message, "Ticket Équilibré")


@dp.message(F.text == BTN_VAR)
async def btn_var(message: Message):
    if message.from_user.id != ADMIN_ID and not is_subscribed(message.from_user.id):
        await message.answer(f"❌ {get_subscription_message(message.from_user.id)}")
        return
    await send_coupon(message, "Ticket Value")


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
    await message.answer(
        "🔑 <b>Pour activer votre abonnement :</b>\n\n"
        "Envoyez la commande <code>/activate VOTRE_CLE</code>\n"
        "Ou envoyez simplement votre clé à 8 caractères (ex: <code>MTJ8XWUZ</code>).",
        parse_mode="HTML"
    )


@dp.message(F.text == "⚙️ Debug")
async def btn_debug(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await debug_cmd(message)


# =========================================================
# SCAN
# =========================================================
async def scan():
    async with STATE["scan_lock"]:
        odds_events = await fetch_all_odds_events()
        matches = build_matches_from_odds(odds_events)

        print(f"📅 Matchs aujourd'hui : {len(matches)}")
        matches = filter_and_rank_matches(matches)

        contexts = {}
        try:
            contexts = await enrich_matches(matches)
        except Exception as e:
            print(f"⚠️ Enrichissement Bzzoiro désactivé : {e}")

        used_today = load_used_today()
        selections = await build_gemini_picks(matches, used_today, contexts)
        print(f"🎯 Sélections : {len(selections)}")

        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)

        used_picks = [s for c in STATE["coupons"] for s in c.legs]
        mark_picks_used(used_picks)

        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        gemini_ok = sum(1 for s in selections if s.source == "gemini")
        with_ctx = sum(1 for s in selections if s.has_context)

        STATE["debug"] = {
            "odds_events": len(odds_events),
            "matched": len(matches),
            "enriched": len(contexts),
            "gemini_ok": gemini_ok,
            "with_ctx": with_ctx,
            "selections": len(selections),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Odds events   : {len(odds_events)}")
        print(f"Matchs croisés: {len(matches)}")
        print(f"Contextes     : {len(contexts)}")
        print(f"Sélections    : {len(selections)}")
        print(f"Avec contexte : {with_ctx}")
        print(f"Coupons       : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} paris, cote {c.combined_odds}")
        print("────────────────────────")


# =========================================================
# BROADCAST / LIFESPAN
# =========================================================
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
                      id="daily_broadcast", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.start()

    bot_task = asyncio.create_task(dp.start_polling(bot))
    print("✅ Bot Telegram démarré.")

    async def background_bootstrap():
        try:
            if download_and_train:
                print("🧠 Entraînement Dixon-Coles en cours (peut prendre 60s)...")
                STATE["dixon_model"] = await download_and_train()
                if STATE["dixon_model"]:
                    print(f"✅ Dixon-Coles actif ({len(STATE['dixon_model'].teams)} équipes)")
                else:
                    print("⚠️ Dixon-Coles indisponible — le bot utilisera Gemini seul.")
            else:
                print("⚠️ Module Dixon-Coles non importé.")

            await asyncio.sleep(5)
            if NOTIFY_ON_STARTUP:
                for chat_id in TARGETS:
                    await safe_send(chat_id, f"{format_header()}\n\n⏳ <b>Bot démarré, analyse en cours...</b>")
            await scan()
            record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
            await broadcast_after_scan()
        except Exception as e:
            print(f"⚠️ background_bootstrap crash: {e}")

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
    return {"status": "ok", "ready": STATE["ready"],
            "selections": len(STATE["selections"]),
            "coupons": [c.name for c in STATE["coupons"]],
            "last_scan": STATE["last_scan"]}


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
