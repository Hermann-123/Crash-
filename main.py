from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "8"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Value betting • Modèle indépendant")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.03"))
MIN_PROB = float(os.getenv("MIN_PROB", "0.65"))
MAX_SIMPLE_SEND = int(os.getenv("MAX_SIMPLE_SEND", "10"))
AI_REVIEW_ENABLED = os.getenv("AI_REVIEW_ENABLED", "true").lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not ODDS_API_KEY:
    raise RuntimeError("ODDS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_BASE = "https://api.the-odds-api.com/v4"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

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
    "soccer/epl",
    "soccer/la-liga",
    "soccer/serie-a",
    "soccer/bundesliga",
    "soccer/ligue-1",
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
    confidence: str
    fair_odds: Optional[float] = None
    # Champs IA
    ai_verdict: str = ""       # ACCEPT / CAUTION / REJECT / ""
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


def parse_dt_safe(s: str) -> Optional[datetime]:
    if not s:
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


def kickoff_local(iso_str: str) -> str:
    dt = parse_dt_safe(iso_str)
    return dt.strftime("%H:%M") if dt else "?"


def is_today(iso_str: str) -> bool:
    dt = parse_dt_safe(iso_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    if dt.date() != now.date():
        return False
    return dt > now + timedelta(minutes=15)


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
# THE ODDS API
# =========================================================
async def fetch_odds_for_sport(client: httpx.AsyncClient, sport_key: str) -> list[dict]:
    url = f"{ODDS_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
    }
    try:
        r = await client.get(url, params=params, timeout=30.0)
    except Exception as e:
        print(f"⚠️ HTTP crash {sport_key}: {e}")
        return []

    remaining = r.headers.get("x-requests-remaining", "?")
    used = r.headers.get("x-requests-used", "?")
    print(f"🌍 {sport_key} -> {r.status_code} | used={used} remaining={remaining}")

    if r.status_code >= 400:
        print(f"⚠️ Body: {r.text[:200]}")
        return []
    try:
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def fetch_all_odds_events() -> list[dict]:
    all_events: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for key in SOCCER_KEYS:
            events = await fetch_odds_for_sport(client, key)
            all_events.extend(events)
            await asyncio.sleep(0.3)
    print(f"📦 Total odds events: {len(all_events)}")
    return all_events


# =========================================================
# GROQ — MATCHING IA
# =========================================================
async def ai_match_teams(client: httpx.AsyncClient, bb_home: str, bb_away: str,
                          candidates: list[tuple[str, str, str]]) -> Optional[str]:
    if not GROQ_API_KEY:
        return None
    if not candidates:
        return None

    cache_key = f"{normalize_name(bb_home)}|{normalize_name(bb_away)}"
    if cache_key in AI_MATCH_CACHE:
        return AI_MATCH_CACHE[cache_key]

    candidates = candidates[:20]
    lignes = "\n".join(f"{i+1}. {h} vs {a}" for i, (_, h, a) in enumerate(candidates))

    prompt = (
        f"Tu es un expert en football. Un modèle donne un match : \"{bb_home} vs {bb_away}\".\n"
        f"Parmi cette liste, quel match correspond au MÊME match ?\n\n"
        f"{lignes}\n\n"
        f"Réponds UNIQUEMENT par le numéro (1-{len(candidates)}) ou 0 si aucun ne correspond. "
        f"Pas d'explication. Juste le numéro."
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "Tu réponds uniquement par un numéro."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_completion_tokens": 10,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

    try:
        r = await client.post(GROQ_URL, json=payload, headers=headers, timeout=15.0)
        if r.status_code >= 400:
            print(f"⚠️ Groq match {r.status_code}: {r.text[:150]}")
            return None
        text = r.json()["choices"][0]["message"]["content"].strip()
        num = int(re.search(r"\d+", text).group())
        if 1 <= num <= len(candidates):
            matched_id = candidates[num - 1][0]
            AI_MATCH_CACHE[cache_key] = matched_id
            return matched_id
        AI_MATCH_CACHE[cache_key] = None
        return None
    except Exception as e:
        print(f"⚠️ Groq match crash: {e}")
        return None


async def ai_rematch_all(bb_data: dict, events: list[dict]) -> int:
    if not GROQ_API_KEY:
        return 0

    today_candidates = [
        (e.get("id", ""), e.get("home_team", ""), e.get("away_team", ""))
        for e in events if is_today(e.get("commence_time", ""))
    ]
    if not today_candidates:
        return 0

    already_matched = set()
    for key in bb_data.keys():
        try:
            k_away, k_home = key.split("|", 1)
        except ValueError:
            continue
        for eid, h, a in today_candidates:
            hn, an = normalize_name(h), normalize_name(a)
            if (hn in k_home or k_home in hn) and (an in k_away or k_away in an):
                already_matched.add(eid)
                break

    unmatched_candidates = [c for c in today_candidates if c[0] not in already_matched]
    if not unmatched_candidates:
        return 0

    print(f"🤖 Groq : {len(unmatched_candidates)} events non appariés")

    count = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        for key, data in bb_data.items():
            bb_home, bb_away = data["home"], data["away"]
            hn, an = normalize_name(bb_home), normalize_name(bb_away)

            found = any(
                (normalize_name(h) in hn or hn in normalize_name(h)) and
                (normalize_name(a) in an or an in normalize_name(a))
                for _, h, a in today_candidates
            )
            if found:
                continue

            matched_id = await ai_match_teams(client, bb_home, bb_away, unmatched_candidates)
            if matched_id:
                count += 1
                print(f"  🤖 Match IA : {bb_home} vs {bb_away} → {matched_id}")
                unmatched_candidates = [c for c in unmatched_candidates if c[0] != matched_id]

            await asyncio.sleep(2.1)  # rate limit 30 RPM

    return count


# =========================================================
# GROQ — REVUE ANALYTIQUE (LE VETO)
# =========================================================
async def ai_review_selection(client: httpx.AsyncClient, sel: Selection) -> None:
    """Analyse une sélection avec Groq. Remplit ses champs ai_*."""
    if not GROQ_API_KEY:
        return

    cache_key = f"rev|{sel.event_id}|{sel.market}|{sel.pick_label}"
    if cache_key in AI_REVIEW_CACHE:
        cached = AI_REVIEW_CACHE[cache_key]
        sel.ai_verdict = cached.get("verdict", "")
        sel.ai_analysis = cached.get("analysis", "")
        sel.ai_advice = cached.get("advice", "")
        return

    prompt = f"""Tu es un analyste football expert, honnête et franc. Tu as le DROIT de refuser un pari.

📋 Contexte du match :
- Match : {sel.home} vs {sel.away}
- Compétition : {sel.league}
- Coup d'envoi : {kickoff_local(sel.kickoff)}
- Marché : {sel.market}
- Pari proposé : {sel.pick_label}
- Cote disponible : {sel.odds} (chez {sel.bookmaker})
- Probabilité du modèle BetBetter : {sel.model_prob*100:.1f}%
- Edge calculé : {sel.edge*100:+.1f}%

🎯 Ta mission :
Analyse ce pari en 3-4 phrases MAXIMUM. Sois franc, tu peux contredire le modèle.
Si tu ne connais pas assez ce match, dis-le et mets CAUTION.

Réponds EXACTEMENT dans ce format (rien d'autre) :

VERDICT: ACCEPT
ANALYSE: [ton analyse en 3-4 phrases courtes. Mentionne les forces, faiblesses et risques principaux. Si tu ne connais pas, dis "données incertaines sur ce match".]
CONSEIL: [1 phrase d'action pour le parieur]

Règles strictes :
- VERDICT = ACCEPT → pari cohérent, feu vert
- VERDICT = CAUTION → risque réel, mise réduite conseillée
- VERDICT = REJECT → pari douteux, ne pas jouer
- N'invente JAMAIS de statistiques, blessures ou compos
- Ne donne pas de probabilités chiffrées
- Pas de cotes alternatives
- Sois bref et direct"""

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "Tu es un analyste football honnête. Tu réponds STRICTEMENT au format demandé."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_completion_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

    try:
        r = await client.post(GROQ_URL, json=payload, headers=headers, timeout=25.0)
        if r.status_code >= 400:
            print(f"⚠️ Groq review {r.status_code}: {r.text[:200]}")
            return
        text = r.json()["choices"][0]["message"]["content"]

        verdict, analysis, advice = "", "", ""
        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                if "ACCEPT" in v:
                    verdict = "ACCEPT"
                elif "REJECT" in v:
                    verdict = "REJECT"
                elif "CAUTION" in v:
                    verdict = "CAUTION"
            elif line.upper().startswith("ANALYSE:"):
                analysis = line.split(":", 1)[1].strip()
            elif line.upper().startswith("CONSEIL:"):
                advice = line.split(":", 1)[1].strip()

        sel.ai_verdict = verdict
        sel.ai_analysis = analysis
        sel.ai_advice = advice

        AI_REVIEW_CACHE[cache_key] = {
            "verdict": verdict, "analysis": analysis, "advice": advice
        }
    except Exception as e:
        print(f"⚠️ Groq review crash: {e}")


async def ai_review_all(selections: list[Selection]) -> None:
    """Fait analyser toutes les sélections par Groq (séquentiel pour respecter le rate limit)."""
    if not GROQ_API_KEY or not AI_REVIEW_ENABLED:
        return
    if not selections:
        return

    print(f"🧠 Revue IA de {len(selections)} sélections...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, s in enumerate(selections):
            await ai_review_selection(client, s)
            if (i + 1) % 5 == 0:
                print(f"  ... {i+1}/{len(selections)}")
            await asyncio.sleep(2.1)

    accepted = sum(1 for s in selections if s.ai_verdict == "ACCEPT")
    caution = sum(1 for s in selections if s.ai_verdict == "CAUTION")
    rejected = sum(1 for s in selections if s.ai_verdict == "REJECT")
    unknown = sum(1 for s in selections if not s.ai_verdict)
    print(f"🧠 Résultat IA : {accepted} ACCEPT, {caution} CAUTION, {rejected} REJECT, {unknown} sans verdict")


# =========================================================
# MATCHING
# =========================================================
def find_best_odds(event: dict, market_key: str, predicate) -> tuple[Optional[float], Optional[str]]:
    best = None
    best_book = None
    for book in event.get("bookmakers", []) or []:
        for mkt in book.get("markets", []) or []:
            if mkt.get("key") != market_key:
                continue
            for o in mkt.get("outcomes", []) or []:
                try:
                    if not predicate(o):
                        continue
                    price = float(o.get("price", 0))
                    if price > 1.01 and (best is None or price > best):
                        best = price
                        best_book = book.get("title") or book.get("key", "?")
                except Exception:
                    continue
    return best, best_book


def _match_name(n: str, target: str) -> bool:
    return n == target or (target and (target in n or n in target))


def find_odds_for_pick(event: dict, pick: dict, home: str, away: str) -> tuple[Optional[float], Optional[str]]:
    market = (pick.get("market") or "").strip()
    selection = (pick.get("selection") or "").strip()
    line = pick.get("line")

    sel_norm = normalize_name(selection)
    home_norm = normalize_name(home)
    away_norm = normalize_name(away)

    if market == "Moneyline":
        if _match_name(sel_norm, home_norm):
            return find_best_odds(event, "h2h", lambda o: _match_name(normalize_name(o.get("name", "")), home_norm))
        if _match_name(sel_norm, away_norm):
            return find_best_odds(event, "h2h", lambda o: _match_name(normalize_name(o.get("name", "")), away_norm))
        if "draw" in sel_norm or "nul" in sel_norm or sel_norm == "x":
            return find_best_odds(event, "h2h", lambda o: "draw" in normalize_name(o.get("name", "")) or normalize_name(o.get("name", "")) == "x")
        return None, None

    if market == "Total":
        is_over = "over" in sel_norm or "plus" in sel_norm
        is_under = "under" in sel_norm or "moins" in sel_norm
        if not (is_over or is_under):
            return None, None
        try:
            target = float(line) if line is not None else None
        except (ValueError, TypeError):
            target = None

        def filt_exact(o):
            n = normalize_name(o.get("name", ""))
            if is_over and n != "over": return False
            if is_under and n != "under": return False
            if target is not None:
                try:
                    return abs(float(o.get("point", -999)) - target) < 0.01
                except (ValueError, TypeError):
                    return False
            return True

        r = find_best_odds(event, "totals", filt_exact)
        if r[0] is not None:
            return r

        if target is not None:
            def filt_fuzzy(o):
                n = normalize_name(o.get("name", ""))
                if is_over and n != "over": return False
                if is_under and n != "under": return False
                try:
                    return abs(float(o.get("point", -999)) - target) <= 0.26
                except (ValueError, TypeError):
                    return False
            return find_best_odds(event, "totals", filt_fuzzy)
        return None, None

    return None, None


def find_bb_for_event(event: dict, bb_data: dict) -> Optional[dict]:
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    event_id = event.get("id", "")

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


def build_selections(odds_events: list[dict], bb_data: dict) -> list[Selection]:
    selections: list[Selection] = []
    seen_keys = set()

    for ev in odds_events:
        try:
            home = ev.get("home_team", "")
            away = ev.get("away_team", "")
            if not home or not away:
                continue

            kickoff = ev.get("commence_time", "")
            if not is_today(kickoff):
                continue

            bb_match = find_bb_for_event(ev, bb_data)
            if not bb_match:
                continue

            event_id = ev.get("id", "")
            league = ev.get("sport_title", "") or bb_match.get("league", "")

            for pick in bb_match["picks"]:
                prob_pct = pick.get("modelProbabilityPct")
                if prob_pct is None:
                    continue
                prob = float(prob_pct) / 100.0
                if prob < MIN_PROB:
                    continue

                market = pick.get("market", "")
                line = pick.get("line")

                if market == "Spread":
                    continue
                if market == "Total":
                    try:
                        line_f = float(line) if line is not None else None
                    except (ValueError, TypeError):
                        line_f = None
                    if line_f not in (0.5, 1.5):
                        continue

                odds, book = find_odds_for_pick(ev, pick, home, away)
                if odds is None:
                    continue

                edge = prob * odds - 1.0
                if edge < MIN_EDGE:
                    continue

                selection = pick.get("selection", "")
                confidence = pick.get("confidence", "")

                if market == "Moneyline":
                    if normalize_name(selection) == normalize_name(home):
                        label = f"Victoire {home}"
                    elif normalize_name(selection) == normalize_name(away):
                        label = f"Victoire {away}"
                    else:
                        label = "Match nul"
                    market_label = "1X2"
                elif market == "Total":
                    label = f"{selection} {line} buts"
                    market_label = "Over/Under"
                else:
                    label = selection
                    market_label = market

                dedup = f"{event_id}|{market_label}|{label}"
                if dedup in seen_keys:
                    continue
                seen_keys.add(dedup)

                fair = pick.get("fairOdds")
                try:
                    fair_f = float(fair) if fair is not None else None
                except (ValueError, TypeError):
                    fair_f = None

                selections.append(Selection(
                    event_id=event_id,
                    league=league,
                    home=home,
                    away=away,
                    kickoff=kickoff,
                    market=market_label,
                    pick_label=label,
                    odds=round(odds, 2),
                    model_prob=round(prob, 4),
                    edge=round(edge, 4),
                    bookmaker=book or "?",
                    confidence=confidence,
                    fair_odds=fair_f,
                ))
        except Exception as e:
            print(f"⚠️ build_selections crash: {e}")

    return selections


# =========================================================
# COUPON BUILDER — filtre les REJECT de l'IA
# =========================================================
def build_coupon(name: str, subtitle: str, emoji: str, pool: list[Selection],
                 min_odds: float, max_odds: float, max_legs: int,
                 globally_used: set) -> Optional[Coupon]:
    # VETO IA : on garde ACCEPT + CAUTION (avec warning), on jette REJECT
    pool = [
        s for s in pool
        if s.odds and s.odds > 1.01
        and s.event_id not in globally_used
        and s.ai_verdict != "REJECT"
    ]
    if not pool:
        return None

    pool.sort(key=lambda s: s.score(), reverse=True)

    legs: list[Selection] = []
    used_events = set()
    used_leagues = set()
    combined_odds = 1.0
    combined_prob = 1.0

    for s in pool:
        if len(legs) >= max_legs:
            break
        if s.event_id in used_events:
            continue
        if s.league in used_leagues:
            continue
        next_odds = combined_odds * s.odds
        if next_odds > max_odds and legs:
            continue
        legs.append(s)
        used_events.add(s.event_id)
        used_leagues.add(s.league)
        combined_odds = next_odds
        combined_prob *= s.model_prob

    if not legs:
        return None
    if combined_odds < min_odds * 0.85:
        return None

    for s in legs:
        globally_used.add(s.event_id)

    return Coupon(
        name=name,
        subtitle=subtitle,
        emoji=emoji,
        legs=legs,
        combined_odds=round(combined_odds, 2),
        combined_prob=round(combined_prob, 4),
        combined_ev=round(combined_prob * combined_odds - 1, 4),
    )


def build_all_coupons(selections: list[Selection]) -> list[Coupon]:
    coupons: list[Coupon] = []
    globally_used: set = set()

    ultra_pool = [s for s in selections if s.model_prob >= 0.75 and s.odds <= 1.55]
    c = build_coupon("Ultra Safe", "1 sélection à très forte probabilité", "🛡",
                     ultra_pool, 1.30, 1.60, 1, globally_used)
    if c:
        coupons.append(c)

    safe_pool = [s for s in selections if s.model_prob >= 0.65 and s.odds <= 1.55]
    c = build_coupon("Safe", "2 sélections solides, cote ~2", "✅",
                     safe_pool, 1.80, 2.50, 2, globally_used)
    if c:
        coupons.append(c)

    eq_pool = [s for s in selections if s.model_prob >= 0.60]
    c = build_coupon("Équilibré", "3 sélections, cote 3-5", "⚖️",
                     eq_pool, 3.0, 5.0, 3, globally_used)
    if c:
        coupons.append(c)

    val_pool = sorted([s for s in selections if s.edge >= 0.05],
                      key=lambda s: s.edge, reverse=True)
    c = build_coupon("Value", "Meilleurs edges ≥5%", "💎",
                     val_pool, 1.60, 3.00, 2, globally_used)
    if c:
        coupons.append(c)

    return coupons


# =========================================================
# FORMAT PREMIUM + IA
# =========================================================
def format_header() -> str:
    return (
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<i>{BRAND_TAGLINE}</i>\n"
        f"<i>📅 {today_pretty()} • 🇨🇮 Côte d’Ivoire</i>"
    )


def format_selection_block(s: Selection, idx: int, show_ai: bool = True) -> str:
    conf_tag = ""
    conf_upper = (s.confidence or "").upper()
    if conf_upper == "STRONG":
        conf_tag = "  🔥 <b>STRONG</b>"
    elif conf_upper == "LEAN":
        conf_tag = "  ⚡ LEAN"

    lines = [
        f"<b>┌─ Sélection #{idx}</b>",
        f"<b>│ 🏟 {s.home}  vs  {s.away}</b>",
        f"│ 🕐 <b>{kickoff_local(s.kickoff)}</b>  •  {s.league}",
        f"│ 🎯 Pari : <b>{s.pick_label}</b>",
        f"│ 🏷 Marché : <i>{s.market}</i>{conf_tag}",
        f"│ 📈 Proba modèle : <b>{s.model_prob*100:.1f}%</b>",
        f"│ 💰 Cote : <b>{s.odds}</b>  ({s.bookmaker})",
        f"│ ⚡ Edge : <b>{s.edge*100:+.1f}%</b>  {edge_bar(s.edge)}",
    ]

    if show_ai and s.ai_verdict:
        emoji = ai_verdict_emoji(s.ai_verdict)
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>AVIS IA</b> : {emoji} <b>{s.ai_verdict}</b>")
        if s.ai_analysis:
            lines.append(f"│ 💬 <i>{s.ai_analysis}</i>")
        if s.ai_advice:
            lines.append(f"│ 💡 <b>{s.ai_advice}</b>")

    lines.append(f"<b>└───────────────</b>")
    return "\n".join(lines)


def format_simples(selections: list[Selection], limit: int = 10) -> str:
    if not selections:
        return (
            f"{format_header()}\n\n"
            f"<b>❌ Aucune value détectée aujourd'hui</b>\n"
            f"<i>Le modèle et le marché sont alignés, on ne force pas.</i>"
        )
    lines = [
        format_header(), "",
        f"<b>📋 TOP VALUE BETS DU JOUR</b>",
        f"<i>{len(selections)} sélections • Top {min(limit, len(selections))}</i>", "",
    ]
    for i, s in enumerate(selections[:limit], 1):
        lines.append(format_selection_block(s, i))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_ia_reviews(selections: list[Selection]) -> str:
    """Affiche UNIQUEMENT l'analyse IA de toutes les sélections (même rejetées)."""
    if not selections:
        return f"{format_header()}\n\n<b>🧠 Aucune analyse IA disponible</b>"

    accepted = [s for s in selections if s.ai_verdict == "ACCEPT"]
    caution = [s for s in selections if s.ai_verdict == "CAUTION"]
    rejected = [s for s in selections if s.ai_verdict == "REJECT"]

    lines = [
        format_header(), "",
        f"<b>🧠 ANALYSE IA DU JOUR</b>",
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>",
        f"✅ ACCEPT : <b>{len(accepted)}</b>",
        f"⚠️ CAUTION : <b>{len(caution)}</b>",
        f"❌ REJECT : <b>{len(rejected)}</b>",
        "",
    ]

    if rejected:
        lines.append(f"<b>❌ PARIS REJETÉS PAR L'IA</b>")
        lines.append("")
        for i, s in enumerate(rejected, 1):
            lines.append(f"<b>{i}. {s.home} vs {s.away}</b>")
            lines.append(f"   🎯 {s.pick_label} @ {s.odds}")
            if s.ai_analysis:
                lines.append(f"   💬 <i>{s.ai_analysis}</i>")
            if s.ai_advice:
                lines.append(f"   💡 {s.ai_advice}")
            lines.append("")

    if caution:
        lines.append(f"<b>⚠️ PARIS AVEC PRUDENCE (CAUTION)</b>")
        lines.append("")
        for i, s in enumerate(caution, 1):
            lines.append(f"<b>{i}. {s.home} vs {s.away}</b>")
            lines.append(f"   🎯 {s.pick_label} @ {s.odds}")
            if s.ai_analysis:
                lines.append(f"   💬 <i>{s.ai_analysis}</i>")
            lines.append("")

    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_coupon(c: Coupon) -> str:
    prob_level = "🟢 Faible risque" if c.combined_prob >= 0.40 else \
                 "🟡 Risque modéré" if c.combined_prob >= 0.20 else \
                 "🔴 Risque élevé"

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


def format_summary() -> str:
    accepted = sum(1 for s in STATE["selections"] if s.ai_verdict == "ACCEPT")
    caution = sum(1 for s in STATE["selections"] if s.ai_verdict == "CAUTION")
    rejected = sum(1 for s in STATE["selections"] if s.ai_verdict == "REJECT")

    return (
        f"{format_header()}\n\n"
        f"<b>✅ ANALYSE DU JOUR PRÊTE</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"🎯 Value bets : <b>{len(STATE['selections'])}</b>\n"
        f"   • ✅ Acceptées IA : <b>{accepted}</b>\n"
        f"   • ⚠️ Prudence IA : <b>{caution}</b>\n"
        f"   • ❌ Rejetées IA : <b>{rejected}</b>\n"
        f"🎫 Coupons disponibles : <b>{len(STATE['coupons'])}</b>\n"
        f"🔄 Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )


def format_coupons_ready() -> str:
    if not STATE["coupons"]:
        return (
            f"{format_header()}\n\n"
            f"<b>📭 Aucun coupon disponible aujourd'hui</b>\n"
            f"<i>L'IA a rejeté les sélections ou pas assez de value.</i>"
        )

    lignes = []
    for c in STATE["coupons"]:
        n_legs = len(c.legs)
        prob = c.combined_prob * 100
        # Compte les CAUTION dans le coupon
        n_caution = sum(1 for s in c.legs if s.ai_verdict == "CAUTION")
        caution_tag = f" • ⚠️ {n_caution}" if n_caution else ""
        lignes.append(
            f"{c.emoji} <b>{c.name}</b>\n"
            f"   • {n_legs} sélection{'s' if n_legs > 1 else ''}\n"
            f"   • Cote <b>{c.combined_odds}</b> • Proba <b>{prob:.0f}%</b>{caution_tag}"
        )

    return (
        f"{format_header()}\n\n"
        f"<b>🔔 COUPONS DU JOUR PRÊTS !</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>{len(STATE['coupons'])} coupons disponibles :</b>\n\n"
        + "\n\n".join(lignes) +
        f"\n\n<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<i>➡️ Choisis un coupon avec le clavier ci-dessous</i>"
    )


def format_bilan(tracker: dict) -> str:
    coupons = tracker.get("coupons", [])
    if not coupons:
        return f"{format_header()}\n\n<b>📊 Bilan</b>\n<i>Aucun historique.</i>"
    return (
        f"{format_header()}\n\n"
        f"<b>📊 BILAN</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"🎫 Tickets enregistrés : <b>{len(coupons)}</b>"
    )


# =========================================================
# TELEGRAM
# =========================================================
async def safe_answer(message: Message, text: str):
    try:
        await message.answer(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Réponse Telegram échouée : {e}")


async def safe_send(chat_id, text: str):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Envoi impossible vers {chat_id}: {e}")


@dp.message(Command("start"))
async def start_cmd(message: Message):
    txt = (
        f"{format_header()}\n\n"
        f"<b>👋 Bienvenue !</b>\n\n"
        f"Ce bot analyse les matchs avec :\n"
        f"  • Un <b>modèle statistique</b> (BetBetter)\n"
        f"  • Les <b>cotes réelles</b> de 40+ bookmakers\n"
        f"  • Une <b>IA analyste (Groq)</b> qui valide ou rejette chaque pari\n\n"
        f"<b>Utilise les boutons ci-dessous 👇</b>"
    )
    await safe_answer(message, txt)


@dp.message(Command("scan"))
async def scan_cmd(message: Message):
    await safe_answer(message, "⏳ Analyse en cours... (peut prendre 2-3 min avec l'IA)")
    await scan()
    await safe_answer(message, format_coupons_ready())
    await asyncio.sleep(0.5)
    await safe_answer(message, format_summary())


@dp.message(Command("debug"))
async def debug_cmd(message: Message):
    d = STATE["debug"]
    txt = (
        f"<b>🔍 DEBUG</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"BetBetter matchs : <b>{d.get('bb_matches', 0)}</b>\n"
        f"Events Odds API : <b>{d.get('odds_events', 0)}</b>\n"
        f"Matchs aujourd'hui : <b>{d.get('today_events', 0)}</b>\n"
        f"Appariés fuzzy : <b>{d.get('matched_fuzzy', 0)}</b>\n"
        f"Appariés IA : <b>{d.get('matched_ai', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
        f"✅ ACCEPT IA : <b>{d.get('ai_accept', 0)}</b>\n"
        f"⚠️ CAUTION IA : <b>{d.get('ai_caution', 0)}</b>\n"
        f"❌ REJECT IA : <b>{d.get('ai_reject', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )
    await safe_answer(message, txt)


@dp.message(F.text == BTN_SIMPLE)
async def btn_simple(message: Message):
    await safe_answer(message, format_simples(STATE["selections"], MAX_SIMPLE_SEND))


async def send_coupon_by_name(message: Message, name: str):
    coupon = next((c for c in STATE["coupons"] if c.name == name), None)
    if coupon:
        await safe_answer(message, format_coupon(coupon))
    else:
        await safe_answer(message, "Aucun coupon disponible pour ce profil aujourd’hui.")


@dp.message(F.text == BTN_SAFE)
async def btn_safe(message: Message):
    await send_coupon_by_name(message, "Safe")


@dp.message(F.text == BTN_BAL)
async def btn_bal(message: Message):
    await send_coupon_by_name(message, "Équilibré")


@dp.message(F.text == BTN_AGG)
async def btn_agg(message: Message):
    await send_coupon_by_name(message, "Ultra Safe")


@dp.message(F.text == BTN_VAL)
async def btn_val(message: Message):
    await send_coupon_by_name(message, "Value")


@dp.message(F.text == BTN_BILAN)
async def btn_bilan(message: Message):
    await safe_answer(message, format_bilan(STATE["tracker"]))


@dp.message(F.text == BTN_SCAN)
async def btn_scan(message: Message):
    await scan_cmd(message)


# =========================================================
# SCAN
# =========================================================
async def scan():
    async with STATE["scan_lock"]:
        bb = fetch_betbetter_picks()
        print(f"🧠 BetBetter matchs uniques: {len(bb)}")

        events = await fetch_all_odds_events()
        print(f"📦 Events Odds API: {len(events)}")

        today_events = [e for e in events if is_today(e.get("commence_time", ""))]
        print(f"📅 Matchs aujourd'hui: {len(today_events)}")

        matched_fuzzy = 0
        for ev in today_events:
            h, a = ev.get("home_team", ""), ev.get("away_team", "")
            key = f"{normalize_name(a)}|{normalize_name(h)}"
            if key in bb:
                matched_fuzzy += 1
            else:
                for k in bb.keys():
                    try:
                        k_away, k_home = k.split("|", 1)
                    except ValueError:
                        continue
                    hn, an = normalize_name(h), normalize_name(a)
                    if (hn in k_home or k_home in hn) and (an in k_away or k_away in an):
                        matched_fuzzy += 1
                        break

        matched_ai = 0
        if GROQ_API_KEY:
            print(f"🤖 Matching Groq en cours...")
            matched_ai = await ai_rematch_all(bb, events)
            print(f"🤖 Matchings IA ajoutés: {matched_ai}")

        # 1. Construire les sélections candidates
        selections = build_selections(events, bb)
        print(f"🎯 Sélections candidates: {len(selections)}")

        # 2. Faire analyser chaque sélection par l'IA (le VETO)
        if AI_REVIEW_ENABLED and GROQ_API_KEY and selections:
            await ai_review_all(selections)

        # 3. Trier
        selections.sort(key=lambda s: s.score(), reverse=True)
        STATE["selections"] = selections

        # 4. Construire coupons (l'IA a déjà veté les REJECT)
        STATE["coupons"] = build_all_coupons(selections)

        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")

        ai_accept = sum(1 for s in selections if s.ai_verdict == "ACCEPT")
        ai_caution = sum(1 for s in selections if s.ai_verdict == "CAUTION")
        ai_reject = sum(1 for s in selections if s.ai_verdict == "REJECT")

        STATE["debug"] = {
            "bb_matches": len(bb),
            "odds_events": len(events),
            "today_events": len(today_events),
            "matched_fuzzy": matched_fuzzy,
            "matched_ai": matched_ai,
            "selections": len(selections),
            "ai_accept": ai_accept,
            "ai_caution": ai_caution,
            "ai_reject": ai_reject,
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Matchs du jour  : {len(today_events)}")
        print(f"Appariés fuzzy  : {matched_fuzzy}")
        print(f"Appariés IA     : {matched_ai}")
        print(f"Sélections      : {len(selections)}")
        print(f"  ✅ ACCEPT IA  : {ai_accept}")
        print(f"  ⚠️ CAUTION IA : {ai_caution}")
        print(f"  ❌ REJECT IA  : {ai_reject}")
        print(f"Coupons         : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} matchs, cote {c.combined_odds}")
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

        # Envoie l'analyse IA des rejets (transparence)
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
    print("✅ Bot prêt (BetBetter + The Odds API + Groq VETO).")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    await bootstrap()

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        daily_broadcast,
        CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ),
        id="daily_broadcast",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
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
        "ai_match_cache": len(AI_MATCH_CACHE),
        "ai_review_cache": len(AI_REVIEW_CACHE),
    }


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
