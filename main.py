"""
WALLSTREET OS v8.0 — 10 ligues · Dixon-Coles · calibration · 4 coupons
"""
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
from functools import lru_cache
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
# 1. CONFIG
# ════════════════════════════════════════════════════════════════
BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
ODDS_API_KEY      = os.getenv("ODDS_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL  = os.getenv("TELEGRAM_CHANNEL", "")

NOTIFY_HOUR       = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE     = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ         = os.getenv("NOTIFY_TZ", "Europe/Paris")
SCAN_WINDOW_HOURS = int(os.getenv("SCAN_WINDOW_HOURS", "36"))

EV_THRESHOLD      = float(os.getenv("EV_THRESHOLD", "0.03"))
KELLY_FRACTION    = float(os.getenv("KELLY_FRACTION", "0.25"))
RHO               = float(os.getenv("RHO", "-0.05"))
MAX_GOALS         = int(os.getenv("MAX_GOALS", "8"))

RECENT_DAYS       = int(os.getenv("RECENT_DAYS", "500"))
DECAY_DAYS        = float(os.getenv("DECAY_DAYS", "180"))
CALIB_SPLIT       = float(os.getenv("CALIB_SPLIT", "0.7"))
CALIB_MIN_BUCKET  = int(os.getenv("CALIB_MIN_BUCKET", "20"))

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
    print("⚠️ W_API > 0 mais pas de THESTATSAPI_KEY → fusion désactivée.")
    W_API = 0.0


# ════════════════════════════════════════════════════════════════
# 2. LIGUES
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

# Alias bidirectionnels (API ↔ CSV) — clé = nom normalisé
TEAM_ALIASES = {
    # Premier League
    "manchester united": "man united", "manchester city": "man city",
    "nottingham forest": "nott m forest", "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham", "newcastle united": "newcastle",
    "west ham united": "west ham", "brighton and hove albion": "brighton",
    "leicester city": "leicester", "leeds united": "leeds",
    "sheffield united": "sheffield utd",
    # Ligue 1
    "paris saint germain": "paris sg", "paris st germain": "paris sg",
    # Allemagne
    "borussia dortmund": "dortmund", "bayer leverkusen": "leverkusen",
    "borussia monchengladbach": "m gladbach", "eintracht frankfurt": "ein frankfurt",
    # Espagne
    "atletico madrid": "ath madrid", "athletic bilbao": "ath bilbao",
    "real betis": "betis", "real sociedad": "sociedad",
    "valencia cf": "valencia", "sevilla fc": "sevilla",
    "celta vigo": "celta", "real mallorca": "mallorca",
    "deportivo alaves": "alaves", "rcd espanyol": "espanyol",
    # Italie
    "ac milan": "milan", "inter milan": "inter", "as roma": "roma",
    "ssc napoli": "napoli", "juventus fc": "juventus",
    # Pays-Bas
    "psv eindhoven": "psv", "fc groningen": "groningen",
    "feyenoord rotterdam": "feyenoord", "sparta rotterdam": "sparta",
    "pec zwolle": "zwolle", "nec nijmegen": "nijmegen",
    "fortuna sittard": "sittard", "heracles almelo": "heracles",
    # Portugal
    "sporting cp": "sporting", "sporting lisbon": "sporting",
    "fc porto": "porto", "sl benfica": "benfica", "sc braga": "braga",
    "boavista fc": "boavista", "fc arouca": "arouca",
    "rio ave fc": "rio ave", "fc famalicao": "famalicao",
    "vitoria guimaraes": "guimaraes", "gil vicente fc": "gil vicente",
    # Belgique
    "club brugge": "brugge", "royal antwerp": "antwerp",
    # Turquie
    "istanbul basaksehir": "basaksehir", "gaziantep fk": "gaziantep",
}


# ════════════════════════════════════════════════════════════════
# 3. UTILITAIRES
# ════════════════════════════════════════════════════════════════
_STOP = {"fc", "cf", "ac", "sc", "sv", "fk", "club", "de", "da", "cd", "ud", "sd"}


@lru_cache(maxsize=4096)
def normalize(name: str) -> str:
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return " ".join(t for t in n.split() if t not in _STOP).strip()


@lru_cache(maxsize=64)
def _build_lookup(teams_key: tuple) -> dict:
    return {normalize(t): t for t in teams_key}


def find_team(api_name: str, teams: dict) -> Optional[str]:
    if not api_name or not teams:
        return None
    if api_name in teams:
        return api_name

    norm = normalize(api_name)
    if not norm:
        return None

    teams_key = tuple(teams.keys())
    lookup = _build_lookup(teams_key)

    # 1) alias
    alias = TEAM_ALIASES.get(norm)
    if alias:
        a = normalize(alias)
        if a in lookup:
            return lookup[a]
        for k, v in lookup.items():
            if a and (a in k or k in a):
                return v

    # 2) exact normalisé
    if norm in lookup:
        return lookup[norm]

    # 3) substring unique
    partials = [k for k in lookup if norm in k or k in norm]
    if len(partials) == 1:
        return lookup[partials[0]]
    if len(partials) > 1:
        partials.sort(key=len)
        return lookup[partials[0]]

    # 4) flou
    close = difflib.get_close_matches(norm, lookup.keys(), n=1, cutoff=0.78)
    return lookup[close[0]] if close else None


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
    except (ValueError, TypeError, AttributeError):
        return "?"


def _parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


# ════════════════════════════════════════════════════════════════
# 4. STRUCTURES
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
# 5. PROVIDERS CSV / API
# ════════════════════════════════════════════════════════════════
class StatsProvider(abc.ABC):
    name: str = "base"
    available: bool = True

    @abc.abstractmethod
    async def team_strengths(self, league: League) -> dict:
        ...


CSV_BASE = "https://www.football-data.co.uk/mmz4281"


@lru_cache(maxsize=1)
def season_codes() -> tuple:
    now = datetime.now()
    start = now.year if now.month >= 7 else now.year - 1
    return tuple(f"{str(start - i)[2:]}{str(start - i + 1)[2:]}" for i in range(2))


def parse_csv(text: str, league: str) -> list[Match]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
        except (KeyError, ValueError, TypeError):
            continue
        if not row.get("HomeTeam") or not row.get("AwayTeam"):
            continue
        out.append(Match(
            league=league, date=row.get("Date", ""),
            home=row["HomeTeam"].strip(), away=row["AwayTeam"].strip(),
            home_goals=hg, away_goals=ag,
            close_home=_f(row.get("B365CH")),
            close_draw=_f(row.get("B365CD")),
            close_away=_f(row.get("B365CA")),
        ))
    return out


class CSVProvider(StatsProvider):
    name = "csv"

    async def _download(self, leagues: list[League],
                        seasons: tuple[str, ...]) -> list[Match]:
        async with httpx.AsyncClient(follow_redirects=True,
                                     timeout=30.0) as client:
            async def one(lg, se):
                url = f"{CSV_BASE}/{se}/{lg.code}.csv"
                for attempt in range(3):
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            return parse_csv(r.text, lg.code)
                        if r.status_code == 404:
                            return []
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.5 * (attempt + 1))
                return []
            batches = await asyncio.gather(
                *[one(lg, se) for lg in leagues for se in seasons])
        return [m for b in batches for m in b]

    async def team_strengths(self, league: League) -> dict:
        matches = await self._download([league], season_codes())
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


def fuse_models(csv_model: dict, api_model: dict) -> dict:
    if not api_model.get("teams") or W_API <= 0:
        return csv_model
    if not csv_model.get("teams"):
        return api_model
    return csv_model


# ════════════════════════════════════════════════════════════════
# 6. MODÈLE DIXON-COLES
# ════════════════════════════════════════════════════════════════
def compute_model(matches: list[Match]) -> dict:
    dated = [(_parse_date(m.date), m) for m in matches]
    dated = [(d, m) for d, m in dated if d is not None]
    if not dated:
        return {"teams": {}, "avg_home": 1.5, "avg_away": 1.2}

    dated.sort(key=lambda x: x[0])
    ref = dated[-1][0]

    stats = defaultdict(lambda: {"hs": 0.0, "hc": 0.0, "as_": 0.0,
                                 "ac": 0.0, "hp": 0.0, "ap": 0.0})
    sum_w = sum_h = sum_a = 0.0

    for d, m in dated:
        age = (ref - d).days
        if age > RECENT_DAYS:
            continue
        w = 0.5 ** (age / DECAY_DAYS)
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
            "played":   int(s["hp"] + s["ap"]),
        }
    return {"teams": teams, "avg_home": avg_home, "avg_away": avg_away}


def _poisson(k: int, mu: float) -> float:
    return math.exp(-mu) * (mu ** k) / math.factorial(k)


def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0: return 1 - lam * mu * rho
    if x == 0 and y == 1: return 1 + lam * rho
    if x == 1 and y == 0: return 1 + mu * rho
    if x == 1 and y == 1: return 1 - rho
    return 1.0


def predict(home: str, away: str, model: dict) -> Optional[Prediction]:
    teams = model["teams"]
    if home not in teams or away not in teams:
        return None
    h, a = teams[home], teams[away]
    lam = h["att_home"] * a["def_away"] * model["avg_home"]
    mu  = a["att_away"] * h["def_home"] * model["avg_away"]

    ph = pd = pa = btts = over = tot = 0.0
    scores = []
    for x in range(MAX_GOALS + 1):
        for y in range(MAX_GOALS + 1):
            p = _poisson(x, lam) * _poisson(y, mu) * _tau(x, y, lam, mu, RHO)
            tot += p
            if x > y: ph += p
            elif x == y: pd += p
            else: pa += p
            if x >= 1 and y >= 1: btts += p
            if x + y > 2: over += p
            scores.append((x, y, p))
    scores.sort(key=lambda s: s[2], reverse=True)
    top = [{"score": f"{x}-{y}", "proba": round(p / tot, 3)}
           for x, y, p in scores[:5]]

    return Prediction(
        lambda_home=round(lam, 2),
        lambda_away=round(mu, 2),
        p_home=ph / tot, p_draw=pd / tot, p_away=pa / tot,
        p_btts=btts / tot, p_over25=over / tot, p_under25=1 - over / tot,
        top_scores=top,
    )


# ════════════════════════════════════════════════════════════════
# 7. CALIBRATION
# ════════════════════════════════════════════════════════════════
def _result_of(m: Match) -> str:
    if m.home_goals > m.away_goals: return "1"
    if m.home_goals < m.away_goals: return "2"
    return "X"


def compute_calibration(by_league: dict[str, list[Match]]) -> dict:
    buckets: dict[int, list[int]] = defaultdict(lambda: [0, 0])

    for code, matches in by_league.items():
        dated = [(_parse_date(m.date), m) for m in matches]
        dated = [(d, m) for d, m in dated if d is not None]
        if len(dated) < 100:
            continue
        dated.sort(key=lambda x: x[0])
        cut = int(len(dated) * CALIB_SPLIT)
        train = [m for _, m in dated[:cut]]
        test  = [m for _, m in dated[cut:]]

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
        if tot >= CALIB_MIN_BUCKET:
            calibration[b] = round(hits / tot, 4)
    total = sum(t for _, t in buckets.values())
    print(f"📐 Calibration : {len(calibration)} buckets "
          f"({total} matchs évalués)")
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
    if score >= 0.70: return "A+"
    if score >= 0.60: return "A"
    if score >= 0.50: return "B"
    if score >= 0.40: return "C"
    return "D"


def compute_reliability(p_model: float, odds: Optional[float],
                        calibration: dict) -> Reliability:
    obs = observed_rate(p_model, calibration)
    p_mkt = (1.0 / odds) if (odds and odds > 1.0) else None
    edge = (p_model - p_mkt) if p_mkt is not None else None
    score = 0.55 * p_model + 0.45 * obs if obs is not None else p_model
    if edge is not None:
        score = max(0.0, min(1.0, score + 0.05 *
                                  (1 if edge > 0.05
                                   else -1 if edge < -0.05 else 0)))
    return Reliability(
        model_prob=round(p_model, 4),
        observed_rate=round(obs, 4) if obs is not None else None,
        market_prob=round(p_mkt, 4) if p_mkt is not None else None,
        edge=round(edge, 4) if edge is not None else None,
        score=round(score, 4),
        grade=_grade_from(score),
    )


# ════════════════════════════════════════════════════════════════
# 8. ODDS
# ════════════════════════════════════════════════════════════════
QUOTA = {"used": 0, "limit": 500, "remaining": None}


async def fetch_odds(client: httpx.AsyncClient, league: League) -> list[dict]:
    url = f"https://api.the-odds-api.com/v4/sports/{league.odds_key}/odds/"
    params = {"apiKey": ODDS_API_KEY, "regions": "eu",
              "markets": "h2h", "oddsFormat": "decimal"}
    for attempt in range(3):
        try:
            r = await client.get(url, params=params, timeout=20.0)
            remaining = r.headers.get("x-requests-remaining")
            used_h    = r.headers.get("x-requests-used")
            if remaining is not None:
                try:
                    QUOTA["remaining"] = int(remaining)
                    QUOTA["used"] = int(used_h) if used_h \
                        else QUOTA["limit"] - int(remaining)
                except ValueError:
                    pass
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                print(f"⚠️ Odds {league.name}: HTTP {r.status_code}")
                return []
        except httpx.HTTPError as e:
            print(f"⚠️ Odds {league.name}: {e}")
            await asyncio.sleep(1 * (attempt + 1))
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


def kelly(prob: float, odds: float) -> float:
    b = odds - 1
    if b <= 0:
        return 0.0
    return max(0.0, ((b * prob - (1 - prob)) / b) * KELLY_FRACTION)


# ════════════════════════════════════════════════════════════════
# 9. ANALYSE / COUPONS
# ════════════════════════════════════════════════════════════════
COUPONS = [
    CouponConfig("SÉCURISÉ",  "🟢", "safe",       1.8,  2.8,  3),
    CouponConfig("ÉQUILIBRÉ", "🟡", "balanced",   3.5,  6.5,  4),
    CouponConfig("AGRESSIF",  "🔴", "aggressive", 9.0, 35.0,  5),
    CouponConfig("VALUE",     "💎", "value",      1.6, 20.0,  3),
]


def analyze(league: League, event: dict, model: dict,
            calibration: dict) -> Optional[Prono]:
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
    odd_map = {"1": odds.get(api_home),
               "X": odds.get("Draw"),
               "2": odds.get(api_away)}
    chosen = odd_map.get(pick)
    ev = stake = None
    is_value = False
    if chosen and chosen > 1.0:
        ev = probs[pick] * chosen - 1
        stake = round(kelly(probs[pick], chosen) * 100, 1)
        is_value = ev >= EV_THRESHOLD

    rel = compute_reliability(probs[pick], chosen, calibration)
    labels = {"1": f"Victoire {api_home}",
              "2": f"Victoire {api_away}",
              "X": "Match nul"}

    return Prono(
        league=league.name, league_code=league.code,
        home=api_home, away=api_away,
        home_model=home, away_model=away,
        kickoff=event.get("commence_time", ""),
        pick=pick, pick_label=labels[pick],
        p_home=round(probs["1"], 4), p_draw=round(probs["X"], 4),
        p_away=round(probs["2"], 4),
        lambda_home=pred.lambda_home, lambda_away=pred.lambda_away,
        top_scores=pred.top_scores,
        odds=round(chosen, 2) if chosen else None,
        ev=round(ev, 4) if ev is not None else None,
        stake=stake or 0.0, is_value=is_value,
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
        name=cfg.name, emoji=cfg.emoji, legs=legs,
        combined_odds=round(odds, 2),
        combined_prob=round(prob, 4),
        combined_ev=round(prob * odds - 1, 4),
    )


# ════════════════════════════════════════════════════════════════
# 10. AFFICHAGE
# ════════════════════════════════════════════════════════════════
EMOJI_BY_NAME = {"SÉCURISÉ": "🟢", "ÉQUILIBRÉ": "🟡",
                 "AGRESSIF": "🔴", "VALUE": "💎"}


def _rel_line(r: Optional[Reliability]) -> str:
    if r is None:
        return ""
    parts = [f"📊 Réussite : <b>{r.score * 100:.0f}%</b> [{r.grade}]",
             f"Modèle {r.model_prob * 100:.0f}%"]
    if r.observed_rate is not None:
        parts.append(f"Observé {r.observed_rate * 100:.0f}%")
    if r.market_prob is not None:
        parts.append(f"Marché {r.market_prob * 100:.0f}%")
    if r.edge is not None:
        parts.append(f"EV {r.edge * 100:+.0f}%")
    return "   " + "  ·  ".join(parts)


def format_prono(p: Prono) -> str:
    tag = " 💎" if p.is_value else ""
    odds_line = f"cote {p.odds}" if p.odds else "cote n/d"
    return (f"<b>{p.home} – {p.away}</b>{tag}\n"
            f"   🕐 {_kickoff_fr(p.kickoff)}  ·  {p.league}\n"
            f"   ➡️ {p.pick_label}  ({odds_line})\n"
            f"{_rel_line(p.reliability)}\n"
            f"   ⚽ {p.lambda_home}–{p.lambda_away}  ·  "
            f"1 {p.p_home * 100:.0f}% / X {p.p_draw * 100:.0f}% / "
            f"2 {p.p_away * 100:.0f}%\n")


def format_coupon(c: Coupon) -> str:
    head = (f"{c.emoji} <b>COUPON {c.name}</b>\n"
            f"Cote totale : <b>{c.combined_odds}</b>  ·  "
            f"Proba : {c.combined_prob * 100:.1f}%  ·  "
            f"EV {c.combined_ev * 100:+.1f}%\n")
    body = []
    for i, p in enumerate(c.legs, 1):
        r = p.reliability
        score_txt = f"  📊 {r.score * 100:.0f}% [{r.grade}]" if r else ""
        body.append(f"{i}. <b>{p.home} – {p.away}</b>\n"
                    f"    🕐 {_kickoff_fr(p.kickoff)}  ·  {p.league}{score_txt}\n"
                    f"    ➡️ {p.pick_label}  (cote {p.odds})\n")
    return head + "\n".join(body)


def format_bilan(tracker: dict) -> str:
    coupons = tracker.get("coupons", [])
    if not coupons:
        return "📊 <b>BILAN</b>\n\nAucun historique pour l'instant."

    resolved = [c for c in coupons if c.get("status") in ("win", "loss")]
    pending  = [c for c in coupons if c.get("status") == "pending"]
    if not resolved:
        return f"📊 <b>BILAN</b>\n\n⏳ {len(pending)} coupon(s) en attente."

    by_name: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0})
    total_bets = total_wins = 0
    total_staked = total_returned = 0.0
    clvs: list[float] = []

    for c in resolved:
        b = by_name[c["name"]]
        b["bets"] += 1; b["staked"] += 1.0
        total_bets += 1; total_staked += 1.0
        if c["status"] == "win":
            b["wins"] += 1; b["returned"] += c["combined_odds"]
            total_wins += 1; total_returned += c["combined_odds"]
        for leg in c.get("legs", []):
            if leg.get("clv") is not None:
                clvs.append(leg["clv"])

    lines = ["📊 <b>BILAN DU SYSTÈME</b>", ""]
    for name in ["SÉCURISÉ", "ÉQUILIBRÉ", "AGRESSIF", "VALUE"]:
        b = by_name.get(name)
        if not b or b["bets"] == 0:
            continue
        roi = (b["returned"] - b["staked"]) / b["staked"] * 100
        wr  = b["wins"] / b["bets"] * 100
        lines.append(f"{EMOJI_BY_NAME[name]} <b>{name}</b>")
        lines.append(f"   Paris : {b['bets']}  ·  Réussite : {wr:.0f}%")
        lines.append(f"   ROI : <b>{roi:+.1f}%</b>")
        lines.append("")

    total_roi = (total_returned - total_staked) / total_staked * 100
    total_wr  = total_wins / total_bets * 100
    lines.append("📈 <b>TOTAL</b>")
    lines.append(f"   Paris : {total_bets}  ·  Réussite : {total_wr:.0f}%")
    lines.append(f"   ROI global : <b>{total_roi:+.1f}%</b>")
    if clvs:
        lines.append(f"   CLV moyen : <b>{sum(clvs) / len(clvs) * 100:+.2f}%</b>")
    if pending:
        lines.append("")
        lines.append(f"⏳ {len(pending)} coupon(s) en attente.")
    return "\n".join(lines)


def format_calibration(calibration: dict) -> str:
    if not calibration:
        return ("📐 <b>CALIBRATION</b>\n\nPas assez de données "
                "historiques pour mesurer la réussite observée.")
    lines = ["📐 <b>CALIBRATION DU MODÈLE</b>",
             "<i>Proba prédite → réussite réellement observée</i>", ""]
    for b in sorted(calibration):
        lo = b * 10
        hi = lo + 10
        lines.append(f"   {lo:>2}-{hi:<2}%  →  <b>{calibration[b] * 100:.1f}%</b>")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 10bis. CLAVIER
# ════════════════════════════════════════════════════════════════
BTN_SAFE       = "🟢 SÉCURISÉ"
BTN_BALANCED   = "🟡 ÉQUILIBRÉ"
BTN_AGGRESSIVE = "🔴 AGRESSIF"
BTN_VALUE      = "💎 VALUE"
BTN_BILAN      = "📊 BILAN"
BTN_SCAN       = "🔄 SCAN"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SAFE),       KeyboardButton(text=BTN_BALANCED)],
        [KeyboardButton(text=BTN_AGGRESSIVE), KeyboardButton(text=BTN_VALUE)],
        [KeyboardButton(text=BTN_BILAN),      KeyboardButton(text=BTN_SCAN)],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    input_field_placeholder="Choisis un ticket…",
)


# ════════════════════════════════════════════════════════════════
# 11. PERSISTANCE
# ════════════════════════════════════════════════════════════════
async def _gist_load() -> Optional[dict]:
    if not (GIST_ID and GITHUB_TOKEN):
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GITHUB_TOKEN}",
                         "Accept": "application/vnd.github+json"},
                timeout=15.0)
            if r.status_code != 200:
                return None
            for name, f in (r.json().get("files") or {}).items():
                if name.endswith(".json"):
                    content = f.get("content", "")
                    return json.loads(content) if content.strip() else {}
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        print(f"⚠️ Lecture Gist échouée : {e}")
    return None


async def _gist_save(state: dict) -> bool:
    if not (GIST_ID and GITHUB_TOKEN):
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"https://api.github.com/gists/{GIST_ID}",
                headers={"Authorization": f"token {GITHUB_TOKEN}",
                         "Accept": "application/vnd.github+json"},
                json={"files": {"wallstreet.json":
                                {"content": json.dumps(state, indent=2)}}},
                timeout=15.0)
            return r.status_code == 200
    except httpx.HTTPError as e:
        print(f"⚠️ Écriture Gist échouée : {e}")
        return False


async def load_tracker() -> dict:
    data = await _gist_load()
    if data is None:
        try:
            with open(LOCAL_DB, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = None
    return data if data else {"coupons": []}


async def save_tracker(data: dict):
    if GIST_ID and GITHUB_TOKEN and await _gist_save(data):
        return
    try:
        with open(LOCAL_DB, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"⚠️ Sauvegarde locale échouée : {e}")


# ════════════════════════════════════════════════════════════════
# 12. TRACKER
# ════════════════════════════════════════════════════════════════
async def record_coupons(coupons: list[Coupon], date_str: str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}

    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing:
            continue
        legs = []
        for leg in c.legs:
            legs.append({
                "league": leg.league_code,
                "home_api": leg.home, "away_api": leg.away,
                "home_model": leg.home_model, "away_model": leg.away_model,
                "pick": leg.pick, "odds": leg.odds,
                "prob": round(leg.pick_prob(), 4),
                "score":    leg.reliability.score if leg.reliability else None,
                "grade":    leg.reliability.grade if leg.reliability else None,
                "observed": leg.reliability.observed_rate if leg.reliability else None,
                "status": "pending", "clv": None,
            })
        tracker["coupons"].append({
            "id": cid, "date": date_str, "name": c.name, "emoji": c.emoji,
            "combined_odds": c.combined_odds, "combined_prob": c.combined_prob,
            "combined_ev": c.combined_ev,
            "status": "pending", "profit": 0.0, "legs": legs,
        })

    tracker["coupons"] = tracker["coupons"][-500:]
    await save_tracker(tracker)
    print(f"💾 {len(coupons)} coupon(s) enregistré(s).")


def _resolve_leg(leg: dict, match: Match):
    hg, ag = match.home_goals, match.away_goals
    if leg["pick"] == "1":
        leg["status"] = "win" if hg > ag else "loss"
    elif leg["pick"] == "2":
        leg["status"] = "win" if ag > hg else "loss"
    else:
        leg["status"] = "win" if hg == ag else "loss"

    close = {"1": match.close_home, "X": match.close_draw,
             "2": match.close_away}.get(leg["pick"])
    if close and leg.get("odds"):
        leg["clv"] = round(leg["odds"] / close - 1, 4)


async def resolve_pending() -> int:
    tracker = STATE["tracker"]
    pending = [c for c in tracker["coupons"] if c.get("status") == "pending"]
    if not pending:
        return 0
    pending_leagues = {leg["league"] for c in pending
                       for leg in c["legs"] if leg["status"] == "pending"}
    if not pending_leagues:
        return 0

    csv_provider = CSVProvider()
    matches = await csv_provider._download(
        [lg for lg in LEAGUES if lg.code in pending_leagues], season_codes())

    # Index par (league, home_normalisé, away_normalisé)
    index = {}
    for m in matches:
        index[(m.league, normalize(m.home), normalize(m.away))] = m

    resolved_something = 0
    for c in pending:
        for leg in c["legs"]:
            if leg["status"] != "pending":
                continue
            # Essaie matching exact puis via alias
            key = (leg["league"],
                   normalize(leg["home_model"]),
                   normalize(leg["away_model"]))
            m = index.get(key)
            if m is None:
                # Matching via find_team
                for candidate in matches:
                    if candidate.league != leg["league"]:
                        continue
                    if find_team(candidate.home, {leg["home_model"]: 1}) \
                       and find_team(candidate.away, {leg["away_model"]: 1}) \
                       and normalize(candidate.home) in normalize(leg["home_api"]) \
                       or normalize(candidate.away) in normalize(leg["away_api"]):
                        m = candidate
                        break
            if m is not None:
                _resolve_leg(leg, m)
        if all(l["status"] != "pending" for l in c["legs"]):
            won = all(l["status"] == "win" for l in c["legs"])
            c["status"] = "win" if won else "loss"
            c["profit"] = round(c["combined_odds"] - 1, 4) if won else -1.0
            resolved_something += 1

    if resolved_something:
        await save_tracker(tracker)
        print(f"✅ {resolved_something} coupon(s) résolu(s).")
    return resolved_something


# ════════════════════════════════════════════════════════════════
# 13. TELEGRAM
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
}

TARGETS: list[int | str] = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if not raw:
        continue
    try:
        TARGETS.append(int(raw))
    except ValueError:
        TARGETS.append(raw)


async def _safe_reply(message: Message, text: str):
    """Envoie un message et protège le bot des crashs."""
    try:
        await message.answer(text, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        print(f"⚠️ Réponse échouée : {e}")


# ── COMMANDES ──────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message):
    fusion = "activée" if (W_API > 0 and THESTATSAPI_KEY) else "désactivée"
    await _safe_reply(message,
        "👋 <b>WallStreet OS v8.0</b>\n\n"
        f"Chaque jour à <b>{NOTIFY_HOUR:02d}:{NOTIFY_MINUTE:02d}</b> "
        f"({NOTIFY_TZ}), je t'envoie les <b>4 tickets du jour</b>.\n\n"
        f"🌍 Ligues couvertes : <b>{len(LEAGUES)}</b>\n"
        f"📐 Calibration : <b>{len(STATE['calibration'])} buckets</b>\n"
        f"🔀 Fusion TheStatsAPI : <b>{fusion}</b>\n\n"
        "👇 Utilise les boutons sous le clavier.")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await _safe_reply(message,
        "❓ <b>AIDE</b>\n\n"
        "• /start — accueil\n"
        "• /scan — relancer le scan manuellement\n"
        "• /today — liste des matchs analysés\n"
        "• /coupons — envoyer les 4 tickets du jour\n"
        "• /bilan — performance du système\n"
        "• /calibration — qualité du modèle\n"
        "• /stats — état interne\n"
        "• /reload — recharger les modèles depuis le CSV\n\n"
        "👇 Les boutons sous le clavier font la même chose.")


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await _safe_reply(message, "🎟️ Menu affiché sous le clavier 👇")


@dp.message(Command("coupons"))
async def cmd_coupons(message: Message):
    if not STATE["coupons"]:
        await _safe_reply(message, "Aucun ticket. Appuie sur 🔄 SCAN.")
        return
    for c in STATE["coupons"]:
        await message.answer(format_coupon(c))
        await asyncio.sleep(0.3)


@dp.message(Command("today"))
async def cmd_today(message: Message):
    if not STATE["pronos"]:
        await _safe_reply(message, "Pas encore de scan. Appuie sur 🔄 SCAN.")
        return
    lines = [f"📅 <b>PRONOSTICS DU JOUR</b> — {len(STATE['pronos'])} matchs\n"]
    for p in STATE["pronos"][:20]:
        lines.append(format_prono(p))
    await _safe_reply(message, "\n".join(lines))


@dp.message(Command("scan"))
async def cmd_scan(message: Message):
    await _safe_reply(message, "⏳ Scan en cours…")
    await scan(window_hours=SCAN_WINDOW_HOURS)
    await _safe_reply(message,
                      f"✅ {len(STATE['pronos'])} matchs · "
                      f"{len(STATE['coupons'])} tickets.")


@dp.message(Command("reload"))
async def cmd_reload(message: Message):
    await _safe_reply(message, "♻️ Rechargement des modèles…")
    try:
        await bootstrap_models()
        await _safe_reply(message,
                          f"✅ Modèles rechargés "
                          f"({len(STATE['models'])} ligues).")
    except Exception as e:
        await _safe_reply(message, f"❌ Échec : {e}")


@dp.message(Command("bilan"))
async def cmd_bilan(message: Message):
    await _safe_reply(message, format_bilan(STATE["tracker"]))


@dp.message(Command("calibration"))
async def cmd_calibration(message: Message):
    await _safe_reply(message, format_calibration(STATE["calibration"]))


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    total_teams = sum(len(m["teams"]) for m in STATE["models"].values())
    coupons = STATE["tracker"]["coupons"]
    resolved = sum(1 for c in coupons if c.get("status") in ("win", "loss"))
    fusion = "ON" if (W_API > 0 and THESTATSAPI_KEY) else "OFF"
    quota_txt = (f"{QUOTA['used']}/{QUOTA['limit']}"
                 if QUOTA["remaining"] is None
                 else f"{QUOTA['used']} utilisés, {QUOTA['remaining']} restants")
    await _safe_reply(message,
        f"📊 <b>État du système — v8.0</b>\n\n"
        f"Ligues modélisées : <b>{len(STATE['models'])}/{len(LEAGUES)}</b>\n"
        f"Équipes connues : <b>{total_teams}</b>\n"
        f"Fusion API : <b>{fusion}</b> (w_csv={W_CSV} / w_api={W_API})\n"
        f"Fenêtre de scan : <b>{SCAN_WINDOW_HOURS}h</b>\n"
        f"Pronos analysés : <b>{len(STATE['pronos'])}</b>\n"
        f"Tickets actifs : <b>{len(STATE['coupons'])}</b>\n"
        f"Historique : <b>{len(coupons)}</b> ({resolved} résolus)\n"
        f"Buckets calibration : <b>{len(STATE['calibration'])}</b>\n"
        f"Quota Odds API : <b>{quota_txt}</b>\n"
        f"Dernier scan : <b>{STATE['last_scan'] or 'jamais'}</b>\n"
        f"Prochaine diffusion : <b>{NOTIFY_HOUR:02d}:{NOTIFY_MINUTE:02d}</b>")


# ── BOUTONS ────────────────────────────────────────────────────
async def _send_coupon(message: Message, name: str):
    coupon = next((c for c in STATE["coupons"] if c.name == name), None)
    if not coupon:
        await _safe_reply(message,
            f"❌ Aucun ticket « {name} » aujourd'hui.\n"
            f"Appuie sur 🔄 SCAN pour rafraîchir.")
        return
    await _safe_reply(message, format_coupon(coupon))


@dp.message(F.text == BTN_SAFE)
async def btn_safe(m: Message):
    await _send_coupon(m, "SÉCURISÉ")

@dp.message(F.text == BTN_BALANCED)
async def btn_balanced(m: Message):
    await _send_coupon(m, "ÉQUILIBRÉ")

@dp.message(F.text == BTN_AGGRESSIVE)
async def btn_aggressive(m: Message):
    await _send_coupon(m, "AGRESSIF")

@dp.message(F.text == BTN_VALUE)
async def btn_value(m: Message):
    await _send_coupon(m, "VALUE")

@dp.message(F.text == BTN_BILAN)
async def btn_bilan(m: Message):
    await _safe_reply(m, format_bilan(STATE["tracker"]))

@dp.message(F.text == BTN_SCAN)
async def btn_scan(m: Message):
    await cmd_scan(m)


@dp.errors()
async def on_error(event):
    print(f"⚠️ Erreur handler : {event.exception}")


# ════════════════════════════════════════════════════════════════
# 14. SCAN
# ════════════════════════════════════════════════════════════════
async def scan(window_hours: int = SCAN_WINDOW_HOURS):
    if not STATE["ready"]:
        print("⏳ Modèles pas prêts.")
        return

    now_utc = datetime.now(timezone.utc)
    limit = now_utc + timedelta(hours=window_hours)
    print(f"🔄 Scan {now_utc:%d/%m %H:%M} → {limit:%d/%m %H:%M} UTC "
          f"(fenêtre {window_hours}h)")

    pronos: list[Prono] = []
    skipped = 0
    unmatched = 0
    api_events = 0

    async with httpx.AsyncClient() as client:
        for lg in LEAGUES:
            model = STATE["models"].get(lg.code)
            if not model or not model["teams"]:
                continue
            events = await fetch_odds(client, lg)
            api_events += len(events)
            lg_ok = 0
            for ev in events:
                try:
                    ko = datetime.fromisoformat(
                        ev["commence_time"].replace("Z", "+00:00"))
                except (KeyError, ValueError, TypeError):
                    skipped += 1
                    continue
                if not (now_utc <= ko <= limit):
                    skipped += 1
                    continue
                if not find_team(ev.get("home_team", ""), model["teams"]) \
                   or not find_team(ev.get("away_team", ""), model["teams"]):
                    unmatched += 1
                    continue
                p = analyze(lg, ev, model, STATE["calibration"])
                if p:
                    pronos.append(p)
                    lg_ok += 1
            print(f"   {lg.name}: {len(events)} events → {lg_ok} pronos")
            await asyncio.sleep(0.5)

    STATE["api_events"] = api_events
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

    print("──────── RÉSUMÉ SCAN ────────")
    print(f"Events API      : {api_events}")
    print(f"Hors fenêtre    : {skipped}")
    print(f"Non reconnus    : {unmatched}")
    print(f"Pronos retenus  : {len(pronos)}")
    print(f"Coupons         : {len(coupons)}")
    print("─────────────────────────────")


# ════════════════════════════════════════════════════════════════
# 15. DIFFUSION
# ════════════════════════════════════════════════════════════════
async def _send(chat_id, text: str):
    for attempt in range(3):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            return True
        except Exception as e:
            print(f"⚠️ Envoi vers {chat_id} (essai {attempt + 1}) : {e}")
            await asyncio.sleep(1.5 * (attempt + 1))
    return False


async def daily_broadcast():
    print("🔔 Diffusion quotidienne...")
    await scan(window_hours=SCAN_WINDOW_HOURS)

    now = datetime.now(ZoneInfo(NOTIFY_TZ))
    date_str = now.strftime("%Y-%m-%d")
    await record_coupons(STATE["coupons"], date_str)
    await resolve_pending()

    pronos  = STATE["pronos"]
    coupons = STATE["coupons"]

    if not pronos:
        if STATE.get("api_events", 0) == 0:
            header = ("🔔 <b>WallStreet OS — AUCUN MATCH</b>\n\n"
                      "⚠️ Aucun match dans les ligues couvertes dans les "
                      f"prochaines {SCAN_WINDOW_HOURS}h, ou quota Odds API "
                      "atteint.\n"
                      "👉 Appuie sur 🔄 SCAN pour réessayer.")
        else:
            header = (f"🔔 <b>WallStreet OS — {now:%d/%m/%Y}</b>\n\n"
                      f"ℹ️ Aucun match à venir dans les prochaines "
                      f"{SCAN_WINDOW_HOURS}h.")
    else:
        header = (f"🔔 <b>TICKETS DU JOUR</b>\n"
                  f"📅 {now:%d/%m/%Y}\n\n"
                  f"❯ <b>{len(pronos)}</b> matchs analysés "
                  f"(fenêtre {SCAN_WINDOW_HOURS}h, {len(LEAGUES)} ligues)\n"
                  f"🎟️ <b>{len(coupons)}</b> tickets constitués\n\n"
                  "👇 Choisis ton ticket sous le clavier.")

    for chat_id in TARGETS:
        if await _send(chat_id, header):
            for c in coupons:
                await _send(chat_id, format_coupon(c))
                await asyncio.sleep(0.6)
    print(f"✅ Diffusion terminée ({len(coupons)} tickets).")


async def startup_notice():
    fusion_txt = "activée" if (W_API > 0 and THESTATSAPI_KEY) else "désactivée"
    text = (f"🟢 <b>WallStreet OS v8.0 en ligne.</b>\n\n"
            f"✅ Modèles + calibration chargés\n"
            f"🌍 <b>{len(LEAGUES)} ligues</b> couvertes\n"
            f"🔀 Fusion TheStatsAPI : <b>{fusion_txt}</b>\n"
            f"⏰ Diffusion à <b>{NOTIFY_HOUR:02d}:{NOTIFY_MINUTE:02d}</b> "
            f"({NOTIFY_TZ})\n"
            f"🎯 Fenêtre : <b>{SCAN_WINDOW_HOURS}h</b>\n\n"
            "👇 Utilise les boutons sous le clavier.")
    for chat_id in TARGETS:
        await _send(chat_id, text)


# ════════════════════════════════════════════════════════════════
# 16. BOOTSTRAP
# ════════════════════════════════════════════════════════════════
async def bootstrap_models():
    print("📥 Chargement des données...")
    csv_provider = CSVProvider()
    api = TheStatsAPIProvider(THESTATSAPI_KEY, THESTATSAPI_BASE,
                              THESTATSAPI_AUTH)

    by_league: dict[str, list[Match]] = defaultdict(list)
    all_matches = await csv_provider._download(LEAGUES, season_codes())
    for m in all_matches:
        by_league[m.league].append(m)
    print(f"   CSV : {len(all_matches)} matchs "
          f"({len(LEAGUES)} ligues × {len(season_codes())} saisons)")

    STATE["calibration"] = compute_calibration(by_league)

    for lg in LEAGUES:
        csv_model = compute_model(by_league.get(lg.code, []))
        if W_API > 0 and api.available:
            try:
                api_model = await api.team_strengths(lg)
            except Exception as e:
                print(f"   ⚠️ API {lg.name} : {e}")
                api_model = {"teams": {}, "avg_home": 1.5, "avg_away": 1.2}
            fused = fuse_models(csv_model, api_model)
        else:
            fused = csv_model
        STATE["models"][lg.code] = fused
        print(f"   {lg.name}: {len(fused['teams'])} équipes")

    STATE["ready"] = True
    print("✅ Modèles prêts.")


# ════════════════════════════════════════════════════════════════
# 17. FASTAPI
# ════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"⚠️ delete_webhook : {e}")

    STATE["tracker"] = await load_tracker()
    await bootstrap_models()

    tz = ZoneInfo(NOTIFY_TZ)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(daily_broadcast,
                      CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE,
                                  timezone=tz),
                      id="daily_coupons", replace_existing=True,
                      max_instances=1, coalesce=True)
    scheduler.add_job(resolve_pending, "interval", hours=6,
                      id="resolver", replace_existing=True,
                      max_instances=1, coalesce=True)
    scheduler.start()

    bot_task = asyncio.create_task(dp.start_polling(bot))

    await startup_notice()
    asyncio.create_task(scan())
    asyncio.create_task(resolve_pending())

    yield

    scheduler.shutdown(wait=False)
    bot_task.cancel()
    try:
        await bot.session.close()
    except Exception:
        pass


app = FastAPI(title="WallStreet OS v8.0", lifespan=lifespan)


@app.get("/")
async def health():
    return {
        "status": "ONLINE",
        "version": "8.0",
        "ready": STATE["ready"],
        "leagues": len(LEAGUES),
        "fusion": (W_API > 0 and bool(THESTATSAPI_KEY)),
        "weights": {"csv": W_CSV, "api": W_API},
        "quota": QUOTA,
        "calibration_buckets": len(STATE["calibration"]),
        "last_scan": STATE["last_scan"],
        "pronos": len(STATE["pronos"]),
        "coupons": [c.name for c in STATE["coupons"]],
        "tracked": len(STATE["tracker"]["coupons"]),
        "next_broadcast": f"{NOTIFY_HOUR:02d}:{NOTIFY_MINUTE:02d} {NOTIFY_TZ}",
    }


@app.get("/ping")
async def ping():
    return {"ok": True, "t": datetime.now(timezone.utc).isoformat()}


@app.get("/debug")
async def debug():
    return {
        "models_teams": {k: len(v["teams"]) for k, v in STATE["models"].items()},
        "pronos": [
            {"match": f"{p.home} – {p.away}", "pick": p.pick,
             "odds": p.odds, "ev": p.ev, "score":
             p.reliability.score if p.reliability else None}
            for p in STATE["pronos"][:20]
        ],
        "coupons": [
            {"name": c.name, "odds": c.combined_odds,
             "legs": [f"{l.home} – {l.away} ({l.pick})" for l in c.legs]}
            for c in STATE["coupons"]
        ],
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", 8080)), reload=False)
