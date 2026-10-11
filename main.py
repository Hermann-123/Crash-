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
    from dixon_coles import download_and_train as dc_train, DixonColesModel
except Exception:
    dc_train = None
    DixonColesModel = None

try:
    from football_data import enrich_match as fd_enrich
except Exception:
    fd_enrich = None

try:
    from markets_extra import compute_corners_probs, compute_cards_probs
except Exception:
    compute_corners_probs = None
    compute_cards_probs = None


# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Value betting • Dixon-Coles + Contexte")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.03"))
MIN_PROB = float(os.getenv("MIN_PROB", "0.55"))
MAX_ENRICH = int(os.getenv("MAX_ENRICH", "12"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not ODDS_API_KEY:
    raise RuntimeError("ODDS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"

SOCCER_KEYS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_efl_champ", "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
]

MAX_LEGS_PER_COUPON = 9
LEAGUE_CORNERS_AVG = 10.5
LEAGUE_CARDS_AVG = 3.8


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
    source: str = "house"
    ai_prob: Optional[float] = None
    ai_verdict: str = ""
    ai_analysis: str = ""
    ai_advice: str = ""
    ai_critique: str = ""
    context: Optional[dict] = None

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


def league_key_from_title(title: str) -> Optional[str]:
    mapping = {
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
    tl = (title or "").lower()
    for k, v in mapping.items():
        if k in tl:
            return v
    return None


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
        tracker["coupons"].append({
            "id": cid, "date": date_str, "name": c.name,
            "combined_odds": c.combined_odds, "status": "pending",
        })
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)


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
# THE ODDS API
# =========================================================
async def fetch_odds_api_league(client: httpx.AsyncClient, sport_key: str) -> list[dict]:
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
        "sport_key": ev.get("sport_key", ""),
        "kickoff": ev.get("commence_time", ""),
        "raw": ev,
    }


def extract_odds_from_event(ev: dict) -> dict:
    result = {"h2h": {}, "spreads": {}, "totals": {}}
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

                elif mk == "spreads":
                    point = out.get("point")
                    if point is None:
                        continue
                    try:
                        pt = float(point)
                    except (ValueError, TypeError):
                        continue
                    if name == home:
                        result["spreads"][f"home_{pt}"] = max(
                            result["spreads"].get(f"home_{pt}", 0), price
                        )
                    elif name == away:
                        result["spreads"][f"away_{pt}"] = max(
                            result["spreads"].get(f"away_{pt}", 0), price
                        )

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
# GEMINI — REVIEW
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

    ctx_block = ""
    if sel.context and sel.context.get("available"):
        hs = sel.context.get("home_stats", {})
        aws = sel.context.get("away_stats", {})
        h2h = sel.context.get("h2h", [])
        h_inj = sel.context.get("home_injuries", [])
        a_inj = sel.context.get("away_injuries", [])

        h2h_str = "; ".join(f"{x['home']} {x['home_goals']}-{x['away_goals']} {x['away']}" for x in h2h[:5]) or "aucun"

        ctx_block = f"""

📊 CONTEXTE RÉEL (API-Football) :

Forme {sel.home} : {hs.get('form', '?')}
Forme {sel.away} : {aws.get('form', '?')}

{sel.home} : {hs.get('goals_for_avg', 0):.2f} buts/m, {hs.get('goals_against_avg', 0):.2f} encaissés/m
{sel.away} : {aws.get('goals_for_avg', 0):.2f} buts/m, {aws.get('goals_against_avg', 0):.2f} encaissés/m

Corners/m : {sel.home} {hs.get('corners_for_avg', 0):.1f} • {sel.away} {aws.get('corners_for_avg', 0):.1f}
Cartons/m : {sel.home} {hs.get('cards_yellow_avg', 0):.1f} • {sel.away} {aws.get('cards_yellow_avg', 0):.1f}

H2H : {h2h_str}
Absents {sel.home} : {', '.join(x['player'] for x in h_inj) or 'aucun'}
Absents {sel.away} : {', '.join(x['player'] for x in a_inj) or 'aucun'}
"""

    prompt = f"""Tu es un analyste football INDÉPENDANT et CRITIQUE.

Match : {sel.home} vs {sel.away}
Compétition : {sel.league}
Heure : {kickoff_local(sel.kickoff)}
Marché : {sel.market}
Pari (généré par Dixon-Coles) : {sel.pick_label}
Cote (vraie) : {sel.odds}
Proba Dixon-Coles : {sel.model_prob*100:.1f}%
Edge : {sel.edge*100:+.1f}%{ctx_block}

⚠️ Analyse avec le CONTEXTE RÉEL ci-dessus.
Sois honnête, n'invente rien. Si données manquantes → dis-le.

Réponds EXACTEMENT :

VERDICT: ACCEPT
PROBA: 72
CRITIQUE_BOT: [1 phrase]
ANALYSE: [2 phrases basées sur les données]
CONSEIL: [1 phrase]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 600},
    }

    try:
        r = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=35.0)
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

            if s.ai_critique:
                c_low = s.ai_critique.lower()
                bad = ["sous-estime", "surestime", "se trompe", "à côté",
                       "douteux", "erreur", "faux", "aberrant", "massif",
                       "suspect", "incohérent"]
                if any(w in c_low for w in bad):
                    s.ai_verdict = "REJECT"

            if (i + 1) % 5 == 0:
                print(f"  ... {i+1}/{len(selections)}")
            await asyncio.sleep(1.2)
    a = sum(1 for s in selections if s.ai_verdict == "ACCEPT")
    c = sum(1 for s in selections if s.ai_verdict == "CAUTION")
    r = sum(1 for s in selections if s.ai_verdict == "REJECT")
    print(f"🧠 IA : {a} ACCEPT, {c} CAUTION, {r} REJECT")


# =========================================================
# HOUSE PICKS
# =========================================================
def build_house_picks(events_norm, odds_by_id, used_today, contexts_by_id=None):
    selections: list[Selection] = []
    seen = set()
    contexts_by_id = contexts_by_id or {}
    stats = {"1x2": 0, "ou": 0, "corners": 0, "cards": 0,
             "rej_edge": 0, "rej_asym": 0}

    if not DC_MODEL:
        return selections

    for fx in events_norm:
        try:
            home, away = fx["home"], fx["away"]
            event_id, league = fx["event_id"], fx["league"]
            kickoff = fx["kickoff"]

            if event_id in used_today:
                continue

            pred = DC_MODEL.predict_all(home, away)
            if not pred:
                continue

            odds = odds_by_id.get(event_id, {})
            if not odds:
                continue

            p_home = pred.get("p_home", 0)
            p_away = pred.get("p_away", 0)
            is_asymmetric = (p_home > 0.65 and p_away < 0.20) or \
                            (p_away > 0.65 and p_home < 0.20)

            ctx = contexts_by_id.get(event_id, {})

            # === 1X2 ===
            h2h = odds.get("h2h", {})
            for key, prob in (("home", p_home),
                              ("draw", pred.get("p_draw", 0)),
                              ("away", p_away)):
                odd = h2h.get(key)
                if not odd or odd <= 1.01:
                    continue
                if prob < MIN_PROB:
                    continue
                edge = prob * odd - 1.0
                if edge < MIN_EDGE:
                    continue
                if edge > 0.15:
                    stats["rej_edge"] += 1
                    continue

                if key == "home":
                    label = f"Victoire {home}"
                elif key == "away":
                    label = f"Victoire {away}"
                else:
                    label = "Match nul"

                dedup = f"{event_id}|1X2|{label}"
                if dedup in seen:
                    continue
                seen.add(dedup)

                selections.append(Selection(
                    event_id=event_id, league=league, home=home, away=away,
                    kickoff=kickoff, market="1X2", pick_label=label,
                    odds=round(odd, 2), model_prob=round(prob, 4),
                    edge=round(edge, 4), bookmaker="The Odds API",
                    source="house", context=ctx,
                ))
                stats["1x2"] += 1

            # === Over/Under ===
            totals = odds.get("totals", {})
            for line in (1.5, 2.5, 3.5):
                for prefix, prob_key in (("over", f"over_{line}"), ("under", f"under_{line}")):
                    prob = pred.get(prob_key)
                    if prob is None or prob < MIN_PROB:
                        continue
                    if prefix == "under" and is_asymmetric:
                        stats["rej_asym"] += 1
                        continue
                    odd = totals.get(f"{prefix}_{line}")
                    if not odd or odd <= 1.01:
                        continue
                    edge = prob * odd - 1.0
                    if edge < MIN_EDGE:
                        continue
                    if edge > 0.15:
                        stats["rej_edge"] += 1
                        continue

                    label = f"{'Over' if prefix == 'over' else 'Under'} {line} buts"
                    dedup = f"{event_id}|O/U|{label}"
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    selections.append(Selection(
                        event_id=event_id, league=league, home=home, away=away,
                        kickoff=kickoff, market="Over/Under", pick_label=label,
                        odds=round(odd, 2), model_prob=round(prob, 4),
                        edge=round(edge, 4), bookmaker="The Odds API",
                        source="house", context=ctx,
                    ))
                    stats["ou"] += 1

            # === Corners (Étape 2) ===
            if compute_corners_probs and ctx and ctx.get("available"):
                hs = ctx.get("home_stats", {})
                aws = ctx.get("away_stats", {})
                h_corners = hs.get("corners_for_avg", 0) or (LEAGUE_CORNERS_AVG * 0.55)
                a_corners = aws.get("corners_for_avg", 0) or (LEAGUE_CORNERS_AVG * 0.45)

                cp = compute_corners_probs(h_corners, a_corners, LEAGUE_CORNERS_AVG)

                # On ne garde que si les équipes ont des moyennes fiables (>0)
                if hs.get("corners_for_avg", 0) > 0 and aws.get("corners_for_avg", 0) > 0:
                    for line in (8.5, 9.5, 10.5):
                        for prefix in ("over", "under"):
                            prob = cp.get(f"{prefix}_{line}")
                            if not prob or prob < MIN_PROB:
                                continue
                            # Pas de cote The Odds API pour les corners → on skip
                            # (à moins qu'on ajoute une estimation ~ fair odds)
                            # Pour l'instant on n'ajoute pas, on log juste
                            pass

                    # Corners par équipe (favori uniquement)
                    for line in (4.5, 5.5):
                        prob = cp.get(f"home_over_{line}")
                        if prob and prob >= MIN_PROB and p_home > 0.5:
                            # Pas de cote API → skip pour l'instant
                            pass

                # Note : sans cote corners de The Odds API, on ne peut pas calculer d'edge.
                # Le calcul est prêt pour quand on aura une source de cotes corners.

            # === Cartons (Étape 2) ===
            if compute_cards_probs and ctx and ctx.get("available"):
                hs = ctx.get("home_stats", {})
                aws = ctx.get("away_stats", {})
                h_cards = hs.get("cards_yellow_avg", 0) or (LEAGUE_CARDS_AVG * 0.5)
                a_cards = aws.get("cards_yellow_avg", 0) or (LEAGUE_CARDS_AVG * 0.5)

                cdp = compute_cards_probs(h_cards, a_cards, LEAGUE_CARDS_AVG)

                if hs.get("cards_yellow_avg", 0) > 0 and aws.get("cards_yellow_avg", 0) > 0:
                    # Pas de cote cartons → skip pour l'instant
                    pass

        except Exception as e:
            print(f"⚠️ build_house_picks crash: {e}")

    print(f"🏠 Picks maison : 1X2={stats['1x2']} O/U={stats['ou']} "
          f"edge_rej={stats['rej_edge']} asym_rej={stats['rej_asym']}")
    return selections


# =========================================================
# COUPONS
# =========================================================
def build_coupon_target(name, subtitle, emoji, pool, min_odds, max_odds,
                        max_legs, globally_used, min_prob):
    pool = [s for s in pool
            if s.odds and s.odds > 1.01
            and s.event_id not in globally_used
            and s.ai_verdict == "ACCEPT"
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
        if next_odds > max_odds:
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
    if combined_odds < min_odds * 0.90:
        return None
    if combined_odds > max_odds:
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
    dt = parse_dt_safe(s.kickoff)
    day_tag = ""
    if dt:
        today = datetime.now(TZ).date()
        if dt.date() == today:
            day_tag = "📍 <b>Aujourd'hui</b> "
        elif dt.date() == today + timedelta(days=1):
            day_tag = "📅 <b>Demain</b> "

    prob_display = s.final_prob() * 100
    prob_source = "IA" if s.ai_prob else "Dixon-Coles"

    lines = [
        f"<b>┌─ Sélection #{idx}</b>",
        f"<b>│ 🏟 {s.home}  vs  {s.away}</b>",
        f"│ {day_tag}🕐 <b>{kickoff_local(s.kickoff)}</b>  •  {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>  <i>({s.market})</i>",
        f"│ 📊 Proba {prob_source} : <b>{prob_display:.1f}%</b>",
        f"│ 💰 Cote : <b>{s.odds}</b>  <i>({s.bookmaker})</i>",
        f"│ ⚡ Edge : <b>{s.edge*100:+.1f}%</b>  {edge_bar(s.edge)}",
    ]

    # Contexte (forme, H2H)
    if s.context and s.context.get("available"):
        hs = s.context.get("home_stats", {})
        aws = s.context.get("away_stats", {})
        lines.append(f"│")
        lines.append(f"│ 📈 Forme : {s.home} <b>{hs.get('form', '?')}</b> • {s.away} <b>{aws.get('form', '?')}</b>")

    if show_ai and s.ai_verdict:
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>AVIS IA</b> : {ai_verdict_emoji(s.ai_verdict)} <b>{s.ai_verdict}</b>")
        if s.ai_critique:
            lines.append(f"│ 🔍 <b>Critique</b> : <i>{s.ai_critique}</i>")
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
        f"Dixon-Coles : <b>{d.get('dc_predictions', 0)}</b>\n"
        f"Events Odds API : <b>{d.get('events_total', 0)}</b>\n"
        f"Events J/J+1 : <b>{d.get('events_upcoming', 0)}</b>\n"
        f"Cotes extraites : <b>{d.get('odds_fetched', 0)}</b>\n"
        f"Contextes enrichis : <b>{d.get('ctx_enriched', 0)}</b>\n"
        f"Picks maison : <b>{d.get('house_picks', 0)}</b>\n"
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
        events_raw = await fetch_all_events_with_odds()
        print(f"📦 Events The Odds API: {len(events_raw)}")

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

        dc_verdicts = {}
        if DC_MODEL:
            for fx in events_norm:
                pred = DC_MODEL.predict_all(fx["home"], fx["away"])
                if pred:
                    dc_verdicts[fx["event_id"]] = pred
            print(f"🧮 Dixon-Coles : {len(dc_verdicts)} prédictions")

        # 🌐 Enrichissement contexte (API-Football)
        contexts_by_id = {}
        if fd_enrich and API_FOOTBALL_KEY and events_norm:
            print(f"🌐 Enrichissement contexte (max {MAX_ENRICH})...")
            to_enrich = events_norm[:MAX_ENRICH]
            for fx in to_enrich:
                sport_key = fx.get("sport_key") or league_key_from_title(fx.get("league", ""))
                if not sport_key:
                    continue
                ctx = await fd_enrich(fx["home"], fx["away"], sport_key)
                if ctx.get("available"):
                    contexts_by_id[fx["event_id"]] = ctx
                await asyncio.sleep(0.3)
            print(f"✅ Contextes enrichis : {len(contexts_by_id)}")

        # 🏠 Picks maison
        used_today = load_used_today()
        selections = build_house_picks(events_norm, odds_by_id, used_today, contexts_by_id)
        print(f"🏠 Picks maison total : {len(selections)}")

        # 🧠 Revue IA
        if GEMINI_API_KEY and selections:
            await ai_review_all(selections)

        selections.sort(key=lambda s: s.score(), reverse=True)
        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)

        used_picks = [s for c in STATE["coupons"] for s in c.legs]
        mark_picks_used(used_picks)

        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")

        STATE["debug"] = {
            "dc_predictions": len(dc_verdicts),
            "events_total": len(events_raw),
            "events_upcoming": len(events_norm),
            "odds_fetched": len(odds_by_id),
            "ctx_enriched": len(contexts_by_id),
            "house_picks": len(selections),
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
        print(f"Cotes extraites     : {len(odds_by_id)}")
        print(f"Contextes enrichis  : {len(contexts_by_id)}")
        print(f"Picks maison        : {len(selections)}")
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
    print("✅ Bot prêt (Dixon-Coles + Contexte + Marchés).")


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
