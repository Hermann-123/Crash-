from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
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

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.04"))
MIN_PROB = float(os.getenv("MIN_PROB", "0.45"))
MAX_SIMPLE_SEND = int(os.getenv("MAX_SIMPLE_SEND", "10"))
HORIZON_HOURS = int(os.getenv("HORIZON_HOURS", "72"))

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

    def score(self) -> float:
        bonus = 0.05 if self.confidence.upper() == "STRONG" else 0.0
        return self.edge + bonus + (self.model_prob - 0.5) * 0.1


@dataclass
class Coupon:
    name: str
    subtitle: str
    legs: list[Selection]
    combined_odds: float
    combined_prob: float
    combined_ev: float


# =========================================================
# GLOBALS
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_SAFE = "Sécurisé"
BTN_BAL = "Équilibré"
BTN_AGG = "Agressif"
BTN_VAL = "Value"
BTN_SIMPLE = "Simples"
BTN_SCAN = "Actualiser"
BTN_BILAN = "Bilan"

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


def premium_header() -> str:
    return (
        f"<b>{BRAND_NAME}</b>\n"
        f"{BRAND_TAGLINE}\n"
        f"{today_pretty()} • Côte d’Ivoire"
    )


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


def is_upcoming(iso_str: str, hours: int = HORIZON_HOURS) -> bool:
    dt = parse_dt_safe(iso_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    delta_h = (dt - now).total_seconds() / 3600
    return -1 <= delta_h <= hours


def parse_bb_game(game: str) -> tuple[str, str]:
    """'Away @ Home' -> (away, home)."""
    if not game:
        return "", ""
    if "@" in game:
        parts = game.split("@")
        return parts[0].strip(), parts[1].strip()
    return "", ""


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
# MATCHING & VALUE
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


def find_odds_for_pick(event: dict, pick: dict, home: str, away: str) -> tuple[Optional[float], Optional[str]]:
    market = (pick.get("market") or "").strip()
    selection = (pick.get("selection") or "").strip()
    line = pick.get("line")

    sel_norm = normalize_name(selection)
    home_norm = normalize_name(home)
    away_norm = normalize_name(away)

    if market == "Moneyline":
        if sel_norm and (sel_norm == home_norm or sel_norm in home_norm or home_norm in sel_norm):
            def filt(o):
                n = normalize_name(o.get("name", ""))
                return n == home_norm or n in home_norm or home_norm in n
        elif sel_norm and (sel_norm == away_norm or sel_norm in away_norm or away_norm in sel_norm):
            def filt(o):
                n = normalize_name(o.get("name", ""))
                return n == away_norm or n in away_norm or away_norm in n
        elif "draw" in sel_norm or "nul" in sel_norm or sel_norm == "x":
            def filt(o):
                n = normalize_name(o.get("name", ""))
                return "draw" in n or n == "x"
        else:
            return None, None
        return find_best_odds(event, "h2h", filt)

    if market == "Total":
        is_over = "over" in sel_norm or "plus" in sel_norm
        is_under = "under" in sel_norm or "moins" in sel_norm
        if not (is_over or is_under):
            return None, None
        try:
            target_point = float(line) if line is not None else None
        except (ValueError, TypeError):
            target_point = None

        def filt(o):
            n = normalize_name(o.get("name", ""))
            if is_over and n != "over":
                return False
            if is_under and n != "under":
                return False
            if target_point is not None:
                try:
                    pt = float(o.get("point", -1))
                    return abs(pt - target_point) < 0.01
                except (ValueError, TypeError):
                    return False
            return True

        return find_best_odds(event, "totals", filt)

    if market == "Spread":
        is_home = bool(sel_norm) and (sel_norm == home_norm or sel_norm in home_norm or home_norm in sel_norm)
        is_away = bool(sel_norm) and (sel_norm == away_norm or sel_norm in away_norm or away_norm in sel_norm)
        if not (is_home or is_away):
            return None, None

        try:
            target_point = float(line) if line is not None else None
        except (ValueError, TypeError):
            target_point = None

        def filt(o):
            n = normalize_name(o.get("name", ""))
            if is_home and not (n == home_norm or n in home_norm or home_norm in n):
                return False
            if is_away and not (n == away_norm or n in away_norm or away_norm in n):
                return False
            if target_point is not None:
                try:
                    pt = float(o.get("point", -999))
                    if abs(pt - target_point) < 0.01:
                        return True
                    if abs(pt + target_point) < 0.01:
                        return True
                    return False
                except (ValueError, TypeError):
                    return False
            return True

        return find_best_odds(event, "spreads", filt)

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

    for ev in odds_events:
        try:
            home = ev.get("home_team", "")
            away = ev.get("away_team", "")
            if not home or not away:
                continue

            key = f"{normalize_name(away)}|{normalize_name(home)}"
            bb_match = bb_data.get(key) or fuzzy_find(bb_data, home, away)
            if not bb_match:
                continue

            kickoff = ev.get("commence_time", "")
            if not is_upcoming(kickoff):
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

                odds, book = find_odds_for_pick(ev, pick, home, away)
                if odds is None:
                    continue

                edge = prob * odds - 1.0
                if edge < MIN_EDGE:
                    continue

                market = pick.get("market", "")
                selection = pick.get("selection", "")
                line = pick.get("line")
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
                    label = f"{selection} {line} buts" if line is not None else selection
                    market_label = "Over/Under"
                elif market == "Spread":
                    label = f"{selection} ({line:+g})" if isinstance(line, (int, float)) else selection
                    market_label = "Handicap"
                else:
                    label = selection
                    market_label = market

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
                ))
        except Exception as e:
            print(f"⚠️ build_selections crash: {e}")

    return selections


# =========================================================
# COUPON BUILDER
# =========================================================
def build_coupon(name: str, subtitle: str, pool: list[Selection],
                 min_odds: float, max_odds: float, max_legs: int) -> Optional[Coupon]:
    pool = [s for s in pool if s.odds and s.odds > 1.01]
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

    return Coupon(
        name=name,
        subtitle=subtitle,
        legs=legs,
        combined_odds=round(combined_odds, 2),
        combined_prob=round(combined_prob, 4),
        combined_ev=round(combined_prob * combined_odds - 1, 4),
    )


def build_all_coupons(selections: list[Selection]) -> list[Coupon]:
    coupons: list[Coupon] = []

    safe_pool = [s for s in selections if s.market == "1X2" and s.odds <= 2.20 and s.model_prob >= 0.55]
    c = build_coupon("Ticket Sécurisé", "Favoris à forte proba (1X2)", safe_pool, 1.6, 3.0, 3)
    if c:
        coupons.append(c)

    bal_pool = [s for s in selections if s.market in ("Over/Under", "Handicap") and 1.6 <= s.odds <= 2.5]
    c = build_coupon("Ticket Équilibré", "Value sur totaux et handicaps", bal_pool, 2.5, 6.0, 4)
    if c:
        coupons.append(c)

    agg_pool = [s for s in selections if s.odds >= 2.3]
    c = build_coupon("Ticket Agressif", "Cotes élevées, gain potentiel fort", agg_pool, 5.0, 30.0, 5)
    if c:
        coupons.append(c)

    val_pool = sorted(selections, key=lambda s: s.edge, reverse=True)
    c = build_coupon("Ticket Value", "Les meilleurs edges du jour", val_pool, 1.6, 12.0, 4)
    if c:
        coupons.append(c)

    return coupons


# =========================================================
# FORMAT
# =========================================================
def format_selection_line(s: Selection, idx: int) -> list[str]:
    conf_tag = ""
    if s.confidence and s.confidence.upper() == "STRONG":
        conf_tag = " 🔥"
    return [
        f"<b>{idx}. {s.home} vs {s.away}</b>",
        f"🕒 {kickoff_local(s.kickoff)} • {s.league}",
        f"✅ <b>{s.pick_label}</b>  <i>({s.market})</i>{conf_tag}",
        f"📊 Modèle : <b>{s.model_prob*100:.1f}%</b> • Cote <b>{s.odds}</b> ({s.bookmaker})",
        f"💎 Edge : <b>{s.edge*100:+.1f}%</b>",
        "",
    ]


def format_simples(selections: list[Selection], limit: int = 10) -> str:
    if not selections:
        return (
            f"{premium_header()}\n\n"
            f"<b>Aucune value détectée</b>\n"
            f"Le modèle et le marché sont alignés aujourd’hui."
        )
    lines = [premium_header(), "", "<b>Top value bets du jour</b>", ""]
    for i, s in enumerate(selections[:limit], 1):
        lines.extend(format_selection_line(s, i))
    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


def format_coupon(c: Coupon) -> str:
    lines = [
        premium_header(),
        "",
        f"<b>{c.name}</b>",
        f"<i>{c.subtitle}</i>",
        f"Cote totale : <b>{c.combined_odds}</b>",
        f"Proba combinée : <b>{c.combined_prob*100:.1f}%</b>",
        f"EV combinée : <b>{c.combined_ev*100:+.1f}%</b>",
        "",
    ]
    for i, s in enumerate(c.legs, 1):
        lines.extend(format_selection_line(s, i))
    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


def format_summary() -> str:
    return (
        f"{premium_header()}\n\n"
        f"<b>Analyse du jour prête</b>\n"
        f"Value bets : <b>{len(STATE['selections'])}</b>\n"
        f"Coupons : <b>{len(STATE['coupons'])}</b>"
    )


def format_bilan(tracker: dict) -> str:
    coupons = tracker.get("coupons", [])
    if not coupons:
        return f"{premium_header()}\n\n<b>Bilan</b>\nAucun historique pour le moment."
    return f"{premium_header()}\n\n<b>Bilan</b>\nTickets enregistrés : <b>{len(coupons)}</b>"


# =========================================================
# TELEGRAM
# =========================================================
async def safe_answer(message: Message, text: str):
    try:
        await message.answer(text, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Réponse Telegram échouée : {e}")


async def safe_send(chat_id, text: str):
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        print(f"⚠️ Envoi impossible vers {chat_id}: {e}")


@dp.message(Command("start"))
async def start_cmd(message: Message):
    await safe_answer(message, f"Bienvenue sur <b>{BRAND_NAME}</b>\n\n{BRAND_TAGLINE}\nUtilise le clavier ci-dessous.")


@dp.message(Command("scan"))
async def scan_cmd(message: Message):
    await safe_answer(message, "⏳ Analyse en cours...")
    await scan()
    await safe_answer(message, format_summary())


@dp.message(Command("debug"))
async def debug_cmd(message: Message):
    d = STATE["debug"]
    txt = (
        f"<b>Debug</b>\n\n"
        f"BetBetter matchs : <b>{d.get('bb_matches', 0)}</b>\n"
        f"Events Odds API : <b>{d.get('odds_events', 0)}</b>\n"
        f"Appariés : <b>{d.get('matched', 0)}</b>\n"
        f"Tentatives : <b>{d.get('attempts', 0)}</b>\n"
        f"Passe proba : <b>{d.get('passed_prob', 0)}</b>\n"
        f"Cotes trouvées : <b>{d.get('found_odds', 0)}</b>\n"
        f"Passe edge : <b>{d.get('passed_edge', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )
    await safe_answer(message, txt)


@dp.message(Command("bbsample"))
async def bbsample_cmd(message: Message):
    if betbetter is None:
        await safe_answer(message, "BetBetter non installé")
        return
    try:
        feed = betbetter.get_picks("soccer/epl")
        picks = feed.get("picks", []) or []
        if not picks:
            await safe_answer(message, "Aucun pick EPL")
            return
        sample = picks[0]
        keys = list(sample.keys())
        txt = "<b>Champs BetBetter</b>\n\n" + "\n".join(f"• <code>{k}</code>" for k in keys)
        txt += "\n\n<b>Exemple :</b>\n"
        for k in keys:
            v = str(sample[k])[:60]
            txt += f"<code>{k}</code> = {v}\n"
        await safe_answer(message, txt)
    except Exception as e:
        await safe_answer(message, f"Erreur: {e}")


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
    await send_coupon_by_name(message, "Ticket Sécurisé")


@dp.message(F.text == BTN_BAL)
async def btn_bal(message: Message):
    await send_coupon_by_name(message, "Ticket Équilibré")


@dp.message(F.text == BTN_AGG)
async def btn_agg(message: Message):
    await send_coupon_by_name(message, "Ticket Agressif")


@dp.message(F.text == BTN_VAL)
async def btn_val(message: Message):
    await send_coupon_by_name(message, "Ticket Value")


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

        market_stats = {"Moneyline": 0, "Total": 0, "Spread": 0, "Autre": 0}
        prob_above = 0
        total_picks = 0
        for match in bb.values():
            for p in match["picks"]:
                total_picks += 1
                mk = p.get("market", "")
                if mk in market_stats:
                    market_stats[mk] += 1
                else:
                    market_stats["Autre"] += 1
                pp = p.get("modelProbabilityPct")
                if pp is not None and float(pp) / 100.0 >= MIN_PROB:
                    prob_above += 1
        print(f"📊 Picks BetBetter total: {total_picks}")
        print(f"   Moneyline={market_stats['Moneyline']} Total={market_stats['Total']} Spread={market_stats['Spread']} Autre={market_stats['Autre']}")
        print(f"   Proba>={MIN_PROB}: {prob_above}")

        events = await fetch_all_odds_events()
        print(f"📦 Events Odds API: {len(events)}")

        matched = 0
        for ev in events:
            h = ev.get("home_team", "")
            a = ev.get("away_team", "")
            key = f"{normalize_name(a)}|{normalize_name(h)}"
            if key in bb or fuzzy_find(bb, h, a):
                matched += 1

        attempts = 0
        passed_prob = 0
        found_odds = 0
        passed_edge = 0

        for ev in events:
            h = ev.get("home_team", "")
            a = ev.get("away_team", "")
            key = f"{normalize_name(a)}|{normalize_name(h)}"
            bb_match = bb.get(key) or fuzzy_find(bb, h, a)
            if not bb_match:
                continue
            for pick in bb_match["picks"]:
                attempts += 1
                pp = pick.get("modelProbabilityPct")
                if pp is None:
                    continue
                prob = float(pp) / 100.0
                if prob < MIN_PROB:
                    continue
                passed_prob += 1
                odds, _ = find_odds_for_pick(ev, pick, h, a)
                if odds is None:
                    continue
                found_odds += 1
                if prob * odds - 1.0 >= MIN_EDGE:
                    passed_edge += 1

        print(f"🔎 Tentatives: {attempts}")
        print(f"   Passe proba: {passed_prob}")
        print(f"   Cote trouvée: {found_odds}")
        print(f"   Passe edge: {passed_edge}")

        selections = build_selections(events, bb)
        selections.sort(key=lambda s: s.score(), reverse=True)

        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        STATE["debug"] = {
            "bb_matches": len(bb),
            "odds_events": len(events),
            "matched": matched,
            "attempts": attempts,
            "passed_prob": passed_prob,
            "found_odds": found_odds,
            "passed_edge": passed_edge,
            "selections": len(selections),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"BetBetter : {len(bb)}")
        print(f"Events    : {len(events)}")
        print(f"Appariés  : {matched}")
        print(f"Sélections: {len(selections)}")
        print(f"Coupons   : {len(STATE['coupons'])}")
        print("────────────────────────")


# =========================================================
# BROADCAST
# =========================================================
async def daily_broadcast():
    await scan()

    date_str = datetime.now(TZ).strftime("%Y-%m-%d")
    record_coupons(STATE["coupons"], date_str)

    for chat_id in TARGETS:
        await safe_send(chat_id, format_summary())
        await asyncio.sleep(0.3)

        if STATE["selections"]:
            await safe_send(chat_id, format_simples(STATE["selections"], MAX_SIMPLE_SEND))
            await asyncio.sleep(0.3)

        for c in STATE["coupons"]:
            await safe_send(chat_id, format_coupon(c))
            await asyncio.sleep(0.3)


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
