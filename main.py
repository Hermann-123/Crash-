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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not ODDS_API_KEY:
    raise RuntimeError("ODDS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_BASE = "https://api.the-odds-api.com/v4"

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

    def score(self) -> float:
        bonus = 0.05 if self.confidence.upper() == "STRONG" else 0.0
        return self.edge + bonus + (self.model_prob - 0.5) * 0.1


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
    """Match uniquement aujourd'hui (date locale) et pas encore commencé."""
    dt = parse_dt_safe(iso_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    if dt.date() != now.date():
        return False
    # Écarte les matchs déjà commencés (buffer 15 min)
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

    if market == "Spread":
        is_home = _match_name(sel_norm, home_norm)
        is_away = _match_name(sel_norm, away_norm)
        if not (is_home or is_away):
            return None, None
        try:
            target = float(line) if line is not None else None
        except (ValueError, TypeError):
            target = None

        def filt_exact(o):
            n = normalize_name(o.get("name", ""))
            if is_home and not _match_name(n, home_norm): return False
            if is_away and not _match_name(n, away_norm): return False
            if target is not None:
                try:
                    pt = float(o.get("point", -999))
                    return abs(pt - target) < 0.01 or abs(pt + target) < 0.01
                except (ValueError, TypeError):
                    return False
            return True

        r = find_best_odds(event, "spreads", filt_exact)
        if r[0] is not None:
            return r

        if target is not None and abs(target - (-0.5)) < 0.01:
            if is_home:
                return find_best_odds(event, "h2h", lambda o: _match_name(normalize_name(o.get("name", "")), home_norm))
            if is_away:
                return find_best_odds(event, "h2h", lambda o: _match_name(normalize_name(o.get("name", "")), away_norm))
        return None, None

    return None, None


def fuzzy_find(bb_data: dict, home: str, away: str) -> Optional[dict]:
    hn = normalize_name(home)
    an = normalize_name(away)
    for key, data in bb_data.items():
        try:
            k_away, k_home = key.split("|", 1)
        except ValueError:
            continue
        if (hn in k_home or k_home in hn) and (an in k_away or k_away in an):
            return data
    for key, data in bb_data.items():
        if hn in key and an in key:
            return data
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

            # ⚠️ FILTRE : uniquement aujourd'hui
            kickoff = ev.get("commence_time", "")
            if not is_today(kickoff):
                continue

            key = f"{normalize_name(away)}|{normalize_name(home)}"
            bb_match = bb_data.get(key) or fuzzy_find(bb_data, home, away)
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

                # Filtre marchés : uniquement 1X2 et Over 0.5 / 1.5
                market = pick.get("market", "")
                line = pick.get("line")

                if market == "Spread":
                    continue  # on bannit les handicaps

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
# COUPON BUILDER
# =========================================================
def build_coupon(name: str, subtitle: str, emoji: str, pool: list[Selection],
                 min_odds: float, max_odds: float, max_legs: int,
                 globally_used: set) -> Optional[Coupon]:
    pool = [s for s in pool if s.odds and s.odds > 1.01 and s.event_id not in globally_used]
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
    """Construit entre 2 et 5 coupons selon les sélections disponibles."""
    coupons: list[Coupon] = []
    globally_used: set = set()

    # 1. ULTRA SAFE — 1 sélection haute proba
    ultra_pool = [s for s in selections if s.model_prob >= 0.75 and s.odds <= 1.55]
    c = build_coupon("Ultra Safe", "1 sélection à très forte probabilité", "🛡",
                     ultra_pool, 1.30, 1.60, 1, globally_used)
    if c:
        coupons.append(c)

    # 2. SAFE — 2 sélections ~70% chacune → cote ~2
    safe_pool = [s for s in selections if s.model_prob >= 0.65 and s.odds <= 1.55]
    c = build_coupon("Safe", "2 sélections solides, cote ~2", "✅",
                     safe_pool, 1.80, 2.50, 2, globally_used)
    if c:
        coupons.append(c)

    # 3. ÉQUILIBRÉ — 3 sélections
    eq_pool = [s for s in selections if s.model_prob >= 0.60]
    c = build_coupon("Équilibré", "3 sélections, cote 3-5", "⚖️",
                     eq_pool, 3.0, 5.0, 3, globally_used)
    if c:
        coupons.append(c)

    # 4. VALUE — top edges ≥5%
    val_pool = sorted([s for s in selections if s.edge >= 0.05],
                      key=lambda s: s.edge, reverse=True)
    c = build_coupon("Value", "Meilleurs edges ≥5%", "💎",
                     val_pool, 1.60, 3.00, 2, globally_used)
    if c:
        coupons.append(c)

    return coupons


# =========================================================
# FORMAT
# =========================================================
def format_header() -> str:
    return (
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<i>{BRAND_TAGLINE}</i>\n"
        f"<i>📅 {today_pretty()} • 🇨🇮 Côte d’Ivoire</i>"
    )


def format_selection_block(s: Selection, idx: int) -> str:
    conf_tag = ""
    conf_upper = (s.confidence or "").upper()
    if conf_upper == "STRONG":
        conf_tag = "  🔥 <b>STRONG</b>"
    elif conf_upper == "LEAN":
        conf_tag = "  ⚡ LEAN"

    return "\n".join([
        f"<b>┌─ Sélection #{idx}</b>",
        f"<b>│ 🏟 {s.home}  vs  {s.away}</b>",
        f"│ 🕐 <b>{kickoff_local(s.kickoff)}</b>  •  {s.league}",
        f"│ 🎯 Pari : <b>{s.pick_label}</b>",
        f"│ 🏷 Marché : <i>{s.market}</i>{conf_tag}",
        f"│ 📈 Proba modèle : <b>{s.model_prob*100:.1f}%</b>",
        f"│ 💰 Cote : <b>{s.odds}</b>  ({s.bookmaker})",
        f"│ ⚡ Edge : <b>{s.edge*100:+.1f}%</b>  {edge_bar(s.edge)}",
        f"<b>└───────────────</b>",
    ])


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
        lines.append(format_selection_block(s, i))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)


def format_summary() -> str:
    return (
        f"{format_header()}\n\n"
        f"<b>✅ ANALYSE DU JOUR PRÊTE</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"🎯 Value bets : <b>{len(STATE['selections'])}</b>\n"
        f"🎫 Coupons disponibles : <b>{len(STATE['coupons'])}</b>\n"
        f"🔄 Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )


def format_coupons_ready() -> str:
    """Notification : types de coupons disponibles."""
    if not STATE["coupons"]:
        return (
            f"{format_header()}\n\n"
            f"<b>📭 Aucun coupon disponible aujourd'hui</b>\n"
            f"<i>Pas assez de value bets sur les matchs du jour.</i>"
        )

    lignes = []
    for c in STATE["coupons"]:
        n_legs = len(c.legs)
        prob = c.combined_prob * 100
        lignes.append(
            f"{c.emoji} <b>{c.name}</b>\n"
            f"   • {n_legs} sélection{'s' if n_legs > 1 else ''}\n"
            f"   • Cote <b>{c.combined_odds}</b> • Proba <b>{prob:.0f}%</b>"
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
        return f"{format_header()}\n\n<b>📊 Bilan</b>\n<i>Aucun historique pour le moment.</i>"
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
        f"Ce bot analyse chaque jour les matchs de football et détecte les "
        f"<b>value bets</b> en comparant :\n"
        f"  • Les probabilités d'un <b>modèle indépendant</b>\n"
        f"  • Les <b>cotes réelles</b> de 40+ bookmakers\n\n"
        f"<b>Utilise les boutons ci-dessous 👇</b>"
    )
    await safe_answer(message, txt)


@dp.message(Command("scan"))
async def scan_cmd(message: Message):
    await safe_answer(message, "⏳ Analyse en cours...")
    await scan()
    # 1. Notification des coupons dispo
    await safe_answer(message, format_coupons_ready())
    await asyncio.sleep(0.5)
    # 2. Résumé
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
        f"Appariés : <b>{d.get('matched', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
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

        # Compte les events du jour
        today_events = [e for e in events if is_today(e.get("commence_time", ""))]
        print(f"📅 Matchs aujourd'hui: {len(today_events)}")

        matched = 0
        for ev in today_events:
            h = ev.get("home_team", "")
            a = ev.get("away_team", "")
            key = f"{normalize_name(a)}|{normalize_name(h)}"
            if key in bb or fuzzy_find(bb, h, a):
                matched += 1

        selections = build_selections(events, bb)
        selections.sort(key=lambda s: s.score(), reverse=True)

        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        STATE["debug"] = {
            "bb_matches": len(bb),
            "odds_events": len(events),
            "today_events": len(today_events),
            "matched": matched,
            "selections": len(selections),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Matchs du jour : {len(today_events)}")
        print(f"Sélections     : {len(selections)}")
        print(f"Coupons        : {len(STATE['coupons'])}")
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
        # 1. Notification coupons prêts
        await safe_send(chat_id, format_coupons_ready())
        await asyncio.sleep(1)

        # 2. Récap
        await safe_send(chat_id, format_summary())
        await asyncio.sleep(0.5)

        # 3. Top value bets
        if STATE["selections"]:
            await safe_send(chat_id, format_simples(STATE["selections"], MAX_SIMPLE_SEND))
            await asyncio.sleep(0.5)

        # 4. Chaque coupon
        for c in STATE["coupons"]:
            await safe_send(chat_id, format_coupon(c))
            await asyncio.sleep(0.5)


# =========================================================
# APP
# =========================================================
async def bootstrap():
    STATE["tracker"] = load_tracker()
    STATE["ready"] = True
    print("✅ Bot prêt (BetBetter + The Odds API).")


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
    }


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
