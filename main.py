from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
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
except Exception:
    predict_match_probas = None

try:
    from football_data import (
        get_fixture_result,
        fetch_all_today_fixtures,
        enrich_match,
    )
except Exception:
    get_fixture_result = None
    fetch_all_today_fixtures = None
    enrich_match = None


# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • API-Football + Gemini")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

MIN_PROB = float(os.getenv("MIN_PROB", "0.60"))
MIN_ODD = float(os.getenv("MIN_ODD", "1.35"))
MAX_MATCHES_TO_ANALYZE = int(os.getenv("MAX_MATCHES_TO_ANALYZE", "25"))
MAX_MATCHES_TO_ENRICH = int(os.getenv("MAX_MATCHES_TO_ENRICH", "10"))

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
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

SOCCER_KEYS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_efl_champ", "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
]

LEAGUE_TITLE_TO_KEY = {
    "premier league": "soccer_epl",
    "la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue 1": "soccer_france_ligue_one",
    "champions league": "soccer_uefa_champs_league",
    "europa league": "soccer_uefa_europa_league",
    "championship": "soccer_efl_champ",
    "eredivisie": "soccer_netherlands_eredivisie",
    "primeira liga": "soccer_portugal_primeira_liga",
}


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
    has_context: bool = False

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
# GLOBALS
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_SAFE = "🔒 Sécurisé"
BTN_BAL = "⚖️ Équilibré"
BTN_VAR = "💎 Value"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_VAR), KeyboardButton(text=BTN_BILAN)],
        [KeyboardButton(text=BTN_SCAN)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choisis un ticket…",
)

STATE = {
    "ready": False,
    "selections": [],
    "coupons": [],
    "last_scan": None,
    "debug": {},
    "tracker": {"coupons": []},
    "scan_lock": asyncio.Lock(),
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


def team_names_match(a: str, b: str) -> bool:
    """Match deux noms d'équipes avec fuzzy intelligent."""
    na = normalize_name(a)
    nb = normalize_name(b)
    if not na or not nb:
        return False

    # Exact
    if na == nb:
        return True

    # Inclusion
    if na in nb or nb in na:
        return True

    # Premier mot identique (>= 4 chars) + reste proche
    wa, wb = na.split(), nb.split()
    if wa and wb and wa[0] == wb[0] and len(wa[0]) >= 4:
        if len(wa) >= 2 and len(wb) >= 2:
            if wa[1][:3] == wb[1][:3]:
                return True
        else:
            return True

    # Similarité globale
    ratio = SequenceMatcher(None, na, nb).ratio()
    if ratio >= 0.80:
        return True

    return False


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


def league_key_from_title(title: str) -> Optional[str]:
    tl = (title or "").lower()
    for k, v in LEAGUE_TITLE_TO_KEY.items():
        if k in tl:
            return v
    return None


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


def evaluate_leg_result(leg: dict, home_goals: int, away_goals: int) -> str:
    if home_goals is None or away_goals is None:
        return "pending"
    market = leg.get("market", "")
    label = leg.get("pick_label", "").lower()
    home = leg.get("home", "").lower()
    away = leg.get("away", "").lower()
    total = home_goals + away_goals

    if market == "1X2":
        if "victoire" in label and home in label:
            return "won" if home_goals > away_goals else "lost"
        if "victoire" in label and away in label:
            return "won" if away_goals > home_goals else "lost"
        if "match nul" in label:
            return "won" if home_goals == away_goals else "lost"

    if market == "Double Chance":
        if "1x" in label:
            return "won" if home_goals >= away_goals else "lost"
        if "x2" in label:
            return "won" if away_goals >= home_goals else "lost"
        if "12" in label or "pas de nul" in label:
            return "won" if home_goals != away_goals else "lost"

    if market == "Handicap":
        if "+0.5" in label:
            if home in label:
                return "won" if home_goals >= away_goals else "lost"
            if away in label:
                return "won" if away_goals >= home_goals else "lost"

    if market == "Over/Under":
        m = re.search(r"(over|under)\s+([\d.]+)", label)
        if m:
            prefix, line = m.group(1), float(m.group(2))
            if prefix == "over":
                return "won" if total > line else "lost"
            else:
                return "won" if total < line else "lost"

    if market == "BTTS":
        if "oui" in label:
            return "won" if (home_goals >= 1 and away_goals >= 1) else "lost"
        if "non" in label:
            return "won" if not (home_goals >= 1 and away_goals >= 1) else "lost"

    return "pending"


async def settle_coupons():
    if not get_fixture_result:
        return
    tracker = STATE["tracker"]
    coupons = tracker.get("coupons", [])
    if not coupons:
        return
    now = datetime.now(TZ)
    updated = 0

    for coupon in coupons:
        if coupon.get("status") != "pending":
            continue
        legs = coupon.get("legs", [])
        any_lost = False

        for leg in legs:
            if leg.get("result") != "pending":
                if leg["result"] == "lost":
                    any_lost = True
                continue
            kickoff_dt = parse_dt_safe(leg.get("kickoff", ""))
            if not kickoff_dt or (now - kickoff_dt).total_seconds() < 9000:
                continue
            result = await get_fixture_result(leg["home"], leg["away"], kickoff_dt.strftime("%Y-%m-%d"))
            if not result or result.get("home_goals") is None:
                continue
            outcome = evaluate_leg_result(leg, result["home_goals"], result["away_goals"])
            leg["result"] = outcome
            if outcome == "lost":
                any_lost = True
            await asyncio.sleep(0.3)

        if any_lost:
            coupon["status"] = "lost"
            updated += 1
        elif all(leg.get("result") != "pending" for leg in legs):
            coupon["status"] = "won"
            updated += 1
    if updated:
        save_tracker(tracker)
        print(f"✅ {updated} coupon(s) réglé(s)")


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
    print(f"💰 Odds API : {len(all_events)} events avec cotes")
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


def match_fixtures_with_odds(fixtures: list[dict], odds_events: list[dict]) -> list[dict]:
    odds_index = {}
    for ev in odds_events:
        h = normalize_name(ev.get("home_team", ""))
        a = normalize_name(ev.get("away_team", ""))
        key = f"{h}|{a}"
        odds_index[key] = ev

    matched = []
    matched_count = 0

    for fx in fixtures:
        h_norm = normalize_name(fx["home"])
        a_norm = normalize_name(fx["away"])

        # 1. Match exact
        ev = odds_index.get(f"{h_norm}|{a_norm}")

        # 2. Fuzzy intelligent
        if not ev:
            for ok, oev in odds_index.items():
                try:
                    oh, oa = ok.split("|", 1)
                except ValueError:
                    continue
                if team_names_match(h_norm, oh) and team_names_match(a_norm, oa):
                    ev = oev
                    print(f"  🔗 Fuzzy : {fx['home']} vs {fx['away']} "
                          f"→ {oev.get('home_team')} vs {oev.get('away_team')}")
                    break

        if ev:
            fx["real_odds"] = extract_real_odds(ev)
            fx["has_odds"] = True
            fx["odds_sport_key"] = ev.get("sport_key", "")
            fx["odds_sport_title"] = ev.get("sport_title", "")
            matched_count += 1
        else:
            fx["real_odds"] = {}
            fx["has_odds"] = False
            fx["odds_sport_key"] = ""
            fx["odds_sport_title"] = ""

        matched.append(fx)

    print(f"🔗 Croisement : {matched_count}/{len(matched)} matchs avec cotes")
    return matched


# =========================================================
# ENRICHISSEMENT
# =========================================================
async def enrich_matches_with_context(matches: list[dict]) -> dict:
    if not enrich_match:
        return {}

    with_odds = [m for m in matches if m.get("has_odds")]
    to_enrich = with_odds[:MAX_MATCHES_TO_ENRICH]

    if not to_enrich:
        print("⚠️ Aucun match avec cotes à enrichir")
        return {}

    print(f"🌐 Enrichissement contexte (max {MAX_MATCHES_TO_ENRICH})...")
    contexts = {}

    for i, fx in enumerate(to_enrich):
        try:
            sport_key = fx.get("odds_sport_key") or ""
            if not sport_key:
                sport_key = league_key_from_title(fx.get("league", "")) or ""
            if not sport_key:
                sport_key = league_key_from_title(fx.get("odds_sport_title", "")) or ""

            if not sport_key:
                print(f"  ⏭ {fx['home']} vs {fx['away']} : pas de league key")
                continue

            ctx = await enrich_match(fx["home"], fx["away"], sport_key)
            if ctx.get("available"):
                contexts[fx["event_id"]] = ctx
                hs = ctx.get("home_stats", {})
                aws = ctx.get("away_stats", {})
                print(f"  ✅ {fx['home']} vs {fx['away']} — "
                      f"forme {hs.get('form', '?')} / {aws.get('form', '?')}")
            else:
                print(f"  ❌ {fx['home']} vs {fx['away']} : pas de contexte")

            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ Enrich crash {fx.get('home')}: {e}")

    print(f"✅ Contextes enrichis : {len(contexts)}/{len(to_enrich)}")
    return contexts


# =========================================================
# BUILD PICKS
# =========================================================
async def build_gemini_picks(matches, contexts, used_today):
    selections: list[Selection] = []
    seen = set()
    stats = {"ok": 0, "fail": 0, "kept": 0, "with_ctx": 0}

    if not predict_match_probas:
        return selections

    with_odds = [m for m in matches if m.get("has_odds")]
    without_odds = [m for m in matches if not m.get("has_odds")]
    ordered = with_odds + without_odds

    async with httpx.AsyncClient(timeout=40.0) as client:
        for fx in ordered[:MAX_MATCHES_TO_ANALYZE]:
            try:
                home, away = fx["home"], fx["away"]
                event_id, league = fx["event_id"], fx["league"]
                kickoff = fx["kickoff"]

                if event_id in used_today:
                    continue

                ctx = contexts.get(event_id)
                has_ctx = ctx is not None and ctx.get("available", False)

                pred = await predict_match_probas(
                    client, home, away, league, kickoff, context=ctx
                )
                if not pred or "markets" not in pred:
                    stats["fail"] += 1
                    continue

                stats["ok"] += 1
                if has_ctx:
                    stats["with_ctx"] += 1

                markets = pred.get("markets", {})
                analysis = pred.get("analysis", "")
                advice = pred.get("advice", "")
                advice_conf = pred.get("advice_confidence", 0)
                try:
                    advice_conf = int(advice_conf)
                    if advice_conf < 0 or advice_conf > 100:
                        advice_conf = 0
                except (ValueError, TypeError):
                    advice_conf = 0

                real_odds = fx.get("real_odds", {})

                market_map = {
                    "victoire_home": (f"Victoire {home}", "1X2"),
                    "match_nul": ("Match nul", "1X2"),
                    "victoire_away": (f"Victoire {away}", "1X2"),
                    "double_chance_1X": (f"Double Chance 1X ({home} ou nul)", "Double Chance"),
                    "double_chance_12": ("Double Chance 12 (Pas de nul)", "Double Chance"),
                    "double_chance_X2": (f"Double Chance X2 (Nul ou {away})", "Double Chance"),
                    "handicap_home_plus_05": (f"{home} +0.5", "Handicap"),
                    "handicap_away_plus_05": (f"{away} +0.5", "Handicap"),
                    "over_2.5": ("Over 2.5 buts", "Over/Under"),
                    "under_2.5": ("Under 2.5 buts", "Over/Under"),
                    "over_3.5": ("Over 3.5 buts", "Over/Under"),
                    "btts_yes": ("Les 2 marquent : Oui", "BTTS"),
                    "btts_no": ("Les 2 marquent : Non", "BTTS"),
                    "over_6.5_corners": ("Plus de 6.5 corners", "Corners"),
                    "over_8.5_corners": ("Plus de 8.5 corners", "Corners"),
                    "over_10.5_corners": ("Plus de 10.5 corners", "Corners"),
                    "home_over_2.5_corners": (f"Plus de 2.5 corners ({home})", "Corners"),
                    "away_over_2.5_corners": (f"Plus de 2.5 corners ({away})", "Corners"),
                    "over_2.5_cartons": ("Plus de 2.5 cartons", "Cartons"),
                    "over_3.5_cartons": ("Plus de 3.5 cartons", "Cartons"),
                }

                candidates = []
                for key, (label, mkt) in market_map.items():
                    prob_pct = markets.get(key)
                    if prob_pct is None:
                        continue
                    try:
                        prob = float(prob_pct) / 100.0
                    except (ValueError, TypeError):
                        continue
                    if prob < MIN_PROB or prob > 0.85:
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

                    score = prob
                    if real_odd:
                        score += 0.5
                    if has_ctx:
                        score += 0.3

                    candidates.append((score, label, mkt, used_odd, real_odd, prob))

                candidates.sort(reverse=True, key=lambda x: x[0])
                used_markets = set()
                kept = 0
                for score, label, mkt, used_odd, real_odd, prob in candidates:
                    if mkt in used_markets:
                        continue
                    used_markets.add(mkt)

                    dedup = f"{event_id}|{mkt}|{label}"
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    selections.append(Selection(
                        event_id=event_id, league=league, home=home, away=away,
                        kickoff=kickoff, market=mkt, pick_label=label,
                        odds=round(used_odd, 2), model_prob=round(prob, 4),
                        edge=0.0,
                        bookmaker="The Odds API" if real_odd else "Gemini",
                        real_odd=real_odd,
                        analysis=analysis,
                        advice=advice,
                        advice_confidence=advice_conf,
                        has_context=has_ctx,
                    ))
                    stats["kept"] += 1
                    kept += 1
                    if kept >= 3:
                        break

                await asyncio.sleep(2.5)

            except Exception as e:
                print(f"⚠️ build_gemini_picks: {e}")

    print(f"🏠 Picks : ok={stats['ok']} échec={stats['fail']} "
          f"gardés={stats['kept']} avec_contexte={stats['with_ctx']}")
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
    used_leagues = set()
    used_markets = set()
    combined_odds = 1.0
    combined_prob = 1.0

    for s in pool:
        if len(legs) >= max_legs:
            break
        if s.event_id in used_events or s.league in used_leagues:
            continue
        if s.market in used_markets and len(legs) >= 2:
            continue

        next_odds = combined_odds * s.odds
        if next_odds > target_max * 1.15 and legs:
            continue

        legs.append(s)
        used_events.add(s.event_id)
        used_leagues.add(s.league)
        used_markets.add(s.market)
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

    pool.sort(key=lambda s: s.final_prob(), reverse=True)

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
        "Ticket Value", f"Top {len(top_list)} pronostics",
        "💎", top_list,
        round(combined_odds, 2), round(combined_prob, 4),
        is_list=True,
    )


def build_all_coupons(selections):
    coupons = []
    used = set()

    pool_safe = [s for s in selections
                 if s.final_prob() >= 0.68
                 and s.market in ("1X2", "Double Chance", "Over/Under", "BTTS")]
    c = build_coupon_target(
        "Ticket Sécurisé", "Cote 2.0 - 2.5 • Priorité sûreté",
        "🔒", pool_safe, SAFE_MIN_ODDS, SAFE_MAX_ODDS, 5, used,
        min_leg_odds=1.35, strict=True,
    )
    if c:
        coupons.append(c)

    pool_bal = [s for s in selections
                if s.final_prob() >= 0.60
                and s.market not in ("Corners", "Cartons")]
    c = build_coupon_target(
        "Ticket Équilibré", "Cote 2.5 - 5.0",
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
    ctx_badge = " ✅ <i>données réelles</i>" if s.has_context else ""

    lines = [
        f"<b>┌─ #{idx} {s.home} vs {s.away}</b>",
        f"│ {day_tag}🕐 <b>{kickoff_local(s.kickoff)}</b> • {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>  <i>({s.market})</i>",
        f"│ 📊 Proba : <b>{prob_pct:.1f}%</b> {prob_bar(s.final_prob())}{ctx_badge}",
    ]

    if s.real_odd:
        lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b> <i>(réelle)</i>")
    else:
        lines.append(f"│ 💰 Cote : <b>{s.odds}</b> <i>(estimée)</i>")

    if not compact and s.analysis:
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>Avis IA</b> :")
        lines.append(f"│ 💬 <i>{s.analysis}</i>")
        if s.advice:
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
            f"<b>🎯 {len(c.legs)} pronostics classés par probabilité</b>",
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
                f"<i>Pas assez de paris à haute proba (cote ≥ {MIN_ODD}).</i>")
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
    total_returned = sum(c.get("combined_odds", 1.0) for c in coupons if c.get("status") == "won")
    profit = total_returned - settled
    roi = (profit / settled * 100) if settled > 0 else 0
    return (
        f"{format_header()}\n\n"
        f"<b>📊 BILAN</b>\n"
        f"🎫 Total : <b>{total}</b>\n"
        f"✅ Gagnés : <b>{won}</b>\n"
        f"❌ Perdus : <b>{lost}</b>\n"
        f"⏳ En attente : <b>{pending}</b>\n\n"
        f"📈 Taux réussite : <b>{wr:.1f}%</b>\n"
        f"💰 ROI : <b>{roi:+.1f}%</b>\n"
        f"💵 Profit : <b>{profit:+.2f}u</b>"
    )


# =========================================================
# TELEGRAM
# =========================================================
async def safe_answer(message, text):
    parts = split_message(text)
    for i, part in enumerate(parts):
        try:
            if i == 0:
                await message.answer(part, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
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


@dp.message(Command("start"))
async def start_cmd(message):
    await safe_answer(message, f"{format_header()}\n\n<b>👋 Bienvenue !</b>\n\nTape /scan.")


@dp.message(Command("scan"))
async def scan_cmd(message):
    await safe_answer(message, "⏳ Analyse en cours (3-5 min)...")
    await scan()
    await broadcast_after_scan()


@dp.message(Command("regler"))
async def regler_cmd(message):
    await safe_answer(message, "⏳ Vérification...")
    await settle_coupons()
    await safe_answer(message, format_bilan(STATE["tracker"]))


@dp.message(Command("debug"))
async def debug_cmd(message):
    d = STATE["debug"]
    txt = (
        f"<b>🔍 DEBUG</b>\n\n"
        f"Matchs API-Football : <b>{d.get('af_fixtures', 0)}</b>\n"
        f"Events avec cotes : <b>{d.get('odds_events', 0)}</b>\n"
        f"Matchs croisés : <b>{d.get('matched', 0)}</b>\n"
        f"Contextes enrichis : <b>{d.get('enriched', 0)}</b>\n"
        f"Matchs Gemini : <b>{d.get('gemini_ok', 0)}</b>\n"
        f"  • avec contexte : <b>{d.get('with_ctx', 0)}</b>\n"
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
async def btn_safe(message):
    await send_coupon(message, "Ticket Sécurisé")


@dp.message(F.text == BTN_BAL)
async def btn_bal(message):
    await send_coupon(message, "Ticket Équilibré")


@dp.message(F.text == BTN_VAR)
async def btn_var(message):
    await send_coupon(message, "Ticket Value")


@dp.message(F.text == BTN_BILAN)
async def btn_bilan(message):
    await safe_answer(message, format_bilan(STATE["tracker"]))


@dp.message(F.text == BTN_SCAN)
async def btn_scan(message):
    await scan_cmd(message)


# =========================================================
# SCAN
# =========================================================
async def scan():
    async with STATE["scan_lock"]:
        af_fixtures = []
        if fetch_all_today_fixtures:
            af_fixtures = await fetch_all_today_fixtures()

        odds_events = await fetch_all_odds_events()

        if af_fixtures:
            matches = match_fixtures_with_odds(af_fixtures, odds_events)
        else:
            print("⚠️ Fallback : utilisation de The Odds API seul")
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
                    "odds_sport_title": ev.get("sport_title", ""),
                })
        print(f"📊 Matchs après croisement : {len(matches)}")

        matches = [m for m in matches if is_today_upcoming(m["kickoff"])]
        print(f"📅 Matchs aujourd'hui : {len(matches)}")

        contexts = await enrich_matches_with_context(matches)

        used_today = load_used_today()
        selections = await build_gemini_picks(matches, contexts, used_today)
        print(f"🎯 Sélections : {len(selections)}")

        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)

        used_picks = [s for c in STATE["coupons"] for s in c.legs]
        mark_picks_used(used_picks)

        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        gemini_ok = sum(1 for s in selections if s.source == "gemini")
        with_ctx = sum(1 for s in selections if s.has_context)

        STATE["debug"] = {
            "af_fixtures": len(af_fixtures),
            "odds_events": len(odds_events),
            "matched": len(matches),
            "enriched": len(contexts),
            "gemini_ok": gemini_ok,
            "with_ctx": with_ctx,
            "selections": len(selections),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"AF fixtures   : {len(af_fixtures)}")
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


async def startup_scan_and_notify():
    try:
        await asyncio.sleep(10)
        if NOTIFY_ON_STARTUP:
            for chat_id in TARGETS:
                await safe_send(chat_id, f"{format_header()}\n\n⏳ <b>Bot démarré, analyse en cours...</b>")
        await scan()
        record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
        await broadcast_after_scan()
    except Exception as e:
        print(f"⚠️ startup crash: {e}")


async def bootstrap():
    STATE["tracker"] = load_tracker()
    STATE["ready"] = True
    print("✅ Bot prêt.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    await bootstrap()

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ),
                      id="daily_broadcast", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(settle_coupons, CronTrigger(hour=10, minute=0, timezone=TZ),
                      id="settle_coupons", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.start()

    bot_task = asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(startup_scan_and_notify())

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
