from __future__ import annotations

import asyncio
import json
import os
import re
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
except Exception:
    predict_match_probas = None

try:
    from football_data import get_fixture_result
except Exception:
    get_fixture_result = None


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
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • Dixon-Coles + Gemini")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

MIN_PROB = float(os.getenv("MIN_PROB", "0.62"))
MAX_MATCHES_TO_ANALYZE = int(os.getenv("MAX_MATCHES_TO_ANALYZE", "20"))

SAFE_MIN_ODDS = 2.0
SAFE_MAX_ODDS = 2.5
BAL_MIN_ODDS = 3.0
BAL_MAX_ODDS = 5.0
BAL_MIN_LEG_ODDS = 1.20
VALUE_TOP_N = 20

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not ODDS_API_KEY:
    raise RuntimeError("ODDS_API_KEY manquant")
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


def is_upcoming(iso_str) -> bool:
    dt = parse_dt_safe(iso_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    if dt.date() == today and dt > now + timedelta(minutes=15):
        return True
    if dt.date() == tomorrow:
        return True
    return False


def prob_bar(p: float) -> str:
    if p >= 0.80:
        return "🟢🟢🟢"
    if p >= 0.72:
        return "🟢🟢⚪"
    if p >= 0.65:
        return "🟢⚪⚪"
    return "⚪⚪⚪"


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
        any_pending = False

        for leg in legs:
            if leg.get("result") != "pending":
                if leg["result"] == "lost":
                    any_lost = True
                continue
            kickoff_dt = parse_dt_safe(leg.get("kickoff", ""))
            if not kickoff_dt or (now - kickoff_dt).total_seconds() < 9000:
                any_pending = True
                continue
            result = await get_fixture_result(leg["home"], leg["away"], kickoff_dt.strftime("%Y-%m-%d"))
            if not result or result.get("home_goals") is None:
                any_pending = True
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
        print(f"🌍 {sport_key} -> {r.status_code}")
        if r.status_code >= 400:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ {sport_key}: {e}")
        return []


async def fetch_all_events() -> list[dict]:
    all_events: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for key in SOCCER_KEYS:
            events = await fetch_odds_api_league(client, key)
            all_events.extend(events)
            await asyncio.sleep(0.3)
    print(f"📦 Total events: {len(all_events)}")
    return all_events


def extract_event_fields(ev: dict) -> dict:
    return {
        "event_id": ev.get("id", ""),
        "home": ev.get("home_team", ""),
        "away": ev.get("away_team", ""),
        "league": ev.get("sport_title", "") or ev.get("sport_key", ""),
        "kickoff": ev.get("commence_time", ""),
    }


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


# =========================================================
# BUILD PICKS
# =========================================================
async def build_gemini_picks(events_norm, used_today):
    selections: list[Selection] = []
    seen = set()
    stats = {"ok": 0, "fail": 0, "markets": 0}

    if not predict_match_probas:
        return selections

    async with httpx.AsyncClient(timeout=40.0) as client:
        for fx in events_norm[:MAX_MATCHES_TO_ANALYZE]:
            try:
                home, away = fx["home"], fx["away"]
                event_id, league = fx["event_id"], fx["league"]
                kickoff = fx["kickoff"]

                if event_id in used_today:
                    continue

                pred = await predict_match_probas(client, home, away, league, kickoff)
                if not pred or "markets" not in pred:
                    stats["fail"] += 1
                    continue

                stats["ok"] += 1
                markets = pred.get("markets", {})
                real_odds = extract_real_odds(fx.get("raw", {}))

                market_map = {
                    "double_chance_1X": (f"Double Chance 1X ({home} ou nul)", "Double Chance"),
                    "double_chance_12": (f"Double Chance 12 (Pas de nul)", "Double Chance"),
                    "double_chance_X2": (f"Double Chance X2 (Nul ou {away})", "Double Chance"),
                    "victoire_home": (f"Victoire {home}", "1X2"),
                    "match_nul": ("Match nul", "1X2"),
                    "victoire_away": (f"Victoire {away}", "1X2"),
                    "over_0.5": ("Over 0.5 buts", "Over/Under"),
                    "over_1.5": ("Over 1.5 buts", "Over/Under"),
                    "over_2.5": ("Over 2.5 buts", "Over/Under"),
                    "over_3.5": ("Over 3.5 buts", "Over/Under"),
                    "btts_yes": ("Les 2 marquent : Oui", "BTTS"),
                    "btts_no": ("Les 2 marquent : Non", "BTTS"),
                }

                for key, (label, mkt) in market_map.items():
                    prob_pct = markets.get(key)
                    if prob_pct is None:
                        continue
                    try:
                        prob = float(prob_pct) / 100.0
                    except (ValueError, TypeError):
                        continue
                    if prob < MIN_PROB or prob > 0.92:
                        continue

                    dedup = f"{event_id}|{mkt}|{label}"
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    fair_odd = round(1.0 / prob, 2)
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

                    selections.append(Selection(
                        event_id=event_id, league=league, home=home, away=away,
                        kickoff=kickoff, market=mkt, pick_label=label,
                        odds=fair_odd, model_prob=round(prob, 4),
                        edge=0.0, bookmaker="Gemini",
                        real_odd=real_odd,
                    ))
                    stats["markets"] += 1

                await asyncio.sleep(1.0)

            except Exception as e:
                print(f"⚠️ build_gemini_picks: {e}")

    print(f"🏠 Picks : matchs_ok={stats['ok']} échec={stats['fail']} marchés={stats['markets']}")
    return selections


# =========================================================
# COUPONS
# =========================================================
def build_coupon_target(name, subtitle, emoji, pool, target_min, target_max,
                        max_legs, globally_used, min_leg_odds=1.01):
    pool = [s for s in pool
            if s.odds >= min_leg_odds
            and s.event_id not in globally_used]
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
        if s.market in used_markets:
            continue

        next_odds = combined_odds * s.odds
        if next_odds > target_max and legs:
            continue

        legs.append(s)
        used_events.add(s.event_id)
        used_leagues.add(s.league)
        used_markets.add(s.market)
        combined_odds = next_odds
        combined_prob *= s.final_prob()

        if combined_odds >= target_min:
            break

    if not legs:
        return None
    if combined_odds < target_min or combined_odds > target_max:
        return None

    legs.sort(key=lambda s: s.odds)
    for s in legs:
        globally_used.add(s.event_id)

    return Coupon(name, subtitle, emoji, legs,
                  round(combined_odds, 2), round(combined_prob, 4))


def build_value_list(selections, top_n=20, globally_used=None):
    if globally_used is None:
        globally_used = set()

    pool = [s for s in selections if s.event_id not in globally_used]
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
        "Ticket Value", f"Top {len(top_list)} pronostics les plus probables",
        "💎", top_list,
        round(combined_odds, 2), round(combined_prob, 4),
        is_list=True,
    )


def build_all_coupons(selections):
    coupons = []
    used = set()

    pool_safe = [s for s in selections if s.final_prob() >= 0.70]
    c = build_coupon_target(
        "Ticket Sécurisé", "Cote 2.0 - 2.5 • Priorité sûreté",
        "🔒", pool_safe, SAFE_MIN_ODDS, SAFE_MAX_ODDS, 5, used,
        min_leg_odds=1.20,
    )
    if c:
        coupons.append(c)

    pool_bal = [s for s in selections if s.final_prob() >= 0.62]
    c = build_coupon_target(
        "Ticket Équilibré", "Cote 3 - 5 • Cote mini 1.20/leg",
        "⚖️", pool_bal, BAL_MIN_ODDS, BAL_MAX_ODDS, 15, used,
        min_leg_odds=BAL_MIN_LEG_ODDS,
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


def format_selection_block(s: Selection, idx: int):
    dt = parse_dt_safe(s.kickoff)
    day_tag = ""
    if dt:
        today = datetime.now(TZ).date()
        if dt.date() == today:
            day_tag = "📍 <b>Aujourd'hui</b> "
        elif dt.date() == today + timedelta(days=1):
            day_tag = "📅 <b>Demain</b> "

    prob_pct = s.final_prob() * 100

    lines = [
        f"<b>┌─ #{idx} {s.home} vs {s.away}</b>",
        f"│ {day_tag}🕐 <b>{kickoff_local(s.kickoff)}</b> • {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>",
        f"│ 📊 Proba : <b>{prob_pct:.1f}%</b> {prob_bar(s.final_prob())}",
    ]
    if s.real_odd:
        lines.append(f"│ 💰 Cote réelle : <b>{s.real_odd}</b>")
    else:
        lines.append(f"│ 💰 Cote théorique : {s.odds}")
    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)


def format_coupon(c: Coupon):
    if c.is_list:
        lines = [
            format_header(), "",
            f"<b>{c.emoji} {c.name.upper()}</b>",
            f"<i>{c.subtitle}</i>", "",
            f"<b>🎯 {len(c.legs)} pronostics classés par probabilité</b>",
            "",
        ]
        for i, s in enumerate(c.legs, 1):
            lines.append(format_selection_block(s, i))
            lines.append("")
        lines.append(f"<i>{PUBLIC_FOOTER}</i>")
        return "\n".join(lines)

    prob_level = "🟢 Faible risque" if c.combined_prob >= 0.40 else \
                 "🟡 Risque modéré" if c.combined_prob >= 0.20 else "🔴 Risque élevé"
    lines = [
        format_header(), "",
        f"<b>{c.emoji} {c.name.upper()}</b>",
        f"<i>{c.subtitle}</i>", "",
        f"<b>┏━━━━━━━━━━━━━━━━━┓</b>",
        f"<b>┃ 📊 RÉCAP TICKET</b>",
        f"<b>┃ 🎫 Cote totale : {c.combined_odds}</b>",
        f"<b>┃ 📈 Proba combinée : {c.combined_prob*100:.1f}%</b>",
        f"<b>┃ 🎯 {prob_level}</b>",
        f"<b>┗━━━━━━━━━━━━━━━━━┛</b>", "",
    ]
    for i, s in enumerate(c.legs, 1):
        lines.append(format_selection_block(s, i))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_summary():
    return (
        f"{format_header()}\n\n"
        f"<b>✅ ANALYSE IA PRÊTE</b>\n"
        f"🎯 Sélections : <b>{len(STATE['selections'])}</b>\n"
        f"🎫 Coupons : <b>{len(STATE['coupons'])}</b>\n"
        f"🔄 Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )


def format_coupons_ready():
    if not STATE["coupons"]:
        return (f"{format_header()}\n\n<b>📭 Aucun coupon aujourd'hui</b>\n"
                f"<i>Pas assez de paris à haute proba.</i>")
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
    try:
        await message.answer(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Answer: {e}")


async def safe_send(chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Send {chat_id}: {e}")


async def broadcast_after_scan():
    """Envoie uniquement le message d'annonce (pas les détails)."""
    for chat_id in TARGETS:
        await safe_send(chat_id, format_coupons_ready())
        await asyncio.sleep(0.5)


@dp.message(Command("start"))
async def start_cmd(message):
    await safe_answer(message, f"{format_header()}\n\n<b>👋 Bienvenue !</b>\n\nTape /scan.")


@dp.message(Command("scan"))
async def scan_cmd(message):
    await safe_answer(message, "⏳ Analyse en cours (2-3 min)...")
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
        f"Events : <b>{d.get('events_total', 0)}</b>\n"
        f"Matchs Gemini : <b>{d.get('gemini_ok', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
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
        events_raw = await fetch_all_events()
        print(f"📦 Events: {len(events_raw)}")

        events_norm = []
        for ev in events_raw:
            fields = extract_event_fields(ev)
            if not fields["event_id"] or not fields["home"] or not fields["away"]:
                continue
            if not is_upcoming(fields["kickoff"]):
                continue
            fields["raw"] = ev
            events_norm.append(fields)
        print(f"🎯 Events J/J+1: {len(events_norm)}")

        used_today = load_used_today()
        selections = await build_gemini_picks(events_norm, used_today)
        print(f"🎯 Sélections: {len(selections)}")

        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)

        used_picks = [s for c in STATE["coupons"] for s in c.legs]
        mark_picks_used(used_picks)

        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        gemini_ok = sum(1 for s in selections if s.source == "gemini")

        STATE["debug"] = {
            "events_total": len(events_raw),
            "gemini_ok": gemini_ok,
            "selections": len(selections),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Sélections : {len(selections)}")
        print(f"Coupons    : {len(STATE['coupons'])}")
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
