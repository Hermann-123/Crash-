from __future__ import annotations

import asyncio
import csv
import difflib
import io
import json
import math
import os
import re
import unicodedata
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
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

RHO = float(os.getenv("RHO", "-0.05"))
MAX_GOALS = int(os.getenv("MAX_GOALS", "8"))
RECENT_DAYS = int(os.getenv("RECENT_DAYS", "500"))
DECAY_DAYS = float(os.getenv("DECAY_DAYS", "180"))
EV_THRESHOLD = float(os.getenv("EV_THRESHOLD", "0.03"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/wallstreet_ci_v10.json")
BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse premium • Matchs du jour")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
ENABLE_INLINE = os.getenv("ENABLE_INLINE", "1") == "1"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not ODDS_API_KEY:
    raise RuntimeError("ODDS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")


# =========================================================
# LIGUES
# =========================================================
@dataclass(frozen=True)
class League:
    code: str
    name: str
    odds_key: str


LEAGUES = [
    League("E0", "Premier League", "soccer_epl"),
    League("E1", "Championship", "soccer_efl_champ"),
    League("D1", "Bundesliga", "soccer_germany_bundesliga"),
    League("I1", "Serie A", "soccer_italy_serie_a"),
    League("SP1", "La Liga", "soccer_spain_la_liga"),
    League("F1", "Ligue 1", "soccer_france_ligue_one"),
    League("N1", "Eredivisie", "soccer_netherlands_eredivisie"),
    League("B1", "Pro League", "soccer_belgium_first_div"),
    League("P1", "Primeira Liga", "soccer_portugal_primeira_liga"),
    League("T1", "Süper Lig", "soccer_turkey_super_league"),
]

TEAM_ALIASES = {
    "manchester united": "man united",
    "manchester city": "man city",
    "nottingham forest": "nott m forest",
    "wolverhampton wanderers": "wolves",
    "wolverhampton": "wolves",
    "tottenham hotspur": "tottenham",
    "newcastle united": "newcastle",
    "west ham united": "west ham",
    "brighton and hove albion": "brighton",
    "brighton hove albion": "brighton",
    "leicester city": "leicester",
    "leeds united": "leeds",
    "sheffield united": "sheffield utd",
    "paris saint germain": "paris sg",
    "paris st germain": "paris sg",
    "borussia dortmund": "dortmund",
    "bayer leverkusen": "leverkusen",
    "borussia monchengladbach": "m gladbach",
    "eintracht frankfurt": "ein frankfurt",
    "atletico madrid": "ath madrid",
    "athletic bilbao": "ath bilbao",
    "real betis": "betis",
    "real sociedad": "sociedad",
    "sevilla fc": "sevilla",
    "celta vigo": "celta",
    "real mallorca": "mallorca",
    "deportivo alaves": "alaves",
    "rcd espanyol": "espanyol",
    "ac milan": "milan",
    "inter milan": "inter",
    "as roma": "roma",
    "ssc napoli": "napoli",
    "juventus fc": "juventus",
    "psv eindhoven": "psv",
    "feyenoord rotterdam": "feyenoord",
    "sparta rotterdam": "sparta",
    "pec zwolle": "zwolle",
    "nec nijmegen": "nijmegen",
    "fortuna sittard": "sittard",
    "heracles almelo": "heracles",
    "fc groningen": "groningen",
    "sporting cp": "sporting",
    "sporting lisbon": "sporting",
    "fc porto": "porto",
    "sl benfica": "benfica",
    "sc braga": "braga",
    "boavista fc": "boavista",
    "fc arouca": "arouca",
    "rio ave fc": "rio ave",
    "fc famalicao": "famalicao",
    "vitoria guimaraes": "guimaraes",
    "gil vicente fc": "gil vicente",
    "club brugge": "brugge",
    "royal antwerp": "antwerp",
    "istanbul basaksehir": "basaksehir",
    "gaziantep fk": "gaziantep",
    "real oviedo": "oviedo",
    "real sporting de gijon": "sp gijon",
    "real sporting club de gijon": "sp gijon",
    "deportivo la coruna": "la coruna",
    "fenerbahce sk": "fenerbahce",
    "besiktas jk": "besiktas",
    "galatasaray sk": "galatasaray",
    "trabzonspor as": "trabzonspor",
}


# =========================================================
# HELPERS
# =========================================================
STOP_WORDS = {"fc", "cf", "ac", "sc", "sv", "fk", "club", "de", "da", "cd", "ud", "sd", "as", "sk"}


@lru_cache(maxsize=4096)
def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return " ".join(x for x in s.split() if x not in STOP_WORDS).strip()


def parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def local_dt_from_iso(iso: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(NOTIFY_TZ))
    except Exception:
        return None


def kickoff_local(iso: str) -> str:
    dt = local_dt_from_iso(iso)
    return dt.strftime("%H:%M") if dt else "?"


def today_local_date():
    return datetime.now(ZoneInfo(NOTIFY_TZ)).date()


def today_pretty() -> str:
    months = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre",
        11: "novembre", 12: "décembre"
    }
    d = datetime.now(ZoneInfo(NOTIFY_TZ))
    return f"{d.day} {months[d.month]} {d.year}"


def safe_float(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x > 1.0 else None
    except Exception:
        return None


def event_key(league_code: str, home: str, away: str, kickoff_iso: str) -> str:
    dt = local_dt_from_iso(kickoff_iso)
    hh = dt.strftime("%Y%m%d%H%M") if dt else "nodate"
    return f"{league_code}|{normalize(home)}|{normalize(away)}|{hh}"


def find_team(api_name: str, teams: dict) -> Optional[str]:
    if not api_name or not teams:
        return None

    if api_name in teams:
        return api_name

    norm_api = normalize(api_name)

    for t in teams:
        if normalize(t) == norm_api:
            return t

    alias = TEAM_ALIASES.get(norm_api)
    if alias:
        alias_norm = normalize(alias)
        for t in teams:
            if normalize(t) == alias_norm:
                return t

    for a, b in TEAM_ALIASES.items():
        if normalize(b) == norm_api:
            for t in teams:
                nt = normalize(t)
                if nt == normalize(a) or nt == normalize(b):
                    return t

    api_tokens = set(norm_api.split())
    candidates = []
    for t in teams:
        nt = normalize(t)
        tt = set(nt.split())
        inter = len(api_tokens & tt)
        union = len(api_tokens | tt) or 1
        j = inter / union
        if j >= 0.5:
            candidates.append((j, len(nt), t))
    if candidates:
        candidates.sort(key=lambda x: (-x[0], x[1]))
        return candidates[0][2]

    partials = []
    for t in teams:
        nt = normalize(t)
        if norm_api in nt or nt in norm_api:
            partials.append((len(nt), t))
    if partials:
        partials.sort(key=lambda x: x[0])
        return partials[0][1]

    lookup = {normalize(t): t for t in teams}
    close = difflib.get_close_matches(norm_api, lookup.keys(), n=1, cutoff=0.72)
    if close:
        return lookup[close[0]]

    return None


# =========================================================
# STRUCTURES
# =========================================================
@dataclass
class Match:
    league: str
    date: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    close_home: Optional[float] = None
    close_draw: Optional[float] = None
    close_away: Optional[float] = None


@dataclass
class Prediction:
    lambda_home: float
    lambda_away: float
    p_home: float
    p_draw: float
    p_away: float
    top_scores: list = field(default_factory=list)


@dataclass
class Prono:
    id: str
    league: str
    league_code: str
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
    home_model: str = ""
    away_model: str = ""
    top_scores: list = field(default_factory=list)

    def pick_prob(self) -> float:
        return {"1": self.p_home, "X": self.p_draw, "2": self.p_away}[self.pick]


@dataclass
class Coupon:
    id: str
    name: str
    emoji: str
    subtitle: str
    legs: list[Prono]
    combined_odds: float
    combined_prob: float
    combined_ev: float


# =========================================================
# CSV / MODELS
# =========================================================
CSV_BASE = "https://www.football-data.co.uk/mmz4281"


def season_codes(n: int = 2) -> list[str]:
    now = datetime.now()
    start = now.year if now.month >= 7 else now.year - 1
    return [f"{str(start - i)[2:]}{str(start - i + 1)[2:]}" for i in range(n)]


def parse_csv(text: str, league: str) -> list[Match]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
        except Exception:
            continue
        if not row.get("HomeTeam") or not row.get("AwayTeam"):
            continue
        out.append(Match(
            league=league,
            date=row.get("Date", ""),
            home=row["HomeTeam"].strip(),
            away=row["AwayTeam"].strip(),
            home_goals=hg,
            away_goals=ag,
            close_home=safe_float(row.get("B365CH")),
            close_draw=safe_float(row.get("B365CD")),
            close_away=safe_float(row.get("B365CA")),
        ))
    return out


async def download_csv_models() -> tuple[dict, dict]:
    by_league = defaultdict(list)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        for lg in LEAGUES:
            for se in season_codes(2):
                url = f"{CSV_BASE}/{se}/{lg.code}.csv"
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        by_league[lg.code].extend(parse_csv(r.text, lg.code))
                except Exception:
                    pass

    models = {}
    for lg in LEAGUES:
        models[lg.code] = compute_model(by_league.get(lg.code, []))
        print(f"📘 {lg.name}: {len(models[lg.code]['teams'])} équipes")
    return models, by_league


def compute_model(matches: list[Match]) -> dict:
    dated = [(parse_date(m.date), m) for m in matches]
    dated = [(d, m) for d, m in dated if d is not None]
    if not dated:
        return {"teams": {}, "avg_home": 1.5, "avg_away": 1.2}

    dated.sort(key=lambda x: x[0])
    ref = dated[-1][0]

    stats = defaultdict(lambda: {
        "hs": 0.0, "hc": 0.0,
        "as_": 0.0, "ac": 0.0,
        "hp": 0.0, "ap": 0.0
    })
    sum_w = sum_h = sum_a = 0.0

    for d, m in dated:
        age = (ref - d).days
        if age > RECENT_DAYS:
            continue
        w = 0.5 ** (age / DECAY_DAYS)

        sh = stats[m.home]
        sh["hs"] += w * m.home_goals
        sh["hc"] += w * m.away_goals
        sh["hp"] += w

        sa = stats[m.away]
        sa["as_"] += w * m.away_goals
        sa["ac"] += w * m.home_goals
        sa["ap"] += w

        sum_w += w
        sum_h += w * m.home_goals
        sum_a += w * m.away_goals

    if sum_w == 0:
        return {"teams": {}, "avg_home": 1.5, "avg_away": 1.2}

    avg_home = sum_h / sum_w
    avg_away = sum_a / sum_w

    teams = {}
    for team, s in stats.items():
        if s["hp"] < 3 or s["ap"] < 3:
            continue
        teams[team] = {
            "att_home": (s["hs"] / s["hp"]) / avg_home,
            "def_home": (s["hc"] / s["hp"]) / avg_away,
            "att_away": (s["as_"] / s["ap"]) / avg_away,
            "def_away": (s["ac"] / s["ap"]) / avg_home,
        }
    return {"teams": teams, "avg_home": avg_home, "avg_away": avg_away}


def poisson(k: int, mu: float) -> float:
    return math.exp(-mu) * (mu ** k) / math.factorial(k)


def tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def predict(home: str, away: str, model: dict) -> Optional[Prediction]:
    teams = model["teams"]
    if home not in teams or away not in teams:
        return None

    h = teams[home]
    a = teams[away]

    lam = h["att_home"] * a["def_away"] * model["avg_home"]
    mu = a["att_away"] * h["def_home"] * model["avg_away"]

    total = ph = pd = pa = 0.0
    scores = []

    for x in range(MAX_GOALS + 1):
        for y in range(MAX_GOALS + 1):
            p = poisson(x, lam) * poisson(y, mu) * tau(x, y, lam, mu, RHO)
            total += p
            if x > y:
                ph += p
            elif x == y:
                pd += p
            else:
                pa += p
            scores.append((x, y, p))

    if total <= 0:
        return None

    scores.sort(key=lambda z: z[2], reverse=True)
    top = [{"score": f"{x}-{y}", "proba": round(p / total, 3)} for x, y, p in scores[:5]]

    return Prediction(
        lambda_home=round(lam, 2),
        lambda_away=round(mu, 2),
        p_home=ph / total,
        p_draw=pd / total,
        p_away=pa / total,
        top_scores=top,
    )


# =========================================================
# ODDS / ANALYSE
# =========================================================
def best_odds(event: dict) -> dict:
    prices = defaultdict(list)
    for bk in event.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for out in mkt.get("outcomes", []):
                price = out.get("price")
                if price:
                    prices[out["name"]].append(price)
    return {k: max(v) for k, v in prices.items()}


def kelly(prob: float, odds: float) -> float:
    b = odds - 1
    if b <= 0:
        return 0.0
    return max(0.0, ((b * prob - (1 - prob)) / b) * KELLY_FRACTION)


def analyze_event(league: League, event: dict, model: dict) -> Optional[Prono]:
    teams = model["teams"]
    api_home = event.get("home_team", "")
    api_away = event.get("away_team", "")

    home_model = find_team(api_home, teams)
    away_model = find_team(api_away, teams)
    if not home_model or not away_model:
        return None

    pred = predict(home_model, away_model, model)
    if not pred:
        return None

    probs = {"1": pred.p_home, "X": pred.p_draw, "2": pred.p_away}
    pick = max(probs, key=probs.get)

    odds_map = best_odds(event)
    selected_odds = {
        "1": odds_map.get(api_home),
        "X": odds_map.get("Draw"),
        "2": odds_map.get(api_away),
    }.get(pick)

    ev = None
    if selected_odds and selected_odds > 1:
        ev = probs[pick] * selected_odds - 1

    labels = {
        "1": f"Victoire {api_home}",
        "X": "Match nul",
        "2": f"Victoire {api_away}",
    }

    pid = event_key(league.code, api_home, api_away, event.get("commence_time", ""))

    return Prono(
        id=pid,
        league=league.name,
        league_code=league.code,
        home=api_home,
        away=api_away,
        kickoff=event.get("commence_time", ""),
        pick=pick,
        pick_label=labels[pick],
        odds=round(selected_odds, 2) if selected_odds else None,
        p_home=round(pred.p_home, 4),
        p_draw=round(pred.p_draw, 4),
        p_away=round(pred.p_away, 4),
        reliability=round(max(probs.values()), 4),
        ev=round(ev, 4) if ev is not None else None,
        home_model=home_model,
        away_model=away_model,
        top_scores=pred.top_scores,
    )


# =========================================================
# PREMIUM UI / COUPONS
# =========================================================
COUPON_CONFIGS = [
    ("TICKET SÉCURISÉ", "🟢", "Sélections les plus fiables", 1.8, 2.8, 3, "safe"),
    ("TICKET ÉQUILIBRÉ", "🟡", "Équilibre entre sécurité et gain", 3.0, 6.5, 4, "balanced"),
    ("TICKET AGRESSIF", "🔴", "Cote forte, risque élevé", 6.0, 20.0, 5, "aggressive"),
    ("TICKET VALUE", "💎", "Valeur potentielle détectée", 1.6, 12.0, 3, "value"),
]


def score_prono(p: Prono) -> float:
    return p.reliability + ((p.ev or 0.0) * 0.15)


def pool_for(pronos: list[Prono], kind: str) -> list[Prono]:
    priced = [p for p in pronos if p.odds and p.odds > 1.0]

    if kind == "safe":
        pool = [p for p in priced if p.reliability >= 0.52 and p.odds <= 1.9]
        pool.sort(key=score_prono, reverse=True)
    elif kind == "balanced":
        pool = [p for p in priced if 1.30 <= p.odds <= 2.60]
        pool.sort(key=score_prono, reverse=True)
    elif kind == "aggressive":
        pool = [p for p in priced if p.odds >= 1.75]
        pool.sort(key=lambda x: (x.odds, x.reliability), reverse=True)
    else:
        pool = [p for p in priced if (p.ev or -999) >= EV_THRESHOLD]
        pool.sort(key=lambda x: ((x.ev or 0), x.reliability), reverse=True)

    # anti-doublons strict
    uniq = {}
    for p in pool:
        uniq[p.id] = p
    return list(uniq.values())


def build_coupon(name: str, emoji: str, subtitle: str,
                 min_odds: float, max_odds: float, max_legs: int,
                 kind: str, pronos: list[Prono]) -> Optional[Coupon]:
    pool = pool_for(pronos, kind)
    if not pool:
        return None

    legs = []
    odds = 1.0
    prob = 1.0
    used = set()

    for p in pool:
        if len(legs) >= max_legs:
            break
        if p.id in used:
            continue
        new_odds = odds * p.odds
        if new_odds > max_odds and legs:
            continue
        legs.append(p)
        used.add(p.id)
        odds = new_odds
        prob *= p.pick_prob()
        if odds >= min_odds:
            break

    if not legs or odds < min_odds * 0.85:
        return None

    cid = f"{datetime.now(ZoneInfo(NOTIFY_TZ)).strftime('%Y%m%d')}-{normalize(name)}"
    return Coupon(
        id=cid,
        name=name,
        emoji=emoji,
        subtitle=subtitle,
        legs=legs,
        combined_odds=round(odds, 2),
        combined_prob=round(prob, 4),
        combined_ev=round(prob * odds - 1, 4),
    )


def top_score_line(p: Prono) -> str:
    if not p.top_scores:
        return ""
    best = p.top_scores[0]
    return f"🎯 Score probable : <b>{best['score']}</b> ({best['proba'] * 100:.1f}%)"


def premium_header() -> str:
    return (
        f"🏷️ <b>{BRAND_NAME}</b>\n"
        f"✨ {BRAND_TAGLINE}\n"
        f"📅 <b>{today_pretty()}</b>\n"
        f"🕓 Côte d’Ivoire • GMT+0"
    )


def format_coupon(c: Coupon) -> str:
    lines = [
        premium_header(),
        "",
        f"{c.emoji} <b>{c.name}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🎯 {c.subtitle}",
        f"💰 Cote totale : <b>{c.combined_odds}</b>",
        f"📈 Probabilité combinée : <b>{c.combined_prob * 100:.1f}%</b>",
        f"💎 EV estimée : <b>{c.combined_ev * 100:+.1f}%</b>",
        "",
    ]

    for i, p in enumerate(c.legs, 1):
        conf = p.reliability * 100
        lines.extend([
            f"<b>{i}. {p.home} vs {p.away}</b>",
            f"🕒 {kickoff_local(p.kickoff)} • {p.league}",
            f"✅ <b>{p.pick_label}</b>",
            f"📊 Confiance : <b>{conf:.0f}%</b> • Cote {p.odds}",
        ])
        if p.ev is not None:
            lines.append(f"💹 EV : <b>{p.ev * 100:+.1f}%</b>")
        tsl = top_score_line(p)
        if tsl:
            lines.append(tsl)
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


def format_summary(pronos_count: int, coupons_count: int) -> str:
    return (
        f"{premium_header()}\n\n"
        f"🔥 <b>TICKETS DU JOUR DISPONIBLES</b>\n"
        f"⚽ Matchs analysés : <b>{pronos_count}</b>\n"
        f"🎟️ Tickets générés : <b>{coupons_count}</b>\n\n"
        f"👇 Choisis ton ticket."
    )


def format_no_match() -> str:
    return (
        f"{premium_header()}\n\n"
        f"❌ <b>Aucun match exploitable aujourd’hui</b>\n"
        f"Le bot envoie uniquement les matchs du jour en heure de Côte d’Ivoire."
    )


def format_top_pronos(pronos: list[Prono]) -> str:
    if not pronos:
        return format_no_match()
    lines = [f"{premium_header()}", "", "🏆 <b>TOP PRONOS DU JOUR</b>", ""]
    for i, p in enumerate(pronos[:10], 1):
        lines.append(f"<b>{i}. {p.home} vs {p.away}</b>")
        lines.append(f"🕒 {kickoff_local(p.kickoff)} • {p.league}")
        lines.append(f"✅ {p.pick_label}")
        lines.append(f"📊 Confiance : <b>{p.reliability * 100:.0f}%</b> • Cote {p.odds or '?'}")
        if p.ev is not None:
            lines.append(f"💎 EV : <b>{p.ev * 100:+.1f}%</b>")
        tsl = top_score_line(p)
        if tsl:
            lines.append(tsl)
        lines.append("")
    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


def format_bilan(tracker: dict) -> str:
    coupons = tracker.get("coupons", [])
    if not coupons:
        return f"{premium_header()}\n\n📊 <b>BILAN</b>\n\nAucun historique pour le moment."

    resolved = [c for c in coupons if c.get("status") in ("win", "loss")]
    pending = [c for c in coupons if c.get("status") == "pending"]

    lines = [premium_header(), "", "📊 <b>BILAN PREMIUM</b>", ""]

    if resolved:
        by_name = defaultdict(lambda: {"bets": 0, "wins": 0, "returned": 0.0, "staked": 0.0})
        total_bets = total_wins = 0
        total_returned = total_staked = 0.0
        emoji_map = {
            "TICKET SÉCURISÉ": "🟢",
            "TICKET ÉQUILIBRÉ": "🟡",
            "TICKET AGRESSIF": "🔴",
            "TICKET VALUE": "💎",
        }

        for c in resolved:
            name = c["name"]
            by_name[name]["bets"] += 1
            by_name[name]["staked"] += 1
            total_bets += 1
            total_staked += 1
            if c["status"] == "win":
                by_name[name]["wins"] += 1
                by_name[name]["returned"] += c.get("combined_odds", 0.0)
                total_wins += 1
                total_returned += c.get("combined_odds", 0.0)

        for name in ["TICKET SÉCURISÉ", "TICKET ÉQUILIBRÉ", "TICKET AGRESSIF", "TICKET VALUE"]:
            b = by_name.get(name)
            if not b or b["bets"] == 0:
                continue
            wr = b["wins"] / b["bets"] * 100
            roi = ((b["returned"] - b["staked"]) / b["staked"]) * 100 if b["staked"] else 0
            lines.append(f"{emoji_map.get(name, '🎟️')} <b>{name}</b>")
            lines.append(f"• Paris : {b['bets']}")
            lines.append(f"• Réussite : {wr:.0f}%")
            lines.append(f"• ROI : <b>{roi:+.1f}%</b>")
            lines.append("")

        total_wr = total_wins / total_bets * 100 if total_bets else 0
        total_roi = ((total_returned - total_staked) / total_staked) * 100 if total_staked else 0
        lines.append("📈 <b>GLOBAL</b>")
        lines.append(f"• Paris : {total_bets}")
        lines.append(f"• Réussite : {total_wr:.0f}%")
        lines.append(f"• ROI : <b>{total_roi:+.1f}%</b>")
    else:
        lines.append("Aucun ticket encore résolu.")

    if pending:
        lines.append("")
        lines.append(f"⏳ En attente : {len(pending)} ticket(s)")
    return "\n".join(lines)


# =========================================================
# TRACKER
# =========================================================
def load_tracker() -> dict:
    try:
        with open(LOCAL_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if data else {"coupons": []}
    except Exception:
        return {"coupons": []}


def save_tracker(data: dict):
    try:
        with open(LOCAL_DB, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Sauvegarde tracker échouée : {e}")


def record_coupons(coupons: list[Coupon], date_str: str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}

    for c in coupons:
        if c.id in existing:
            continue
        tracker["coupons"].append({
            "id": c.id,
            "date": date_str,
            "name": c.name,
            "combined_odds": c.combined_odds,
            "status": "pending",
            "legs": [
                {
                    "id": leg.id,
                    "league_code": leg.league_code,
                    "home": leg.home,
                    "away": leg.away,
                    "pick": leg.pick,
                    "kickoff": leg.kickoff,
                    "status": "pending",
                }
                for leg in c.legs
            ],
        })

    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)


def leg_result_from_scores(home_score: int, away_score: int, pick: str) -> str:
    if home_score > away_score:
        res = "1"
    elif home_score == away_score:
        res = "X"
    else:
        res = "2"
    return "win" if res == pick else "loss"


async def fetch_scores_for_league(client: httpx.AsyncClient, league: League, days_from: int = 3) -> list[dict]:
    url = f"https://api.the-odds-api.com/v4/sports/{league.odds_key}/scores/"
    params = {
        "apiKey": ODDS_API_KEY,
        "daysFrom": days_from,
    }
    try:
        r = await client.get(url, params=params, timeout=20.0)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


async def resolve_pending():
    tracker = STATE["tracker"]
    pending = [c for c in tracker["coupons"] if c.get("status") == "pending"]
    if not pending:
        return 0

    resolved_count = 0
    async with httpx.AsyncClient() as client:
        scores_by_league = {}
        for lg in LEAGUES:
            scores_by_league[lg.code] = await fetch_scores_for_league(client, lg, days_from=3)
            await asyncio.sleep(0.25)

        score_index = {}
        for lg_code, events in scores_by_league.items():
            for ev in events:
                home = ev.get("home_team", "")
                away = ev.get("away_team", "")
                kickoff = ev.get("commence_time", "")
                completed = ev.get("completed", False)
                scores = ev.get("scores") or []
                if not completed or len(scores) < 2:
                    continue

                hscore = ascore = None
                for s in scores:
                    if s.get("name") == home:
                        hscore = int(s.get("score"))
                    elif s.get("name") == away:
                        ascore = int(s.get("score"))
                if hscore is None or ascore is None:
                    continue

                key = event_key(lg_code, home, away, kickoff)
                score_index[key] = (hscore, ascore)

        for coupon in pending:
            all_done = True
            all_win = True
            any_result = False

            for leg in coupon["legs"]:
                if leg["status"] in ("win", "loss"):
                    any_result = True
                    if leg["status"] == "loss":
                        all_win = False
                    continue

                res = score_index.get(leg["id"])
                if not res:
                    all_done = False
                    continue

                hscore, ascore = res
                leg_status = leg_result_from_scores(hscore, ascore, leg["pick"])
                leg["status"] = leg_status
                any_result = True
                if leg_status == "loss":
                    all_win = False

            if any_result and all_done:
                coupon["status"] = "win" if all_win else "loss"
                resolved_count += 1

    save_tracker(tracker)
    return resolved_count


# =========================================================
# TELEGRAM
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_SAFE = "🟢 SÉCURISÉ"
BTN_BAL = "🟡 ÉQUILIBRÉ"
BTN_AGG = "🔴 AGRESSIF"
BTN_VAL = "💎 VALUE"
BTN_BILAN = "📊 BILAN"
BTN_SCAN = "🔄 SCAN"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_AGG), KeyboardButton(text=BTN_VAL)],
        [KeyboardButton(text=BTN_BILAN), KeyboardButton(text=BTN_SCAN)],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    input_field_placeholder="Choisis un ticket…",
)

INLINE_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Sécurisé", callback_data="coupon:safe"),
            InlineKeyboardButton(text="🟡 Équilibré", callback_data="coupon:balanced"),
        ],
        [
            InlineKeyboardButton(text="🔴 Agressif", callback_data="coupon:aggressive"),
            InlineKeyboardButton(text="💎 Value", callback_data="coupon:value"),
        ],
        [
            InlineKeyboardButton(text="🏆 Top pronos", callback_data="view:top"),
            InlineKeyboardButton(text="📊 Bilan", callback_data="view:bilan"),
        ],
    ]
)

STATE = {
    "models": {},
    "history": {},
    "ready": False,
    "pronos": [],
    "coupons": [],
    "last_scan": None,
    "debug": {},
    "tracker": {"coupons": []},
}

TARGETS: list[int | str] = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if not raw:
        continue
    try:
        TARGETS.append(int(raw))
    except ValueError:
        TARGETS.append(raw)


async def safe_send(chat_id, text: str, inline: bool = False):
    try:
        kwargs = {"chat_id": chat_id, "text": text, "reply_markup": MAIN_KEYBOARD}
        if inline and ENABLE_INLINE:
            kwargs["reply_markup"] = INLINE_KEYBOARD
        await bot.send_message(**kwargs)
    except Exception as e:
        print(f"⚠️ Envoi Telegram échoué vers {chat_id}: {e}")


async def safe_answer(message: Message, text: str, inline: bool = False):
    try:
        if inline and ENABLE_INLINE:
            await message.answer(text, reply_markup=INLINE_KEYBOARD)
        else:
            await message.answer(text, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Réponse Telegram échouée : {e}")


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await safe_answer(
        message,
        f"👋 <b>Bienvenue sur {BRAND_NAME}</b>\n\n"
        f"✨ {BRAND_TAGLINE}\n\n"
        f"• Matchs du jour uniquement\n"
        f"• Heure Côte d’Ivoire\n"
        f"• Tickets intelligents\n"
        f"• Bilan automatique\n\n"
        f"👇 Choisis une option.",
        inline=True,
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await safe_answer(
        message,
        "❓ <b>AIDE</b>\n\n"
        "• /start — accueil premium\n"
        "• /scan — relancer le scan du jour\n"
        "• /today — top pronos du jour\n"
        "• /coupons — afficher tous les tickets\n"
        "• /bilan — bilan des tickets\n"
        "• /debug — état technique",
        inline=True,
    )


@dp.message(Command("scan"))
async def cmd_scan(message: Message):
    await safe_answer(message, "⏳ Scan premium du jour en cours...")
    await scan_today_only()
    await resolve_pending()
    if not STATE["coupons"]:
        await safe_answer(message, format_no_match(), inline=True)
        return
    await safe_answer(
        message,
        format_summary(len(STATE["pronos"]), len(STATE["coupons"])),
        inline=True,
    )


@dp.message(Command("today"))
async def cmd_today(message: Message):
    await safe_answer(message, format_top_pronos(STATE["pronos"]), inline=True)


@dp.message(Command("coupons"))
async def cmd_coupons(message: Message):
    if not STATE["coupons"]:
        await safe_answer(message, format_no_match(), inline=True)
        return
    for c in STATE["coupons"]:
        await safe_answer(message, format_coupon(c), inline=True)
        await asyncio.sleep(0.3)


@dp.message(Command("bilan"))
async def cmd_bilan(message: Message):
    await resolve_pending()
    await safe_answer(message, format_bilan(STATE["tracker"]), inline=True)


@dp.message(Command("debug"))
async def cmd_debug(message: Message):
    d = STATE["debug"]
    txt = (
        f"🛠️ <b>DEBUG</b>\n\n"
        f"Date locale : <b>{d.get('today_local', '?')}</b>\n"
        f"Events API : <b>{d.get('total_events', 0)}</b>\n"
        f"Hors jour : <b>{d.get('out_of_day', 0)}</b>\n"
        f"Non reconnus : <b>{d.get('unmatched', 0)}</b>\n"
        f"Rejetés : <b>{d.get('rejected', 0)}</b>\n"
        f"Retenus : <b>{d.get('kept', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )
    await safe_answer(message, txt)


async def send_coupon_by_name(message: Message, name: str):
    coupon = next((c for c in STATE["coupons"] if c.name == name), None)
    if not coupon:
        await safe_answer(message, format_no_match(), inline=True)
        return
    await safe_answer(message, format_coupon(coupon), inline=True)


@dp.message(F.text == BTN_SAFE)
async def btn_safe(message: Message):
    await send_coupon_by_name(message, "TICKET SÉCURISÉ")


@dp.message(F.text == BTN_BAL)
async def btn_bal(message: Message):
    await send_coupon_by_name(message, "TICKET ÉQUILIBRÉ")


@dp.message(F.text == BTN_AGG)
async def btn_agg(message: Message):
    await send_coupon_by_name(message, "TICKET AGRESSIF")


@dp.message(F.text == BTN_VAL)
async def btn_val(message: Message):
    await send_coupon_by_name(message, "TICKET VALUE")


@dp.message(F.text == BTN_BILAN)
async def btn_bilan(message: Message):
    await resolve_pending()
    await safe_answer(message, format_bilan(STATE["tracker"]), inline=True)


@dp.message(F.text == BTN_SCAN)
async def btn_scan(message: Message):
    await cmd_scan(message)


# =========================================================
# SCAN
# =========================================================
async def fetch_odds(client: httpx.AsyncClient, league: League) -> list[dict]:
    url = f"https://api.the-odds-api.com/v4/sports/{league.odds_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    for attempt in range(3):
        try:
            r = await client.get(url, params=params, timeout=20.0)
            if r.status_code == 200:
                data = r.json()
                print(f"🌐 {league.name}: {len(data)} event(s)")
                return data
            if r.status_code == 429:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            print(f"⚠️ Odds API {league.name}: HTTP {r.status_code}")
            return []
        except Exception as e:
            print(f"⚠️ Odds API {league.name}: {e}")
            await asyncio.sleep(1 + attempt)
    return []


async def scan_today_only():
    if not STATE["ready"]:
        print("⏳ modèles pas prêts")
        return

    local_today = today_local_date()
    pronos: list[Prono] = []

    total_events = 0
    out_of_day = 0
    unmatched = 0
    rejected = 0
    kept = 0

    seen = set()

    print(f"🔄 Scan jour CI | {local_today}")

    async with httpx.AsyncClient() as client:
        for lg in LEAGUES:
            model = STATE["models"].get(lg.code, {"teams": {}})
            if not model["teams"]:
                continue

            events = await fetch_odds(client, lg)
            total_events += len(events)

            lg_kept = 0
            for ev in events:
                local_dt = local_dt_from_iso(ev.get("commence_time", ""))
                if not local_dt:
                    rejected += 1
                    continue

                if local_dt.date() != local_today:
                    out_of_day += 1
                    continue

                p = analyze_event(lg, ev, model)
                if not p:
                    home_api = ev.get("home_team", "")
                    away_api = ev.get("away_team", "")
                    if home_api or away_api:
                        unmatched += 1
                        print(f"❌ Non reconnu: {home_api} vs {away_api}")
                    else:
                        rejected += 1
                    continue

                if p.id in seen:
                    continue
                seen.add(p.id)

                pronos.append(p)
                kept += 1
                lg_kept += 1

            print(f"   {lg.name}: {lg_kept} prono(s) du jour")
            await asyncio.sleep(0.35)

    pronos.sort(key=score_prono, reverse=True)
    STATE["pronos"] = pronos

    coupons = []
    built_ids = set()
    for cfg in COUPON_CONFIGS:
        c = build_coupon(*cfg, pronos)
        if c and c.id not in built_ids:
            coupons.append(c)
            built_ids.add(c.id)

    STATE["coupons"] = coupons
    STATE["last_scan"] = datetime.now(ZoneInfo(NOTIFY_TZ)).strftime("%d/%m %H:%M")
    STATE["debug"] = {
        "today_local": str(local_today),
        "total_events": total_events,
        "out_of_day": out_of_day,
        "unmatched": unmatched,
        "rejected": rejected,
        "kept": kept,
        "coupons": len(coupons),
    }

    print("──────── RÉSUMÉ JOUR ────────")
    print(f"Events API total : {total_events}")
    print(f"Hors du jour     : {out_of_day}")
    print(f"Non reconnus     : {unmatched}")
    print(f"Rejetés          : {rejected}")
    print(f"Retenus          : {kept}")
    print(f"Coupons          : {len(coupons)}")
    print("─────────────────────────────")


# =========================================================
# BROADCAST
# =========================================================
async def daily_broadcast():
    print("🔔 Diffusion quotidienne Ultra Premium...")
    await scan_today_only()
    await resolve_pending()

    header = format_no_match() if not STATE["coupons"] else format_summary(len(STATE["pronos"]), len(STATE["coupons"]))
    date_str = datetime.now(ZoneInfo(NOTIFY_TZ)).strftime("%Y-%m-%d")
    record_coupons(STATE["coupons"], date_str)

    for chat_id in TARGETS:
        await safe_send(chat_id, header, inline=True)
        await asyncio.sleep(0.4)

        if STATE["coupons"]:
            for c in STATE["coupons"]:
                await safe_send(chat_id, format_coupon(c), inline=True)
                await asyncio.sleep(0.5)

    print("✅ Diffusion terminée.")


# =========================================================
# BOOTSTRAP / APP
# =========================================================
async def bootstrap():
    print("📥 Chargement des modèles CSV...")
    models, history = await download_csv_models()
    STATE["models"] = models
    STATE["history"] = history
    STATE["tracker"] = load_tracker()
    STATE["ready"] = True
    print("✅ Bot Ultra Premium CI prêt.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    await bootstrap()

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(NOTIFY_TZ))
    scheduler.add_job(
        daily_broadcast,
        CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=ZoneInfo(NOTIFY_TZ)),
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


app = FastAPI(title="WallStreet CI Ultra Premium v10", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "version": "10.0",
        "brand": BRAND_NAME,
        "timezone": NOTIFY_TZ,
        "today_local": str(today_local_date()),
        "ready": STATE["ready"],
        "pronos": len(STATE["pronos"]),
        "coupons": [c.name for c in STATE["coupons"]],
        "last_scan": STATE["last_scan"],
    }


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
