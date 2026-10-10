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
    import betbetter
except Exception:
    betbetter = None

try:
    from dixon_coles import download_and_train as dc_train, DixonColesModel
except Exception:
    dc_train = None
    DixonColesModel = None


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
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Value betting • Modèle indépendant")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.02"))
MIN_PROB = float(os.getenv("MIN_PROB", "0.55"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not ODDS_API_KEY:
    raise RuntimeError("ODDS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"

# Championnats à scanner (1 requête par ligue)
SOCCER_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_efl_champ",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
]

BETBETTER_LEAGUES = [
    "soccer/epl", "soccer/la-liga", "soccer/serie-a",
    "soccer/bundesliga", "soccer/ligue-1",
]

MAX_LEGS_PER_COUPON = 9


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
    confidence: str
    dc_prob: Optional[float] = None
    ai_prob: Optional[float] = None
    fair_odds: Optional[float] = None
    ai_verdict: str = ""
    ai_analysis: str = ""
    ai_advice: str = ""
    ai_critique: str = ""

    def final_prob(self) -> float:
        if self.ai_prob and 0.30 <= self.ai_prob <= 0.92:
            return self.ai_prob
        return self.model_prob

    def score(self) -> float:
        return self.final_prob() * 100 + self.edge * 10


@dataclass
class Coupon:
    name: str
    subtitle: str
    emoji: str
    legs: list[Selection]
    combined_odds: float
    combined_prob: float
    combined_ev: float


# =========================================================
# GLOBALS
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_SAFE = "🔒 Sécurisé"
BTN_BAL = "⚖️ Équilibré"
BTN_VAL = "💎 Value"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_VAL), KeyboardButton(text=BTN_BILAN)],
        [KeyboardButton(text=BTN_SCAN)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choisis un ticket…",
)

AI_MATCH_CACHE: dict = {}
AI_REVIEW_CACHE: dict = {}
DC_MODEL: Optional["DixonColesModel"] = None

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
    for suffix in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(suffix, "")
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


def parse_bb_game(game: str) -> tuple[str, str]:
    if not game:
        return "", ""
    if "@" in game:
        parts = game.split("@")
        return parts[0].strip(), parts[1].strip()
    return "", ""


def edge_bar(edge: float) -> str:
    if edge >= 0.10:
        return "🟢🟢🟢"
    if edge >= 0.07:
        return "🟢🟢⚪"
    if edge >= 0.04:
        return "🟢⚪⚪"
    return "⚪⚪⚪"


def ai_verdict_emoji(v: str) -> str:
    return {"ACCEPT": "✅", "CAUTION": "⚠️", "REJECT": "❌"}.get(v.upper(), "❓")


# =========================================================
# TRACKER + ANTI-RÉPÉTITION
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
        tracker["coupons"].append({
            "id": cid, "date": date_str, "name": c.name,
            "combined_odds": c.combined_odds, "status": "pending",
        })
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)


# =========================================================
# DIXON-COLES
# =========================================================
async def init_dixon_coles():
    global DC_MODEL
    if dc_train is None:
        print("⚠️ Module Dixon-Coles non disponible")
        return
    try:
        print("🎓 Initialisation Dixon-Coles...")
        DC_MODEL = await dc_train()
    except Exception as e:
        print(f"⚠️ Dixon-Coles init crash: {e}")
        DC_MODEL = None


# =========================================================
# BETBETTER
# =========================================================
def fetch_betbetter_picks() -> dict[str, dict]:
    if betbetter is None:
        print("⚠️ betbetter non installé")
        return {}
    result: dict[str, dict] = {}
    for league in BETBETTER_LEAGUES:
        try:
            feed = betbetter.get_picks(league)
            picks = feed.get("picks", []) or []
            print(f"🧠 BetBetter {league}: {len(picks)} picks")
            for p in picks:
                if p.get("locked"):
                    continue
                game = p.get("game", "")
                away, home = parse_bb_game(game)
                if not home or not away:
                    continue
                key = f"{normalize_name(away)}|{normalize_name(home)}"
                if key not in result:
                    result[key] = {
                        "away": away, "home": home,
                        "kickoff": p.get("gameTimeUtc", ""),
                        "league": league, "picks": [],
                    }
                result[key]["picks"].append(p)
        except Exception as e:
            print(f"⚠️ betbetter {league}: {e}")
    return result


# =========================================================
# THE ODDS API — FIXTURES + COTES (1 appel par ligue)
# =========================================================
async def fetch_odds_api_league(client: httpx.AsyncClient, sport_key: str) -> list[dict]:
    """Récupère les matchs + cotes d'une ligue."""
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
    }
    try:
        r = await client.get(url, params=params, timeout=30.0)

        remaining = r.headers.get("x-requests-remaining", "?")
        used = r.headers.get("x-requests-used", "?")
        print(f"🌍 {sport_key} -> {r.status_code} | used={used} remaining={remaining}")

        if r.status_code >= 400:
            print(f"⚠️ Body: {r.text[:200]}")
            return []

        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ {sport_key} crash: {e}")
        return []


async def fetch_all_events_with_odds() -> list[dict]:
    """Récupère tous les matchs avec leurs cotes (10 requêtes max)."""
    all_events: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for key in SOCCER_KEYS:
            events = await fetch_odds_api_league(client, key)
            all_events.extend(events)
            await asyncio.sleep(0.3)
    print(f"📦 Total events The Odds API: {len(all_events)}")
    return all_events


def extract_event_fields(ev: dict) -> dict:
    return {
        "event_id": ev.get("id", ""),
        "home": ev.get("home_team", ""),
        "away": ev.get("away_team", ""),
        "league": ev.get("sport_title", "") or ev.get("sport_key", ""),
        "kickoff": ev.get("commence_time", ""),
        "raw": ev,
    }


def extract_odds_from_event(ev: dict) -> dict:
    """
    Extrait les meilleures cotes (meilleure cote = la plus haute)
    pour h2h, totals et btts depuis les bookmakers.
    """
    result = {"h2h": {}, "totals": {}, "btts": {}}

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
                    if name == home:
                        result["h2h"]["home"] = max(result["h2h"].get("home", 0), price)
                    elif name == away:
                        result["h2h"]["away"] = max(result["h2h"].get("away", 0), price)
                    elif "draw" in name.lower():
                        result["h2h"]["draw"] = max(result["h2h"].get("draw", 0), price)

                elif mk == "totals":
                    point = out.get("point")
                    if point is None:
                        continue
                    is_over = "over" in name.lower()
                    is_under = "under" in name.lower()
                    if is_over or is_under:
                        try:
                            key = f"{'over' if is_over else 'under'}_{float(point)}"
                        except (ValueError, TypeError):
                            continue
                        result["totals"][key] = max(result["totals"].get(key, 0), price)

    return result


# =========================================================
# GEMINI — MATCHING
# =========================================================
async def ai_match_teams(client, bb_home, bb_away, candidates) -> Optional[str]:
    if not GEMINI_API_KEY or not candidates:
        return None
    cache_key = f"{normalize_name(bb_home)}|{normalize_name(bb_away)}"
    if cache_key in AI_MATCH_CACHE:
        return AI_MATCH_CACHE[cache_key]
    candidates = candidates[:20]
    lignes = "\n".join(f"{i+1}. {h} vs {a}" for i, (_, h, a) in enumerate(candidates))
    prompt = (
        f'Match modèle : "{bb_home} vs {bb_away}".\n'
        f"Trouve le MÊME match dans la liste :\n\n{lignes}\n\n"
        f"Réponds UNIQUEMENT par le numéro (1-{len(candidates)}) ou 0."
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"temperature": 0, "maxOutputTokens": 20}}
    try:
        r = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=15.0)
        if r.status_code >= 400:
            AI_MATCH_CACHE[cache_key] = None
            return None
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        m = re.search(r"\d+", text)
        if not m:
            AI_MATCH_CACHE[cache_key] = None
            return None
        num = int(m.group())
        if 1 <= num <= len(candidates):
            mid = candidates[num - 1][0]
            AI_MATCH_CACHE[cache_key] = mid
            return mid
        AI_MATCH_CACHE[cache_key] = None
        return None
    except Exception:
        AI_MATCH_CACHE[cache_key] = None
        return None


async def ai_rematch_all(bb_data, fixtures_norm) -> int:
    if not GEMINI_API_KEY:
        return 0
    candidates = [(f["event_id"], f["home"], f["away"]) for f in fixtures_norm if f["event_id"]]
    if not candidates:
        return 0
    already = set()
    for key in bb_data.keys():
        try:
            k_away, k_home = key.split("|", 1)
        except ValueError:
            continue
        for eid, h, a in candidates:
            hn, an = normalize_name(h), normalize_name(a)
            if (hn in k_home or k_home in hn) and (an in k_away or k_away in an):
                already.add(eid)
                break
    unmatched = [c for c in candidates if c[0] not in already]
    if not unmatched:
        return 0
    print(f"🤖 Gemini : {len(unmatched)} events non appariés")
    count = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        for key, data in bb_data.items():
            bb_home, bb_away = data["home"], data["away"]
            hn, an = normalize_name(bb_home), normalize_name(bb_away)
            found = any(
                (normalize_name(h) in hn or hn in normalize_name(h)) and
                (normalize_name(a) in an or an in normalize_name(a))
                for _, h, a in candidates
            )
            if found:
                continue
            mid = await ai_match_teams(client, bb_home, bb_away, unmatched)
            if mid:
                count += 1
                print(f"  🤖 Match IA : {bb_home} vs {bb_away} → {mid}")
                unmatched = [c for c in unmatched if c[0] != mid]
            await asyncio.sleep(1.2)
    return count


# =========================================================
# GEMINI — REVIEW CRITIQUE
# =========================================================
async def ai_review_selection(client, sel: Selection) -> None:
    if not GEMINI_API_KEY:
        return
    cache_key = f"rev|{sel.event_id}|{sel.market}|{sel.pick_label}"
    if cache_key in AI_REVIEW_CACHE:
        c = AI_REVIEW_CACHE[cache_key]
        sel.ai_verdict = c.get("verdict", "")
        sel.ai_analysis = c.get("analysis", "")
        sel.ai_advice = c.get("advice", "")
        sel.ai_prob = c.get("prob")
        sel.ai_critique = c.get("critique", "")
        return

    dc_line = ""
    if sel.dc_prob is not None:
        dc_line = f"\nProba Dixon-Coles : {sel.dc_prob*100:.1f}%"

    prompt = f"""Tu es un analyste football INDÉPENDANT et CRITIQUE.

Match : {sel.home} vs {sel.away}
Compétition : {sel.league}
Heure : {kickoff_local(sel.kickoff)}
Marché : {sel.market}
Pari : {sel.pick_label}
Cote (vraie, chez bookmaker) : {sel.odds}
Proba croisée du bot : {sel.model_prob*100:.1f}%{dc_line}
Edge : {sel.edge*100:+.1f}%

⚠️ Sois mesuré. ACCEPT si cohérent, CAUTION si risque, REJECT si douteux.

Réponds EXACTEMENT :

VERDICT: ACCEPT
PROBA: 72
CRITIQUE_BOT: [1 phrase]
ANALYSE: [2 phrases]
CONSEIL: [1 phrase]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 500},
    }

    try:
        r = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=30.0)
        if r.status_code >= 400:
            return
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]

        verdict, analysis, advice, critique = "", "", "", ""
        ai_prob = None
        for line in text.split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                if "ACCEPT" in v:
                    verdict = "ACCEPT"
                elif "REJECT" in v:
                    verdict = "REJECT"
                elif "CAUTION" in v:
                    verdict = "CAUTION"
            elif upper.startswith("PROBA:"):
                m = re.search(r"(\d+(?:\.\d+)?)", line)
                if m:
                    try:
                        p = float(m.group(1))
                        if 35 <= p <= 92:
                            ai_prob = p / 100.0
                    except ValueError:
                        pass
            elif upper.startswith("CRITIQUE_BOT:"):
                critique = line.split(":", 1)[1].strip()
            elif upper.startswith("ANALYSE:"):
                analysis = line.split(":", 1)[1].strip()
            elif upper.startswith("CONSEIL:"):
                advice = line.split(":", 1)[1].strip()

        sel.ai_verdict = verdict
        sel.ai_analysis = analysis
        sel.ai_advice = advice
        sel.ai_prob = ai_prob
        sel.ai_critique = critique
        AI_REVIEW_CACHE[cache_key] = {
            "verdict": verdict, "analysis": analysis, "advice": advice,
            "prob": ai_prob, "critique": critique,
        }
    except Exception as e:
        print(f"⚠️ Gemini review crash: {e}")


async def ai_review_all(selections: list[Selection]) -> None:
    if not GEMINI_API_KEY or not selections:
        return
    print(f"🧠 Revue IA de {len(selections)} sélections...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, s in enumerate(selections):
            await ai_review_selection(client, s)
            if (i + 1) % 5 == 0:
                print(f"  ... {i+1}/{len(selections)}")
            await asyncio.sleep(1.2)
    a = sum(1 for s in selections if s.ai_verdict == "ACCEPT")
    c = sum(1 for s in selections if s.ai_verdict == "CAUTION")
    r = sum(1 for s in selections if s.ai_verdict == "REJECT")
    print(f"🧠 IA : {a} ACCEPT, {c} CAUTION, {r} REJECT")


# =========================================================
# PICK LABEL
# =========================================================
def pick_label_for(market, selection, home, away, line):
    sel_norm = normalize_name(selection)
    market_lower = (market or "").strip().lower()
    home_norm = normalize_name(home)
    away_norm = normalize_name(away)

    if market_lower in ("moneyline", "money line", "1x2", "winner", "match winner"):
        if sel_norm and (sel_norm == home_norm or sel_norm in home_norm or home_norm in sel_norm):
            return f"Victoire {home}", "1X2"
        if sel_norm and (sel_norm == away_norm or sel_norm in away_norm or away_norm in sel_norm):
            return f"Victoire {away}", "1X2"
        if "draw" in sel_norm or "nul" in sel_norm or sel_norm == "x":
            return "Match nul", "1X2"
        return None, None

    if (market_lower in ("total", "totals", "over/under", "goals", "goals over/under")
            or "over" in sel_norm or "under" in sel_norm
            or "plus" in sel_norm or "moins" in sel_norm):
        is_over = "over" in sel_norm or "plus" in sel_norm
        is_under = "under" in sel_norm or "moins" in sel_norm
        if not (is_over or is_under):
            return None, None
        try:
            line_f = float(line) if line is not None else None
        except (ValueError, TypeError):
            line_f = None
        if line_f is None or line_f < 0.5 or line_f > 5.5:
            return None, None
        return f"{'Over' if is_over else 'Under'} {line_f} buts", "Over/Under"

    if "btts" in market_lower or "both" in market_lower or "les 2" in market_lower:
        if "yes" in sel_norm or "oui" in sel_norm or sel_norm == "gg":
            return "Les 2 marquent : Oui", "BTTS"
        if "no" in sel_norm or "non" in sel_norm or sel_norm == "ng":
            return "Les 2 marquent : Non", "BTTS"
        return None, None

    if market_lower in ("spread", "handicap", "asian handicap", "handicap asiatique"):
        try:
            lf = float(line) if line is not None else 0.0
        except (ValueError, TypeError):
            lf = 0.0
        if sel_norm and (sel_norm == home_norm or sel_norm in home_norm or home_norm in sel_norm):
            return f"{home} ({lf:+g})", "Handicap"
        if sel_norm and (sel_norm == away_norm or sel_norm in away_norm or away_norm in sel_norm):
            return f"{away} ({lf:+g})", "Handicap"
        return None, None

    if sel_norm:
        if sel_norm == home_norm or (sel_norm and sel_norm in home_norm):
            return f"Victoire {home}", "1X2"
        if sel_norm == away_norm or (sel_norm and sel_norm in away_norm):
            return f"Victoire {away}", "1X2"

    return None, None


# =========================================================
# SELECTIONS
# =========================================================
def find_bb_for_event(ev_fields, bb_data):
    home = ev_fields.get("home", "")
    away = ev_fields.get("away", "")
    event_id = ev_fields.get("event_id", "")
    key = f"{normalize_name(away)}|{normalize_name(home)}"
    if key in bb_data:
        return bb_data[key]
    hn, an = normalize_name(home), normalize_name(away)
    for ck, cid in AI_MATCH_CACHE.items():
        if cid == event_id and "|" in ck:
            c_away, c_home = ck.split("|", 1)
            for k, d in bb_data.items():
                try:
                    d_away, d_home = k.split("|", 1)
                except ValueError:
                    continue
                if d_away == c_away and d_home == c_home:
                    return d
    for k, d in bb_data.items():
        try:
            k_away, k_home = k.split("|", 1)
        except ValueError:
            continue
        if (hn in k_home or k_home in hn) and (an in k_away or k_away in an):
            return d
    return None


def build_selections(fixtures_norm, odds_by_id, bb_data, dc_verdicts=None):
    selections: list[Selection] = []
    seen = set()
    used_today = load_used_today()
    dc_verdicts = dc_verdicts or {}
    stats = {"total": 0, "skip_used": 0, "skip_prob": 0, "skip_label": 0,
             "skip_odd": 0, "skip_edge": 0, "dc_used": 0, "dc_disagree": 0,
             "no_odds": 0}

    for fx in fixtures_norm:
        try:
            home, away = fx.get("home", ""), fx.get("away", "")
            event_id, league = fx.get("event_id", ""), fx.get("league", "")
            kickoff = fx.get("kickoff", "")
            if not home or not away or not event_id:
                continue
            if not is_upcoming(kickoff):
                continue
            if event_id in used_today:
                stats["skip_used"] += 1
                continue

            bb_match = find_bb_for_event(fx, bb_data)
            if not bb_match:
                continue

            odds = odds_by_id.get(event_id, {})
            if not (odds.get("h2h") or odds.get("totals")):
                stats["no_odds"] += 1
                continue

            dc = dc_verdicts.get(event_id)

            for pick in bb_match["picks"]:
                stats["total"] += 1
                prob_pct = pick.get("modelProbabilityPct")
                if prob_pct is None:
                    continue
                prob = float(prob_pct) / 100.0

                market = pick.get("market", "")
                selection = pick.get("selection", "")
                line = pick.get("line")
                pick_label, market_label = pick_label_for(market, selection, home, away, line)
                if not pick_label:
                    stats["skip_label"] += 1
                    continue

                dc_prob = None
                if dc and market_label == "1X2":
                    if pick_label.startswith("Victoire "):
                        team = pick_label.replace("Victoire ", "")
                        dc_prob = dc["home"] if normalize_name(team) == normalize_name(home) else dc["away"]
                    elif pick_label == "Match nul":
                        dc_prob = dc["draw"]
                    if dc_prob is not None:
                        stats["dc_used"] += 1
                        ecart = abs(prob - dc_prob)
                        if ecart > 0.15:
                            stats["dc_disagree"] += 1
                        prob = (prob * 0.6) + (dc_prob * 0.4)

                if prob < MIN_PROB:
                    stats["skip_prob"] += 1
                    continue

                best_odd = None
                bookmaker = "The Odds API"

                if market_label == "1X2":
                    if pick_label.startswith("Victoire "):
                        team = pick_label.replace("Victoire ", "")
                        if normalize_name(team) == normalize_name(home):
                            best_odd = odds.get("h2h", {}).get("home")
                        else:
                            best_odd = odds.get("h2h", {}).get("away")
                    else:
                        best_odd = odds.get("h2h", {}).get("draw")
                elif market_label == "Over/Under":
                    try:
                        lf = float(line)
                    except (ValueError, TypeError):
                        lf = None
                    key = f"{'over' if 'Over' in pick_label else 'under'}_{lf}"
                    best_odd = odds.get("totals", {}).get(key)

                if best_odd is None or best_odd <= 1.01:
                    stats["skip_odd"] += 1
                    continue

                edge = prob * best_odd - 1.0
                if edge < MIN_EDGE:
                    stats["skip_edge"] += 1
                    continue

                dedup = f"{event_id}|{market_label}|{pick_label}"
                if dedup in seen:
                    continue
                seen.add(dedup)

                selections.append(Selection(
                    event_id=event_id, league=league, home=home, away=away,
                    kickoff=kickoff, market=market_label, pick_label=pick_label,
                    odds=round(best_odd, 2), model_prob=round(prob, 4),
                    edge=round(edge, 4), bookmaker=bookmaker,
                    confidence=pick.get("confidence", ""),
                    dc_prob=round(dc_prob, 4) if dc_prob is not None else None,
                ))
        except Exception as e:
            print(f"⚠️ build_selections crash: {e}")

    print(f"📊 STATS: total={stats['total']} skip_used={stats['skip_used']} "
          f"skip_prob={stats['skip_prob']} skip_label={stats['skip_label']} "
          f"skip_odd={stats['skip_odd']} skip_edge={stats['skip_edge']} "
          f"no_odds={stats['no_odds']} dc_used={stats['dc_used']} "
          f"dc_disagree={stats['dc_disagree']}")
    return selections


# =========================================================
# COUPONS
# =========================================================
def build_coupon_target(name, subtitle, emoji, pool, min_odds, max_odds,
                        max_legs, globally_used, min_prob):
    pool = [s for s in pool
            if s.odds and s.odds > 1.01
            and s.event_id not in globally_used
            and s.ai_verdict != "REJECT"
            and s.final_prob() >= min_prob]
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
        if next_odds > max_odds and legs:
            if combined_odds < min_odds:
                legs.append(s)
                used_events.add(s.event_id)
                used_leagues.add(s.league)
                used_markets.add(s.market)
                combined_odds = next_odds
                combined_prob *= s.final_prob()
            break
        legs.append(s)
        used_events.add(s.event_id)
        used_leagues.add(s.league)
        used_markets.add(s.market)
        combined_odds = next_odds
        combined_prob *= s.final_prob()
        if combined_odds >= min_odds:
            break

    if not legs:
        return None
    if combined_odds < min_odds * 0.65:
        return None
    if combined_odds > max_odds * 1.30:
        return None

    legs.sort(key=lambda s: s.odds)

    for s in legs:
        globally_used.add(s.event_id)

    return Coupon(name, subtitle, emoji, legs,
                  round(combined_odds, 2), round(combined_prob, 4),
                  round(combined_prob * combined_odds - 1, 4))


def build_all_coupons(selections):
    coupons = []
    used = set()

    pool_safe = [s for s in selections if s.final_prob() >= 0.65]
    c = build_coupon_target("Ticket Sécurisé", "Cote ~2 • Proba ≥ 65%",
                            "🔒", pool_safe, 1.70, 2.30, 4, used, min_prob=0.65)
    if c:
        coupons.append(c)

    pool_bal = [s for s in selections if s.final_prob() >= 0.58]
    c = build_coupon_target("Ticket Équilibré", "Cote 3-4.5 • Proba ≥ 58%",
                            "⚖️", pool_bal, 2.80, 4.80, 6, used, min_prob=0.58)
    if c:
        coupons.append(c)

    pool_val = [s for s in selections if s.final_prob() >= 0.50]
    c = build_coupon_target("Ticket Value", "Cote 9-10 • Proba ≥ 50%",
                            "💎", pool_val, 7.50, 12.00, 9, used, min_prob=0.50)
    if c:
        coupons.append(c)

    if not coupons and selections:
        top = sorted(selections, key=lambda s: s.final_prob(), reverse=True)[:3]
        if top:
            top.sort(key=lambda s: s.odds)
            combined_odds = 1.0
            combined_prob = 1.0
            for s in top:
                combined_odds *= s.odds
                combined_prob *= s.final_prob()
            coupons.append(Coupon(
                "Sélection du jour", "Top 3 meilleures probas",
                "⭐", top, round(combined_odds, 2), round(combined_prob, 4),
                round(combined_prob * combined_odds - 1, 4),
            ))

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


def format_selection_block(s: Selection, idx: int, show_ai=True):
    conf_tag = ""
    if (s.confidence or "").upper() == "STRONG":
        conf_tag = "  🔥 <b>STRONG</b>"
    elif (s.confidence or "").upper() == "LEAN":
        conf_tag = "  ⚡ LEAN"

    dt = parse_dt_safe(s.kickoff)
    day_tag = ""
    if dt:
        today = datetime.now(TZ).date()
        if dt.date() == today:
            day_tag = "📍 <b>Aujourd'hui</b> "
        elif dt.date() == today + timedelta(days=1):
            day_tag = "📅 <b>Demain</b> "

    prob_display = s.final_prob() * 100
    prob_source = "IA" if s.ai_prob else "croisée"

    lines = [
        f"<b>┌─ Sélection #{idx}</b>",
        f"<b>│ 🏟 {s.home}  vs  {s.away}</b>",
        f"│ {day_tag}🕐 <b>{kickoff_local(s.kickoff)}</b>  •  {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>  <i>({s.market})</i>{conf_tag}",
        f"│ 📊 Proba {prob_source} : <b>{prob_display:.1f}%</b>",
    ]
    if s.dc_prob is not None:
        lines.append(f"│ 🧮 Dixon-Coles : <b>{s.dc_prob*100:.1f}%</b>")

    lines.append(f"│ 💰 Cote : <b>{s.odds}</b>  <i>({s.bookmaker})</i>")
    lines.append(f"│ ⚡ Edge : <b>{s.edge*100:+.1f}%</b>  {edge_bar(s.edge)}")

    if show_ai and s.ai_verdict:
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>AVIS IA</b> : {ai_verdict_emoji(s.ai_verdict)} <b>{s.ai_verdict}</b>")
        if s.ai_critique:
            lines.append(f"│ 🔍 <b>Critique du bot</b> : <i>{s.ai_critique}</i>")
        if s.ai_analysis:
            lines.append(f"│ 💬 <i>{s.ai_analysis}</i>")
        if s.ai_advice:
            lines.append(f"│ 💡 <b>{s.ai_advice}</b>")
    lines.append(f"<b>└───────────────</b>")
    return "\n".join(lines)


def format_coupon(c: Coupon):
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
        f"<b>┃ 💎 EV estimée : {c.combined_ev*100:+.1f}%</b>",
        f"<b>┃ 🎯 {prob_level}</b>",
        f"<b>┗━━━━━━━━━━━━━━━━━┛</b>", "",
    ]
    for i, s in enumerate(c.legs, 1):
        lines.append(format_selection_block(s, i, show_ai=True))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_summary():
    a = sum(1 for s in STATE["selections"] if s.ai_verdict == "ACCEPT")
    c = sum(1 for s in STATE["selections"] if s.ai_verdict == "CAUTION")
    r = sum(1 for s in STATE["selections"] if s.ai_verdict == "REJECT")
    return (
        f"{format_header()}\n\n"
        f"<b>✅ ANALYSE PRÊTE</b>\n"
        f"🎯 Value bets : <b>{len(STATE['selections'])}</b>\n"
        f"   • ✅ {a} • ⚠️ {c} • ❌ {r}\n"
        f"🎫 Coupons : <b>{len(STATE['coupons'])}</b>\n"
        f"🔄 Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )


def format_coupons_ready():
    if not STATE["coupons"]:
        return (f"{format_header()}\n\n<b>📭 Aucun coupon disponible</b>\n"
                f"<i>Sélections trouvées : {len(STATE['selections'])}</i>")
    lignes = []
    for c in STATE["coupons"]:
        n = len(c.legs)
        lignes.append(
            f"{c.emoji} <b>{c.name}</b>\n"
            f"   • {n} sélection{'s' if n > 1 else ''}\n"
            f"   • Cote <b>{c.combined_odds}</b> • Proba <b>{c.combined_prob*100:.0f}%</b>"
        )
    return (
        f"{format_header()}\n\n"
        f"<b>🔔 COUPONS DU JOUR PRÊTS !</b>\n\n"
        f"<b>{len(STATE['coupons'])} coupons :</b>\n\n" + "\n\n".join(lignes)
    )


def format_bilan(tracker):
    coupons = tracker.get("coupons", [])
    return f"{format_header()}\n\n<b>📊 Bilan</b>\n🎫 Tickets : <b>{len(coupons)}</b>"


# =========================================================
# TELEGRAM
# =========================================================
async def safe_answer(message, text):
    try:
        await message.answer(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Answer crash: {e}")


async def safe_send(chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Send crash {chat_id}: {e}")


async def broadcast_after_scan():
    for chat_id in TARGETS:
        await safe_send(chat_id, format_coupons_ready())
        await asyncio.sleep(1)
        await safe_send(chat_id, format_summary())
        await asyncio.sleep(0.5)
        for c in STATE["coupons"]:
            await safe_send(chat_id, format_coupon(c))
            await asyncio.sleep(0.5)


@dp.message(Command("start"))
async def start_cmd(message):
    await safe_answer(message, f"{format_header()}\n\n<b>👋 Bienvenue !</b>\n\nTape /scan pour lancer l'analyse.")


@dp.message(Command("scan"))
async def scan_cmd(message):
    await safe_answer(message, "⏳ Analyse en cours...")
    await scan()
    await broadcast_after_scan()


@dp.message(Command("debug"))
async def debug_cmd(message):
    d = STATE["debug"]
    txt = (
        f"<b>🔍 DEBUG</b>\n\n"
        f"BetBetter matchs : <b>{d.get('bb_matches', 0)}</b>\n"
        f"Dixon-Coles prédictions : <b>{d.get('dc_predictions', 0)}</b>\n"
        f"Events Odds API : <b>{d.get('events_total', 0)}</b>\n"
        f"Events J/J+1 : <b>{d.get('events_upcoming', 0)}</b>\n"
        f"Matchés fuzzy : <b>{d.get('matched_fuzzy', 0)}</b>\n"
        f"Matchés IA : <b>{d.get('matched_ai', 0)}</b>\n"
        f"Cotes extraites : <b>{d.get('odds_fetched', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
        f"✅ ACCEPT : <b>{d.get('ai_accept', 0)}</b>\n"
        f"⚠️ CAUTION : <b>{d.get('ai_caution', 0)}</b>\n"
        f"❌ REJECT : <b>{d.get('ai_reject', 0)}</b>\n"
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


@dp.message(F.text == BTN_VAL)
async def btn_val(message):
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
        bb = fetch_betbetter_picks()
        print(f"🧠 BetBetter matchs: {len(bb)}")

        # 1. Récupérer tous les events avec cotes (10 requêtes)
        events_raw = await fetch_all_events_with_odds()
        print(f"📦 Events The Odds API: {len(events_raw)}")

        # 2. Normaliser + filtrer J/J+1
        events_norm = []
        odds_by_id = {}
        for ev in events_raw:
            fields = extract_event_fields(ev)
            if not fields["event_id"] or not fields["home"] or not fields["away"]:
                continue
            if not is_upcoming(fields["kickoff"]):
                continue
            events_norm.append(fields)
            odds_by_id[fields["event_id"]] = extract_odds_from_event(ev)
        print(f"🎯 Events J/J+1: {len(events_norm)}")

        # 3. Dixon-Coles
        dc_verdicts = {}
        if DC_MODEL:
            for fx in events_norm:
                pred = DC_MODEL.predict(fx["home"], fx["away"])
                if pred:
                    dc_verdicts[fx["event_id"]] = pred
            print(f"🧮 Dixon-Coles : {len(dc_verdicts)} prédictions")

        # 4. Matching fuzzy
        matched_fuzzy = sum(1 for fx in events_norm if find_bb_for_event(fx, bb))

        # 5. Matching IA
        matched_ai = 0
        if GEMINI_API_KEY and events_norm:
            matched_ai = await ai_rematch_all(bb, events_norm)
            print(f"🤖 IA matchs ajoutés: {matched_ai}")

        # 6. Construire les sélections
        selections = build_selections(events_norm, odds_by_id, bb, dc_verdicts)
        print(f"🎯 Sélections: {len(selections)}")

        if GEMINI_API_KEY and selections:
            await ai_review_all(selections)

        selections.sort(key=lambda s: s.score(), reverse=True)
        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)

        used_picks = [s for c in STATE["coupons"] for s in c.legs]
        mark_picks_used(used_picks)

        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")

        STATE["debug"] = {
            "bb_matches": len(bb),
            "dc_predictions": len(dc_verdicts),
            "events_total": len(events_raw),
            "events_upcoming": len(events_norm),
            "matched_fuzzy": matched_fuzzy,
            "matched_ai": matched_ai,
            "odds_fetched": len(odds_by_id),
            "selections": len(selections),
            "ai_accept": sum(1 for s in selections if s.ai_verdict == "ACCEPT"),
            "ai_caution": sum(1 for s in selections if s.ai_verdict == "CAUTION"),
            "ai_reject": sum(1 for s in selections if s.ai_verdict == "REJECT"),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Events The Odds API : {len(events_raw)}")
        print(f"Events J/J+1        : {len(events_norm)}")
        print(f"Dixon-Coles         : {len(dc_verdicts)}")
        print(f"Matchés fuzzy       : {matched_fuzzy}")
        print(f"Matchés IA          : {matched_ai}")
        print(f"Cotes extraites     : {len(odds_by_id)}")
        print(f"Sélections          : {len(selections)}")
        print(f"Coupons             : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} legs, cote {c.combined_odds}")
        print("────────────────────────")


# =========================================================
# BROADCAST
# =========================================================
async def daily_broadcast():
    await scan()
    record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    await broadcast_after_scan()


async def startup_scan_and_notify():
    try:
        await asyncio.sleep(5)
        if NOTIFY_ON_STARTUP:
            for chat_id in TARGETS:
                await safe_send(chat_id, f"{format_header()}\n\n⏳ <b>Bot démarré, analyse en cours...</b>")
        await scan()
        record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
        await broadcast_after_scan()
    except Exception as e:
        print(f"⚠️ startup_scan crash: {e}")


async def bootstrap():
    STATE["tracker"] = load_tracker()
    await init_dixon_coles()
    STATE["ready"] = True
    print("✅ Bot prêt (The Odds API).")


# =========================================================
# APP
# =========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    await bootstrap()

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_broadcast,
                      CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ),
                      id="daily_broadcast", replace_existing=True,
                      max_instances=1, coalesce=True)
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
    return {
        "status": "ok", "ready": STATE["ready"],
        "selections": len(STATE["selections"]),
        "coupons": [c.name for c in STATE["coupons"]],
        "last_scan": STATE["last_scan"],
        "dc_trained": DC_MODEL is not None,
    }


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
