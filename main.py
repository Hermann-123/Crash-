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

# --- IMPORTS ---
try:
    from gemini_predictor import predict_match_probas
except Exception as e:
    predict_match_probas = None
    print(f"❌ gemini_predictor: {e}")

try:
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception as e:
    enrich_match_with_bzzoiro = None
    print(f"❌ bzzoiro_data: {e}")

try:
    from bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
except Exception as e:
    fetch_bzzoiro_predictions_direct = None
    print(f"❌ bzzoiro_predictions_direct: {e}")

try:
    from odds_fallback import fetch_all_odds as fetch_infer_odds
    print("✅ odds_fallback chargé")
except Exception as e:
    fetch_infer_odds = None
    print(f"❌ odds_fallback: {e}")

try:
    from dixon_coles import download_and_train
except Exception as e:
    download_and_train = None
    print(f"❌ dixon_coles: {e}")

# --- VARIABLES ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
INFERSPORTS_API_KEY = os.getenv("INFERSPORTS_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")

try: ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except ValueError: ADMIN_ID = 0

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

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "35"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

# MOTEUR 1 : ODDS +3
ODDS3_MIN_SCORE = 80
ODDS3_MIN_ODDS = 3.0
ODDS3_MAX_ODDS = 4.5
ODDS3_MIN_LEG_ODDS = 1.55
ODDS3_MAX_LEG_ODDS = 2.20
ODDS3_MIN_LEGS = 2
ODDS3_MAX_LEGS = 3

# MOTEUR 2 : ODDS +10
ODDS10_MIN_SCORE = 75
ODDS10_MIN_ODDS = 10.0
ODDS10_MIN_LEG_ODDS = 1.35
ODDS10_MAX_LEG_ODDS = 1.65
ODDS10_MIN_LEGS = 6
ODDS10_MAX_LEGS = 8

# MOTEUR 3 : MT-FIN
MTFIN_MIN_SCORE = 75
MTFIN_MIN_PROB = 0.38
MTFIN_TOP_N = 3
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45

# MOTEUR 4 : MATCH NUL
DRAW_MIN_SCORE = 75
DRAW_MIN_PROB = 0.26
DRAW_GEMINI_MIN_PROB = 0.20
DRAW_MIN_ODD = 3.10
DRAW_TOP_N = 3

TELEGRAM_MAX_LEN = 3800
TZ = ZoneInfo(NOTIFY_TZ)

if not BOT_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise RuntimeError("Variables d'environnement manquantes")

# --- UTILITAIRES ---
def today_pretty():
    d = datetime.now(TZ)
    months = {1:"janvier",2:"février",3:"mars",4:"avril",5:"mai",6:"juin",7:"juillet",8:"août",9:"septembre",10:"octobre",11:"novembre",12:"décembre"}
    return f"{d.day} {months[d.month]} {d.year}"

def normalize_name(s: str) -> str:
    if not s: return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_dt_safe(s) -> Optional[datetime]:
    if not s: return None
    try:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except: return None

def format_kickoff(kickoff_str) -> str:
    if not kickoff_str: return "?"
    dt = parse_dt_safe(kickoff_str)
    if dt: return dt.strftime("%d/%m %H:%M")
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
    if len(text) <= max_len: return [text]
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
        hg, ag = _poisson_sample(lambda_home), _poisson_sample(lambda_away)
        ht_hg = _binomial_sample(hg, MTFIN_FIRST_HALF_RATIO)
        ht_ag = _binomial_sample(ag, MTFIN_FIRST_HALF_RATIO)
        ht_res = "V1" if ht_hg > ht_ag else ("X" if ht_hg == ht_ag else "V2")
        ft_res = "V1" if hg > ag else ("X" if hg == ag else "V2")
        key = f"{ht_res}/{ft_res}"
        counts[key] = counts.get(key, 0) + 1
    return {k: round(v / n_sims, 4) for k, v in counts.items()}

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
    model_prob: float # ATTENTION ICI
    real_odd: Optional[float] = None
    value: float = -1.0
    quality: int = 0
    prob_gemini: float = 0.0 # ATTENTION ICI
    prob_dixon: float = 0.0
    prob_bzzoiro: float = 0.0
    analysis: str = ""
    advices: list = None
    htft_pick: str = ""
    prob_sources_agreement: str = "" # ATTENTION ICI

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

def record_coupons(coupons, date_str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing: continue
        legs = [{"event_id": s.event_id, "home": s.home, "away": s.away, "market": s.market, "pick_label": s.pick_label, "odds": s.odds, "kickoff": s.kickoff, "result": "pending"} for s in c.legs]
        tracker["coupons"].append({"id": cid, "date": date_str, "name": c.name, "combined_odds": c.combined_odds, "legs": legs, "status": "pending"})
    tracker["coupons"] = tracker["coupons"][-500:]
    save_tracker(tracker)

def prescore_match(md) -> int:
    score = 0
    if md.real_odds: score += 25
    if md.bzz_markets: score += 15
    if md.dixon_pred: score += 15
    if md.has_h2h: score += 10
    return score

async def enrich_and_analyze(pool):
    async with httpx.AsyncClient(timeout=70.0) as client:
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
                
                if predict_match_probas:
                    pred = await predict_match_probas(client, md.home, md.away, md.league, md.kickoff, context=ctx)
                    if pred and "markets" in pred:
                        md.gemini_markets = pred.get("markets", {})
                        md.analysis = pred.get("analysis", "")
                        md.advices = []
                        for a in (pred.get("advices") or [])[:2]:
                            if isinstance(a, dict) and a.get("text"):
                                try: c = int(a.get("confidence", 0))
                                except: c = 0
                                md.advices.append({"text": str(a["text"]).strip(), "confidence": c})
                
                if STATE.get("dixon_model"):
                    try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                    except: pass
                
                await asyncio.sleep(2.0) # Pause un peu plus longue
            except Exception as e:
                print(f"  ⚠️ {e}")

def compute_probability(md, market_key):
    gem = dix = bzz = 0.0
    if md.gemini_markets and md.gemini_markets.get(market_key) is not None:
        try: gem = float(md.gemini_markets[market_key]) / 100.0
        except: pass
    if md.dixon_pred:
        dk = {"victoire_home":"p_home","match_nul":"p_draw","victoire_away":"p_away","over_2.5":"over_2.5","under_2.5":"under_2.5","over_3.5":"over_3.5","over_1.5":"over_1.5","under_1.5":"under_1.5","btts_yes":"btts_yes","btts_no":"btts_no"}.get(market_key)
        if dk: dix = md.dixon_pred.get(dk, 0)
    if md.bzz_markets and md.bzz_markets.get(market_key) is not None:
        try: bzz = float(md.bzz_markets[market_key]) / 100.0
        except: pass
    
    sources = [x for x in (gem, dix, bzz) if x > 0]
    if not sources: return 0.0, gem, dix, bzz, ""
    final = sum(sources) / len(sources)
    
    emoji = ""
    if len(sources) >= 2:
        delta = max(sources) - min(sources)
        if len(sources) == 3 and delta <= 0.10: emoji = "🤝🤝"
        elif delta <= 0.10: emoji = "🤝"
        elif delta >= 0.15: emoji = "⚠️"
    return final, gem, dix, bzz, emoji

def compute_quality_odds3(md, market_key, prob, real_odd, value, emoji):
    pts = 0
    if real_odd: pts += 15
    h2h_pts = 10 if md.has_h2h else 0
    dixon_pts = 10 if md.dixon_pred else 0
    pts += h2h_pts + dixon_pts + (20 - h2h_pts - dixon_pts)
    if md.bzz_markets: pts += 5
    
    if emoji == "🤝🤝": pts += 30
    elif emoji == "🤝": pts += 20
    
    if value >= 0.10: pts += 20
    elif value >= 0.07: pts += 15
    elif value >= 0.05: pts += 10
    
    if 1.60 <= real_odd <= 2.20: pts += 15
    elif 1.55 <= real_odd < 1.60: pts += 12
    
    return max(0, min(100, pts))

def build_odds3_coupon(pool):
    candidates = []
    for md in pool:
        if not md.home or not md.away: continue
        best_market = None
        
        for market_key, (label, mkt_type) in {
            "btts_yes": ("BTTS Oui", "BTTS"), "btts_no": ("BTTS Non", "BTTS"),
            "over_2.5": ("Over 2.5", "O/U"), "under_2.5": ("Under 2.5", "O/U"),
            "over_1.5": ("Over 1.5", "O/U"), "under_3.5": ("Under 3.5", "O/U"),
            "double_chance_1X": ("DC 1X", "DC"), "double_chance_X2": ("DC X2", "DC"), "double_chance_12": ("DC 12 (Pas de Nul)", "DC"),
        }.items():
            prob, g, d, b, emoji = compute_probability(md, market_key)
            if prob < 0.58 or emoji not in ["🤝", "🤝🤝"]: continue
            
            real_odd = md.real_odds.get(market_key)
            is_estimated = False
            if not real_odd:
                real_odd = round(1.0 / prob, 2)
                is_estimated = True
                
            if not (ODDS3_MIN_LEG_ODDS <= real_odd <= ODDS3_MAX_LEG_ODDS): continue
            value = 0.0 if is_estimated else (prob * real_odd) - 1.0
            
            pts = compute_quality_odds3(md, market_key, prob, real_odd, value, emoji)
            if pts < ODDS3_MIN_SCORE: continue
            
            # ⚠️ CORRECTION DU BUG ICI
            if not best_market or prob > best_market.model_prob or (prob == best_market.model_prob and pts > best_market.quality):
                best_market = Selection(
                    event_id=md.event_id, league=md.league, home=md.home, away=md.away, kickoff=md.kickoff,
                    market=mkt_type, pick_label=label, odds=real_odd, model_prob=prob, 
                    real_odd=real_odd if not is_estimated else None, value=value, quality=pts, 
                    prob_gemini=g, prob_dixon=d, prob_bzzoiro=b, analysis=md.analysis, advices=md.advices, prob_sources_agreement=emoji
                )

        if best_market:
            candidates.append(best_market)

    candidates.sort(key=lambda x: (x.quality, x.value), reverse=True)
    legs, used_ev, used_mk, used_lg = [], set(), set(), set()
    odds, prob = 1.0, 1.0
    for c in candidates:
        if len(legs) >= ODDS3_MAX_LEGS: break
        if c.event_id in used_ev or c.market in used_mk or (c.league in used_lg and len(legs) >= 2): continue
        if odds * c.odds > ODDS3_MAX_ODDS and legs: continue
        
        legs.append(c)
        used_ev.add(c.event_id); used_mk.add(c.market); used_lg.add(c.league)
        odds *= c.odds; prob *= c.model_prob
        if odds >= ODDS3_MIN_ODDS and len(legs) >= ODDS3_MIN_LEGS: break
        
    if len(legs) >= ODDS3_MIN_LEGS and odds >= ODDS3_MIN_ODDS:
        print(f"  → Odds +3 OK : {len(legs)} legs, cote {odds:.2f}")
        return Coupon("Odds +3", f"Cote {ODDS3_MIN_ODDS}-{ODDS3_MAX_ODDS}", "🎯", legs, round(odds, 2), round(prob, 4), show_analysis=True)
    print("  → Odds +3 : 0 match qualifié")
    return None

def build_odds10_coupon(pool):
    candidates = []
    for md in pool:
        if not md.home or not md.away: continue
        best_market = None
        
        for market_key, (label, mkt_type) in {
            "btts_yes": ("BTTS Oui", "BTTS"), "over_2.5": ("Over 2.5", "O/U"), "under_2.5": ("Under 2.5", "O/U"),
            "over_1.5": ("Over 1.5", "O/U"), "double_chance_1X": ("DC 1X", "DC"), "double_chance_X2": ("DC X2", "DC"), "double_chance_12": ("DC 12 (Pas de Nul)", "DC"),
        }.items():
            prob, g, d, b, emoji = compute_probability(md, market_key)
            if prob < 0.65 or emoji not in ["🤝", "🤝🤝"]: continue
            
            real_odd = md.real_odds.get(market_key)
            is_est = False
            if not real_odd:
                real_odd = round(1.0 / prob, 2)
                is_est = True
            
            if not is_est and not (ODDS10_MIN_LEG_ODDS <= real_odd <= ODDS10_MAX_LEG_ODDS): continue
            elif is_est and not (1.10 <= real_odd <= 1.55): continue
            
            value = 0.0 if is_est else (prob * real_odd) - 1.0
            
            pts = 15 + (10 if md.has_h2h else 0) + (10 if md.dixon_pred else 0) + 5
            pts += (30 if emoji == "🤝🤝" else 20)
            if prob >= 0.75: pts += 20
            elif prob >= 0.65: pts += 10
            pts = max(0, min(100, pts))
            
            if pts < ODDS10_MIN_SCORE: continue
            
            # ⚠️ CORRECTION DU BUG ICI AUSSI
            if not best_market or prob > best_market.model_prob:
                best_market = Selection(
                    event_id=md.event_id, league=md.league, home=md.home, away=md.away, kickoff=md.kickoff,
                    market=mkt_type, pick_label=label, odds=real_odd, model_prob=prob, 
                    real_odd=real_odd if not is_est else None, value=value, quality=pts, 
                    prob_gemini=g, prob_dixon=d, prob_bzzoiro=b
                )

        if best_market:
            candidates.append(best_market)

    candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    legs, used_ev, mkt_c, lg_c = [], set(), {}, {}
    odds, prob = 1.0, 1.0
    for c in candidates:
        if len(legs) >= ODDS10_MAX_LEGS: break
        if c.event_id in used_ev or mkt_c.get(c.market, 0) >= 1 or lg_c.get(c.league, 0) >= 2: continue
        
        legs.append(c)
        used_ev.add(c.event_id)
        mkt_c[c.market] = mkt_c.get(c.market, 0) + 1
        lg_c[c.league] = lg_c.get(c.league, 0) + 1
        odds *= c.odds; prob *= c.model_prob
        if odds >= ODDS10_MIN_ODDS and len(legs) >= ODDS10_MIN_LEGS: break

    if len(legs) >= ODDS10_MIN_LEGS and odds >= ODDS10_MIN_ODDS:
        print(f"  → Odds +10 OK : {len(legs)} legs, cote {odds:.2f}")
        return Coupon("Odds +10", f"Cote ≥ {ODDS10_MIN_ODDS}", "🔥", legs, round(odds, 2), round(prob, 4), show_analysis=False)
    print("  → Odds +10 : 0 match qualifié")
    return None

def build_mtft_coupon(pool):
    if not STATE.get("dixon_model"): return None
    picks = []
    for md in pool:
        if not md.home or not md.away or not md.dixon_pred: continue
        if (md.real_odds.get("home") and md.real_odds["home"] < 1.35) or (md.real_odds.get("away") and md.real_odds["away"] < 1.35): continue
        
        htft_probs = simulate_htft(md.dixon_pred.get("lambda_home", 1.3), md.dixon_pred.get("lambda_away", 1.1))
        best_pick, best_prob = None, 0.0
        for combo in ("V1/V1", "V2/V2"):
            p = htft_probs.get(combo, 0)
            if p > best_prob: best_prob = p; best_pick = combo
        
        if not best_pick or best_prob < MTFIN_MIN_PROB: continue
        
        pts = 15 + (10 if md.has_h2h else 0) + 10 + (30 if best_prob >= 0.65 else 20) + 15 + 10
        if pts < MTFIN_MIN_SCORE: continue
        picks.append({"md": md, "pick": best_pick, "prob": best_prob, "score": pts})

    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    top = picks[:MTFIN_TOP_N]
    if len(top) < MTFIN_TOP_N: return None
    
    legs = []
    for p in top:
        md = p["md"]
        legs.append(Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "MT-Fin", f"MT/Fin : {p['pick']}", round(1.0/p["prob"], 2), p["prob"], None, -1.0, p["score"], htft_pick=p["pick"]))
        
    co, cp = 1.0, 1.0
    for s in legs: co *= s.odds; cp *= s.model_prob
    return Coupon("MT-Fin", "3 matchs Dixon", "⏱", legs, round(co, 2), round(cp, 4), is_list=True, show_analysis=False)

def build_draw_coupon(pool):
    candidates = []
    for md in pool:
        if not md.home or not md.away: continue
        
        prob_draw, g, d, b, emoji = compute_probability(md, "match_nul")
        if prob_draw < DRAW_MIN_PROB or (g > 0 and g < DRAW_GEMINI_MIN_PROB) or emoji == "⚠️": continue
        
        home_odd, away_odd = md.real_odds.get("home"), md.real_odds.get("away")
        draw_odd = md.real_odds.get("draw")
        if not draw_odd: draw_odd = round(1.0 / prob_draw, 2) if prob_draw > 0 else 3.20
        if draw_odd < DRAW_MIN_ODD: continue
        if (home_odd and home_odd < 1.80) or (away_odd and away_odd < 1.80): continue
        
        pts = 30 + (25 if prob_draw >= 0.33 else 18) + (20 if g >= 0.30 else 12) + 10 + (5 if md.has_h2h else 0) + 10
        pts = max(0, min(100, pts))
        if pts < DRAW_MIN_SCORE: continue
        
        candidates.append(Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "Match Nul", "Nul", draw_odd, prob_draw, draw_odd if md.real_odds.get("draw") else None, (prob_draw*draw_odd)-1, pts))
        
    candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    top = candidates[:DRAW_TOP_N]
    if len(top) < DRAW_TOP_N: return None
    
    co, cp = 1.0, 1.0
    for s in top: co *= s.odds; cp *= s.model_prob
    return Coupon("Match Nul", "3 nuls combinés", "🤝", top, round(co, 2), round(cp, 4), is_list=True, show_analysis=False)

def build_all_coupons(pool):
    coupons = []
    used_ids = set()
    def fresh(): return [md for md in pool if md.event_id not in used_ids]

    if c := build_odds10_coupon(fresh()):
        coupons.append(c); used_ids.update(s.event_id for s in c.legs)
    if c := build_odds3_coupon(fresh()):
        coupons.append(c); used_ids.update(s.event_id for s in c.legs)
    if c := build_draw_coupon(fresh()):
        coupons.append(c); used_ids.update(s.event_id for s in c.legs)
    if c := build_mtft_coupon(fresh()):
        coupons.append(c); used_ids.update(s.event_id for s in c.legs)

    return coupons

# --- FORMATAGE ---
def format_header():
    return f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n<i>{BRAND_TAGLINE}</i>\n<i>📅 {today_pretty()} • 🇨🇮 Côte d’Ivoire</i>"

def format_selection_block(s, idx, show_analysis):
    lines = [f"<b>┌─ #{idx} {s.home} vs {s.away}</b>", f"│ 📅 <b>{format_kickoff(s.kickoff)}</b> • {s.league}", f"│ 🎯 <b>{s.pick_label}</b>", f"│ 📊 Proba : <b>{s.model_prob*100:.1f}%</b> {prob_bar(s.model_prob)}"]
    if s.quality > 0: lines.append(f"│ ⭐ Qualité : <b>{s.quality}/100</b> {quality_bar(s.quality)}")
    if s.htft_pick: lines.append(f"│ ⏱ MT/Fin : <b>{s.htft_pick}</b>")
    
    if s.real_odd:
        lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b>")
    else:
        lines.append(f"│ 💰 Cote : <b>{s.odds}</b> <i>(estimée)</i>")
        
    if s.value > 0: lines.append(f"│ 💎 Value : <b>+{s.value*100:.1f}%</b>")
    if show_analysis and s.analysis: lines.append(f"│\n│ 🧠 <b>Avis IA</b> :\n│ 💬 <i>{s.analysis[:250]}</i>")
    lines.append(f"<b>└─────────────</b>")
    return "\n".join(lines)

def format_coupon(c):
    if c.is_list:
        lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{c.subtitle}</i>", "", f"<b>🎯 {len(c.legs)} pronostics</b>", ""]
        for i, s in enumerate(c.legs, 1): lines.append(format_selection_block(s, i, getattr(c, "show_analysis", False)) + "\n")
    else:
        lines = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{c.subtitle}</i>", "", f"<b>┏━━━━━━━━━━━━━━━┓</b>", f"<b>┃ 🎫 Cote : {c.combined_odds}</b>", f"<b>┃ 📈 Proba : {c.combined_prob*100:.1f}%</b>", f"<b>┗━━━━━━━━━━━━━━━┛</b>", ""]
        for i, s in enumerate(c.legs, 1): lines.append(format_selection_block(s, i, getattr(c, "show_analysis", False)) + "\n")
    lines.append(f"<i>{PUBLIC_FOOTER}</i>")
    return "\n".join(lines)

def format_coupons_ready():
    if not STATE["coupons"]:
        return f"{format_header()}\n\n<b>📭 Aucun coupon aujourd'hui</b>"
    lignes = []
    for c in STATE["coupons"]:
        lignes.append(f"{c.emoji} <b>{c.name}</b> • {len(c.legs)} paris • Cote <b>{c.combined_odds}</b>")
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

# --- HANDLERS ---
@dp.message(Command("start"))
async def start_cmd(m):
    kb = ADMIN_KEYBOARD if m.from_user.id == ADMIN_ID else USER_KEYBOARD
    msg = f"{format_header()}\n\n<b>👋 Admin</b>\n/scan pour analyser." if m.from_user.id == ADMIN_ID else f"{format_header()}\n\n{get_subscription_message(m.from_user.id)}"
    await safe_answer(m, msg, reply_markup=kb)

@dp.message(Command("scan"))
async def scan_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, "⏳ Analyse des matchs en cours (Patientez 3-5 min)...")
    await scan()
    await broadcast_after_scan()

@dp.message(Command("debug"))
async def debug_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    d = STATE["debug"]
    await safe_answer(m, f"<b>🔍 DEBUG</b>\n\nDixon : {'✅' if STATE['dixon_model'] else '❌'}\nPool total : {d.get('pool',0)}\nMatchs avec cotes : {d.get('bzz_odds',0)}\nCoupons : {d.get('coupons',0)}")

@dp.message(F.text == BTN_ODDS3)
async def b_odds3(m): await send_coupon(m, "Odds +3")
@dp.message(F.text == BTN_ODDS10)
async def b_odds10(m): await send_coupon(m, "Odds +10")
@dp.message(F.text == BTN_MTFT)
async def b_mtft(m): await send_coupon(m, "MT-Fin")
@dp.message(F.text == BTN_DRAW)
async def b_draw(m): await send_coupon(m, "Match Nul")

async def send_coupon(m, name):
    c = next((x for x in STATE["coupons"] if x.name == name), None)
    if c: await safe_answer(m, format_coupon(c))
    else: await safe_answer(m, f"Aucun coupon {name} aujourd'hui.")

@dp.message(F.text == BTN_BILAN)
async def b_bilan(m): await safe_answer(m, format_bilan(STATE["tracker"]))
@dp.message(F.text == BTN_SCAN)
async def b_scan(m): await scan_cmd(m)
@dp.message(F.text == BTN_ACTIVATE)
async def b_act(m): await m.answer("🔑 /activate VOTRE_CLE")
@dp.message(F.text == "⚙️ Debug")
async def b_debug(m): await debug_cmd(m)

# --- SCAN PRINCIPAL ---
async def scan():
    async with STATE["scan_lock"]:
        bzz_preds = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try: bzz_preds = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except Exception as e: print(f"⚠️ Bzz: {e}")

        infer_odds = {}
        if bzz_preds and fetch_infer_odds:
            matches_for_infer = [
                {"event_id": b["event_id"], "home": b["home"], "away": b["away"], "kickoff": b.get("kickoff", "")}
                for b in bzz_preds
            ]
            try: infer_odds = await fetch_infer_odds(matches_for_infer)
            except Exception as e: print(f"⚠️ Infer crash: {e}")

        if not bzz_preds:
            print("⚠️ Aucune donnée source.")
            STATE["coupons"] = []
            return

        pool = []
        for bz in bzz_preds:
            md = MatchData(
                event_id=bz["event_id"], league=bz.get("league", ""), home=bz["home"], away=bz["away"], kickoff=bz.get("kickoff", ""),
                real_odds=infer_odds.get(bz["event_id"], {}), bzz_markets=bz.get("bzz_markets", {}), bzz_confidence=bz.get("bzz_confidence", 0)
            )
            if STATE.get("dixon_model"):
                try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                except: pass
            pool.append(md)

        print(f"📋 Pool total : {len(pool)} matchs (Bzzoiro + InferSports)")
        
        pool.sort(key=prescore_match, reverse=True)
        top_pool = pool[:GEMINI_MAX_CALLS_PER_SCAN]

        await enrich_and_analyze(top_pool)

        STATE["coupons"] = build_all_coupons(top_pool)
        
        STATE["debug"] = {"pool": len(pool), "bzz_odds": len(infer_odds), "coupons": len(STATE["coupons"])}
        print(f"──────── RÉSUMÉ ────────")
        print(f"Coupons : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} legs, cote {c.combined_odds}")
        print("────────────────────────")

async def daily_broadcast():
    await scan()
    record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    await broadcast_after_scan()

# --- LIFESPAN (CORRECTION DU SCAN À 00H) ---
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
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ), id="daily", replace_existing=True)
    scheduler.start()
    bot_task = asyncio.create_task(dp.start_polling(bot))
    print(f"✅ Scan programmé à {NOTIFY_HOUR:02d}h{NOTIFY_MINUTE:02d}")

    async def bootstrap():
        try:
            if download_and_train:
                STATE["dixon_model"] = await download_and_train()
                print(f"✅ Dixon ({len(STATE['dixon_model'].teams) if STATE['dixon_model'] else 0})")
            
            await asyncio.sleep(5)
            
            # On ne lance plus le scan au démarrage !
            if NOTIFY_ON_STARTUP:
                for cid in TARGETS: 
                    await safe_send(cid, f"{format_header()}\n\n⏳ Bot démarré. En attente du scan automatique de {NOTIFY_HOUR:02d}h00...")
            
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
