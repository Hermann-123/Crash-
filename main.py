from __future__ import annotations

import asyncio
import json
import math
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
SPORTMONKS_API_KEY = os.getenv("SPORTMONKS_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "8"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/sportmonks_tracker.json")
BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse premium • Tous matchs du jour")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")

MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.46"))
MAX_SIMPLE_SEND = int(os.getenv("MAX_SIMPLE_SEND", "10"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not SPORTMONKS_API_KEY:
    raise RuntimeError("SPORTMONKS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
SPORTMONKS_BASE = "https://api.sportmonks.com/v3/football"


# =========================================================
# MODELS
# =========================================================
@dataclass
class Prono:
    fixture_id: int
    league: str
    home: str
    away: str
    kickoff: str
    pick: str
    pick_label: str
    odds: Optional[float]
    p_home: float
    p_draw: float
    p_away: float
    reliability: float
    ev: Optional[float] = None
    reasons: list[str] = field(default_factory=list)

    def pick_prob(self) -> float:
        return {"1": self.p_home, "X": self.p_draw, "2": self.p_away}[self.pick]


@dataclass
class Coupon:
    name: str
    subtitle: str
    legs: list[Prono]
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
    "pronos": [],
    "coupons": [],
    "last_scan": None,
    "debug": {},
    "tracker": {"coupons": []},
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


def today_local_date():
    return datetime.now(TZ).date()


def today_pretty():
    months = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre",
        11: "novembre", 12: "décembre"
    }
    d = datetime.now(TZ)
    return f"{d.day} {months[d.month]} {d.year}"


def local_dt_from_any(value) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(int(value), tz=ZoneInfo("UTC")).astimezone(TZ)

        raw = str(value).strip()
        if not raw:
            return None

        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), tz=ZoneInfo("UTC")).astimezone(TZ)

        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(TZ)
        except Exception:
            pass

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=ZoneInfo("UTC"))
                return dt.astimezone(TZ)
            except Exception:
                pass
    except Exception:
        return None
    return None


def kickoff_local(value) -> str:
    dt = local_dt_from_any(value)
    return dt.strftime("%H:%M") if dt else "?"


def safe_odd(v) -> Optional[float]:
    try:
        x = float(v)
        if x > 1.01:
            return x
    except Exception:
        pass
    return None


def normalize_probs(p1: float, px: float, p2: float) -> tuple[float, float, float]:
    s = p1 + px + p2
    if s <= 0:
        return 0.33, 0.34, 0.33
    return p1 / s, px / s, p2 / s


def premium_header() -> str:
    return (
        f"<b>{BRAND_NAME}</b>\n"
        f"{BRAND_TAGLINE}\n"
        f"{today_pretty()} • Côte d’Ivoire"
    )


def score_prono(p: Prono) -> float:
    return p.reliability + ((p.ev or 0.0) * 0.25)


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
# SPORTMONKS RAW ACCESS
# =========================================================
def sm_headers():
    return {"Accept": "application/json"}


async def sportmonks_get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    params = params or {}
    params["api_token"] = SPORTMONKS_API_KEY
    url = f"{SPORTMONKS_BASE}{path}"
    r = await client.get(url, params=params, headers=sm_headers(), timeout=40.0)
    print(f"🌍 GET {r.url} -> {r.status_code}")
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        print(f"📦 keys={list(data.keys())[:8]}")
    return data


def extract_participants(fixture: dict) -> tuple[str, str]:
    participants = fixture.get("participants", []) or fixture.get("participants_data", [])
    home = away = ""

    for p in participants:
        loc = (p.get("meta") or {}).get("location") or p.get("location")
        name = p.get("name") or p.get("participant_name") or ""
        if str(loc).lower() == "home":
            home = name
        elif str(loc).lower() == "away":
            away = name

    if not home or not away:
        names = [p.get("name") or p.get("participant_name") for p in participants if (p.get("name") or p.get("participant_name"))]
        if len(names) >= 2:
            home, away = names[0], names[1]
    return home, away


def extract_league_name(fixture: dict) -> str:
    league = fixture.get("league") or {}
    if isinstance(league, dict):
        return league.get("name") or fixture.get("league_name") or "Unknown League"
    return fixture.get("league_name") or "Unknown League"


def extract_kickoff(fixture: dict):
    direct = [
        fixture.get("starting_at"),
        fixture.get("startingAt"),
        fixture.get("date"),
        fixture.get("starting_at_timestamp"),
    ]
    for x in direct:
        if x:
            return x

    time_block = fixture.get("time")
    if isinstance(time_block, dict):
        for key in ["starting_at", "date_time", "timestamp"]:
            val = time_block.get(key)
            if val:
                return val
        sa = time_block.get("starting_at")
        if isinstance(sa, dict):
            for key in ["date_time", "timestamp", "datetime", "date"]:
                val = sa.get(key)
                if val:
                    return val
    return ""


def extract_scores(fixture: dict) -> tuple[Optional[int], Optional[int]]:
    scores = fixture.get("scores") or []
    home = away = None

    if isinstance(scores, list):
        for s in scores:
            desc = str(s.get("description") or "").lower()
            score_block = s.get("score") or {}
            goals = score_block.get("goals")
            participant = s.get("participant") or {}
            loc = (participant.get("meta") or {}).get("location") or participant.get("location")

            if goals is None:
                continue

            if str(loc).lower() == "home":
                home = goals
            elif str(loc).lower() == "away":
                away = goals

            if desc == "current":
                if str(loc).lower() == "home":
                    home = goals
                elif str(loc).lower() == "away":
                    away = goals

    return home, away


def extract_1x2_odds(fixture: dict) -> dict:
    result = {"1": None, "X": None, "2": None}

    odds_sources = []
    for key in ["odds", "bookmakers", "markets", "prices"]:
        val = fixture.get(key)
        if val:
            odds_sources.append(val)

    if isinstance(fixture.get("odds"), dict):
        data = fixture["odds"].get("data")
        if data:
            odds_sources.append(data)

    for block in odds_sources:
        if isinstance(block, dict):
            result["1"] = result["1"] or safe_odd(block.get("home") or block.get("1"))
            result["X"] = result["X"] or safe_odd(block.get("draw") or block.get("x") or block.get("X"))
            result["2"] = result["2"] or safe_odd(block.get("away") or block.get("2"))

    for block in odds_sources:
        if not isinstance(block, list):
            continue
        for item in block:
            try:
                name = str(item.get("name") or item.get("label") or item.get("market_name") or "").lower()
                outcome = str(item.get("outcome") or item.get("outcome_name") or "").lower()
                value = safe_odd(item.get("value") or item.get("odd") or item.get("price"))
                if value is None:
                    continue

                if name in ["home", "1", "team 1", "home win"] or outcome in ["home", "1"]:
                    result["1"] = result["1"] or value
                elif name in ["draw", "x"] or outcome in ["draw", "x"]:
                    result["X"] = result["X"] or value
                elif name in ["away", "2", "team 2", "away win"] or outcome in ["away", "2"]:
                    result["2"] = result["2"] or value
            except Exception:
                continue

    return result


async def fetch_all_today_fixtures() -> list[dict]:
    date_str = today_iso()
    all_items = []

    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
        attempts = [
            (f"/fixtures/date/{date_str}", {"include": "participants;league;odds;scores"}),
            ("/fixtures", {"include": "participants;league;odds;scores", "date": date_str}),
            ("/fixtures", {"filters": f"date:{date_str}", "include": "participants;league;odds;scores"}),
            ("/fixtures", {"include": "participants;league;odds;scores"}),
        ]

        for path, params in attempts:
            try:
                data = await sportmonks_get(client, path, params=params)
                items = data.get("data", []) or []
                print(f"📡 Tentative {path} => {len(items)}")
                if items:
                    all_items = items
                    break
            except Exception as e:
                print(f"⚠️ Tentative échouée {path}: {e}")

    print(f"📦 Fixtures brutes récupérées: {len(all_items)}")
    return all_items


# =========================================================
# GENERIC ENGINE
# =========================================================
def implied_probs_from_odds(odds: dict) -> tuple[float, float, float]:
    o1 = safe_odd(odds.get("1"))
    ox = safe_odd(odds.get("X"))
    o2 = safe_odd(odds.get("2"))

    p1 = (1 / o1) if o1 else 0.0
    px = (1 / ox) if ox else 0.0
    p2 = (1 / o2) if o2 else 0.0
    return normalize_probs(p1, px, p2)


def extract_recent_form_points(team_obj: dict) -> Optional[float]:
    # Placeholder souple : selon plan Sportmonks
    # Si la donnée n'existe pas, on renvoie None
    for key in ["form_points", "recent_points", "form"]:
        val = team_obj.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def generic_analyze_fixture(fixture: dict) -> Optional[Prono]:
    try:
        fixture_id = int(fixture.get("id") or 0)
        if not fixture_id:
            return None

        home, away = extract_participants(fixture)
        if not home or not away:
            return None

        kickoff = extract_kickoff(fixture)
        dt = local_dt_from_any(kickoff)
        if not dt or dt.date() != today_local_date():
            return None

        league = extract_league_name(fixture)
        odds = extract_1x2_odds(fixture)

        p1, px, p2 = implied_probs_from_odds(odds)

        # Si aucune cote exploitable, on saute
        if max(p1, px, p2) <= 0:
            return None

        reasons = []

        # Base: probabilités implicites
        s1, sx, s2 = p1, px, p2

        # Bonus domicile léger
        s1 += 0.03
        s2 -= 0.01
        reasons.append("avantage domicile")

        # Réduction légère du nul si extrêmes marqués
        favorite_gap = abs(s1 - s2)
        if favorite_gap > 0.12:
            sx -= 0.03
            reasons.append("écart de force sur les cotes")

        # Clamp
        s1 = max(0.01, s1)
        sx = max(0.01, sx)
        s2 = max(0.01, s2)
        s1, sx, s2 = normalize_probs(s1, sx, s2)

        probs = {"1": s1, "X": sx, "2": s2}
        pick = max(probs, key=probs.get)

        labels = {
            "1": f"Victoire {home}",
            "X": "Match nul",
            "2": f"Victoire {away}",
        }

        selected_odds = safe_odd(odds.get(pick))
        ev = None
        if selected_odds:
            ev = probs[pick] * selected_odds - 1

        reliability = probs[pick]

        if reliability < MIN_CONFIDENCE:
            return None

        return Prono(
            fixture_id=fixture_id,
            league=league,
            home=home,
            away=away,
            kickoff=str(kickoff),
            pick=pick,
            pick_label=labels[pick],
            odds=round(selected_odds, 2) if selected_odds else None,
            p_home=round(s1, 4),
            p_draw=round(sx, 4),
            p_away=round(s2, 4),
            reliability=round(reliability, 4),
            ev=round(ev, 4) if ev is not None else None,
            reasons=reasons,
        )
    except Exception as e:
        print(f"⚠️ generic_analyze_fixture crash: {e}")
        return None


def build_coupon(name: str, subtitle: str, pronos: list[Prono], min_odds: float, max_odds: float, max_legs: int) -> Optional[Coupon]:
    try:
        pool = [p for p in pronos if p.odds and p.odds > 1.01]
        if not pool:
            return None

        pool.sort(key=score_prono, reverse=True)

        legs = []
        used = set()
        odds = 1.0
        prob = 1.0

        for p in pool:
            if len(legs) >= max_legs:
                break
            if p.fixture_id in used:
                continue

            test_odds = odds * p.odds
            if test_odds > max_odds and legs:
                continue

            legs.append(p)
            used.add(p.fixture_id)
            odds = test_odds
            prob *= p.pick_prob()

            if odds >= min_odds:
                break

        if not legs:
            return None
        if odds < min_odds * 0.85:
            return None

        return Coupon(
            name=name,
            subtitle=subtitle,
            legs=legs,
            combined_odds=round(odds, 2),
            combined_prob=round(prob, 4),
            combined_ev=round(prob * odds - 1, 4),
        )
    except Exception as e:
        print(f"⚠️ build_coupon crash [{name}]: {e}")
        return None


# =========================================================
# FORMAT
# =========================================================
def format_simple_pronos(pronos: list[Prono], limit: int = 10) -> str:
    if not pronos:
        return (
            f"{premium_header()}\n\n"
            f"<b>Aucun prono exploitable aujourd’hui</b>\n"
            f"Le système n’a trouvé aucune sélection suffisamment fiable."
        )

    lines = [premium_header(), "", "<b>Sélections simples du jour</b>", ""]
    for i, p in enumerate(pronos[:limit], 1):
        lines.append(f"<b>{i}. {p.home} vs {p.away}</b>")
        lines.append(f"🕒 {kickoff_local(p.kickoff)} • {p.league}")
        lines.append(f"✅ <b>{p.pick_label}</b>")
        lines.append(f"📊 Confiance : <b>{p.reliability * 100:.0f}%</b>")
        if p.odds:
            lines.append(f"💰 Cote : <b>{p.odds}</b>")
        if p.ev is not None:
            lines.append(f"💎 EV : <b>{p.ev * 100:+.1f}%</b>")
        if p.reasons:
            lines.append(f"🧠 Signal : {', '.join(p.reasons[:2])}")
        lines.append("")
    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


def format_coupon(c: Coupon) -> str:
    lines = [
        premium_header(),
        "",
        f"<b>{c.name}</b>",
        c.subtitle,
        f"Cote totale : <b>{c.combined_odds}</b>",
        f"Probabilité combinée : <b>{c.combined_prob * 100:.1f}%</b>",
        f"EV estimée : <b>{c.combined_ev * 100:+.1f}%</b>",
        "",
    ]
    for i, p in enumerate(c.legs, 1):
        lines.append(f"<b>{i}. {p.home} vs {p.away}</b>")
        lines.append(f"🕒 {kickoff_local(p.kickoff)} • {p.league}")
        lines.append(f"✅ <b>{p.pick_label}</b>")
        line = f"📊 Confiance : <b>{p.reliability * 100:.0f}%</b>"
        if p.odds:
            line += f" • Cote {p.odds}"
        lines.append(line)
        if p.ev is not None:
            lines.append(f"💎 EV : <b>{p.ev * 100:+.1f}%</b>")
        lines.append("")
    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


def format_summary() -> str:
    return (
        f"{premium_header()}\n\n"
        f"<b>Analyse du jour prête</b>\n"
        f"Pronostics simples : <b>{len(STATE['pronos'])}</b>\n"
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
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Envoi impossible vers {chat_id}: {e}")


@dp.message(Command("start"))
async def start_cmd(message: Message):
    await safe_answer(message, f"Bienvenue sur <b>{BRAND_NAME}</b>\n\n{BRAND_TAGLINE}\nUtilise le clavier ci-dessous.")


@dp.message(Command("scan"))
async def scan_cmd(message: Message):
    await safe_answer(message, "⏳ Analyse en cours...")
    await scan_today_only()
    await safe_answer(message, format_summary())


@dp.message(Command("debug"))
async def debug_cmd(message: Message):
    d = STATE["debug"]
    txt = (
        f"<b>Debug</b>\n\n"
        f"Fixtures brutes : <b>{d.get('fixtures_raw', 0)}</b>\n"
        f"Analysées : <b>{d.get('analyzed', 0)}</b>\n"
        f"Retenues : <b>{d.get('kept', 0)}</b>\n"
        f"Hors date : <b>{d.get('out_of_day', 0)}</b>\n"
        f"Sans odds : <b>{d.get('no_odds', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )
    await safe_answer(message, txt)


@dp.message(F.text == BTN_SIMPLE)
async def btn_simple(message: Message):
    await safe_answer(message, format_simple_pronos(STATE["pronos"], MAX_SIMPLE_SEND))


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
async def scan_today_only():
    fixtures = await fetch_all_today_fixtures()

    pronos = []
    analyzed = 0
    kept = 0
    out_of_day = 0
    no_odds = 0

    for fx in fixtures:
        try:
            kickoff = extract_kickoff(fx)
            dt = local_dt_from_any(kickoff)
            if not dt or dt.date() != today_local_date():
                out_of_day += 1
                continue

            odds = extract_1x2_odds(fx)
            if not any([odds.get("1"), odds.get("X"), odds.get("2")]):
                no_odds += 1
                continue

            analyzed += 1
            p = generic_analyze_fixture(fx)
            if p:
                pronos.append(p)
                kept += 1
        except Exception as e:
            print(f"⚠️ scan fixture crash: {e}")

    pronos.sort(key=score_prono, reverse=True)
    STATE["pronos"] = pronos

    coupons = []
    configs = [
        ("Ticket Sécurisé", "Sélections les plus stables", 1.8, 2.8, 3),
        ("Ticket Équilibré", "Bon compromis risque/gain", 3.0, 6.0, 4),
        ("Ticket Agressif", "Risque plus fort, gain plus haut", 6.0, 20.0, 5),
        ("Ticket Value", "Sélections avec meilleure value", 1.8, 12.0, 3),
    ]
    for cfg in configs:
        c = build_coupon(*cfg, pronos)
        if c:
            coupons.append(c)

    STATE["coupons"] = coupons
    STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
    STATE["debug"] = {
        "fixtures_raw": len(fixtures),
        "analyzed": analyzed,
        "kept": kept,
        "out_of_day": out_of_day,
        "no_odds": no_odds,
        "coupons": len(coupons),
    }

    print("──────── RÉSUMÉ TOUS MATCHS ────────")
    print(f"Fixtures brutes : {len(fixtures)}")
    print(f"Analysées       : {analyzed}")
    print(f"Retenues        : {kept}")
    print(f"Hors date       : {out_of_day}")
    print(f"Sans odds       : {no_odds}")
    print(f"Coupons         : {len(coupons)}")
    print("────────────────────────────────────")


# =========================================================
# BROADCAST
# =========================================================
async def daily_broadcast():
    await scan_today_only()

    date_str = datetime.now(TZ).strftime("%Y-%m-%d")
    record_coupons(STATE["coupons"], date_str)

    for chat_id in TARGETS:
        await safe_send(chat_id, format_summary())
        await asyncio.sleep(0.3)

        if STATE["pronos"]:
            await safe_send(chat_id, format_simple_pronos(STATE["pronos"], MAX_SIMPLE_SEND))
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
    print("✅ Bot tous matchs prêt.")


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
    asyncio.create_task(scan_today_only())

    yield

    scheduler.shutdown(wait=False)
    bot_task.cancel()
    try:
        await bot.session.close()
    except Exception:
        pass


app = FastAPI(title="Sportmonks Generic Bot", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "ready": STATE["ready"],
        "today": str(today_local_date()),
        "pronos": len(STATE["pronos"]),
        "coupons": [c.name for c in STATE["coupons"]],
        "last_scan": STATE["last_scan"],
    }


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
