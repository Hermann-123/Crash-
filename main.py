from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import secrets
import string
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
    from gemini_predictor import predict_match_probas
except Exception as e:
    predict_match_probas = None
    print(f"❌ gemini_predictor: {e}")

try:
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception:
    enrich_match_with_bzzoiro = None

try:
    from bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
except Exception:
    fetch_bzzoiro_predictions_direct = None

try:
    from bzzoiro_odds import fetch_all_odds as fetch_bzzoiro_odds
except Exception:
    fetch_bzzoiro_odds = None

try:
    from dixon_coles import download_and_train
except Exception:
    download_and_train = None

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

try:
    ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except ValueError:
    ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "10"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")

BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • 4 Moteurs")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "20"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

# MOTEUR 1 : ODDS +3
ODDS3_MIN_SCORE = 80
ODDS3_FALLBACK_SCORE = 75
ODDS3_MIN_ODDS = 3.0
ODDS3_MAX_ODDS = 4.5
ODDS3_MIN_LEG_ODDS = 1.55
ODDS3_MAX_LEG_ODDS = 2.20
ODDS3_MIN_LEG_ODDS_ABS = 1.25
ODDS3_MIN_VALUE = 0.05
ODDS3_MIN_LEGS = 2
ODDS3_MAX_LEGS = 3

# MOTEUR 2 : ODDS +10
ODDS10_MIN_SCORE = 80
ODDS10_FALLBACK_SCORE = 75
ODDS10_MIN_ODDS = 10.0
ODDS10_MIN_LEG_ODDS = 1.40
ODDS10_MAX_LEG_ODDS = 1.60
ODDS10_MIN_PROB = 0.68
ODDS10_MIN_VALUE = 0.03
ODDS10_MIN_LEGS = 6
ODDS10_MAX_LEGS = 8
ODDS10_MAX_SAME_MARKET = 2
ODDS10_MAX_SAME_LEAGUE = 2

# MOTEUR 3 : MT-FIN
MTFIN_MIN_SCORE = 80
MTFIN_FALLBACK_SCORE = 75
MTFIN_MIN_PROB = 0.40
MTFIN_TOP_N = 3
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45
MTFIN_MIN_COMBINED_ODDS = 1.35

# MOTEUR 4 : MATCH NUL
DRAW_MIN_SCORE = 80
DRAW_FALLBACK_SCORE = 75
DRAW_MIN_PROB = 0.28
DRAW_GEMINI_MIN_PROB = 0.22
DRAW_MIN_ODD = 3.10
DRAW_MIN_HOME_ODD = 1.80
DRAW_MAX_HOME_ODD = 4.50
DRAW_MIN_AWAY_ODD = 1.80
DRAW_MAX_AWAY_ODD = 4.50
DRAW_TOP_N = 3

TELEGRAM_MAX_LEN = 3800

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY manquant")
if not ADMIN_ID:
    raise RuntimeError("TELEGRAM_ADMIN_ID manquant")

TZ = ZoneInfo(NOTIFY_TZ)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

SOCCER_KEYS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_efl_champ", "soccer_efl_league_one",
    "soccer_spain_la_liga2", "soccer_italy_serie_b",
    "soccer_germany_bundesliga2", "soccer_france_ligue_two",
    "soccer_netherlands_eredivisie", "soccer_netherlands_eerste_divisie",
    "soccer_portugal_primeira_liga", "soccer_belgium_first_div",
    "soccer_turkey_super_league", "soccer_greece_super_league",
    "soccer_scotland_premiership",
    "soccer_brazil_campeonato", "soccer_argentina_primera_division",
    "soccer_usa_mls", "soccer_mexico_ligamx", "soccer_japan_j_league",
]

def today_pretty():
    months = {1:"janvier",2:"février",3:"mars",4:"avril",5:"mai",6:"juin",
              7:"juillet",8:"août",9:"septembre",10:"octobre",11:"novembre",12:"décembre"}
    d = datetime.now(TZ)
    return f"{d.day} {months[d.month]} {d.year}"

def normalize_name(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_dt_safe(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(int(s), tz=ZoneInfo("UTC")).astimezone(TZ)
        except Exception:
            return None
    s = str(s).strip()
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None

def format_kickoff(kickoff_str) -> str:
    if not kickoff_str:
        return "?"
    s = str(kickoff_str).strip()
    dt = parse_dt_safe(s)
    if dt:
        if "T" in s or (" " in s and len(s) > 10):
            return dt.strftime("%d/%m %H:%M")
        return dt.strftime("%d/%m")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(3)}/{m.group(2)}"
    return "?"

def prob_bar(p: float) -> str:
    if p >= 0.80: return "🟢🟢🟢"
    if p >= 0.72: return "🟢🟢⚪"
    if p >= 0.65: return "🟢⚪⚪"
    return "⚪⚪⚪"

def quality_bar(score: int) -> str:
    if score >= 90: return "⭐⭐⭐⭐⭐"
    if score >= 85: return "⭐⭐⭐⭐"
    if score >= 80: return "⭐⭐⭐"
    if score >= 75: return "⭐⭐"
    return "⭐"

def split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current: parts.append(current.rstrip())
            current = line + "\n"
        else: current += line + "\n"
    if current.strip(): parts.append(current.rstrip())
    return parts

def _poisson_sample(lam: float) -> int:
    if lam <= 0: return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L and k < 20:
        k += 1
        p *= random.random()
    return k - 1

def _binomial_sample(n: int, p: float) -> int:
    return sum(1 for _ in range(n) if random.random() < p)

def simulate_htft(lambda_home: float, lambda_away: float, n_sims: int = MTFIN_SIMULATIONS) -> dict:
    counts = {}
    for _ in range(n_sims):
        hg = _poisson_sample(lambda_home)
        ag = _poisson_sample(lambda_away)
        ht_hg = _binomial_sample(hg, MTFIN_FIRST_HALF_RATIO)
        ht_ag = _binomial_sample(ag, MTFIN_FIRST_HALF_RATIO)
        ht_res = "V1" if ht_hg > ht_ag else ("X" if ht_hg == ht_ag else "V2")
        ft_res = "V1" if hg > ag else ("X" if hg == ag else "V2")
        key = f"{ht_res}/{ft_res}"
        counts[key] = counts.get(key, 0) + 1
    return {k: round(v / n_sims, 4) for k, v in counts.items()}

W_GEMINI = 0.35
W_DIXON = 0.25
W_BZZOIRO = 0.40

def combine_sources(gem, dixon, bzz):
    result = {}
    g, d, b = {}, {}, {}
    for k in ["victoire_home", "match_nul", "victoire_away", "over_2.5", "under_2.5",
              "over_3.5", "btts_yes", "btts_no", "btts_over_25"]:
        v = gem.get(k)
        if v is not None:
            try: g[k] = max(0, min(1, float(v) / 100.0))
            except: pass
    if dixon:
        d = {"victoire_home": dixon.get("p_home",0), "match_nul": dixon.get("p_draw",0),
             "victoire_away": dixon.get("p_away",0), "over_2.5": dixon.get("over_2.5",0),
             "under_2.5": dixon.get("under_2.5",0), "over_3.5": dixon.get("over_3.5",0),
             "btts_yes": dixon.get("btts_yes",0), "btts_no": dixon.get("btts_no",0)}
    if bzz:
        for k in list(g.keys()) + ["btts_over_25"]:
            v = bzz.get(k)
            if v is not None:
                try: b[k] = max(0, min(1, float(v) / 100.0))
                except: pass
    all_k = set(g) | set(d) | set(b)
    for k in all_k:
        tw, tv = 0.0, 0.0
        if k in g: tv += W_GEMINI * g[k]; tw += W_GEMINI
        if k in d and d[k] > 0: tv += W_DIXON * d[k]; tw += W_DIXON
        if k in b: tv += W_BZZOIRO * b[k]; tw += W_BZZOIRO
        if tw > 0: result[k] = tv / tw
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = result["victoire_home"] + result["match_nul"]
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = result["victoire_home"] + result["victoire_away"]
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = result["match_nul"] + result["victoire_away"]
    return result

@dataclass
class MatchData:
    event_id: str
    league: str
    home: str
    away: str
    kickoff: str
    real_odds: dict
    bzz_markets: dict
    bzz_confidence: float
    gemini_markets: dict = None
    dixon_pred: dict = None
    analysis: str = ""
    advices: list = None
    has_h2h: bool = False
    h2h_data: dict = None
    sport_key: str = ""
    bzz_odds_source: str = ""

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
    real_odd: Optional[float] = None
    value: float = -1.0
    quality: int = 0
    prob_gemini: float = 0.0
    prob_dixon: float = 0.0
    prob_bzzoiro: float = 0.0
    analysis: str = ""
    advices: list = None
    htft_pick: str = ""
    prob_sources_agreement: str = ""

    def final_prob(self) -> float:
        return self.model_prob

@dataclass
class Coupon:
    name: str
    subtitle: str
    emoji: str
    legs: list
    combined_odds: float
    combined_prob: float
    is_list: bool = False
    show_analysis: bool = False

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BTN_ODDS3 = "🎯 Odds +3"
BTN_ODDS10 = "🔥 Odds +10"
BTN_MTFT = "⏱ MT-Fin"
BTN_DRAW = "🤝 Match Nul"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"
BTN_ACTIVATE = "🔑 Activer mon abonnement"

USER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_ODDS10)],
        [KeyboardButton(text=BTN_MTFT), KeyboardButton(text=BTN_DRAW)],
        [KeyboardButton(text=BTN_BILAN), KeyboardButton(text=BTN_ACTIVATE)],
    ], resize_keyboard=True, input_field_placeholder="Choisis un ticket…")

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_ODDS10)],
        [KeyboardButton(text=BTN_MTFT), KeyboardButton(text=BTN_DRAW)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Debug")],
    ], resize_keyboard=True, input_field_placeholder="Admin…")

STATE = {
    "ready": False, "coupons": [], "last_scan": None, "debug": {},
    "tracker": {"coupons": []}, "keys": {}, "users": {},
    "scan_lock": asyncio.Lock(), "dixon_model": None,
}

TARGETS = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try: TARGETS.append(int(raw))
        except ValueError: TARGETS.append(raw)

def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except: pass

def is_subscribed(user_id):
    uid = str(user_id)
    if uid not in STATE["users"]: return False
    exp = STATE["users"][uid].get("expires_at")
    if not exp: return False
    try: return datetime.now(TZ) < datetime.fromisoformat(exp)
    except: return False

def get_subscription_message(user_id):
    if is_subscribed(user_id):
        exp = STATE["users"][str(user_id)]["expires_at"]
        return f"✅ Actif jusqu'au {datetime.fromisoformat(exp).strftime('%d/%m/%Y à %H:%M')}."
    return "❌ Abonnement expiré.\n@hermann1123"

def load_tracker():
    try:
        with open(LOCAL_DB, "r", encoding="utf-8") as f: return json.load(f)
    except: return {"coupons": []}

def save_tracker(data):
    try:
        with open(LOCAL_DB, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except: pass

def load_used_today():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    u = STATE["tracker"].get("used_today", {})
    if u.get("date") != today: return set()
    return set(u.get("ids", []))

def save_used_today(ids):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    ex = STATE["tracker"].get("used_today", {})
    merged = (set(ex.get("ids", [])) | ids) if ex.get("date") == today else ids
    STATE["tracker"]["used_today"] = {"date": today, "ids": list(merged)}
    save_tracker(STATE["tracker"])

def mark_picks_used(selections):
    if selections: save_used_today({s.event_id for s in selections})

def record_coupons(coupons, date_str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing: continue
        legs = [{"event_id": s.event_id, "home": s.home, "away": s.away,
                 "market": s.market, "pick_label": s.pick_label, "odds": s.odds,
                 "kickoff": s.kickoff, "result": "pending"} for s in c.legs]
        tracker["coupons"].append({"id": cid, "date": date_str, "name": c.name,
                                   "combined_odds": c.combined_odds, "legs": legs, "status": "pending"})
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)

async def fetch_odds_api_league(client, sport_key):
    try:
        r = await client.get(f"{ODDS_API_BASE}/sports/{sport_key}/odds", params={
            "apiKey": ODDS_API_KEY, "regions": "eu", "markets": "h2h,totals,btts",
            "oddsFormat": "decimal"}, timeout=30.0)
        if r.status_code >= 400: return []
        data = r.json()
        return data if isinstance(data, list) else []
    except: return []

async def fetch_all_odds_events():
    events = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for k in SOCCER_KEYS:
            events.extend(await fetch_odds_api_league(client, k))
            await asyncio.sleep(0.15)
    print(f"💰 Odds API : {len(events)} events")
    return events

def extract_real_odds(ev):
    res = {"home": None, "draw": None, "away": None, "over_2.5": None,
           "under_2.5": None, "btts_yes": None, "btts_no": None}
    h, a = ev.get("home_team",""), ev.get("away_team","")
    for bk in ev.get("bookmakers", []) or []:
        for m in bk.get("markets", []) or []:
            k = m.get("key","")
            for o in m.get("outcomes", []) or []:
                try: p = float(o.get("price", 0))
                except: continue
                if p <= 1.01: continue
                n = o.get("name","")
                if k == "h2h":
                    if n == h and res["home"] is None: res["home"] = p
                    elif n == a and res["away"] is None: res["away"] = p
                    elif "draw" in n.lower() and res["draw"] is None: res["draw"] = p
                elif k == "totals" and o.get("point") == 2.5:
                    if "over" in n.lower() and res["over_2.5"] is None: res["over_2.5"] = p
                    elif "under" in n.lower() and res["under_2.5"] is None: res["under_2.5"] = p
                elif k == "btts":
                    if "yes" in n.lower() and res["btts_yes"] is None: res["btts_yes"] = p
                    elif "no" in n.lower() and res["btts_no"] is None: res["btts_no"] = p
    return res

async def build_match_pool(bzz_preds, odds_events):
    pool = []
    used_events = set()
    for bzz in bzz_preds:
        matched = None
        hn, an = normalize_name(bzz["home"]), normalize_name(bzz["away"])
        for ev in odds_events:
            eh, ea = normalize_name(ev.get("home_team","")), normalize_name(ev.get("away_team",""))
            if (hn in eh or eh in hn) and (an in ea or ea in an):
                matched = ev; break
        md = MatchData(
            event_id=bzz["event_id"], league=bzz.get("league",""),
            home=bzz["home"], away=bzz["away"], kickoff=bzz.get("kickoff",""),
            real_odds=extract_real_odds(matched) if matched else {},
            bzz_markets=bzz.get("bzz_markets", {}),
            bzz_confidence=bzz.get("bzz_confidence", 0),
            sport_key=matched.get("sport_key","") if matched else "",
        )
        if matched:
            used_events.add(matched.get("id"))
            if matched.get("commence_time"):
                md.kickoff = matched["commence_time"]
        pool.append(md)
    for ev in odds_events:
        if ev.get("id") in used_events: continue
        h, a = ev.get("home_team",""), ev.get("away_team","")
        if not h or not a: continue
        pool.append(MatchData(
            event_id=ev.get("id",""), league=ev.get("sport_title",""),
            home=h, away=a, kickoff=ev.get("commence_time",""),
            real_odds=extract_real_odds(ev), bzz_markets={}, bzz_confidence=0,
            sport_key=ev.get("sport_key",""),
        ))
    return pool

async def enrich_and_analyze(pool):
    async with httpx.AsyncClient(timeout=40.0) as client:
        for idx, md in enumerate(pool[:GEMINI_MAX_CALLS_PER_SCAN]):
            try:
                print(f"  [{idx+1}/{min(len(pool), GEMINI_MAX_CALLS_PER_SCAN)}] {md.home} vs {md.away}")
                ctx = None
                if enrich_match_with_bzzoiro:
                    dt = parse_dt_safe(md.kickoff)
                    date_iso = dt.strftime("%Y-%m-%d") if dt else datetime.now(TZ).strftime("%Y-%m-%d")
                    ctx = await enrich_match_with_bzzoiro(md.home, md.away, date_iso)
                    if ctx and ctx.get("available"):
                        md.has_h2h = True
                        md.h2h_data = ctx.get("h2h", {})
                if predict_match_probas:
                    pred = await predict_match_probas(client, md.home, md.away, md.league, md.kickoff, context=ctx)
                    if pred and "markets" in pred:
                        md.gemini_markets = pred.get("markets", {})
                        md.analysis = pred.get("analysis", "")
                        adv = []
                        for a in (pred.get("advices") or [])[:2]:
                            if isinstance(a, dict) and a.get("text"):
                                try: c = int(a.get("confidence", 0))
                                except: c = 0
                                adv.append({"text": str(a["text"]).strip(), "confidence": c})
                        md.advices = adv
                if STATE.get("dixon_model"):
                    try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                    except: pass
                await asyncio.sleep(1.2)
            except Exception as e:
                print(f"  ⚠️ {e}")

def compute_probability(md, market_key):
    gem = dix = bzz = 0.0
    if md.gemini_markets:
        v = md.gemini_markets.get(market_key)
        if v is not None:
            try: gem = float(v) / 100.0
            except: pass
    if md.dixon_pred:
        dk = {"victoire_home":"p_home","match_nul":"p_draw","victoire_away":"p_away",
              "over_2.5":"over_2.5","under_2.5":"under_2.5","over_3.5":"over_3.5",
              "btts_yes":"btts_yes","btts_no":"btts_no"}.get(market_key)
        if dk: dix = md.dixon_pred.get(dk, 0)
    if md.bzz_markets:
        v = md.bzz_markets.get(market_key)
        if v is not None:
            try: bzz = float(v) / 100.0
            except: pass
    sources = [x for x in (gem, dix, bzz) if x > 0]
    if not sources: return 0.0, gem, dix, bzz, ""
    final = sum(sources) / len(sources)
    if len(sources) >= 2:
        delta = max(sources) - min(sources)
        if len(sources) == 3 and delta <= 0.10: emoji = "🤝🤝"
        elif delta <= 0.10: emoji = "🤝"
        elif delta >= 0.15: emoji = "⚠️"
        else: emoji = ""
    else: emoji = ""
    return final, gem, dix, bzz, emoji

def compute_quality_odds3(md, market_key, prob, real_odd, value):
    pts = 0
    if real_odd: pts += 15
    if md.has_h2h: pts += 10
    if md.dixon_pred: pts += 10
    if md.bzz_markets: pts += 5
    _, g, d, b, emoji = compute_probability(md, market_key)
    n = sum(1 for x in (g, d, b) if x > 0)
    if n >= 3: pts += 25
    elif n == 2: pts += 15
    if value >= 0.10: pts += 20
    elif value >= 0.07: pts += 15
    elif value >= 0.05: pts += 10
    elif value >= 0.03: pts += 5
    if 1.60 <= real_odd <= 2.20: pts += 15
    elif 1.40 <= real_odd < 1.60: pts += 10
    elif real_odd < 1.30: pts -= 10
    return max(0, min(100, pts))

def build_odds3_coupon(pool):
    candidates = []
    for md in pool:
        for market_key, (label, mkt_type) in {
            "btts_yes": ("BTTS Oui", "BTTS"),
            "btts_no": ("BTTS Non", "BTTS"),
            "over_2.5": ("Over 2.5 buts", "O/U"),
            "under_2.5": ("Under 2.5 buts", "O/U"),
            "double_chance_1X": ("DC 1X", "DC"),
            "double_chance_X2": ("DC X2", "DC"),
            "victoire_home": (f"Victoire {md.home}", "1X2"),
            "victoire_away": (f"Victoire {md.away}", "1X2"),
        }.items():
            prob, g, d, b, emoji = compute_probability(md, market_key)
            if prob <= 0: continue
            if emoji == "⚠️": continue
            real_odd = None
            if market_key == "victoire_home": real_odd = md.real_odds.get("home")
            elif market_key == "victoire_away": real_odd = md.real_odds.get("away")
            elif market_key == "over_2.5": real_odd = md.real_odds.get("over_2.5")
            elif market_key == "under_2.5": real_odd = md.real_odds.get("under_2.5")
            elif market_key == "btts_yes": real_odd = md.real_odds.get("btts_yes")
            elif market_key == "btts_no": real_odd = md.real_odds.get("btts_no")
            
            if real_odd is None:
                real_odd = 1.0 / prob if prob > 0 else None
                if real_odd is None: continue
                is_estimated = True
            else:
                is_estimated = False
            
            if real_odd < ODDS3_MIN_LEG_ODDS_ABS: continue
            if not (ODDS3_MIN_LEG_ODDS <= real_odd <= ODDS3_MAX_LEG_ODDS): continue
            if prob < 0.50: continue
            
            if is_estimated:
                value = 0.0
            else:
                value = (prob * real_odd) - 1.0
                if value < ODDS3_MIN_VALUE: continue
            
            quality = compute_quality_odds3(md, market_key, prob, real_odd, value)
            if is_estimated: quality -= 15
            
            candidates.append(Selection(
                event_id=md.event_id, league=md.league, home=md.home, away=md.away,
                kickoff=md.kickoff, market=mkt_type, pick_label=label,
                odds=round(real_odd, 2), model_prob=round(prob, 4),
                real_odd=real_odd if not is_estimated else None,
                value=round(value, 4), quality=quality,
                prob_gemini=round(g, 3), prob_dixon=round(d, 3), prob_bzzoiro=round(b, 3),
                analysis=md.analysis, advices=md.advices,
                prob_sources_agreement=emoji,
            ))
    
    print(f"  → Odds +3 : {len(candidates)} candidats bruts")
    valid = [c for c in candidates if c.quality >= ODDS3_MIN_SCORE]
    if not valid:
        valid = [c for c in candidates if c.quality >= 60]
        if valid: print(f"  → Odds +3 : fallback à 60")
    
    if not valid: 
        print(f"  → Odds +3 : 0 match qualifié")
        return None
    
    valid.sort(key=lambda x: (x.quality, x.value), reverse=True)
    by_event = {}
    for c in valid:
        if c.event_id not in by_event: by_event[c.event_id] = c
    valid = list(by_event.values())
    valid.sort(key=lambda x: (x.quality, x.value), reverse=True)
    
    legs, used_events, used_markets, used_leagues = [], set(), set(), set()
    odds, prob = 1.0, 1.0
    for c in valid:
        if len(legs) >= ODDS3_MAX_LEGS: break
        if c.event_id in used_events: continue
        if c.market in used_markets: continue
        if c.league in used_leagues and len(legs) >= 2: continue
        next_o = odds * c.odds
        if next_o > ODDS3_MAX_ODDS and legs: continue
        legs.append(c)
        used_events.add(c.event_id)
        used_markets.add(c.market)
        used_leagues.add(c.league)
        odds = next_o
        prob *= c.model_prob
        if odds >= ODDS3_MIN_ODDS and len(legs) >= ODDS3_MIN_LEGS: break
    
    if len(legs) < ODDS3_MIN_LEGS or odds < ODDS3_MIN_ODDS:
        print(f"  → Odds +3 : seulement {len(legs)} legs, cote {odds:.2f}")
        return None
    
    print(f"  → Odds +3 OK : {len(legs)} legs, cote {odds:.2f}")
    return Coupon("Odds +3", f"Cote {ODDS3_MIN_ODDS}-{ODDS3_MAX_ODDS} • Anti-Vague",
                  "🎯", legs, round(odds, 2), round(prob, 4), show_analysis=True)

def compute_quality_odds10(md, market_key, prob, real_odd, value):
    pts = 0
    if real_odd: pts += 15
    if md.has_h2h: pts += 10
    if md.dixon_pred: pts += 10
    if md.bzz_markets: pts += 5
    _, g, d, b, emoji = compute_probability(md, market_key)
    n = sum(1 for x in (g,d,b) if x > 0)
    if n >= 3: pts += 25
    elif n == 2: pts += 15
    if prob >= 0.75: pts += 20
    elif prob >= 0.68: pts += 10
    if value >= 0.05: pts += 10
    elif value >= 0.03: pts += 5
    pts += 5
    return max(0, min(100, pts))

def build_odds10_coupon(pool):
    candidates = []
    for md in pool:
        home_odd = md.real_odds.get("home")
        away_odd = md.real_odds.get("away")
        if home_odd and home_odd < 1.30: continue
        if away_odd and away_odd < 1.30: continue
        
        for market_key, (label, mkt_type) in {
            "btts_yes": ("BTTS Oui", "BTTS"),
            "over_2.5": ("Over 2.5 buts", "O/U"),
            "under_2.5": ("Under 2.5 buts", "O/U"),
            "double_chance_1X": ("DC 1X", "DC"),
            "double_chance_X2": ("DC X2", "DC"),
            "victoire_home": (f"Victoire {md.home}", "1X2"),
            "victoire_away": (f"Victoire {md.away}", "1X2"),
        }.items():
            prob, g, d, b, emoji = compute_probability(md, market_key)
            if prob < ODDS10_MIN_PROB: continue
            if emoji == "⚠️": continue
            real_odd = None
            if market_key == "victoire_home": real_odd = home_odd
            elif market_key == "victoire_away": real_odd = away_odd
            elif market_key == "over_2.5": real_odd = md.real_odds.get("over_2.5")
            elif market_key == "under_2.5": real_odd = md.real_odds.get("under_2.5")
            elif market_key == "btts_yes": real_odd = md.real_odds.get("btts_yes")
            if real_odd is None:
                real_odd = 1.0 / prob if prob > 0 else None
                if real_odd is None: continue
                is_est = True
            else:
                is_est = False
            
            if not is_est:
                if not (ODDS10_MIN_LEG_ODDS <= real_odd <= ODDS10_MAX_LEG_ODDS): continue
                value = (prob * real_odd) - 1.0
                if value < ODDS10_MIN_VALUE: continue
            else:
                if not (1.10 <= real_odd <= 1.55): continue
                value = 0.0
            
            quality = compute_quality_odds10(md, market_key, prob, real_odd, value)
            candidates.append(Selection(
                event_id=md.event_id, league=md.league, home=md.home, away=md.away,
                kickoff=md.kickoff, market=mkt_type, pick_label=label,
                odds=round(real_odd,2), model_prob=round(prob,4),
                real_odd=real_odd if not is_est else None,
                value=round(value,4), quality=quality,
                prob_gemini=round(g,3), prob_dixon=round(d,3), prob_bzzoiro=round(b,3),
            ))
    
    print(f"  → Odds +10 : {len(candidates)} candidats bruts")
    valid = [c for c in candidates if c.quality >= ODDS10_MIN_SCORE]
    if not valid:
        valid = [c for c in candidates if c.quality >= 60]
    
    if not valid:
        print(f"  → Odds +10 : 0 match")
        return None
    
    by_event = {}
    for c in valid:
        if c.event_id not in by_event or c.quality > by_event[c.event_id].quality:
            by_event[c.event_id] = c
    valid = list(by_event.values())
    valid.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    
    legs, used_events = [], set()
    market_count = {}
    league_count = {}
    odds, prob = 1.0, 1.0
    
    for c in valid:
        if len(legs) >= ODDS10_MAX_LEGS: break
        if c.event_id in used_events: continue
        if market_count.get(c.market, 0) >= ODDS10_MAX_SAME_MARKET: continue
        if league_count.get(c.league, 0) >= ODDS10_MAX_SAME_LEAGUE: continue
        legs.append(c)
        used_events.add(c.event_id)
        market_count[c.market] = market_count.get(c.market, 0) + 1
        league_count[c.league] = league_count.get(c.league, 0) + 1
        odds *= c.odds
        prob *= c.model_prob
        if odds >= ODDS10_MIN_ODDS and len(legs) >= ODDS10_MIN_LEGS: break
    
    if len(legs) < ODDS10_MIN_LEGS or odds < ODDS10_MIN_ODDS:
        print(f"  → Odds +10 : {len(legs)} legs, cote {odds:.2f} → rejeté")
        return None
    
    print(f"  → Odds +10 OK : {len(legs)} legs, cote {odds:.2f}")
    return Coupon("Odds +10", f"Cote ≥ {ODDS10_MIN_ODDS} • Stabilité",
                  "🔥", legs, round(odds,2), round(prob,4), show_analysis=False)

def build_mtft_coupon(pool):
    if not STATE.get("dixon_model"):
        print("  → MT-Fin : Dixon non disponible")
        return None
    
    picks = []
    for md in pool:
        if not md.dixon_pred: continue
        if md.real_odds.get("home") and md.real_odds["home"] < 1.30: continue
        if md.real_odds.get("away") and md.real_odds["away"] < 1.30: continue
        
        lam_h = md.dixon_pred.get("lambda_home", 1.3)
        lam_a = md.dixon_pred.get("lambda_away", 1.1)
        htft_probs = simulate_htft(lam_h, lam_a)
        if not htft_probs: continue
        
        best_pick, best_prob = None, 0.0
        for combo in ("V1/V1", "V2/V2"):
            p = htft_probs.get(combo, 0)
            if p > best_prob:
                best_prob = p; best_pick = combo
        
        if not best_pick or best_prob < MTFIN_MIN_PROB: continue
        
        score = 0
        if md.dixon_pred: score += 15
        if md.has_h2h: score += 10
        if md.gemini_markets: score += 10
        if best_prob >= 0.65: score += 30
        elif best_prob >= 0.55: score += 20
        score += 15
        if md.has_h2h: score += 5
        score += 10
        
        if score < MTFIN_MIN_SCORE:
            if score < MTFIN_FALLBACK_SCORE: continue
        
        picks.append({
            "md": md, "pick": best_pick, "prob": best_prob, "score": score,
        })
    
    print(f"  → MT-Fin : {len(picks)} matchs valides")
    if len(picks) < MTFIN_TOP_N:
        print(f"  → MT-Fin : besoin de {MTFIN_TOP_N}, seulement {len(picks)}")
        return None
    
    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    
    top, used_leagues = [], set()
    for p in picks:
        if len(top) >= MTFIN_TOP_N: break
        lg = p["md"].league
        if lg in used_leagues and len(top) < MTFIN_TOP_N - 1: continue
        top.append(p)
        used_leagues.add(lg)
    
    if len(top) < MTFIN_TOP_N:
        top = picks[:MTFIN_TOP_N]
    
    legs = []
    for p in top:
        md = p["md"]
        est_odd = round(1.0 / p["prob"], 2) if p["prob"] > 0 else 3.5
        legs.append(Selection(
            event_id=md.event_id, league=md.league, home=md.home, away=md.away,
            kickoff=md.kickoff, market="MT-Fin",
            pick_label=f"Mi-temps/Fin : {p['pick']}",
            odds=est_odd, model_prob=p["prob"], real_odd=None,
            value=-1.0, quality=p["score"],
            htft_pick=p["pick"],
        ))
        print(f"     🥇 {md.home} vs {md.away} : {p['pick']} ({p['prob']*100:.1f}%)")
    
    co, cp = 1.0, 1.0
    for s in legs: co *= s.odds; cp *= s.model_prob
    return Coupon("MT-Fin", "3 matchs Bookmaker Blind", "⏱", legs,
                  round(co,2), round(cp,4), is_list=True, show_analysis=False)

def build_draw_coupon(pool):
    candidates = []
    for md in pool:
        home_odd = md.real_odds.get("home")
        away_odd = md.real_odds.get("away")
        draw_odd = md.real_odds.get("draw")
        
        if home_odd and not (DRAW_MIN_HOME_ODD <= home_odd <= DRAW_MAX_HOME_ODD): continue
        if away_odd and not (DRAW_MIN_AWAY_ODD <= away_odd <= DRAW_MAX_AWAY_ODD): continue
        if draw_odd and draw_odd < DRAW_MIN_ODD: continue
        
        prob_draw, g, d, b, emoji = compute_probability(md, "match_nul")
        if prob_draw < DRAW_MIN_PROB: continue
        if g > 0 and g < DRAW_GEMINI_MIN_PROB: continue
        if emoji == "⚠️": continue
        
        score = 0
        if home_odd and away_odd:
            if 2.20 <= min(home_odd, away_odd) and max(home_odd, away_odd) <= 3.50: score += 30
            elif 1.80 <= min(home_odd, away_odd): score += 20
        else:
            if prob_draw >= 0.30 and g >= 0.28: score += 20
            elif prob_draw >= 0.28: score += 12
        if prob_draw >= 0.33: score += 25
        elif prob_draw >= 0.28: score += 18
        if g >= 0.30: score += 20
        elif g >= 0.25: score += 12
        if d >= 0.28: score += 10
        if md.has_h2h: score += 5
        score += 10
        
        if score < DRAW_MIN_SCORE and score < DRAW_FALLBACK_SCORE: continue
        
        used_odd = draw_odd if draw_odd else (1.0 / prob_draw if prob_draw > 0 else 3.0)
        candidates.append(Selection(
            event_id=md.event_id, league=md.league, home=md.home, away=md.away,
            kickoff=md.kickoff, market="Match Nul",
            pick_label="Match Nul (X)",
            odds=round(used_odd,2), model_prob=round(prob_draw,4),
            real_odd=draw_odd, value=round((prob_draw * used_odd) - 1.0, 4),
            quality=score,
            prob_gemini=round(g,3), prob_dixon=round(d,3), prob_bzzoiro=round(b,3),
        ))
    
    print(f"  → Match Nul : {len(candidates)} candidats")
    valid = [c for c in candidates if c.quality >= DRAW_MIN_SCORE]
    if not valid:
        valid = [c for c in candidates if c.quality >= 60]
    if not valid:
        print(f"  → Match Nul : 0 match qualifié")
        return None
    
    by_event = {}
    for c in valid:
        if c.event_id not in by_event or c.quality > by_event[c.event_id].quality:
            by_event[c.event_id] = c
    valid = list(by_event.values())
    valid.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    
    top = valid[:DRAW_TOP_N]
    if len(top) < DRAW_TOP_N:
        print(f"  → Match Nul : besoin de {DRAW_TOP_N}, seulement {len(top)}")
        return None
    
    co, cp = 1.0, 1.0
    for s in top: co *= s.odds; cp *= s.model_prob
    print(f"  → Match Nul OK : {len(top)} nuls, cote {co:.2f}")
    return Coupon("Match Nul", f"3 nuls combinés", "🤝", top,
                  round(co,2), round(cp,4), is_list=True, show_analysis=False)

def build_all_coupons(pool):
    coupons = []
    if c := build_odds3_coupon(pool): coupons.append(c)
    if c := build_odds10_coupon(pool): coupons.append(c)
    if c := build_mtft_coupon(pool): coupons.append(c)
    if c := build_draw_coupon(pool): coupons.append(c)
    return coupons

def format_header():
    return (f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"<i>{BRAND_TAGLINE}</i>\n"
            f"<i>📅 {today_pretty()} • 🇨🇮 Côte d’Ivoire</i>")

def format_selection_block(s, idx, show_analysis):
    lines = [
        f"<b>┌─ #{idx} {s.home} vs {s.away}</b>",
        f"│ 📅 <b>{format_kickoff(s.kickoff)}</b> • {s.league}",
        f"│ 🎯 <b>{s.pick_label}</b>",
        f"│ 📊 Proba : <b>{s.model_prob*100:.1f}%</b> {prob_bar(s.model_prob)}",
    ]
    if s.quality > 0:
        lines.append(f"│ ⭐ Qualité : <b>{s.quality}/100</b> {quality_bar(s.quality)}")
    if s.prob_sources_agreement:
        lines.append(f"│ 🤝 Consensus : {s.prob_sources_agreement}")
    if s.htft_pick:
        lines.append(f"│ ⏱ MT/Fin : <b>{s.htft_pick}</b>")
    if s.real_odd:
        lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b>")
    else:
        lines.append(f"│ 💰 Cote : <b>{s.odds}</b> <i>(estimée)</i>")
    if s.value > 0:
        lines.append(f"│ 💎 Value : <b>+{s.value*100:.1f}%</b>")
    if s.prob_gemini > 0 or s.prob_dixon > 0 or s.prob_bzzoiro > 0:
        src = []
        if s.prob_gemini > 0: src.append(f"G:{s.prob_gemini*100:.0f}%")
        if s.prob_dixon > 0: src.append(f"D:{s.prob_dixon*100:.0f}%")
        if s.prob_bzzoiro > 0: src.append(f"B:{s.prob_bzzoiro*100:.0f}%")
        if src: lines.append(f"│ <i>{' × '.join(src)}</i>")
    if show_analysis and s.analysis:
        lines.append(f"│")
        lines.append(f"│ 🧠 <b>Avis IA</b> :")
        lines.append(f"│ 💬 <i>{s.analysis[:250]}</i>")
    if show_analysis and s.advices:
        lines.append(f"│ 💡 Conseils :")
        for a in s.advices:
            t = a.get("text","").strip()
            if t: lines.append(f"│  • {t}")
    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)

def format_coupon(c):
    show_ana = getattr(c, "show_analysis", False)
    if c.is_list:
        lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>",
                 f"<i>{c.subtitle}</i>", "", f"<b>🎯 {len(c.legs)} pronostics</b>", ""]
        for i, s in enumerate(c.legs, 1):
            lines.append(format_selection_block(s, i, show_ana))
            lines.append("")
        lines.append(f"<i>{PUBLIC_FOOTER}</i>")
        return "\n".join(lines)
    prob_level = "🟢 Faible" if c.combined_prob >= 0.40 else "🟡 Modéré" if c.combined_prob >= 0.15 else "🔴 Élevé"
    lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>",
             f"<i>{c.subtitle}</i>", "",
             f"<b>┏━━━━━━━━━━━━━━━┓</b>",
             f"<b>┃ 🎫 Cote : {c.combined_odds}</b>",
             f"<b>┃ 📈 Proba : {c.combined_prob*100:.1f}%</b>",
             f"<b>┃ 🎯 {prob_level}</b>",
             f"<b>┗━━━━━━━━━━━━━━━┛</b>", ""]
    for i, s in enumerate(c.legs, 1):
        lines.append(format_selection_block(s, i, show_ana))
        lines.append("")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)

def format_coupons_ready():
    if not STATE["coupons"]:
        return f"{format_header()}\n\n<b>📭 Aucun coupon aujourd'hui</b>"
    lignes = []
    for c in STATE["coupons"]:
        n = len(c.legs)
        if c.is_list:
            lignes.append(f"{c.emoji} <b>{c.name}</b> • {n} pronostics")
        else:
            lignes.append(f"{c.emoji} <b>{c.name}</b> • {n} paris • Cote <b>{c.combined_odds}</b>")
    return f"{format_header()}\n\n<b>🔔 COUPONS DU JOUR !</b>\n\n" + "\n\n".join(lignes)

def format_bilan(tracker):
    cps = tracker.get("coupons", [])
    if not cps: return f"{format_header()}\n\n<b>📊 Bilan</b>\n<i>Aucun historique.</i>"
    won = sum(1 for c in cps if c.get("status") == "won")
    lost = sum(1 for c in cps if c.get("status") == "lost")
    pending = sum(1 for c in cps if c.get("status") == "pending")
    settled = won + lost
    wr = (won/settled*100) if settled > 0 else 0
    return f"{format_header()}\n\n<b>📊 BILAN</b>\n🎫 {len(cps)}\n✅ {won}\n❌ {lost}\n⏳ {pending}\n📈 {wr:.1f}%"

async def safe_answer(msg, txt, reply_markup=None):
    for i, p in enumerate(split_message(txt)):
        try:
            if i == 0: await msg.answer(p, parse_mode="HTML", reply_markup=reply_markup)
            else: await msg.answer(p, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e: print(f"⚠️ {e}")

async def safe_send(cid, txt):
    for p in split_message(txt):
        try:
            await bot.send_message(chat_id=cid, text=p, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except Exception as e: print(f"⚠️ {e}")

async def broadcast_after_scan():
    for cid in TARGETS:
        await safe_send(cid, format_coupons_ready())
        await asyncio.sleep(0.5)
    for uid_str in STATE["users"]:
        try:
            uid = int(uid_str)
            if is_subscribed(uid):
                await safe_send(uid, format_coupons_ready())
                await asyncio.sleep(0.3)
        except: pass

@dp.message(Command("start"))
async def start_cmd(m):
    if m.from_user.id == ADMIN_ID:
        await safe_answer(m, f"{format_header()}\n\n<b>👋 Admin</b>\n/scan pour analyser.", reply_markup=ADMIN_KEYBOARD)
    else:
        await safe_answer(m, f"{format_header()}\n\n{get_subscription_message(m.from_user.id)}", reply_markup=USER_KEYBOARD)

@dp.message(Command("keygen"))
async def keygen(m):
    if m.from_user.id != ADMIN_ID: return
    a = m.text.split()
    if len(a) != 2: await m.answer("Usage: /keygen [1m|1s|60]"); return
    d, days = a[1].lower(), 0
    if d.endswith("m"): days = int(d[:-1])*30
    elif d.endswith("s"): days = int(d[:-1])*7
    else: days = int(d)
    if days <= 0: await m.answer("❌"); return
    k = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    STATE["keys"][k] = {"days": days, "used": False, "created_at": datetime.now(TZ).isoformat()}
    save_json(KEYS_DB, STATE["keys"])
    await m.answer(f"🔑 <code>{k}</code> • {days}j", parse_mode="HTML")

@dp.message(Command("activate", "vip", "cle", "key"))
async def activate_cmd(m):
    a = m.text.split()
    if len(a) != 2: await m.answer("Usage: /activate <clé>"); return
    await process_act(m, a[1].strip().upper())

@dp.message(F.text.regexp(r"^[A-Z0-9]{8}$"))
async def raw_key(m): await process_act(m, m.text.strip().upper())

async def process_act(m, k):
    if k not in STATE["keys"]: await m.answer("❌ Invalide."); return
    kd = STATE["keys"][k]
    if kd.get("used"): await m.answer("❌ Déjà utilisée."); return
    d = kd["days"]; exp = datetime.now(TZ) + timedelta(days=d)
    STATE["users"][str(m.from_user.id)] = {"expires_at": exp.isoformat(), "key_used": k}
    kd["used"] = True; kd["used_by"] = str(m.from_user.id); kd["used_at"] = datetime.now(TZ).isoformat()
    save_json(KEYS_DB, STATE["keys"]); save_json(USERS_DB, STATE["users"])
    await m.answer(f"✅ Actif jusqu'au {exp.strftime('%d/%m/%Y')}", parse_mode="HTML", reply_markup=USER_KEYBOARD)

@dp.message(Command("scan"))
async def scan_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, "⏳ Analyse en cours (3-5 min)...")
    await scan()
    await broadcast_after_scan()

@dp.message(Command("debug"))
async def debug_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    d = STATE["debug"]
    await safe_answer(m, f"<b>🔍 DEBUG</b>\n\nDixon : {'✅' if STATE['dixon_model'] else '❌'}\nOdds : {d.get('odds_events',0)}\nBzzoiro odds : {d.get('bzz_odds',0)}\nBzzoiro : {d.get('bzz_predictions',0)}\nPool : {d.get('pool',0)}\nCoupons : {d.get('coupons',0)}")

async def send_coupon(m, name):
    c = next((x for x in STATE["coupons"] if x.name == name), None)
    if c: await safe_answer(m, format_coupon(c))
    else: await safe_answer(m, "Aucun coupon aujourd'hui.")

@dp.message(F.text == BTN_ODDS3)
async def b_odds3(m):
    if m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await m.answer(f"❌ {get_subscription_message(m.from_user.id)}"); return
    await send_coupon(m, "Odds +3")

@dp.message(F.text == BTN_ODDS10)
async def b_odds10(m):
    if m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await m.answer(f"❌ {get_subscription_message(m.from_user.id)}"); return
    await send_coupon(m, "Odds +10")

@dp.message(F.text == BTN_MTFT)
async def b_mtft(m):
    if m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await m.answer(f"❌ {get_subscription_message(m.from_user.id)}"); return
    await send_coupon(m, "MT-Fin")

@dp.message(F.text == BTN_DRAW)
async def b_draw(m):
    if m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await m.answer(f"❌ {get_subscription_message(m.from_user.id)}"); return
    await send_coupon(m, "Match Nul")

@dp.message(F.text == BTN_BILAN)
async def b_bilan(m):
    if m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await m.answer(f"❌ {get_subscription_message(m.from_user.id)}"); return
    await safe_answer(m, format_bilan(STATE["tracker"]))

@dp.message(F.text == BTN_SCAN)
async def b_scan(m):
    if m.from_user.id != ADMIN_ID: return
    await scan_cmd(m)

@dp.message(F.text == BTN_ACTIVATE)
async def b_act(m): await m.answer("🔑 /activate VOTRE_CLE")

@dp.message(F.text == "⚙️ Debug")
async def b_debug(m):
    if m.from_user.id != ADMIN_ID: return
    await debug_cmd(m)

async def scan():
    async with STATE["scan_lock"]:
        bzz_preds = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try: bzz_preds = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except Exception as e: print(f"⚠️ Bzz: {e}")

        odds_events = await fetch_all_odds_events()

        if not odds_events and not bzz_preds:
            STATE["coupons"] = []
            print("⚠️ 0 source")
            return

        pool = await build_match_pool(bzz_preds, odds_events)
        print(f"📋 Pool : {len(pool)} matchs")

        # === FALLBACK BZZOIRO ODDS si Odds API vide ===
        bzz_odds_count = 0
        if not odds_events and fetch_bzzoiro_odds:
            bzz_ids = []
            for md in pool:
                if md.event_id.startswith("bzz_"):
                    try: bzz_ids.append(int(md.event_id.replace("bzz_", "")))
                    except ValueError: pass
            if bzz_ids:
                print(f"📊 Récupération cotes Bzzoiro pour {len(bzz_ids)} matchs...")
                bzz_odds_map = await fetch_bzzoiro_odds(bzz_ids)
                bzz_odds_count = len(bzz_odds_map)
                for md in pool:
                    try: eid = int(md.event_id.replace("bzz_", ""))
                    except ValueError: continue
                    if eid in bzz_odds_map:
                        md.real_odds = bzz_odds_map[eid]
                        md.bzz_odds_source = "bzzoiro"

        if not pool:
            STATE["coupons"] = []
            return

        await enrich_and_analyze(pool)
        STATE["coupons"] = build_all_coupons(pool)
        mark_picks_used([s for c in STATE["coupons"] for s in c.legs])
        STATE["last_scan"] = datetime.now(TZ).strftime("%d/%m %H:%M")
        STATE["debug"] = {
            "odds_events": len(odds_events),
            "bzz_odds": bzz_odds_count,
            "bzz_predictions": len(bzz_preds),
            "pool": len(pool),
            "coupons": len(STATE["coupons"])
        }
        print(f"──────── RÉSUMÉ ────────")
        print(f"Pool : {len(pool)}")
        print(f"Coupons : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} legs, cote {c.combined_odds}")
        print("────────────────────────")

async def daily_broadcast():
    await scan()
    record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    await broadcast_after_scan()

@asynccontextmanager
async def lifespan(app):
    try: await bot.delete_webhook(drop_pending_updates=True)
    except: pass
    STATE["tracker"] = load_tracker()
    STATE["keys"] = load_json(KEYS_DB)
    STATE["users"] = load_json(USERS_DB)
    STATE["ready"] = True
    print("✅ Bot prêt.")

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ),
                      id="daily", replace_existing=True)
    scheduler.start()
    bot_task = asyncio.create_task(dp.start_polling(bot))
    print(f"✅ Scan à {NOTIFY_HOUR:02d}h{NOTIFY_MINUTE:02d}")

    async def bootstrap():
        try:
            if download_and_train:
                STATE["dixon_model"] = await download_and_train()
                print(f"✅ Dixon ({len(STATE['dixon_model'].teams) if STATE['dixon_model'] else 0})")
            await asyncio.sleep(5)
            if NOTIFY_ON_STARTUP:
                for cid in TARGETS: await safe_send(cid, f"{format_header()}\n\n⏳ Démarrage...")
            await daily_broadcast()
        except Exception as e: print(f"⚠️ {e}")

    asyncio.create_task(bootstrap())
    yield
    scheduler.shutdown(wait=False); bot_task.cancel()
    try: await bot.session.close()
    except: pass

app = FastAPI(title="Value Bot", lifespan=lifespan)

@app.get("/")
async def root(): return {"status": "ok", "coupons": [c.name for c in STATE["coupons"]]}

@app.get("/debug")
async def debug_http(): return STATE["debug"]

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
