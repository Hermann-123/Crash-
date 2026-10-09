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

try:
    import betbetter
except Exception:
    betbetter = None


# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
THERUNDOWN_KEY = os.getenv("THERUNDOWN_API_KEY", "")
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
HORIZON_HOURS = int(os.getenv("HORIZON_HOURS", "48"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not THERUNDOWN_KEY:
    raise RuntimeError("THERUNDOWN_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
RUNDOWN_BASE = "https://therundown.io/api/v2"

# TheRundown sport IDs (football = 4)
SPORT_ID = 4
# Markets : 1 = Moneyline (1X2), 2 = Spread, 3 = Total
MARKET_IDS = "1,2,3"
# Bookmakers : Pinnacle(3), Bet365(19), etc. (voir /api/v2/affiliates)
AFFILIATE_IDS = "3,19,23"

# BetBetter league slugs (football)
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
    source: str

    def score(self) -> float:
        return self.edge + (self.model_prob - 0.5) * 0.1


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


def iso_to_local(iso_str: str) -> Optional[datetime]:
    if not iso_str:
        return None
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
# THERUNDOWN — Cotes réelles
# =========================================================
async def fetch_rundown_events(client: httpx.AsyncClient) -> list[dict]:
    url = f"{RUNDOWN_BASE}/sports/{SPORT_ID}/events/{today_iso()}"
    params = {
        "market_ids": MARKET_IDS,
        "affiliate_ids": AFFILIATE_IDS,
        "main_line": "true",
        "hide_closed": "true",
    }
    headers = {"X-TheRundown-Key": THERUNDOWN_KEY}
    try:
        r = await client.get(url, params=params, headers=headers, timeout=30.0)
    except Exception as e:
        print(f"⚠️ TheRundown crash: {e}")
        return []

    remaining = r.headers.get("x-datapoints-remaining", "?")
    print(f"🌍 TheRundown -> {r.status_code} | datapoints_remaining={remaining}")

    if r.status_code >= 400:
        print(f"⚠️ Body: {r.text[:300]}")
        return []

    try:
        return r.json().get("events", []) or []
    except Exception:
        return []


def parse_rundown_event(ev: dict) -> dict[str, float]:
    """Retourne {outcome_label: best_odd} pour un event TheRundown."""
    out: dict[str, float] = {}
    teams = ev.get("teams", []) or []
    home = next((t.get("name") for t in teams if not t.get("is_away")), "")
    away = next((t.get("name") for t in teams if t.get("is_away")), "")

    for mkt in ev.get("markets", []) or []:
        mkt_id = mkt.get("market_id")
        name = str(mkt.get("name", "")).lower()

        for part in mkt.get("participants", []) or []:
            p_name = part.get("name", "")
            prices = part.get("line_prices", []) or []
            best = None
            for p in prices:
                price = p.get("price")
                if isinstance(price, (int, float)) and price > 1.01:
                    if best is None or price > best:
                        best = float(price)

            if best is None:
                continue

            if mkt_id == 1 or "moneyline" in name or "1x2" in name:
                if p_name == home:
                    out[f"1|{home}"] = best
                elif p_name == away:
                    out[f"2|{away}"] = best
                elif "draw" in p_name.lower() or p_name.lower() == "x":
                    out["X|Match nul"] = best
            elif mkt_id == 3 or "total" in name:
                point = part.get("line") or part.get("point") or ""
                label = f"O/U {point}|{p_name} {point} buts"
                out[label] = best

    return out


# =========================================================
# BETBETTER — Probabilités modèle
# =========================================================
def fetch_betbetter_probs() -> dict[str, dict]:
    """Retourne {clé_match: {selection: prob}} depuis BetBetter."""
    if betbetter is None:
        print("⚠️ betbetter non installé")
        return {}

    result: dict[str, dict] = {}
    for league in BETBETTER_LEAGUES:
        try:
            feed = betbetter.get_picks(league)
            for p in feed.get("picks", []) or []:
                game = p.get("game", "")
                selection = p.get("selection", "")
                prob_pct = p.get("modelProbabilityPct")
                if prob_pct is None:
                    continue
                key = f"{game}".lower().strip()
                result.setdefault(key, {})[selection.lower().strip()] = float(prob_pct) / 100.0
        except Exception as e:
            print(f"⚠️ betbetter {league}: {e}")
    return result


# =========================================================
# VALUE ENGINE
# =========================================================
def match_betbetter(bb: dict, home: str, away: str) -> dict[str, float]:
    """Cherche les probas BetBetter pour un match (match approximatif)."""
    h = home.lower().strip()
    a = away.lower().strip()
    for key, sels in bb.items():
        if h in key and a in key:
            return sels
        if h in key or a in key:
            return sels
    return {}


def build_selections(events: list[dict], bb: dict) -> list[Selection]:
    selections: list[Selection] = []

    for ev in events:
        try:
            teams = ev.get("teams", []) or []
            home = next((t.get("name") for t in teams if not t.get("is_away")), "")
            away = next((t.get("name") for t in teams if t.get("is_away")), "")
            kickoff = ev.get("event_date", "")
            event_id = ev.get("event_id", "")
            league = ev.get("sport_name") or ev.get("league_name") or "Football"

            if not home or not away:
                continue
            if not is_upcoming(kickoff):
                continue

            probs = match_betbetter(bb, home, away)
            odds_map = parse_rundown_event(ev)

            for key, odd in odds_map.items():
                market_code, pick_label = key.split("|", 1)

                prob = None
                if market_code == "1":
                    prob = probs.get(home.lower()) or probs.get("home")
                elif market_code == "2":
                    prob = probs.get(away.lower()) or probs.get("away")
                elif market_code == "X":
                    prob = probs.get("draw") or probs.get("x")
                else:
                    continue

                if prob is None or prob < MIN_PROB:
                    continue

                edge = prob * odd - 1.0
                if edge < MIN_EDGE:
                    continue

                if market_code == "1":
                    market_label = "1X2"
                elif market_code == "2":
                    market_label = "1X2"
                elif market_code == "X":
                    market_label = "1X2"
                else:
                    market_label = "Over/Under"

                selections.append(Selection(
                    event_id=event_id,
                    league=league,
                    home=home,
                    away=away,
                    kickoff=kickoff,
                    market=market_label,
                    pick_label=pick_label,
                    odds=round(odd, 2),
                    model_prob=round(prob, 4),
                    edge=round(edge, 4),
                    bookmaker="TheRundown",
                    source="BetBetter model",
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

    safe_pool = [s for s in selections if s.odds <= 2.20 and s.model_prob >= 0.55]
    c = build_coupon("Ticket Sécurisé", "Sélections les plus stables", safe_pool, 1.6, 2.8, 3)
    if c:
        coupons.append(c)

    bal_pool = [s for s in selections if s.market == "1X2" and 1.5 <= s.odds <= 3.5]
    c = build_coupon("Ticket Équilibré", "Bon compromis risque/gain", bal_pool, 3.0, 6.0, 4)
    if c:
        coupons.append(c)

    agg_pool = [s for s in selections if s.odds >= 2.5]
    c = build_coupon("Ticket Agressif", "Risque plus fort, gain plus haut", agg_pool, 5.0, 25.0, 5)
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
    return [
        f"<b>{idx}. {s.home} vs {s.away}</b>",
        f"🕒 {kickoff_local(s.kickoff)} • {s.league}",
        f"✅ <b>{s.pick_label}</b>  <i>({s.market})</i>",
        f"📊 Modèle : <b>{s.model_prob*100:.1f}%</b> • Cote <b>{s.odds}</b>",
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
        f"Events TheRundown : <b>{d.get('events_raw', 0)}</b>\n"
        f"Matchs BetBetter : <b>{d.get('bb_matches', 0)}</b>\n"
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
        bb = fetch_betbetter_probs()
        print(f"🧠 BetBetter matchs récupérés: {len(bb)}")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            events = await fetch_rundown_events(client)

        print(f"📦 Events TheRundown: {len(events)}")

        selections = build_selections(events, bb)
        selections.sort(key=lambda s: s.score(), reverse=True)

        STATE["selections"] = selections
        STATE["coupons"] = build_all_coupons(selections)
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        STATE["debug"] = {
            "events_raw": len(events),
            "bb_matches": len(bb),
            "selections": len(selections),
            "coupons": len(STATE["coupons"]),
        }

        print("──────── RÉSUMÉ ────────")
        print(f"Events TheRundown : {len(events)}")
        print(f"Matchs BetBetter  : {len(bb)}")
        print(f"Sélections        : {len(selections)}")
        print(f"Coupons           : {len(STATE['coupons'])}")
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
    print("✅ Bot prêt (TheRundown + BetBetter).")


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
