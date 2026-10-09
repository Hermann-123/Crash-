from __future__ import annotations

import asyncio
import json
import os
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
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse premium • Tous matchs du jour")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.03"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.50"))
MAX_SIMPLE_SEND = int(os.getenv("MAX_SIMPLE_SEND", "10"))
HORIZON_HOURS = int(os.getenv("HORIZON_HOURS", "36"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not ODDS_API_KEY:
    raise RuntimeError("ODDS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_BASE = "https://api.the-odds-api.com/v4"
SHARP_BOOK = "pinnacle"

SOCCER_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_efl_champ",
]


# =========================================================
# MODELS
# =========================================================
@dataclass
class Selection:
    match_id: str
    league: str
    home: str
    away: str
    kickoff: str
    market: str
    pick_label: str
    odds: float
    sharp_prob: float
    edge: float
    bookmaker: str
    reason: str

    def score(self) -> float:
        return self.edge + (self.sharp_prob - 0.5) * 0.1


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


def iso_to_local(iso_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None


def kickoff_local(iso_str: str) -> str:
    dt = iso_to_local(iso_str)
    return dt.strftime("%H:%M") if dt else "?"


def is_upcoming(iso_str: str, hours: int = HORIZON_HOURS) -> bool:
    dt = iso_to_local(iso_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    delta_h = (dt - now).total_seconds() / 3600
    return -1 <= delta_h <= hours


def devig(pairs: list[tuple[str, float]]) -> dict[str, float]:
    inv = [(n, 1.0 / o) for n, o in pairs if o and o > 1.01]
    s = sum(v for _, v in inv)
    if s <= 0:
        return {}
    return {n: v / s for n, v in inv}


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
# ODDS API
# =========================================================
async def fetch_odds_for_sport(client: httpx.AsyncClient, sport_key: str) -> list[dict]:
    url = f"{ODDS_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,totals,btts",
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


async def fetch_all_events() -> list[dict]:
    all_events = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for key in SOCCER_KEYS:
            events = await fetch_odds_for_sport(client, key)
            all_events.extend(events)
            await asyncio.sleep(0.3)
    print(f"📦 Total events: {len(all_events)}")
    return all_events


# =========================================================
# VALUE ENGINE
# =========================================================
def find_sharp_book(bookmakers: list[dict]) -> Optional[dict]:
    for b in bookmakers:
        if b.get("key") == SHARP_BOOK:
            return b
    return None


def extract_selections(event: dict) -> list[Selection]:
    books = event.get("bookmakers", []) or []
    sharp = find_sharp_book(books)
    if not sharp:
        return []

    home = event.get("home_team", "")
    away = event.get("away_team", "")
    league = event.get("sport_title", "")
    kickoff = event.get("commence_time", "")
    match_id = event.get("id", "")

    out: list[Selection] = []

    for market in sharp.get("markets", []):
        mk = market.get("key")
        outcomes = market.get("outcomes", []) or []

        if mk == "h2h":
            raw = [(o["name"], float(o["price"])) for o in outcomes if "price" in o]
        elif mk == "totals":
            raw = [(f"{o['name']} {o.get('point')}", float(o["price"])) for o in outcomes if "price" in o and "point" in o]
        elif mk == "btts":
            raw = [(o["name"], float(o["price"])) for o in outcomes if "price" in o]
        else:
            continue

        sharp_probs = devig(raw)
        if not sharp_probs:
            continue

        for name, prob in sharp_probs.items():
            best_odd = None
            best_book = None
            for b in books:
                if b.get("key") == SHARP_BOOK:
                    continue
                for m2 in b.get("markets", []) or []:
                    if m2.get("key") != mk:
                        continue
                    for o2 in m2.get("outcomes", []) or []:
                        if mk == "totals":
                            name2 = f"{o2.get('name')} {o2.get('point')}"
                        else:
                            name2 = o2.get("name")
                        if name2 != name:
                            continue
                        price = float(o2.get("price", 0))
                        if price > 1.01 and (best_odd is None or price > best_odd):
                            best_odd = price
                            best_book = b.get("title", b.get("key", "?"))

            if best_odd is None:
                continue

            edge = prob * best_odd - 1.0
            if edge < MIN_EDGE or prob < MIN_CONFIDENCE:
                continue

            if mk == "h2h":
                if name == home:
                    label = f"Victoire {home}"
                elif name == away:
                    label = f"Victoire {away}"
                else:
                    label = "Match nul"
                market_label = "1X2"
            elif mk == "totals":
                label = f"{name} buts"
                market_label = "Over/Under"
            elif mk == "btts":
                label = f"Les 2 marquent : {name}"
                market_label = "BTTS"
            else:
                label = name
                market_label = mk

            out.append(Selection(
                match_id=match_id,
                league=league,
                home=home,
                away=away,
                kickoff=kickoff,
                market=market_label,
                pick_label=label,
                odds=round(best_odd, 2),
                sharp_prob=round(prob, 4),
                edge=round(edge, 4),
                bookmaker=best_book or "?",
                reason=f"Pinnacle {prob*100:.1f}% • {best_book} @ {best_odd:.2f}",
            ))

    return out


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
    used_matches = set()
    used_leagues = set()
    combined_odds = 1.0
    combined_prob = 1.0

    for s in pool:
        if len(legs) >= max_legs:
            break
        if s.match_id in used_matches:
            continue
        if s.league in used_leagues:
            continue
        next_odds = combined_odds * s.odds
        if next_odds > max_odds and legs:
            continue
        legs.append(s)
        used_matches.add(s.match_id)
        used_leagues.add(s.league)
        combined_odds = next_odds
        combined_prob *= s.sharp_prob

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

    safe_pool = [s for s in selections if s.odds <= 2.20 and s.sharp_prob >= 0.55]
    c = build_coupon("Ticket Sécurisé", "Sélections les plus stables", safe_pool, 1.6, 2.8, 3)
    if c:
        coupons.append(c)

    bal_pool = [s for s in selections if s.market == "1X2" and 1.5 <= s.odds <= 3.5]
    c = build_coupon("Ticket Équilibré", "Bon compromis risque/gain", bal_pool, 3.0, 6.0, 4)
    if c:
        coupons.append(c)

    agg_pool = [s for s in selections if s.market in ("Over/Under", "BTTS") or s.odds >= 3.0]
    c = build_coupon("Ticket Agressif", "Risque plus fort, gain plus haut", agg_pool, 6.0, 25.0, 5)
    if c:
        coupons.append(c)

    val_pool = sorted(selections, key=lambda s: s.edge, reverse=True)
    c = build_coupon("Ticket Value", "Les meilleures opportunités de value", val_pool, 1.8, 12.0, 3)
    if c:
        coupons.append(c)

    return coupons


# =========================================================
# FORMAT
# =========================================================
def format_selection_line(s: Selection, idx: int) -> list[str]:
    lines = [
        f"<b>{idx}. {s.home} vs {s.away}</b>",
        f"🕒 {kickoff_local(s.kickoff)} • {s.league}",
        f"✅ <b>{s.pick_label}</b>  <i>({s.market})</i>",
        f"📊 Proba sharp : <b>{s.sharp_prob*100:.1f}%</b> • Cote <b>{s.odds}</b> ({s.bookmaker})",
        f"💎 Edge : <b>{s.edge*100:+.1f}%</b>",
        "",
    ]
    return lines


def format_simples(selections: list[Selection], limit: int = 10) -> str:
    if not selections:
        return (
            f"{premium_header()}\n\n"
            f"<b>Aucune value détectée</b>\n"
            f"Le marché est efficient aujourd’hui, on ne force pas."
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
        f"Events bruts : <b>{d.get('events_raw', 0)}</b>\n"
        f"Upcoming : <b>{d.get('upcoming', 0)}</b>\n"
        f"Sélections : <b>{d.get('selections', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>\n"
        f"API remaining : <b>{d.get('api_remaining', '?')}</b>\n"
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
        events = await fetch_all_events()

        upcoming = [e for e in events if is_upcoming(e.get("commence_time", ""))]
        print(f"🕐 Upcoming (fenêtre {HORIZON_HOURS}h): {len(upcoming)}")

        selections: list[Selection] = []
        for ev in upcoming:
            try:
                sels = extract_selections(ev)
                selections.extend(sels)
            except Exception as e:
                print(f"⚠️ extract crash: {e}")

        selections.sort(key=lambda s: s.score(), reverse=True)
        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        STATE["debug"] = {
            "events_raw": len(events),
            "upcoming": len(upcoming),
            "selections": len(selections),
            "coupons": len(STATE["coupons"]),
            "api_remaining": "check logs",
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Events     : {len(events)}")
        print(f"Upcoming   : {len(upcoming)}")
        print(f"Sélections : {len(selections)}")
        print(f"Coupons    : {len(STATE['coupons'])}")
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
    print("✅ Bot prêt.")


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


app = FastAPI(title="Odds Value Bot", lifespan=lifespan)


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
