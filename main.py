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


# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
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

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.03"))
MIN_PROB = float(os.getenv("MIN_PROB", "0.60"))
MAX_SIMPLE_SEND = int(os.getenv("MAX_SIMPLE_SEND", "10"))
AI_REVIEW_ENABLED = os.getenv("AI_REVIEW_ENABLED", "true").lower() == "true"
MAX_FIXTURES_FOR_ODDS = int(os.getenv("MAX_FIXTURES_FOR_ODDS", "25"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not FOOTBALL_API_KEY:
    raise RuntimeError("FOOTBALL_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
FOOTBALL_API_BASE = "https://api.5dollarfootballapi.com/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"

BETBETTER_LEAGUES = [
    "soccer/epl",
    "soccer/la-liga",
    "soccer/serie-a",
    "soccer/bundesliga",
    "soccer/ligue-1",
]

STRUCTURE_LOGGED = {"fixtures": False, "odds": False}


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
    fair_odds: Optional[float] = None
    ai_verdict: str = ""
    ai_analysis: str = ""
    ai_advice: str = ""

    def score(self) -> float:
        bonus = 0.05 if self.confidence.upper() == "STRONG" else 0.0
        ai_bonus = 0.10 if self.ai_verdict == "ACCEPT" else (-0.15 if self.ai_verdict == "REJECT" else 0.0)
        return self.edge + bonus + ai_bonus + (self.model_prob - 0.5) * 0.1


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
BTN_AGG = "🔥 Agressif"
BTN_VAL = "💎 Value"
BTN_SIMPLE = "📋 Simples"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_AGG), KeyboardButton(text=BTN_VAL)],
        [KeyboardButton(text=BTN_SIMPLE), KeyboardButton(text=BTN_BILAN)],
        [KeyboardButton(text=BTN_SCAN)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choisis un ticket…",
)

AI_MATCH_CACHE: dict = {}
AI_REVIEW_CACHE: dict = {}

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
def today_iso():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def today_pretty():
    months = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre",
        11: "novembre", 12: "décembre",
    }
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
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_dt_safe(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(int(s), tz=ZoneInfo("UTC")).astimezone(TZ)
        except Exception:
            return None
    s = str(s).strip()
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    s = s.replace("Z", "+00:00")
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
    """Accepte aujourd'hui (pas commencé) et demain."""
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


def auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {FOOTBALL_API_KEY}",
        "Accept": "application/json",
    }


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


def record_coupons(coupons: list[Coupon], date_str: str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing:
            continue
        tracker["coupons"].append({
            "id": cid,
            "date": date_str,
            "name": c.name,
            "combined_odds": c.combined_odds,
            "status": "pending",
        })
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)


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
                        "away": away,
                        "home": home,
                        "kickoff": p.get("gameTimeUtc", ""),
                        "league": league,
                        "picks": [],
                    }
                result[key]["picks"].append(p)
        except Exception as e:
            print(f"⚠️ betbetter {league}: {e}")
    return result


# =========================================================
# 5DOLLARFOOTBALLAPI — FIXTURES
# =========================================================
async def fetch_fixtures(client: httpx.AsyncClient) -> list[dict]:
    """Récupère les fixtures sur 48h en 2 fenêtres de 24h."""
    now = datetime.now(TZ)

    start1 = int(now.timestamp())
    end1 = int((now + timedelta(hours=24)).timestamp())
    start2 = end1
    end2 = int((now + timedelta(hours=48)).timestamp())

    windows = [(start1, end1), (start2, end2)]
    all_fixtures: list[dict] = []
    seen_ids = set()

    for start, end in windows:
        url = f"{FOOTBALL_API_BASE}/fixtures"
        params = {"start_time": start, "end_time": end}
        try:
            r = await client.get(url, params=params, headers=auth_headers(), timeout=30.0)
            print(f"🌍 /fixtures [{start}..{end}] -> {r.status_code}")

            if r.status_code >= 400:
                print(f"⚠️ Body: {r.text[:400]}")
                continue

            data = r.json()

            if not STRUCTURE_LOGGED["fixtures"]:
                print("=" * 70)
                print("🔬 STRUCTURE BRUTE DES FIXTURES")
                print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
                print("=" * 70)
                STRUCTURE_LOGGED["fixtures"] = True

            fixtures = []
            if isinstance(data, list):
                fixtures = data
            elif isinstance(data, dict):
                for key in ("data", "fixtures", "response", "matches", "results", "events"):
                    if isinstance(data.get(key), list):
                        fixtures = data[key]
                        break

            print(f"📦 Fenêtre [{start}..{end}] : {len(fixtures)} fixtures")

            for fx in fixtures:
                if not isinstance(fx, dict):
                    continue
                fid = str(fx.get("id") or fx.get("fixture_id") or "")
                if fid and fid in seen_ids:
                    continue
                if fid:
                    seen_ids.add(fid)
                all_fixtures.append(fx)

        except Exception as e:
            print(f"⚠️ /fixtures crash [{start}..{end}]: {e}")

    print(f"📦 Total fixtures uniques: {len(all_fixtures)}")

    # Filtre J/J+1 via kickoff_utc ou kickoff_ts
    upcoming = []
    for fx in all_fixtures:
        dt_val = (
            fx.get("kickoff_utc")
            or fx.get("kickoff_ts")
            or fx.get("date")
            or fx.get("kickoff")
            or ""
        )
        if is_upcoming(dt_val):
            upcoming.append(fx)

    print(f"📅 Fixtures J/J+1: {len(upcoming)}")
    return upcoming


def extract_fixture_fields(fx: dict) -> dict:
    # Structure 5DFootballAPI : {"teams": {"home": {"name": "..."}, "away": {...}}}
    teams = fx.get("teams") or {}
    home = ""
    away = ""
    if isinstance(teams, dict):
        h = teams.get("home")
        a = teams.get("away")
        if isinstance(h, dict):
            home = h.get("name") or ""
        elif isinstance(h, str):
            home = h
        if isinstance(a, dict):
            away = a.get("name") or ""
        elif isinstance(a, str):
            away = a

    # Fallback pour d'autres formats possibles
    if not home:
        for k in ("home_team", "homeTeam", "home", "team_home"):
            v = fx.get(k)
            if isinstance(v, dict):
                home = v.get("name") or ""
                if home: break
            elif isinstance(v, str) and v:
                home = v
                break

    if not away:
        for k in ("away_team", "awayTeam", "away", "team_away"):
            v = fx.get(k)
            if isinstance(v, dict):
                away = v.get("name") or ""
                if away: break
            elif isinstance(v, str) and v:
                away = v
                break

    # Ligue
    league = ""
    lg = fx.get("league")
    if isinstance(lg, dict):
        league = lg.get("name") or ""
    elif isinstance(lg, str):
        league = lg

    # Kickoff : kickoff_utc en priorité, sinon kickoff_ts (Unix)
    kickoff = (
        fx.get("kickoff_utc")
        or fx.get("kickoff_ts")
        or fx.get("date")
        or fx.get("kickoff")
        or ""
    )

    event_id = str(fx.get("id") or "")

    return {
        "event_id": event_id,
        "home": home,
        "away": away,
        "league": league,
        "kickoff": kickoff,
        "raw": fx,
    }


# =========================================================
# 5DOLLARFOOTBALLAPI — ODDS
# =========================================================
async def fetch_odds(client: httpx.AsyncClient, fixture_id: str) -> dict:
    url = f"{FOOTBALL_API_BASE}/fixtures/{fixture_id}/odds"
    try:
        r = await client.get(url, headers=auth_headers(), timeout=20.0)
        if r.status_code >= 400:
            if r.status_code != 404:
                print(f"⚠️ Odds {fixture_id} -> {r.status_code}: {r.text[:150]}")
            return {}

        data = r.json()

        if not STRUCTURE_LOGGED["odds"]:
            print("=" * 70)
            print("🔬 STRUCTURE BRUTE DES COTES")
            print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
            print("=" * 70)
            STRUCTURE_LOGGED["odds"] = True

        return data
    except Exception as e:
        print(f"⚠️ Odds crash {fixture_id}: {e}")
        return {}


def parse_odds(data) -> dict:
    result = {"h2h": {}, "totals": {}, "btts": {}}
    if not isinstance(data, (dict, list)):
        return result

    def walk(obj):
        if isinstance(obj, dict):
            for k in ("home", "draw", "away", "1", "X", "2", "home_win", "draw_win", "away_win"):
                v = obj.get(k)
                if isinstance(v, (int, float)) and v > 1.01:
                    key = {"1": "home", "X": "draw", "2": "away",
                           "home_win": "home", "draw_win": "draw", "away_win": "away"}.get(k, k)
                    if key in ("home", "draw", "away"):
                        result["h2h"][key] = max(result["h2h"].get(key, 0), float(v))
                elif isinstance(v, dict):
                    p = v.get("price") or v.get("odd") or v.get("cote") or v.get("value") or v.get("decimal")
                    if isinstance(p, (int, float)) and p > 1.01:
                        key = {"1": "home", "X": "draw", "2": "away",
                               "home_win": "home", "draw_win": "draw", "away_win": "away"}.get(k, k)
                        if key in ("home", "draw", "away"):
                            result["h2h"][key] = max(result["h2h"].get(key, 0), float(p))

            for k, v in obj.items():
                k_low = str(k).lower()
                m = re.match(r"(over|under)_?([\d.]+)", k_low)
                if m:
                    if isinstance(v, (int, float)) and v > 1.01:
                        result["totals"][f"{m.group(1)}_{m.group(2)}"] = float(v)
                    elif isinstance(v, dict):
                        p = v.get("price") or v.get("odd") or v.get("value")
                        if isinstance(p, (int, float)) and p > 1.01:
                            result["totals"][f"{m.group(1)}_{m.group(2)}"] = float(p)

                if k_low in ("btts_yes", "yes", "both_teams_yes", "gg", "btts.yes"):
                    if isinstance(v, (int, float)) and v > 1.01:
                        result["btts"]["yes"] = float(v)
                    elif isinstance(v, dict):
                        p = v.get("price") or v.get("odd")
                        if isinstance(p, (int, float)) and p > 1.01:
                            result["btts"]["yes"] = float(p)
                if k_low in ("btts_no", "no", "both_teams_no", "ng", "btts.no"):
                    if isinstance(v, (int, float)) and v > 1.01:
                        result["btts"]["no"] = float(v)
                    elif isinstance(v, dict):
                        p = v.get("price") or v.get("odd")
                        if isinstance(p, (int, float)) and p > 1.01:
                            result["btts"]["no"] = float(p)

            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return result


# =========================================================
# GEMINI — MATCHING IA
# =========================================================
async def ai_match_teams(client: httpx.AsyncClient, bb_home: str, bb_away: str,
                          candidates: list[tuple[str, str, str]]) -> Optional[str]:
    if not GEMINI_API_KEY or not candidates:
        return None
    cache_key = f"{normalize_name(bb_home)}|{normalize_name(bb_away)}"
    if cache_key in AI_MATCH_CACHE:
        return AI_MATCH_CACHE[cache_key]

    candidates = candidates[:20]
    lignes = "\n".join(f"{i+1}. {h} vs {a}" for i, (_, h, a) in enumerate(candidates))

    prompt = (
        f"Match modèle : \"{bb_home} vs {bb_away}\".\n"
        f"Trouve le MÊME match dans la liste :\n\n"
        f"{lignes}\n\n"
        f"Réponds UNIQUEMENT par le numéro (1-{len(candidates)}) ou 0."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 20},
    }

    try:
        r = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=15.0)
        if r.status_code >= 400:
            AI_MATCH_CACHE[cache_key] = None
            return None
        data = r.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            AI_MATCH_CACHE[cache_key] = None
            return None
        match = re.search(r"\d+", text)
        if not match:
            AI_MATCH_CACHE[cache_key] = None
            return None
        num = int(match.group())
        if 1 <= num <= len(candidates):
            matched_id = candidates[num - 1][0]
            AI_MATCH_CACHE[cache_key] = matched_id
            return matched_id
        AI_MATCH_CACHE[cache_key] = None
        return None
    except Exception:
        AI_MATCH_CACHE[cache_key] = None
        return None


async def ai_rematch_all(bb_data: dict, fixtures_norm: list[dict]) -> int:
    if not GEMINI_API_KEY:
        return 0
    candidates = [(f["event_id"], f["home"], f["away"]) for f in fixtures_norm if f["event_id"]]
    if not candidates:
        return 0

    already_matched = set()
    for key in bb_data.keys():
        try:
            k_away, k_home = key.split("|", 1)
        except ValueError:
            continue
        for eid, h, a in candidates:
            hn, an = normalize_name(h), normalize_name(a)
            if (hn in k_home or k_home in hn) and (an in k_away or k_away in an):
                already_matched.add(eid)
                break

    unmatched = [c for c in candidates if c[0] not in already_matched]
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

            matched_id = await ai_match_teams(client, bb_home, bb_away, unmatched)
            if matched_id:
                count += 1
                print(f"  🤖 Match IA : {bb_home} vs {bb_away} → {matched_id}")
                unmatched = [c for c in unmatched if c[0] != matched_id]

            await asyncio.sleep(2.0)

    return count


# =========================================================
# GEMINI — REVUE ANALYTIQUE
# =========================================================
async def ai_review_selection(client: httpx.AsyncClient, sel: Selection) -> None:
    if not GEMINI_API_KEY:
        return

    cache_key = f"rev|{sel.event_id}|{sel.market}|{sel.pick_label}"
    if cache_key in AI_REVIEW_CACHE:
        cached = AI_REVIEW_CACHE[cache_key]
        sel.ai_verdict = cached.get("verdict", "")
        sel.ai_analysis = cached.get("analysis", "")
        sel.ai_advice = cached.get("advice", "")
        return

    prompt = f"""Tu es un analyste football honnête. Tu as le DROIT de refuser un pari.

Match : {sel.home} vs {sel.away}
Compétition : {sel.league}
Heure : {kickoff_local(sel.kickoff)}
Marché : {sel.market}
Pari : {sel.pick_label}
Cote : {sel.odds}
Proba modèle : {sel.model_prob*100:.1f}%
Edge : {sel.edge*100:+.1f}%

Réponds EXACTEMENT :

VERDICT: ACCEPT
ANALYSE: [3 phrases max]
CONSEIL: [1 phrase]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
    }

    try:
        r = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=30.0)
        if r.status_code >= 400:
            return
        data = r.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return

        verdict, analysis, advice = "", "", ""
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
            elif upper.startswith("ANALYSE:"):
                analysis = line.split(":", 1)[1].strip()
            elif upper.startswith("CONSEIL:"):
                advice = line.split(":", 1)[1].strip()

        sel.ai_verdict = verdict
        sel.ai_analysis = analysis
        sel.ai_advice = advice
        AI_REVIEW_CACHE[cache_key] = {"verdict": verdict, "analysis": analysis, "advice": advice}
    except Exception as e:
        print(f"⚠️ Gemini review crash: {e}")


async def ai_review_all(selections: list[Selection]) -> None:
    if not GEMINI_API_KEY or not AI_REVIEW_ENABLED or not selections:
        return
    print(f"🧠 Revue IA de {len(selections)} sélections...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, s in enumerate(selections):
            await ai_review_selection(client, s)
            if (i + 1) % 5 == 0:
                print(f"  ... {i+1}/{len(selections)}")
            await asyncio.sleep(2.0)

    accepted = sum(1 for s in selections if s.ai_verdict == "ACCEPT")
    caution = sum(1 for s in selections if s.ai_verdict == "CAUTION")
    rejected = sum(1 for s in selections if s.ai_verdict == "REJECT")
    print(f"🧠 IA : {accepted} ACCEPT, {caution} CAUTION, {rejected} REJECT")


# =========================================================
# FIND BB FOR FIXTURE
# =========================================================
def find_bb_for_fixture(fx: dict, bb_data: dict) -> Optional[dict]:
    home = fx.get("home", "")
    away = fx.get("away", "")
    event_id = fx.get("event_id", "")

    key = f"{normalize_name(away)}|{normalize_name(home)}"
    if key in bb_data:
        return bb_data[key]

    hn, an = normalize_name(home), normalize_name(away)

    for cache_key, cached_id in AI_MATCH_CACHE.items():
        if cached_id == event_id and "|" in cache_key:
            c_away, c_home = cache_key.split("|", 1)
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


# =========================================================
# BUILD SELECTIONS
# =========================================================
def build_selections(fixtures_norm: list[dict], odds_map: dict, bb_data: dict) -> list[Selection]:
    selections: list[Selection] = []
    seen_keys = set()

    for fx in fixtures_norm:
        try:
            home = fx.get("home", "")
            away = fx.get("away", "")
            event_id = fx.get("event_id", "")
            league = fx.get("league", "")
            kickoff = fx.get("kickoff", "")

            if not home or not away or not event_id:
                continue
            if not is_upcoming(kickoff):
                continue

            bb_match = find_bb_for_fixture(fx, bb_data)
            if not bb_match:
                continue

            odds = odds_map.get(event_id, {})
            if not odds:
                continue

            for pick in bb_match["picks"]:
                prob_pct = pick.get("modelProbabilityPct")
                if prob_pct is None:
                    continue
                prob = float(prob_pct) / 100.0
                if prob < MIN_PROB:
                    continue

                market = pick.get("market", "")
                selection = pick.get("selection", "")
                line = pick.get("line")

                best_odd = None
                market_label = ""
                pick_label = ""

                if market == "Moneyline":
                    if normalize_name(selection) == normalize_name(home):
                        best_odd = odds.get("h2h", {}).get("home")
                        pick_label = f"Victoire {home}"
                    elif normalize_name(selection) == normalize_name(away):
                        best_odd = odds.get("h2h", {}).get("away")
                        pick_label = f"Victoire {away}"
                    else:
                        best_odd = odds.get("h2h", {}).get("draw")
                        pick_label = "Match nul"
                    market_label = "1X2"

                elif market == "Total":
                    is_over = "over" in selection.lower() or "plus" in selection.lower()
                    is_under = "under" in selection.lower() or "moins" in selection.lower()
                    if not (is_over or is_under):
                        continue
                    try:
                        line_f = float(line) if line is not None else None
                    except (ValueError, TypeError):
                        line_f = None
                    if line_f not in (0.5, 1.5, 2.5):
                        continue
                    key = f"{'over' if is_over else 'under'}_{line_f}"
                    best_odd = odds.get("totals", {}).get(key)
                    pick_label = f"{'Over' if is_over else 'Under'} {line_f} buts"
                    market_label = "Over/Under"

                elif market == "BTTS" or "both teams" in market.lower():
                    if "yes" in selection.lower():
                        best_odd = odds.get("btts", {}).get("yes")
                        pick_label = "Les 2 marquent : Oui"
                    else:
                        best_odd = odds.get("btts", {}).get("no")
                        pick_label = "Les 2 marquent : Non"
                    market_label = "BTTS"

                if best_odd is None or best_odd <= 1.01:
                    continue

                edge = prob * best_odd - 1.0
                if edge < MIN_EDGE:
                    continue

                dedup = f"{event_id}|{market_label}|{pick_label}"
                if dedup in seen_keys:
                    continue
                seen_keys.add(dedup)

                selections.append(Selection(
                    event_id=event_id,
                    league=league,
                    home=home,
                    away=away,
                    kickoff=kickoff,
                    market=market_label,
                    pick_label=pick_label,
                    odds=round(best_odd, 2),
                    model_prob=round(prob, 4),
                    edge=round(edge, 4),
                    bookmaker="5DFootballAPI",
                    confidence=pick.get("confidence", ""),
                ))
        except Exception as e:
            print(f"⚠️ build_selections crash: {e}")

    return selections


# =========================================================
# COUPON BUILDER
# =========================================================
def build_coupon(name, subtitle, emoji, pool, min_odds, max_odds, max_legs, globally_used):
    pool = [s for s in pool if s.odds and s.odds > 1.01 and s.event_id not in globally_used and s.ai_verdict != "REJECT"]
    if not pool:
        return None
    pool.sort(key=lambda s: s.score(), reverse=True)

    legs = []
    used_events = set()
    used_leagues = set()
    combined_odds = 1.0
    combined_prob = 1.0

    for s in pool:
        if len(legs) >= max_legs:
            break
        if s.event_id in used_events or s.league in used_leagues:
            continue
        next_odds = combined_odds * s.odds
        if next_odds > max_odds and legs:
            continue
        legs.append(s)
        used_events.add(s.event_id)
        used_leagues.add(s.league)
        combined_odds = next_odds
        combined_prob *= s.model_prob

    if not legs or combined_odds < min_odds * 0.85:
        return None

    for s in legs:
        globally_used.add(s.event_id)

    return Coupon(name, subtitle, emoji, legs, round(combined_odds, 2), round(combined_prob, 4), round(combined_prob * combined_odds - 1, 4))


def build_all_coupons(selections):
    coupons = []
    used = set()

    pools = [
        ("Ultra Safe", "1 sélection à très forte probabilité", "🛡", lambda s: s.model_prob >= 0.70 and s.odds <= 1.60, 1.20, 1.60, 1),
        ("Safe", "2 sélections solides, cote ~2", "✅", lambda s: s.model_prob >= 0.60 and s.odds <= 1.60, 1.70, 2.50, 2),
        ("Équilibré", "3 sélections, cote 3-5", "⚖️", lambda s: s.model_prob >= 0.55, 2.5, 5.0, 3),
    ]
    for name, sub, emo, cond, min_o, max_o, legs in pools:
        c = build_coupon(name, sub, emo, [s for s in selections if cond(s)], min_o, max_o, legs, used)
        if c:
            coupons.append(c)

    val = sorted([s for s in selections if s.edge >= 0.04], key=lambda s: s.edge, reverse=True)
    c = build_coupon("Value", "Meilleurs edges ≥4%", "💎", val, 1.50, 3.00, 2, used)
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


def format_selection_block(s, idx, show_ai=True):
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

    lines = [
        f"<b>┌─ Sélection #{idx}</b>",
        f"<b>│ 🏟 {s.home}  vs  {s.away}</b>",
        f"│ {day_tag}🕐 <b>{kickoff_local(s.kickoff)}</b>  •  {s.league}",
        f"│ 🎯 Pari : <b>{s.pick_label}</b>",
        f"│ 🏷 Marché : <i>{s.market}</i>{conf_tag}",
        f"│ 📈 Proba modèle : <b>{s.model_prob*100:.1f}%</b>",
        f"│ 💰 Cote : <b>{s.odds}</b>",
        f"│ ⚡ Edge : <b>{s.edge*100:+.1f}%</b>  {edge_bar(s.edge)}",
    ]
    if show_ai and s.ai_verdict:
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>AVIS IA</b> : {ai_verdict_emoji(s.ai_verdict)} <b>{s.ai_verdict}</b>")
        if s.ai_analysis:
            lines.append(f"│ 💬 <i>{s.ai_analysis}</i>")
        if s.ai_advice:
            lines.append(f"│ 💡 <b>{s.ai_advice}</b>")
    lines.append(f"<b>└───────────────</b>")
    return "\n".join(lines)


def format_simples(selections, limit=10):
    if not selections:
        return f"{format_header()}\n\n<b>❌ Aucune value détectée</b>"
    lines = [format_header(), "", f"<b>📋 TOP VALUE BETS</b>", f"<i>{len(selections)} sélections</i>", ""]
    for i, s in enumerate(selections[:limit], 1):
        lines.append(format_selection_block(s, i))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_ia_reviews(selections):
    if not selections:
        return f"{format_header()}\n\n<b>🧠 Aucune analyse IA</b>"
    accepted = [s for s in selections if s.ai_verdict == "ACCEPT"]
    caution = [s for s in selections if s.ai_verdict == "CAUTION"]
    rejected = [s for s in selections if s.ai_verdict == "REJECT"]

    lines = [format_header(), "", f"<b>🧠 ANALYSE IA</b>", f"✅ {len(accepted)} • ⚠️ {len(caution)} • ❌ {len(rejected)}", ""]

    if rejected:
        lines.append("<b>❌ REJETÉS</b>")
        for i, s in enumerate(rejected, 1):
            lines.append(f"<b>{i}. {s.home} vs {s.away}</b>")
            lines.append(f"   🎯 {s.pick_label} @ {s.odds}")
            if s.ai_analysis:
                lines.append(f"   💬 <i>{s.ai_analysis}</i>")
            lines.append("")

    if caution:
        lines.append("<b>⚠️ PRUDENCE</b>")
        for i, s in enumerate(caution, 1):
            lines.append(f"<b>{i}. {s.home} vs {s.away}</b> — 🎯 {s.pick_label} @ {s.odds}")
            if s.ai_analysis:
                lines.append(f"   💬 <i>{s.ai_analysis}</i>")
            lines.append("")

    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_coupon(c):
    prob_level = "🟢 Faible risque" if c.combined_prob >= 0.40 else "🟡 Risque modéré" if c.combined_prob >= 0.20 else "🔴 Risque élevé"
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
        return f"{format_header()}\n\n<b>📭 Aucun coupon disponible</b>"
    lignes = []
    for c in STATE["coupons"]:
        n = len(c.legs)
        lignes.append(f"{c.emoji} <b>{c.name}</b>\n   • {n} sélection{'s' if n > 1 else ''}\n   • Cote <b>{c.combined_odds}</b> • Proba <b>{c.combined_prob*100:.0f}%</b>")
    return (
        f"{format_header()}\n\n"
        f"<b>🔔 COUPONS DU JOUR PRÊTS !</b>\n\n"
        f"<b>{len(STATE['coupons'])} coupons :</b>\n\n"
        + "\n\n".join(lignes)
    )


def format_bilan(tracker):
    coupons = tracker.get("coupons", [])
    return f"{format_header()}\n\n<b>📊 Bilan</b>\n🎫 Tickets : <b>{len(coupons)}</b>"


# =========================================================
# TELEGRAM HANDLERS
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


@dp.message(Command("start"))
async def start_cmd(message):
    await safe_answer(message, f"{format_header()}\n\n<b>👋 Bienvenue !</b>\n\nTape /scan pour lancer l'analyse.")


@dp.message(Command("scan"))
async def scan_cmd(message):
    await safe_answer(message, "⏳ Analyse en cours...")
    await scan()
    await safe_answer(message, format_coupons_ready())
    await asyncio.sleep(0.5)
    await safe_answer(message, format_summary())


@dp.message(Command("debug"))
async def debug_cmd(message):
    d = STATE["debug"]
    txt = (
        f"<b>🔍 DEBUG</b>\n\n"
        f"BetBetter matchs : <b>{d.get('bb_matches', 0)}</b>\n"
        f"Fixtures brutes : <b>{d.get('fixtures_raw', 0)}</b>\n"
        f"Fixtures J/J+1 : <b>{d.get('fixtures_upcoming', 0)}</b>\n"
        f"Appariés fuzzy : <b>{d.get('matched_fuzzy', 0)}</b>\n"
        f"Appariés IA : <b>{d.get('matched_ai', 0)}</b>\n"
        f"Cotes récupérées : <b>{d.get('odds_fetched', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
        f"✅ ACCEPT : <b>{d.get('ai_accept', 0)}</b>\n"
        f"⚠️ CAUTION : <b>{d.get('ai_caution', 0)}</b>\n"
        f"❌ REJECT : <b>{d.get('ai_reject', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>"
    )
    await safe_answer(message, txt)


@dp.message(F.text == BTN_SIMPLE)
async def btn_simple(message):
    await safe_answer(message, format_simples(STATE["selections"], MAX_SIMPLE_SEND))


async def send_coupon(message, name):
    c = next((x for x in STATE["coupons"] if x.name == name), None)
    if c:
        await safe_answer(message, format_coupon(c))
    else:
        await safe_answer(message, "Aucun coupon pour ce profil.")


@dp.message(F.text == BTN_SAFE)
async def btn_safe(message):
    await send_coupon(message, "Safe")

@dp.message(F.text == BTN_BAL)
async def btn_bal(message):
    await send_coupon(message, "Équilibré")

@dp.message(F.text == BTN_AGG)
async def btn_agg(message):
    await send_coupon(message, "Ultra Safe")

@dp.message(F.text == BTN_VAL)
async def btn_val(message):
    await send_coupon(message, "Value")

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

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            fixtures_raw = await fetch_fixtures(client)
            fixtures_norm = [extract_fixture_fields(fx) for fx in fixtures_raw]
            fixtures_norm = [f for f in fixtures_norm if f["event_id"] and f["home"] and f["away"]]
            print(f"🎯 Fixtures normalisées: {len(fixtures_norm)}")

            matched_fuzzy = 0
            for fx in fixtures_norm:
                if find_bb_for_fixture(fx, bb):
                    matched_fuzzy += 1

            matched_ai = 0
            if GEMINI_API_KEY and fixtures_norm:
                print("🤖 Matching Gemini...")
                matched_ai = await ai_rematch_all(bb, fixtures_norm)
                print(f"🤖 IA matchs ajoutés: {matched_ai}")

            to_odds = [fx for fx in fixtures_norm if find_bb_for_fixture(fx, bb)][:MAX_FIXTURES_FOR_ODDS]
            print(f"💰 Récupération cotes pour {len(to_odds)} matchs...")

            odds_map = {}
            for fx in to_odds:
                raw = await fetch_odds(client, fx["event_id"])
                if raw:
                    parsed = parse_odds(raw)
                    if parsed.get("h2h") or parsed.get("totals") or parsed.get("btts"):
                        odds_map[fx["event_id"]] = parsed
                await asyncio.sleep(0.2)

            print(f"💰 Cotes parsées: {len(odds_map)}")

        selections = build_selections(fixtures_norm, odds_map, bb)
        print(f"🎯 Sélections: {len(selections)}")

        if AI_REVIEW_ENABLED and GEMINI_API_KEY and selections:
            await ai_review_all(selections)

        selections.sort(key=lambda s: s.score(), reverse=True)
        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")

        STATE["debug"] = {
            "bb_matches": len(bb),
            "fixtures_raw": len(fixtures_raw),
            "fixtures_upcoming": len(fixtures_norm),
            "matched_fuzzy": matched_fuzzy,
            "matched_ai": matched_ai,
            "odds_fetched": len(odds_map),
            "selections": len(selections),
            "ai_accept": sum(1 for s in selections if s.ai_verdict == "ACCEPT"),
            "ai_caution": sum(1 for s in selections if s.ai_verdict == "CAUTION"),
            "ai_reject": sum(1 for s in selections if s.ai_verdict == "REJECT"),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Fixtures brutes    : {len(fixtures_raw)}")
        print(f"Fixtures J/J+1     : {len(fixtures_norm)}")
        print(f"Matchés fuzzy      : {matched_fuzzy}")
        print(f"Matchés IA         : {matched_ai}")
        print(f"Cotes récupérées   : {len(odds_map)}")
        print(f"Sélections         : {len(selections)}")
        print(f"Coupons            : {len(STATE['coupons'])}")
        print("────────────────────────")


# =========================================================
# BROADCAST
# =========================================================
async def daily_broadcast():
    await scan()
    date_str = datetime.now(TZ).strftime("%Y-%m-%d")
    record_coupons(STATE["coupons"], date_str)

    for chat_id in TARGETS:
        await safe_send(chat_id, format_coupons_ready())
        await asyncio.sleep(1)
        await safe_send(chat_id, format_summary())
        await asyncio.sleep(0.5)
        if STATE["selections"]:
            await safe_send(chat_id, format_ia_reviews(STATE["selections"]))
            await asyncio.sleep(0.5)
            await safe_send(chat_id, format_simples(STATE["selections"], MAX_SIMPLE_SEND))
            await asyncio.sleep(0.5)
        for c in STATE["coupons"]:
            await safe_send(chat_id, format_coupon(c))
            await asyncio.sleep(0.5)


# =========================================================
# APP
# =========================================================
async def bootstrap():
    STATE["tracker"] = load_tracker()
    STATE["ready"] = True
    print("✅ Bot prêt (BetBetter + 5DFootballAPI + Gemini).")


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
    scheduler.start()

    bot_task = asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(scan())

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
        "status": "ok",
        "ready": STATE["ready"],
        "selections": len(STATE["selections"]),
        "coupons": [c.name for c in STATE["coupons"]],
        "last_scan": STATE["last_scan"],
    }


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
