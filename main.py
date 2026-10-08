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

RHO = float(os.getenv("RHO", "-0.05"))
MAX_GOALS = int(os.getenv("MAX_GOALS", "8"))
RECENT_DAYS = int(os.getenv("RECENT_DAYS", "500"))
DECAY_DAYS = float(os.getenv("DECAY_DAYS", "180"))
EV_THRESHOLD = float(os.getenv("EV_THRESHOLD", "0.03"))

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/wallstreet_ci_sportmonks.json")
BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse premium • Matchs du jour")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not SPORTMONKS_API_KEY:
    raise RuntimeError("SPORTMONKS_API_KEY manquant")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("TELEGRAM_ADMIN_ID ou TELEGRAM_CHANNEL manquant")

TZ = ZoneInfo(NOTIFY_TZ)
SPORTMONKS_BASE = "https://api.sportmonks.com/v3/football"


# =========================================================
# LEAGUES
# =========================================================
@dataclass(frozen=True)
class League:
    code: str
    name: str


LEAGUES = [
    League("E0", "Premier League"),
    League("E1", "Championship"),
    League("D1", "Bundesliga"),
    League("I1", "Serie A"),
    League("SP1", "La Liga"),
    League("F1", "Ligue 1"),
    League("N1", "Eredivisie"),
    League("B1", "Pro League"),
    League("P1", "Primeira Liga"),
    League("T1", "Süper Lig"),
]

LEAGUE_NAME_TO_CODE = {
    "Premier League": "E0",
    "Championship": "E1",
    "Bundesliga": "D1",
    "Serie A": "I1",
    "La Liga": "SP1",
    "Ligue 1": "F1",
    "Eredivisie": "N1",
    "Pro League": "B1",
    "Primeira Liga": "P1",
    "Süper Lig": "T1",
}

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
    if not iso:
        return None
    try:
        if isinstance(iso, (int, float)):
            return datetime.fromtimestamp(int(iso), tz=ZoneInfo("UTC")).astimezone(TZ)
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None


def kickoff_local(iso: str) -> str:
    dt = local_dt_from_iso(iso)
    return dt.strftime("%H:%M") if dt else "?"


def today_local_date():
    return datetime.now(TZ).date()


def today_iso():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def today_pretty():
    months = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre",
        11: "novembre", 12: "décembre"
    }
    d = datetime.now(TZ)
    return f"{d.day} {months[d.month]} {d.year}"


def safe_odd(v) -> Optional[float]:
    try:
        x = float(v)
        if x > 1.0:
            return x
    except Exception:
        pass
    return None


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
    home_model: str = ""
    away_model: str = ""
    top_scores: list = field(default_factory=list)

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
# CSV / MODEL
# =========================================================
CSV_BASE = "https://www.football-data.co.uk/mmz4281"


def season_codes(n: int = 2) -> list:
    now = datetime.now()
    start = now.year if now.month >= 7 else now.year - 1
    return [f"{str(start - i)[2:]}{str(start - i + 1)[2:]}" for i in range(n)]


def parse_csv(text: str, league: str) -> list:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
            home = row["HomeTeam"].strip()
            away = row["AwayTeam"].strip()
        except Exception:
            continue
        out.append(Match(league, row.get("Date", ""), home, away, hg, ag))
    return out


async def download_csv_models() -> dict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        all_matches = defaultdict(list)
        for lg in LEAGUES:
            for se in season_codes(2):
                url = f"{CSV_BASE}/{se}/{lg.code}.csv"
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        all_matches[lg.code].extend(parse_csv(r.text, lg.code))
                except Exception:
                    pass

    models = {}
    for lg in LEAGUES:
        models[lg.code] = compute_model(all_matches.get(lg.code, []))
        print(f"📘 {lg.name}: {len(models[lg.code]['teams'])} équipes")
    return models


def compute_model(matches: list, recent_days: int = RECENT_DAYS, decay_days: float = DECAY_DAYS) -> dict:
    dated = [(parse_date(m.date), m) for m in matches]
    dated = [(d, m) for d, m in dated if d is not None]
    if not dated:
        return {"teams": {}, "avg_home": 1.5, "avg_away": 1.2}

    dated.sort(key=lambda x: x[0])
    ref = dated[-1][0]

    stats = defaultdict(lambda: {"hs": 0.0, "hc": 0.0, "as_": 0.0, "ac": 0.0, "hp": 0.0, "ap": 0.0})
    sum_w = sum_h = sum_a = 0.0

    for d, m in dated:
        age = (ref - d).days
        if age > recent_days:
            continue
        w = 0.5 ** (age / decay_days)

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


def predict(home: str, away: str, model: dict, max_goals: int = MAX_GOALS) -> Optional[Prediction]:
    teams = model["teams"]
    if home not in teams or away not in teams:
        return None

    h = teams[home]
    a = teams[away]

    lam = h["att_home"] * a["def_away"] * model["avg_home"]
    mu = a["att_away"] * h["def_home"] * model["avg_away"]

    ph = pd = pa = total = 0.0
    scores = []

    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
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
# SPORTMONKS
# =========================================================
def sm_headers():
    return {"Accept": "application/json"}


async def sportmonks_get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    params = params or {}
    params["api_token"] = SPORTMONKS_API_KEY
    url = f"{SPORTMONKS_BASE}{path}"
    r = await client.get(url, params=params, headers=sm_headers(), timeout=30.0)
    r.raise_for_status()
    return r.json()


def extract_participants(fixture: dict) -> tuple[str, str]:
    participants = fixture.get("participants", []) or fixture.get("participants_data", [])
    home = away = ""

    for p in participants:
        meta = (p.get("meta") or {}).get("location") or p.get("location")
        name = p.get("name") or p.get("participant_name") or ""
        if str(meta).lower() == "home":
            home = name
        elif str(meta).lower() == "away":
            away = name

    if not home or not away:
        names = [p.get("name") or p.get("participant_name") for p in participants if (p.get("name") or p.get("participant_name"))]
        if len(names) >= 2:
            home, away = names[0], names[1]

    return home, away


def extract_league_name(fixture: dict) -> str:
    league = fixture.get("league") or fixture.get("league_id") or {}
    if isinstance(league, dict):
        return league.get("name") or fixture.get("league_name") or ""
    return fixture.get("league_name") or ""


def extract_kickoff_iso(fixture: dict) -> str:
    candidates = [
        fixture.get("starting_at"),
        fixture.get("startingAt"),
        fixture.get("date"),
        fixture.get("starting_at_timestamp"),
        ((fixture.get("time") or {}).get("starting_at") if isinstance(fixture.get("time"), dict) else None),
        (((fixture.get("time") or {}).get("starting_at") or {}).get("date_time") if isinstance((fixture.get("time") or {}).get("starting_at"), dict) else None),
        (((fixture.get("time") or {}).get("starting_at") or {}).get("timestamp") if isinstance((fixture.get("time") or {}).get("starting_at"), dict) else None),
    ]
    for c in candidates:
        if c:
            return c
    return ""


def extract_1x2_odds(fixture: dict) -> dict:
    result = {"1": None, "X": None, "2": None}

    odds_sources = []
    for key in ["odds", "bookmakers", "markets", "prices"]:
        value = fixture.get(key)
        if value:
            odds_sources.append(value)

    if isinstance(fixture.get("odds"), dict):
        data = fixture["odds"].get("data")
        if data:
            odds_sources.append(data)

    for block in odds_sources:
        if isinstance(block, dict):
            home = block.get("home") or block.get("1")
            draw = block.get("draw") or block.get("x") or block.get("X")
            away = block.get("away") or block.get("2")
            result["1"] = result["1"] or safe_odd(home)
            result["X"] = result["X"] or safe_odd(draw)
            result["2"] = result["2"] or safe_odd(away)

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


async def fetch_sportmonks_fixtures_today() -> list[dict]:
    date_str = today_iso()
    async with httpx.AsyncClient() as client:
        data = await sportmonks_get(
            client,
            f"/fixtures/date/{date_str}",
            params={"include": "participants;league;odds"},
        )
        return data.get("data", []) or []


# =========================================================
# ANALYSE
# =========================================================
def analyze_fixture(fixture: dict, models: dict) -> Optional[Prono]:
    try:
        fixture_id = int(fixture.get("id") or 0)
        if not fixture_id:
            return None

        league_name = extract_league_name(fixture)
        league_code = LEAGUE_NAME_TO_CODE.get(league_name)
        if not league_code:
            return None

        model = models.get(league_code, {"teams": {}})
        if not model["teams"]:
            return None

        home_api, away_api = extract_participants(fixture)
        if not home_api or not away_api:
            return None

        kickoff = extract_kickoff_iso(fixture)
        if not kickoff:
            return None

        local_dt = local_dt_from_iso(kickoff)
        if not local_dt:
            return None

        if local_dt.date() != today_local_date():
            return None

        home_model = find_team(home_api, model["teams"])
        away_model = find_team(away_api, model["teams"])
        if not home_model or not away_model:
            return None

        pred = predict(home_model, away_model, model)
        if not pred:
            return None

        probs = {"1": pred.p_home, "X": pred.p_draw, "2": pred.p_away}
        pick = max(probs, key=probs.get)

        odds_map = extract_1x2_odds(fixture)
        selected_odds = safe_odd(odds_map.get(pick))

        labels = {
            "1": f"Victoire {home_api}",
            "X": "Match nul",
            "2": f"Victoire {away_api}",
        }

        ev = None
        if selected_odds:
            ev = probs[pick] * selected_odds - 1

        return Prono(
            fixture_id=fixture_id,
            league=league_name,
            home=home_api,
            away=away_api,
            kickoff=str(kickoff),
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
    except Exception as e:
        print(f"⚠️ analyze_fixture crash: {e}")
        return None


def score_prono(p: Prono) -> float:
    return p.reliability + ((p.ev or 0.0) * 0.15)


def build_coupon(name: str, subtitle: str, pronos: list[Prono], min_odds: float, max_odds: float, max_legs: int) -> Optional[Coupon]:
    try:
        pool = []
        for p in pronos:
            if not hasattr(p, "odds"):
                continue
            odd = safe_odd(p.odds)
            if odd:
                p.odds = odd
                pool.append(p)

        if not pool:
            return None

        pool.sort(key=score_prono, reverse=True)

        legs = []
        odds = 1.0
        prob = 1.0
        used = set()

        for p in pool:
            if len(legs) >= max_legs:
                break
            if p.fixture_id in used:
                continue
            if not p.odds:
                continue

            new_odds = odds * p.odds
            if new_odds > max_odds and legs:
                continue

            legs.append(p)
            used.add(p.fixture_id)
            odds = new_odds
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
def premium_header() -> str:
    return (
        f"<b>{BRAND_NAME}</b>\n"
        f"{BRAND_TAGLINE}\n"
        f"{today_pretty()} • Côte d’Ivoire"
    )


def top_score_line(p: Prono) -> str:
    if not p.top_scores:
        return ""
    best = p.top_scores[0]
    return f"🎯 Score probable : <b>{best['score']}</b> ({best['proba'] * 100:.1f}%)"


def format_coupon(c: Coupon) -> str:
    lines = [
        premium_header(),
        "",
        f"<b>{c.name}</b>",
        f"{c.subtitle}",
        f"Cote totale : <b>{c.combined_odds}</b>",
        f"Probabilité combinée : <b>{c.combined_prob * 100:.1f}%</b>",
    ]
    if c.combined_ev is not None:
        lines.append(f"EV estimée : <b>{c.combined_ev * 100:+.1f}%</b>")
    lines.append("")

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
        tsl = top_score_line(p)
        if tsl:
            lines.append(tsl)
        lines.append("")

    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


def format_summary(pronos_count: int, coupons_count: int) -> str:
    return (
        f"{premium_header()}\n\n"
        f"<b>Tickets du jour disponibles</b>\n"
        f"Matchs analysés : <b>{pronos_count}</b>\n"
        f"Tickets générés : <b>{coupons_count}</b>"
    )


def format_no_match() -> str:
    return (
        f"{premium_header()}\n\n"
        f"<b>Aucun match exploitable aujourd’hui</b>\n"
        f"Le système n’a retenu aucune sélection fiable pour le moment."
    )


def format_top_pronos(pronos: list[Prono]) -> str:
    if not pronos:
        return format_no_match()

    lines = [premium_header(), "", "<b>Top pronos du jour</b>", ""]
    for i, p in enumerate(pronos[:10], 1):
        lines.append(f"<b>{i}. {p.home} vs {p.away}</b>")
        lines.append(f"🕒 {kickoff_local(p.kickoff)} • {p.league}")
        lines.append(f"✅ {p.pick_label}")
        line = f"📊 Confiance : <b>{p.reliability * 100:.0f}%</b>"
        if p.odds:
            line += f" • Cote {p.odds}"
        lines.append(line)
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
        return f"{premium_header()}\n\n<b>Bilan</b>\nAucun historique pour le moment."

    resolved = [c for c in coupons if c.get("status") in ("win", "loss")]
    pending = [c for c in coupons if c.get("status") == "pending"]

    if not resolved:
        return f"{premium_header()}\n\n<b>Bilan</b>\n⏳ {len(pending)} ticket(s) en attente."

    by_name = defaultdict(lambda: {"bets": 0, "wins": 0, "returned": 0.0, "staked": 0.0})
    total_bets = total_wins = 0
    total_returned = total_staked = 0.0

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

    lines = [premium_header(), "", "<b>Bilan</b>", ""]
    for name in ["Ticket Sécurisé", "Ticket Équilibré", "Ticket Agressif", "Ticket Value"]:
        b = by_name.get(name)
        if not b or b["bets"] == 0:
            continue
        wr = b["wins"] / b["bets"] * 100
        roi = ((b["returned"] - b["staked"]) / b["staked"]) * 100 if b["staked"] else 0
        lines.append(f"<b>{name}</b>")
        lines.append(f"• Paris : {b['bets']}")
        lines.append(f"• Réussite : {wr:.0f}%")
        lines.append(f"• ROI : <b>{roi:+.1f}%</b>")
        lines.append("")

    total_wr = total_wins / total_bets * 100 if total_bets else 0
    total_roi = ((total_returned - total_staked) / total_staked) * 100 if total_staked else 0
    lines.append("<b>Global</b>")
    lines.append(f"• Paris : {total_bets}")
    lines.append(f"• Réussite : {total_wr:.0f}%")
    lines.append(f"• ROI : <b>{total_roi:+.1f}%</b>")
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
        cid = f"{date_str}-{normalize(c.name)}"
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
# TELEGRAM
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_SAFE = "Sécurisé"
BTN_BAL = "Équilibré"
BTN_AGG = "Agressif"
BTN_VAL = "Value"
BTN_BILAN = "Bilan"
BTN_SCAN = "Actualiser"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BAL)],
        [KeyboardButton(text=BTN_AGG), KeyboardButton(text=BTN_VAL)],
        [KeyboardButton(text=BTN_BILAN), KeyboardButton(text=BTN_SCAN)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choisis un ticket…",
)

STATE = {
    "models": {},
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


async def safe_answer(message: Message, text: str):
    try:
        await message.answer(text, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Réponse Telegram échouée : {e}")


async def safe_send(chat_id, text: str):
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Envoi Telegram échoué vers {chat_id}: {e}")


@dp.message(Command("start"))
async def start_cmd(message: Message):
    await safe_answer(
        message,
        f"Bienvenue sur <b>{BRAND_NAME}</b>\n\n"
        f"{BRAND_TAGLINE}\n"
        f"Fuseau : Côte d’Ivoire\n\n"
        f"Utilise le clavier ci-dessous."
    )


@dp.message(Command("scan"))
async def scan_cmd(message: Message):
    await safe_answer(message, "⏳ Analyse en cours...")
    await scan_today_only()
    if not STATE["coupons"]:
        await safe_answer(message, format_no_match())
        return
    await safe_answer(message, format_summary(len(STATE["pronos"]), len(STATE["coupons"])))


@dp.message(Command("today"))
async def today_cmd(message: Message):
    await safe_answer(message, format_top_pronos(STATE["pronos"]))


@dp.message(Command("bilan"))
async def bilan_cmd(message: Message):
    await safe_answer(message, format_bilan(STATE["tracker"]))


@dp.message(Command("debug"))
async def debug_cmd(message: Message):
    d = STATE["debug"]
    txt = (
        f"<b>Debug</b>\n\n"
        f"Date locale : <b>{d.get('today_local', '?')}</b>\n"
        f"Fixtures total : <b>{d.get('fixtures_total', 0)}</b>\n"
        f"Retenus : <b>{d.get('kept', 0)}</b>\n"
        f"Rejetés : <b>{d.get('rejected', 0)}</b>\n"
        f"Sans cotes : <b>{d.get('without_odds', 0)}</b>\n"
        f"Sans ligue compatible : <b>{d.get('unknown_league', 0)}</b>\n"
        f"Sans équipe reconnue : <b>{d.get('unmatched_teams', 0)}</b>\n"
        f"Coupons : <b>{d.get('coupons', 0)}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>"
    )
    await safe_answer(message, txt)


async def send_coupon_by_name(message: Message, name: str):
    coupon = next((c for c in STATE["coupons"] if c.name == name), None)
    if not coupon:
        await safe_answer(message, format_no_match())
        return
    await safe_answer(message, format_coupon(coupon))


@dp.message(F.text == BTN_SAFE)
async def button_safe(message: Message):
    await send_coupon_by_name(message, "Ticket Sécurisé")


@dp.message(F.text == BTN_BAL)
async def button_bal(message: Message):
    await send_coupon_by_name(message, "Ticket Équilibré")


@dp.message(F.text == BTN_AGG)
async def button_agg(message: Message):
    await send_coupon_by_name(message, "Ticket Agressif")


@dp.message(F.text == BTN_VAL)
async def button_val(message: Message):
    await send_coupon_by_name(message, "Ticket Value")


@dp.message(F.text == BTN_BILAN)
async def button_bilan(message: Message):
    await safe_answer(message, format_bilan(STATE["tracker"]))


@dp.message(F.text == BTN_SCAN)
async def button_scan(message: Message):
    await scan_cmd(message)


# =========================================================
# SCAN
# =========================================================
async def scan_today_only():
    if not STATE["ready"]:
        print("⏳ modèles pas prêts")
        return

    fixtures = []
    try:
        fixtures = await fetch_sportmonks_fixtures_today()
    except Exception as e:
        print(f"⚠️ fetch Sportmonks échoué: {e}")
        STATE["debug"] = {
            "today_local": str(today_local_date()),
            "fixtures_total": 0,
            "kept": 0,
            "rejected": 0,
            "without_odds": 0,
            "unknown_league": 0,
            "unmatched_teams": 0,
            "coupons": 0,
            "error": str(e),
        }
        return

    print(f"🌐 Sportmonks fixtures du jour: {len(fixtures)}")

    pronos = []
    rejected = 0
    kept = 0
    without_odds = 0
    unknown_league = 0
    unmatched_teams = 0
    seen = set()

    for fx in fixtures:
        try:
            league_name = extract_league_name(fx)
            if league_name not in LEAGUE_NAME_TO_CODE:
                unknown_league += 1
                continue

            home_api, away_api = extract_participants(fx)
            league_code = LEAGUE_NAME_TO_CODE[league_name]
            model = STATE["models"].get(league_code, {"teams": {}})

            if not home_api or not away_api:
                rejected += 1
                continue

            if not find_team(home_api, model["teams"]) or not find_team(away_api, model["teams"]):
                unmatched_teams += 1
                continue

            p = analyze_fixture(fx, STATE["models"])
            if not p:
                rejected += 1
                continue

            if p.fixture_id in seen:
                continue
            seen.add(p.fixture_id)

            if not p.odds:
                without_odds += 1

            pronos.append(p)
            kept += 1
        except Exception as e:
            print(f"⚠️ analyse fixture échouée: {e}")
            rejected += 1

    pronos.sort(key=score_prono, reverse=True)
    STATE["pronos"] = pronos

    coupons = []
    configs = [
        ("Ticket Sécurisé", "Sélections les plus fiables", 1.8, 2.8, 3),
        ("Ticket Équilibré", "Équilibre entre sécurité et gain", 3.0, 6.0, 4),
        ("Ticket Agressif", "Cote plus élevée, risque plus fort", 6.0, 20.0, 5),
        ("Ticket Value", "Opportunités de valeur", 1.6, 12.0, 3),
    ]

    for cfg in configs:
        try:
            c = build_coupon(*cfg, pronos)
            if c:
                coupons.append(c)
        except Exception as e:
            print(f"⚠️ coupon config crash {cfg[0]}: {e}")

    STATE["coupons"] = coupons
    STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
    STATE["debug"] = {
        "today_local": str(today_local_date()),
        "fixtures_total": len(fixtures),
        "kept": kept,
        "rejected": rejected,
        "without_odds": without_odds,
        "unknown_league": unknown_league,
        "unmatched_teams": unmatched_teams,
        "coupons": len(coupons),
    }

    print("──────── RÉSUMÉ SPORTMONKS ────────")
    print(f"Fixtures total         : {len(fixtures)}")
    print(f"Retenus                : {kept}")
    print(f"Rejetés                : {rejected}")
    print(f"Sans cotes             : {without_odds}")
    print(f"Ligues non compatibles : {unknown_league}")
    print(f"Équipes non reconnues  : {unmatched_teams}")
    print(f"Coupons                : {len(coupons)}")
    print("───────────────────────────────────")


# =========================================================
# BROADCAST
# =========================================================
async def daily_broadcast():
    await scan_today_only()

    text = format_no_match() if not STATE["coupons"] else format_summary(len(STATE["pronos"]), len(STATE["coupons"]))
    date_str = datetime.now(TZ).strftime("%Y-%m-%d")
    record_coupons(STATE["coupons"], date_str)

    for chat_id in TARGETS:
        await safe_send(chat_id, text)
        await asyncio.sleep(0.3)
        for c in STATE["coupons"]:
            await safe_send(chat_id, format_coupon(c))
            await asyncio.sleep(0.3)


# =========================================================
# APP
# =========================================================
async def bootstrap():
    print("📥 Chargement des modèles CSV...")
    STATE["models"] = await download_csv_models()
    STATE["tracker"] = load_tracker()
    STATE["ready"] = True
    print("✅ Bot Sportmonks prêt.")


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


app = FastAPI(title="Sportmonks Premium Bot", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "provider": "Sportmonks",
        "timezone": NOTIFY_TZ,
        "ready": STATE["ready"],
        "today_local": str(today_local_date()),
        "pronos": len(STATE["pronos"]),
        "coupons": [c.name for c in STATE["coupons"]],
        "last_scan": STATE["last_scan"],
    }


@app.get("/debug")
async def debug_http():
    return STATE["debug"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
