from __future__ import annotations

import abc
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
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI


# ════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════
BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
ODDS_API_KEY      = os.getenv("ODDS_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL  = os.getenv("TELEGRAM_CHANNEL", "")

NOTIFY_HOUR       = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE     = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ         = os.getenv("NOTIFY_TZ", "Europe/Paris")
SCAN_WINDOW_HOURS = int(os.getenv("SCAN_WINDOW_HOURS", "168"))

EV_THRESHOLD      = float(os.getenv("EV_THRESHOLD", "0.03"))
RHO               = float(os.getenv("RHO", "-0.05"))

THESTATSAPI_KEY   = os.getenv("THESTATSAPI_KEY", "")
THESTATSAPI_BASE  = os.getenv("THESTATSAPI_BASE", "https://api.thestatsapi.com/v1")
THESTATSAPI_AUTH  = os.getenv("THESTATSAPI_AUTH", "header")
W_CSV             = float(os.getenv("W_CSV", "0.7"))
W_API             = float(os.getenv("W_API", "0.0"))

GIST_ID           = os.getenv("GIST_ID", "")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
LOCAL_DB          = os.getenv("LOCAL_DB", "/tmp/wallstreet.json")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN manquant.")
if not ODDS_API_KEY:
    raise RuntimeError("❌ ODDS_API_KEY manquant.")
if not TELEGRAM_ADMIN_ID and not TELEGRAM_CHANNEL:
    raise RuntimeError("❌ Renseigne TELEGRAM_ADMIN_ID et/ou TELEGRAM_CHANNEL.")

if W_API > 0 and not THESTATSAPI_KEY:
    print("⚠️ W_API > 0 mais pas de THESTATSAPI_KEY → fusion API désactivée.")
    W_API = 0.0


# ════════════════════════════════════════════════════════════════
# LIGUES
# ════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class League:
    code: str
    name: str
    odds_key: str
    api_id: str


LEAGUES = [
    League("E0",  "Premier League", "soccer_epl",                    "epl"),
    League("E1",  "Championship",   "soccer_efl_champ",              "efl-champ"),
    League("D1",  "Bundesliga",     "soccer_germany_bundesliga",     "bundesliga"),
    League("I1",  "Serie A",        "soccer_italy_serie_a",          "serie-a"),
    League("SP1", "La Liga",        "soccer_spain_la_liga",          "la-liga"),
    League("F1",  "Ligue 1",        "soccer_france_ligue_one",       "ligue-1"),
    League("N1",  "Eredivisie",     "soccer_netherlands_eredivisie", "eredivisie"),
    League("B1",  "Pro League",     "soccer_belgium_first_div",      "belgian-pro"),
    League("P1",  "Primeira Liga",  "soccer_portugal_primeira_liga", "primeira-liga"),
    League("T1",  "Süper Lig",      "soccer_turkey_super_league",    "super-lig"),
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
    "valencia cf": "valencia",
    "sevilla fc": "sevilla",
    "ac milan": "milan",
    "inter milan": "inter",
    "as roma": "roma",
    "ssc napoli": "napoli",
    "juventus fc": "juventus",
    "psv eindhoven": "psv",
    "fc groningen": "groningen",
    "sporting cp": "sporting",
    "sporting lisbon": "sporting",
    "fc porto": "porto",
    "sl benfica": "benfica",
    "sc braga": "braga",
    "galatasaray": "galatasaray",
    "fenerbahce": "fenerbahce",
    "besiktas": "besiktas",
    "trabzonspor": "trabzonspor",
    "club brugge": "brugge",
    "anderlecht": "anderlecht",
    "royal antwerp": "antwerp",
    "genk": "genk",

    "celta vigo": "celta",
    "real mallorca": "mallorca",
    "mallorca": "mallorca",
    "deportivo alaves": "alaves",
    "alaves": "alaves",
    "espanyol barcelona": "espanyol",
    "rcd espanyol": "espanyol",
    "leganes": "leganes",
    "real oviedo": "oviedo",
    "real zaragoza": "zaragoza",
    "malaga cf": "malaga",
    "deportivo la coruna": "la coruna",

    "boavista fc": "boavista",
    "fc arouca": "arouca",
    "rio ave fc": "rio ave",
    "fc famalicao": "famalicao",
    "sporting braga": "braga",
    "vitoria guimaraes": "guimaraes",
    "gil vicente fc": "gil vicente",
    "estrela amadora": "estrela",
    "casa pia": "casa pia",
    "moreirense fc": "moreirense",

    "feyenoord rotterdam": "feyenoord",
    "go ahead eagles": "go ahead eagles",
    "sparta rotterdam": "sparta",
    "pec zwolle": "zwolle",
    "nec nijmegen": "nijmegen",
    "fortuna sittard": "sittard",
    "heracles almelo": "heracles",
    "willem ii": "willem ii",

    "istanbul basaksehir": "basaksehir",
    "gaziantep fk": "gaziantep",
    "goztepe": "goztepe",
    "genclerbirligi": "genclerbirligi",
    "kasimpasa": "kasimpasa",
    "samsunspor": "samsunspor",
}


# ════════════════════════════════════════════════════════════════
# UTILITAIRES
# ════════════════════════════════════════════════════════════════
def normalize(name: str) -> str:
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()

    stopwords = {
        "fc", "cf", "ac", "sc", "sv", "fk", "club",
        "de", "da", "cd", "ud", "sd"
    }
    tokens = [t for t in n.split() if t not in stopwords]
    return " ".join(tokens).strip()


def find_team(api_name: str, teams: dict) -> Optional[str]:
    if api_name in teams:
        return api_name

    norm_api = normalize(api_name)

    alias = TEAM_ALIASES.get(norm_api)
    if alias:
        alias_norm = normalize(alias)
        for t in teams:
            if normalize(t) == alias_norm:
                return t

    for t in teams:
        if normalize(t) == norm_api:
            return t

    candidates = []
    for t in teams:
        nt = normalize(t)
        if nt in norm_api or norm_api in nt:
            candidates.append(t)

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        candidates.sort(key=lambda x: len(normalize(x)))
        return candidates[0]

    lookup = {normalize(t): t for t in teams}
    close = difflib.get_close_matches(norm_api, lookup.keys(), n=1, cutoff=0.72)
    if close:
        return lookup[close[0]]

    return None


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _kickoff_fr(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(NOTIFY_TZ)).strftime("%d/%m %H:%M")
    except Exception:
        return "?"


def _parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


# ════════════════════════════════════════════════════════════════
# STRUCTURES
# ════════════════════════════════════════════════════════════════
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
    p_btts: float
    p_over25: float
    p_under25: float
    top_scores: list = field(default_factory=list)


@dataclass
class Reliability:
    model_prob: float
    observed_rate: Optional[float]
    market_prob: Optional[float]
    edge: Optional[float]
    score: float
    grade: str


@dataclass
class Prono:
    league: str
    league_code: str
    home: str
    away: str
    home_model: str
    away_model: str
    kickoff: str
    pick: str
    pick_label: str
    p_home: float
    p_draw: float
    p_away: float
    lambda_home: float
    lambda_away: float
    top_scores: list
    odds: Optional[float]
    ev: Optional[float]
    stake: float
    is_value: bool
    key: str
    reliability: Optional[Reliability] = None

    def pick_prob(self) -> float:
        return {"1": self.p_home, "X": self.p_draw, "2": self.p_away}[self.pick]


@dataclass
class Coupon:
    name: str
    emoji: str
    legs: list[Prono]
    combined_odds: float
    combined_prob: float
    combined_ev: float


@dataclass(frozen=True)
class CouponConfig:
    name: str
    emoji: str
    pool: str
    min_odds: float
    max_odds: float
    max_legs: int


# ════════════════════════════════════════════════════════════════
# PROVIDERS
# ════════════════════════════════════════════════════════════════
class StatsProvider(abc.ABC):
    name: str = "base"
    available: bool = True

    @abc.abstractmethod
    async def team_strengths(self, league: League) -> dict:
        ...


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
            close_home=_f(row.get("B365CH")),
            close_draw=_f(row.get("B365CD")),
            close_away=_f(row.get("B365CA")),
        ))
    return out


class CSVProvider(StatsProvider):
    name = "csv"

    async def _download(self, leagues: list[League], seasons: list[str]) -> list[Match]:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            async def one(lg, se):
                try:
                    r = await client.get(f"{CSV_BASE}/{se}/{lg.code}.csv", timeout=30.0)
                    return parse_csv(r.text, lg.code) if r.status_code == 200 else []
                except httpx.HTTPError:
                    return []
            batches = await asyncio.gather(*[one(lg, se) for lg in leagues for se in seasons])
        return [m for b in batches for m in b]

    async def team_strengths(self, league: League) -> dict:
        matches = await self._download([league], season_codes(2))
        return compute_model(matches)


class TheStatsAPIProvider(StatsProvider):
    name = "thestatsapi"

    def __init__(self, api_key: str, base_url: str, auth_mode: str = "header"):
        self.api_key = api_key
        self.base = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.available = bool(api_key)

    async def team_strengths(self, league: League) -> dict:
        return {"teams": {}, "avg_home": 1.5, "avg_away": 1.2}


def fuse_models(csv_model: dict, api_model: dict, w_csv: float = W_CSV, w_api: float = W_API) -> dict:
    if not api_model.get("teams") or w_api <= 0:
        return csv_model
    if not csv_model.get("teams"):
        return api_model
    return csv_model


# ════════════════════════════════════════════════════════════════
# MODELE
# ════════════════════════════════════════════════════════════════
def compute_model(matches: list[Match], recent_days: int = 500, decay_days: float = 180.0) -> dict:
    dated = [(_parse_date(m.date), m) for m in matches]
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

        s = stats[m.home]
        s["hs"] += w * m.home_goals
        s["hc"] += w * m.away_goals
        s["hp"] += w

        s = stats[m.away]
        s["as_"] += w * m.away_goals
        s["ac"] += w * m.home_goals
        s["ap"] += w

        sum_w += w
        sum_h += w * m.home_goals
        sum_a += w * m.away_goals

    if sum_w == 0:
        return {"teams": {}, "avg_home": 1.5, "avg_away": 1.2}

    avg_home, avg_away = sum_h / sum_w, sum_a / sum_w
    teams = {}
    for team, s in stats.items():
        if s["hp"] < 3 or s["ap"] < 3:
            continue
        teams[team] = {
            "att_home": (s["hs"] / s["hp"]) / avg_home,
            "def_home": (s["hc"] / s["hp"]) / avg_away,
            "att_away": (s["as_"] / s["ap"]) / avg_away,
            "def_away": (s["ac"] / s["ap"]) / avg_home,
            "played": int(s["hp"] + s["ap"]),
        }
    return {"teams": teams, "avg_home": avg_home, "avg_away": avg_away}


def _poisson(k: int, mu: float) -> float:
    return math.exp(-mu) * (mu ** k) / math.factorial(k)


def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def predict(home: str, away: str, model: dict, rho: float = RHO, max_goals: int = 8) -> Optional[Prediction]:
    teams = model["teams"]
    if home not in teams or away not in teams:
        return None

    h, a = teams[home], teams[away]
    lam = h["att_home"] * a["def_away"] * model["avg_home"]
    mu = a["att_away"] * h["def_home"] * model["avg_away"]

    ph = pd = pa = btts = over = tot = 0.0
    scores = []

    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = _poisson(x, lam) * _poisson(y, mu) * _tau(x, y, lam, mu, rho)
            tot += p
            if x > y:
                ph += p
            elif x == y:
                pd += p
            else:
                pa += p
            if x >= 1 and y >= 1:
                btts += p
            if x + y > 2:
                over += p
            scores.append((x, y, p))

    scores.sort(key=lambda s: s[2], reverse=True)
    top = [{"score": f"{x}-{y}", "proba": round(p / tot, 3)} for x, y, p in scores[:5]]

    return Prediction(
        lambda_home=round(lam, 2),
        lambda_away=round(mu, 2),
        p_home=ph / tot,
        p_draw=pd / tot,
        p_away=pa / tot,
        p_btts=btts / tot,
        p_over25=over / tot,
        p_under25=1 - over / tot,
        top_scores=top,
    )


# ════════════════════════════════════════════════════════════════
# CALIBRATION
# ════════════════════════════════════════════════════════════════
def _result_of(m: Match) -> str:
    if m.home_goals > m.away_goals:
        return "1"
    if m.home_goals < m.away_goals:
        return "2"
    return "X"


def compute_calibration(by_league: dict[str, list[Match]], split: float = 0.7, min_bucket: int = 20) -> dict:
    buckets: dict[int, list[int]] = defaultdict(lambda: [0, 0])

    for _, matches in by_league.items():
        dated = [(_parse_date(m.date), m) for m in matches]
        dated = [(d, m) for d, m in dated if d is not None]
        if len(dated) < 100:
            continue

        dated.sort(key=lambda x: x[0])
        cut = int(len(dated) * split)
        train = [m for _, m in dated[:cut]]
        test = [m for _, m in dated[cut:]]

        model = compute_model(train)
        if not model["teams"]:
            continue

        for m in test:
            pred = predict(m.home, m.away, model)
            if not pred:
                continue
            probs = {"1": pred.p_home, "X": pred.p_draw, "2": pred.p_away}
            pick = max(probs, key=probs.get)
            p = probs[pick]
            hit = 1 if pick == _result_of(m) else 0
            b = min(int(p * 10), 9)
            buckets[b][0] += hit
            buckets[b][1] += 1

    calibration = {}
    for b, (hits, tot) in buckets.items():
        if tot >= min_bucket:
            calibration[b] = round(hits / tot, 4)

    total = sum(t for _, t in buckets.values())
    print(f"📐 Calibration : {len(calibration)} buckets ({total} matchs évalués)")
    return calibration


def observed_rate(p_model: float, calibration: dict) -> Optional[float]:
    if not calibration:
        return None
    b = min(int(p_model * 10), 9)
    if b in calibration:
        return calibration[b]
    for delta in (1, -1, 2, -2):
        if (b + delta) in calibration:
            return calibration[b + delta]
    return None


def _grade_from(score: float) -> str:
    if score >= 0.70:
        return "A+"
    if score >= 0.60:
        return "A"
    if score >= 0.50:
        return "B"
    if score >= 0.40:
        return "C"
    return "D"


def compute_reliability(p_model: float, odds: Optional[float], calibration: dict) -> Reliability:
    obs = observed_rate(p_model, calibration)
    p_mkt = (1.0 / odds) if (odds and odds > 1.0) else None
    edge = (p_model - p_mkt) if p_mkt is not None else None

    if obs is not None:
        score = 0.55 * p_model + 0.45 * obs
    else:
        score = p_model

    if edge is not None:
        score = max(0.0, min(1.0, score + 0.05 * (1 if edge > 0.05 else -1 if edge < -0.05 else 0)))

    return Reliability(
        model_prob=round(p_model, 4),
        observed_rate=round(obs, 4) if obs is not None else None,
        market_prob=round(p_mkt, 4) if p_mkt is not None else None,
        edge=round(edge, 4) if edge is not None else None,
        score=round(score, 4),
        grade=_grade_from(score),
    )


# ════════════════════════════════════════════════════════════════
# ODDS
# ════════════════════════════════════════════════════════════════
QUOTA = {"used": 0, "limit": 500, "remaining": None}


async def fetch_odds(client: httpx.AsyncClient, league: League) -> list[dict]:
    url = f"https://api.the-odds-api.com/v4/sports/{league.odds_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    try:
        r = await client.get(url, params=params, timeout=20.0)

        remaining = r.headers.get("x-requests-remaining")
        used_h = r.headers.get("x-requests-used")
        if remaining is not None:
            try:
                QUOTA["remaining"] = int(remaining)
                QUOTA["used"] = int(used_h) if used_h else QUOTA["limit"] - int(remaining)
            except ValueError:
                pass

        if r.status_code != 200:
            print(f"⚠️ The Odds API {league.name}: HTTP {r.status_code}")
            return []

        data = r.json()
        print(f"🌐 Odds API {league.name}: {len(data)} event(s)")
        return data

    except httpx.HTTPError as e:
        print(f"⚠️ The Odds API {league.name}: {e}")
        return []


def best_odds(event: dict) -> dict[str, float]:
    prices: dict[str, list] = defaultdict(list)
    for bk in event.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for out in mkt.get("outcomes", []):
                p = out.get("price")
                if p:
                    prices[out["name"]].append(p)
    return {k: max(v) for k, v in prices.items()}


def kelly(prob: float, odds: float, fraction: float = 0.25) -> float:
    b = odds - 1
    if b <= 0:
        return 0.0
    return max(0.0, ((b * prob - (1 - prob)) / b) * fraction)


# ════════════════════════════════════════════════════════════════
# COUPONS
# ════════════════════════════════════════════════════════════════
COUPONS = [
    CouponConfig("SÉCURISÉ",  "🟢", "safe",       1.8,  2.8, 3),
    CouponConfig("ÉQUILIBRÉ", "🟡", "balanced",   3.5,  6.5, 4),
    CouponConfig("AGRESSIF",  "🔴", "aggressive", 9.0, 35.0, 5),
    CouponConfig("VALUE",     "💎", "value",      1.6, 20.0, 3),
]


def analyze(league: League, event: dict, model: dict, calibration: dict) -> Optional[Prono]:
    teams = model["teams"]
    home = find_team(event["home_team"], teams)
    away = find_team(event["away_team"], teams)

    if not home or not away:
        return None

    pred = predict(home, away, model)
    if not pred:
        return None

    probs = {"1": pred.p_home, "X": pred.p_draw, "2": pred.p_away}
    pick = max(probs, key=probs.get)

    odds = best_odds(event)
    api_home, api_away = event["home_team"], event["away_team"]
    odd_map = {"1": odds.get(api_home), "X": odds.get("Draw"), "2": odds.get(api_away)}
    chosen = odd_map.get(pick)

    ev = stake = None
    is_value = False
    if chosen and chosen > 1.0:
        ev = probs[pick] * chosen - 1
        stake = round(kelly(probs[pick], chosen) * 100, 1)
        is_value = ev >= EV_THRESHOLD

    rel = compute_reliability(probs[pick], chosen, calibration)
    labels = {
        "1": f"Victoire {api_home}",
        "2": f"Victoire {api_away}",
        "X": "Match nul",
    }

    return Prono(
        league=league.name,
        league_code=league.code,
        home=api_home,
        away=api_away,
        home_model=home,
        away_model=away,
        kickoff=event.get("commence_time", ""),
        pick=pick,
        pick_label=labels[pick],
        p_home=round(probs["1"], 4),
        p_draw=round(probs["X"], 4),
        p_away=round(probs["2"], 4),
        lambda_home=pred.lambda_home,
        lambda_away=pred.lambda_away,
        top_scores=pred.top_scores,
        odds=round(chosen, 2) if chosen else None,
        ev=round(ev, 4) if ev is not None else None,
        stake=stake or 0.0,
        is_value=is_value,
        key=f"{event.get('id', '')}-{pick}",
        reliability=rel,
    )


def _score(p: Prono) -> float:
    return p.reliability.score if p.reliability else p.pick_prob()


def _pool(pronos: list[Prono], kind: str) -> list[Prono]:
    priced = [p for p in pronos if p.odds and p.odds > 1.0]
    if kind == "safe":
        pool = [p for p in priced if _score(p) >= 0.55 and p.odds <= 1.9]
        pool.sort(key=_score, reverse=True)
    elif kind == "balanced":
        pool = [p for p in priced if _score(p) >= 0.40 and 1.3 <= p.odds <= 2.6]
        pool.sort(key=_score, reverse=True)
    elif kind == "aggressive":
        pool = [p for p in priced if p.odds >= 2.0]
        pool.sort(key=lambda p: p.odds, reverse=True)
    else:
        pool = [p for p in priced if p.is_value]
        pool.sort(key=lambda p: (p.ev or 0), reverse=True)
    return pool


def build_coupon(cfg: CouponConfig, pronos: list[Prono]) -> Optional[Coupon]:
    pool = _pool(pronos, cfg.pool)
    if not pool:
        return None

    legs: list[Prono] = []
    odds = 1.0
    prob = 1.0
    used: set[str] = set()

    for p in pool:
        if len(legs) >= cfg.max_legs:
            break
        if p.key in used:
            continue
        if any(p.home == l.home and p.away == l.away for l in legs):
            continue
        new_odds = odds * p.odds
        if new_odds > cfg.max_odds and legs:
            continue
        legs.append(p)
        used.add(p.key)
        odds = new_odds
        prob *= p.pick_prob()
        if odds >= cfg.min_odds:
            break

    if not legs or odds < cfg.min_odds * 0.85:
        return None

    return Coupon(
        name=cfg.name,
        emoji=cfg.emoji,
        legs=legs,
        combined_odds=round(odds, 2),
        combined_prob=round(prob, 4),
        combined_ev=round(prob * odds - 1, 4),
    )


# ════════════════════════════════════════════════════════════════
# AFFICHAGE
# ════════════════════════════════════════════════════════════════
EMOJI_BY_NAME = {
    "SÉCURISÉ": "🟢",
    "ÉQUILIBRÉ": "🟡",
    "AGRESSIF": "🔴",
    "VALUE": "💎",
}


def format_coupon(c: Coupon) -> str:
    head = (
        f"{c.emoji} <b>COUPON {c.name}</b>\n"
        f"Cote totale : <b>{c.combined_odds}</b>  ·  "
        f"Proba : {c.combined_prob * 100:.1f}%  ·  "
        f"EV {c.combined_ev * 100:+.1f}%\n"
    )
    body = []
    for i, p in enumerate(c.legs, 1):
        score_txt = ""
        if p.reliability:
            score_txt = f"  📊 {p.reliability.score * 100:.0f}% [{p.reliability.grade}]"
        body.append(
            f"{i}. <b>{p.home} – {p.away}</b>\n"
            f"    🕐 {_kickoff_fr(p.kickoff)}  ·  {p.league}{score_txt}\n"
            f"    ➡️ {p.pick_label}  (cote {p.odds})\n"
        )
    return head + "\n".join(body)


def format_bilan(tracker: dict) -> str:
    coupons = tracker.get("coupons", [])
    if not coupons:
        return "📊 <b>BILAN</b>\n\nAucun historique pour l'instant."

    resolved = [c for c in coupons if c.get("status") in ("win", "loss")]
    pending = [c for c in coupons if c.get("status") == "pending"]
    if not resolved:
        return f"📊 <b>BILAN</b>\n\n⏳ {len(pending)} coupon(s) en attente."

    by_name: dict[str, dict] = defaultdict(lambda: {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0})
    total_bets = total_wins = 0
    total_staked = total_returned = 0.0

    for c in resolved:
        b = by_name[c["name"]]
        b["bets"] += 1
        b["staked"] += 1.0
        total_bets += 1
        total_staked += 1.0
        if c["status"] == "win":
            b["wins"] += 1
            b["returned"] += c["combined_odds"]
            total_wins += 1
            total_returned += c["combined_odds"]

    lines = ["📊 <b>BILAN DU SYSTÈME</b>", ""]
    for name in ["SÉCURISÉ", "ÉQUILIBRÉ", "AGRESSIF", "VALUE"]:
        b = by_name.get(name)
        if not b or b["bets"] == 0:
            continue
        roi = (b["returned"] - b["staked"]) / b["staked"] * 100
        wr = b["wins"] / b["bets"] * 100
        lines.append(f"{EMOJI_BY_NAME[name]} <b>{name}</b>")
        lines.append(f"   Paris : {b['bets']}  ·  Réussite : {wr:.0f}%")
        lines.append(f"   ROI : <b>{roi:+.1f}%</b>")
        lines.append("")

    total_roi = (total_returned - total_staked) / total_staked * 100
    total_wr = total_wins / total_bets * 100
    lines.append("📈 <b>TOTAL</b>")
    lines.append(f"   Paris : {total_bets}  ·  Réussite : {total_wr:.0f}%")
    lines.append(f"   ROI global : <b>{total_roi:+.1f}%</b>")
    if pending:
        lines.append("")
        lines.append(f"⏳ {len(pending)} coupon(s) en attente.")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# CLAVIER BAS
# ════════════════════════════════════════════════════════════════
BTN_SAFE       = "🟢 SÉCURISÉ"
BTN_BALANCED   = "🟡 ÉQUILIBRÉ"
BTN_AGGRESSIVE = "🔴 AGRESSIF"
BTN_VALUE      = "💎 VALUE"
BTN_BILAN      = "📊 BILAN"
BTN_SCAN       = "🔄 SCAN"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE), KeyboardButton(text=BTN_BALANCED)],
        [KeyboardButton(text=BTN_AGGRESSIVE), KeyboardButton(text=BTN_VALUE)],
        [KeyboardButton(text=BTN_BILAN), KeyboardButton(text=BTN_SCAN)],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    input_field_placeholder="Choisis un ticket…",
)


# ════════════════════════════════════════════════════════════════
# PERSISTANCE
# ════════════════════════════════════════════════════════════════
async def _gist_load() -> Optional[dict]:
    if not (GIST_ID and GITHUB_TOKEN):
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
                timeout=15.0,
            )
            if r.status_code != 200:
                return None
            for name, f in (r.json().get("files") or {}).items():
                if name.endswith(".json"):
                    content = f.get("content", "")
                    return json.loads(content) if content.strip() else {}
    except Exception as e:
        print(f"⚠️ Lecture Gist échouée : {e}")
    return None


async def _gist_save(state: dict) -> bool:
    if not (GIST_ID and GITHUB_TOKEN):
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
                json={"files": {"wallstreet.json": {"content": json.dumps(state, indent=2)}}},
                timeout=15.0,
            )
            return r.status_code == 200
    except Exception as e:
        print(f"⚠️ Écriture Gist échouée : {e}")
        return False


async def load_tracker() -> dict:
    data = await _gist_load()
    if data is None:
        try:
            with open(LOCAL_DB, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
    return data if data else {"coupons": []}


async def save_tracker(data: dict):
    if GIST_ID and GITHUB_TOKEN and await _gist_save(data):
        return
    try:
        with open(LOCAL_DB, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Sauvegarde locale échouée : {e}")


# ════════════════════════════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════════════════════════════
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

STATE = {
    "models": {},
    "ready": False,
    "last_scan": None,
    "pronos": [],
    "coupons": [],
    "scan_day": None,
    "tracker": {"coupons": []},
    "calibration": {},
    "api_events": 0,
    "debug": {},
}

TARGETS: list[int | str] = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if not raw:
        continue
    try:
        TARGETS.append(int(raw))
    except ValueError:
        TARGETS.append(raw)


# ════════════════════════════════════════════════════════════════
# COMMANDES
# ════════════════════════════════════════════════════════════════
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>WallStreet OS</b>\n\n"
        "Utilise les boutons sous le clavier.\n"
        "Si tu veux tester tout de suite : appuie sur 🔄 SCAN",
        reply_markup=MAIN_KEYBOARD
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("🎟️ Menu affiché sous le clavier 👇", reply_markup=MAIN_KEYBOARD)


@dp.message(Command("scan"))
async def cmd_scan(message: Message):
    await message.answer("⏳ Scan en cours...", reply_markup=MAIN_KEYBOARD)
    await smart_scan(debug=True)
    await message.answer(
        f"✅ {len(STATE['pronos'])} matchs · {len(STATE['coupons'])} tickets.",
        reply_markup=MAIN_KEYBOARD
    )


@dp.message(Command("debugscan"))
async def cmd_debugscan(message: Message):
    await message.answer("🛠️ Debug scan en cours...", reply_markup=MAIN_KEYBOARD)
    await smart_scan(debug=True)
    d = STATE.get("debug", {})
    txt = (
        "🛠️ <b>DEBUG SCAN</b>\n\n"
        f"Fenêtre finale : <b>{d.get('window_hours', 0)}h</b>\n"
        f"Events API : <b>{d.get('total_events', 0)}</b>\n"
        f"Hors fenêtre : <b>{d.get('out_window', 0)}</b>\n"
        f"Équipes non reconnues : <b>{d.get('unmatched', 0)}</b>\n"
        f"Analyses rejetées : <b>{d.get('rejected', 0)}</b>\n"
        f"Pronos retenus : <b>{d.get('ok', 0)}</b>\n"
        f"Coupons : <b>{len(STATE['coupons'])}</b>"
    )
    await message.answer(txt, reply_markup=MAIN_KEYBOARD)


@dp.message(Command("bilan"))
async def cmd_bilan(message: Message):
    await message.answer(format_bilan(STATE["tracker"]), reply_markup=MAIN_KEYBOARD)


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    total_teams = sum(len(m["teams"]) for m in STATE["models"].values())
    await message.answer(
        f"📊 <b>Statut</b>\n\n"
        f"Ready : <b>{STATE['ready']}</b>\n"
        f"Ligues : <b>{len(STATE['models'])}/{len(LEAGUES)}</b>\n"
        f"Équipes : <b>{total_teams}</b>\n"
        f"Pronos : <b>{len(STATE['pronos'])}</b>\n"
        f"Coupons : <b>{len(STATE['coupons'])}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>\n"
        f"Quota odds : <b>{QUOTA['used']}/{QUOTA['limit']}</b>",
        reply_markup=MAIN_KEYBOARD
    )


# ════════════════════════════════════════════════════════════════
# BOUTONS CLAVIER
# ════════════════════════════════════════════════════════════════
async def _send_coupon(message: Message, name: str):
    coupon = next((c for c in STATE["coupons"] if c.name == name), None)
    if not coupon:
        d = STATE.get("debug", {})
        await message.answer(
            f"❌ Aucun ticket « {name} » disponible.\n\n"
            f"Fenêtre: {d.get('window_hours', 0)}h\n"
            f"Events API: {d.get('total_events', 0)}\n"
            f"Hors fenêtre: {d.get('out_window', 0)}\n"
            f"Non reconnus: {d.get('unmatched', 0)}\n"
            f"Rejetés: {d.get('rejected', 0)}\n\n"
            f"Appuie sur 🔄 SCAN.",
            reply_markup=MAIN_KEYBOARD
        )
        return
    await message.answer(format_coupon(coupon), reply_markup=MAIN_KEYBOARD)


@dp.message(F.text == BTN_SAFE)
async def btn_safe(message: Message):
    await _send_coupon(message, "SÉCURISÉ")


@dp.message(F.text == BTN_BALANCED)
async def btn_balanced(message: Message):
    await _send_coupon(message, "ÉQUILIBRÉ")


@dp.message(F.text == BTN_AGGRESSIVE)
async def btn_aggressive(message: Message):
    await _send_coupon(message, "AGRESSIF")


@dp.message(F.text == BTN_VALUE)
async def btn_value(message: Message):
    await _send_coupon(message, "VALUE")


@dp.message(F.text == BTN_BILAN)
async def btn_bilan(message: Message):
    await message.answer(format_bilan(STATE["tracker"]), reply_markup=MAIN_KEYBOARD)


@dp.message(F.text == BTN_SCAN)
async def btn_scan(message: Message):
    await message.answer("⏳ Scan en cours...", reply_markup=MAIN_KEYBOARD)
    await smart_scan(debug=True)
    await message.answer(
        f"✅ {len(STATE['pronos'])} matchs · {len(STATE['coupons'])} tickets.",
        reply_markup=MAIN_KEYBOARD
    )


# ════════════════════════════════════════════════════════════════
# SCAN
# ════════════════════════════════════════════════════════════════
async def scan(window_hours: int, debug: bool = False):
    if not STATE["ready"]:
        print("⏳ Modèles pas prêts.")
        return

    now_utc = datetime.now(timezone.utc)
    limit = now_utc + timedelta(hours=window_hours)

    print(f"🔄 Scan {now_utc:%d/%m %H:%M} → {limit:%d/%m %H:%M} UTC...")
    print(f"🧪 SCAN_WINDOW_HOURS utilisé = {window_hours}")
    print(f"🧪 NOW UTC = {now_utc.isoformat()}")
    print(f"🧪 LIMIT UTC = {limit.isoformat()}")

    pronos: list[Prono] = []
    total_events = 0
    total_out_window = 0
    total_unmatched = 0
    total_no_pred = 0
    total_ok = 0
    by_league_debug = {}

    async with httpx.AsyncClient() as client:
        for lg in LEAGUES:
            model = STATE["models"].get(lg.code)
            if not model or not model["teams"]:
                print(f"   {lg.name}: modèle vide")
                by_league_debug[lg.name] = {"events": 0, "out_window": 0, "unmatched": 0, "rejected": 0, "ok": 0}
                continue

            events = await fetch_odds(client, lg)
            total_events += len(events)

            lg_out_window = 0
            lg_unmatched = 0
            lg_no_pred = 0
            lg_ok = 0

            print(f"   {lg.name}: {len(events)} event(s) API")

            for ev in events:
                try:
                    ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                except Exception:
                    lg_no_pred += 1
                    total_no_pred += 1
                    continue

                if not (now_utc <= ko <= limit):
                    lg_out_window += 1
                    total_out_window += 1
                    continue

                home_api = ev.get("home_team", "")
                away_api = ev.get("away_team", "")

                home = find_team(home_api, model["teams"])
                away = find_team(away_api, model["teams"])

                if not home or not away:
                    lg_unmatched += 1
                    total_unmatched += 1
                    print(f"      ❌ Non reconnu: {home_api} vs {away_api}")
                    continue

                p = analyze(lg, ev, model, STATE["calibration"])
                if not p:
                    lg_no_pred += 1
                    total_no_pred += 1
                    print(f"      ⚠️ Analyse rejetée: {home_api} vs {away_api}")
                    continue

                pronos.append(p)
                lg_ok += 1
                total_ok += 1

            by_league_debug[lg.name] = {
                "events": len(events),
                "out_window": lg_out_window,
                "unmatched": lg_unmatched,
                "rejected": lg_no_pred,
                "ok": lg_ok,
            }

            print(
                f"   {lg.name}: ok={lg_ok} | hors_fenêtre={lg_out_window} | "
                f"non_reconnus={lg_unmatched} | rejetés={lg_no_pred}"
            )
            await asyncio.sleep(0.5)

    STATE["api_events"] = total_events
    pronos.sort(key=lambda p: _score(p), reverse=True)
    STATE["pronos"] = pronos

    coupons = []
    for cfg in COUPONS:
        c = build_coupon(cfg, pronos)
        if c:
            coupons.append(c)

    STATE["coupons"] = coupons
    STATE["scan_day"] = datetime.now(ZoneInfo(NOTIFY_TZ)).date()
    STATE["last_scan"] = datetime.now(ZoneInfo(NOTIFY_TZ)).strftime("%d/%m %H:%M")

    STATE["debug"] = {
        "window_hours": window_hours,
        "total_events": total_events,
        "out_window": total_out_window,
        "unmatched": total_unmatched,
        "rejected": total_no_pred,
        "ok": total_ok,
        "by_league": by_league_debug,
    }

    print("──────── RÉSUMÉ SCAN ────────")
    print(f"Events API total      : {total_events}")
    print(f"Hors fenêtre          : {total_out_window}")
    print(f"Équipes non reconnues : {total_unmatched}")
    print(f"Analyses rejetées     : {total_no_pred}")
    print(f"Pronos retenus        : {total_ok}")
    print(f"Coupons construits    : {len(coupons)}")
    print("─────────────────────────────")


async def smart_scan(debug: bool = False):
    for hours in [SCAN_WINDOW_HOURS, 168, 720]:
        print(f"🚀 Tentative de scan avec fenêtre = {hours}h")
        await scan(window_hours=hours, debug=debug)
        if STATE["pronos"]:
            print(f"✅ Scan réussi avec fenêtre {hours}h")
            return
    print("❌ Aucun prono trouvé même après fallback.")


# ════════════════════════════════════════════════════════════════
# BOOTSTRAP
# ════════════════════════════════════════════════════════════════
async def bootstrap_models():
    print("📥 Chargement des données...")
    csvp = CSVProvider()
    api = TheStatsAPIProvider(THESTATSAPI_KEY, THESTATSAPI_BASE, THESTATSAPI_AUTH)

    by_league: dict[str, list[Match]] = defaultdict(list)
    all_matches = await csvp._download(LEAGUES, season_codes(2))
    for m in all_matches:
        by_league[m.league].append(m)

    print(f"   CSV : {len(all_matches)} matchs chargés.")
    STATE["calibration"] = compute_calibration(by_league)

    for lg in LEAGUES:
        csv_model = compute_model(by_league.get(lg.code, []))
        if W_API > 0 and api.available:
