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
import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from zoneinfo import ZoneInfo
from collections import defaultdict

import httpx
import numpy as np
from scipy.stats import poisson
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

# --- VARIABLES & ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
INFERSPORTS_API_KEY = os.getenv("INFERSPORTS_API_KEY", "")

TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "5968288964")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "-1003982738017")

try: ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except ValueError: ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "0"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")

LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")

BRAND_NAME = os.getenv("BRAND_NAME", "Volatility Index")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Analyse IA • 3 Moteurs")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Joue responsable.")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "50"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

# RÈGLES MT-FIN
MTFIN_MIN_SCORE = 70
MTFIN_MIN_PROB = 0.35
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45

# RÈGLES MATCH NUL
DRAW_MIN_SCORE = 70
DRAW_MAX_ECART = 0.20
DRAW_MIN_PROB = 0.28

# RÈGLES ODDS +3 (AUTH & HYBRIDE)
ODDS3_AUTH_MIN_COTE = 2.5
ODDS3_AUTH_MAX_COTE = 3.0
ODDS3_HYB_MIN_COTE = 3.0
ODDS3_HYB_MAX_COTE = 5.0
ODDS3_MIN_COTE_LEG = 1.30 # Anti-Vague strict

MATCH_START_BUFFER_MINUTES = 10
RESERVE_KEYWORDS = ["jong", " fc b", " ii", " u21", " u19", " castilla", " reserves", " b team"]

TELEGRAM_MAX_LEN = 3800
TZ = ZoneInfo(NOTIFY_TZ)

if not BOT_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise RuntimeError("Variables d'environnement manquantes")

# =====================================================================
# 🎫 ARCHITECTURE
# =====================================================================
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
    has_h2h: bool = False

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
    analysis: str = ""
    htft_pick: str = ""

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
BTN_MTFT = "⏱ MT-Fin"
BTN_DRAW = "🤝 Match Nul"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"
BTN_ACTIVATE = "🔑 Activer mon abonnement"

USER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_MTFT)],
        [KeyboardButton(text=BTN_DRAW)],
        [KeyboardButton(text=BTN_BILAN), KeyboardButton(text=BTN_ACTIVATE)],
    ], resize_keyboard=True, input_field_placeholder="Choisis un ticket…")

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_MTFT)],
        [KeyboardButton(text=BTN_DRAW)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Debug")],
    ], resize_keyboard=True, input_field_placeholder="Admin…")

STATE = {
    "ready": False, "coupons": [], "last_scan": None, "debug": {},
    "tracker": {"coupons": []}, "keys": {}, "users": {},
    "scan_lock": asyncio.Lock(), "dixon_model": None, "last_scan_date": ""
}

TARGETS = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try: TARGETS.append(int(raw))
        except ValueError: TARGETS.append(raw)

# =====================================================================
# 🔐 SYSTÈME D'ABONNEMENT
# =====================================================================
def is_subscribed(user_id):
    if user_id == ADMIN_ID: return True
    uid = str(user_id)
    if uid not in STATE["users"]: return False
    exp = STATE["users"][uid].get("expires_at")
    if not exp: return False
    try: return datetime.now(TZ) < datetime.fromisoformat(exp)
    except: return False

def get_subscription_message(user_id):
    if user_id == ADMIN_ID: return "✅ Admin"
    if is_subscribed(user_id):
        exp = STATE["users"][str(user_id)]["expires_at"]
        return f"✅ Actif jusqu'au {datetime.fromisoformat(exp).strftime('%d/%m/%Y à %H:%M')}."
    return "❌ Abonnement expiré ou inexistant.\nContactez @hermann1123 pour activer votre clé."

def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except: pass

def load_tracker(): return load_json(LOCAL_DB, {"coupons": []})
def save_tracker(data): save_json(LOCAL_DB, data)

# =====================================================================
# 🛠️ UTILITAIRES
# =====================================================================
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

def is_reserve_team(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in RESERVE_KEYWORDS)

def parse_dt_safe(s) -> Optional[datetime]:
    if not s: return None
    try:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except: return None

def format_kickoff(kickoff_str) -> str:
    if not kickoff_str: return "Heure non confirmée"
    dt = parse_dt_safe(kickoff_str)
    if dt:
        if dt.hour == 0 and dt.minute == 0: return dt.strftime("%d/%m") + " (heure non confirmée)"
        return dt.strftime("%d/%m %H:%M")
    return "Heure non confirmée"

def is_match_not_started(kickoff_str) -> bool:
    if not kickoff_str: return True
    dt = parse_dt_safe(kickoff_str)
    if not dt: return True
    if dt.hour == 0 and dt.minute == 0: return True
    now = datetime.now(TZ)
    buffer = timedelta(minutes=MATCH_START_BUFFER_MINUTES)
    return dt > (now + buffer)

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
        if len(sources) == 3 and delta <= 0.12: emoji = "🤝🤝" 
        elif delta <= 0.15: emoji = "🤝" 
        elif delta >= 0.20: emoji = "⚠️"
    return final, gem, dix, bzz, emoji

# =====================================================================
# 🎯 MOTEUR 1 : MT-FIN & MATCH NUL
# =====================================================================
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

def build_mtft_coupon_and_bonus(pool):
    picks = []
    bonus_pool = []

    for md in pool:
        if not md.home or not md.away or not md.dixon_pred: continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        
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

        pts = 15 + (10 if md.has_h2h else 0) + 10 + (30 if best_prob >= 0.65 else 20) + 15 + 10
        if pts < MTFIN_MIN_SCORE: continue

        est_odd = round(1.0 / best_prob, 2) if best_prob > 0 else 3.5
        picks.append({
            "md": md, "pick": best_pick, "prob": best_prob, "score": pts, "odd": est_odd
        })

    coupon = None
    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    top3 = picks[:3]
    
    if len(top3) > 0:
        legs = [Selection(p["md"].event_id, p["md"].league, p["md"].home, p["md"].away, p["md"].kickoff, "MT-Fin", f"MT/Fin : {p['pick']}", p["odd"], p["prob"], None, -1.0, p["score"], htft_pick=p["pick"]) for p in top3]
        co, cp = 1.0, 1.0
        for s in legs: co *= s.odds; cp *= s.model_prob
        subtitle = f"{len(legs)} matchs Bookmaker Blind" if len(legs) < 3 else "3 matchs Bookmaker Blind"
        coupon = Coupon("MT-Fin", subtitle, "⏱", legs, round(co, 2), round(cp, 4), is_list=True, show_analysis=False)

    for p in top3:
        md = p["md"]
        if p["pick"] == "V1/V1":
            fav_market = f"Victoire {md.home} (1)"
            real_odd = md.real_odds.get("home")
        else:
            fav_market = f"Victoire {md.away} (2)"
            real_odd = md.real_odds.get("away")

        ft_res = p["pick"].split("/")[1]
        fav_ft_prob = sum(htft_probs.get(f"{ht}/{ft_res}", 0) for ht in ("V1", "V2", "X"))

        is_est = False
        if not real_odd:
            real_odd = max(1.30, round(1.0 / fav_ft_prob, 2)) if fav_ft_prob > 0 else 2.0
            is_est = True
            
        # Anti-Vague strict sur le bonus
        if real_odd < ODDS3_MIN_COTE_LEG: continue
            
        bonus_pool.append({
            "match": md, "type": fav_market, "odds": real_odd,
            "proba": fav_ft_prob * 100, "ai": md.analysis,
            "real_odd": real_odd if not is_est else None,
            "source": "MT-FIN", "kickoff": md.kickoff,
        })

    return coupon, bonus_pool

def build_draw_coupon_and_bonus(pool):
    candidates = []

    for md in pool:
        if not md.home or not md.away: continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        
        prob_draw, g, d, b, emoji = compute_probability(md, "match_nul")
        if prob_draw < DRAW_MIN_PROB or (g > 0 and g < 0.22) or emoji == "⚠️": continue
        
        home_odd, away_odd = md.real_odds.get("home"), md.real_odds.get("away")
        draw_odd = md.real_odds.get("draw")
        
        if not draw_odd: draw_odd = round(1.0 / prob_draw, 2) if prob_draw > 0 else 3.20
        if draw_odd < 3.10: continue
        if (home_odd and home_odd < 1.80) or (away_odd and away_odd < 1.80): continue
        
        pts = 30 + (25 if prob_draw >= 0.33 else 18) + (20 if g >= 0.30 else 12) + 10 + (5 if md.has_h2h else 0) + 10
        pts = max(0, min(100, pts))
        if pts < DRAW_MIN_SCORE: continue
        
        candidates.append(Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "Match Nul", "Match Nul (X)", draw_odd, prob_draw, draw_odd if md.real_odds.get("draw") else None, (prob_draw*draw_odd)-1, pts))

    coupon = None
    candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    top3 = candidates[:3]
    
    if len(top3) > 0:
        co, cp = 1.0, 1.0
        for s in top3: co *= s.odds; cp *= s.model_prob
        subtitle = f"{len(top3)} nuls combinés" if len(top3) < 3 else "3 nuls combinés"
        coupon = Coupon("Match Nul", subtitle, "🤝", top3, round(co, 2), round(cp, 4), is_list=True, show_analysis=False)

    candidates.sort(key=lambda x: x.model_prob, reverse=False) 
    bottom_candidates = candidates[:3]
    
    bonus_pool = []
    for c in bottom_candidates:
        md = next((m for m in pool if m.event_id == c.event_id), None)
        if not md: continue
        
        prob_home, _, _, _, _ = compute_probability(md, "victoire_home")
        prob_away, _, _, _, _ = compute_probability(md, "victoire_away")
        prob_dc_12 = prob_home + prob_away

        dc12_odd = round(1.0 / prob_dc_12, 2) if prob_dc_12 > 0 else 1.30

        if dc12_odd < ODDS3_MIN_COTE_LEG: continue

        bonus_pool.append({
            "match": md, "type": "Double Chance 12 (Pas de Nul)",
            "odds": dc12_odd, "proba": prob_dc_12 * 100,
            "ai": md.analysis, "real_odd": None,
            "source": "MATCH-NUL", "kickoff": md.kickoff,
        })

    return coupon, bonus_pool

# =====================================================================
# 🎯 MOTEUR 3 : ODDS +3 (AUTH & HYBRIDE)
# =====================================================================
def build_third_pillar(pool) -> list:
    pool_result = []
    for md in pool:
        if not md.home or not md.away: continue
        if not is_match_not_started(md.kickoff): continue
        if is_reserve_team(md.home) or is_reserve_team(md.away): continue
        
        best_candidate = None
        for market_key, (label, mkt_type) in {
            "btts_yes": ("BTTS Oui", "BTTS"),
            "btts_no": ("BTTS Non", "BTTS"),
            "over_1.5": ("Over 1.5", "O/U"),
            "under_2.5": ("Under 2.5", "O/U"),
            "over_2.5": ("Over 2.5", "O/U"),
        }.items():
            prob, g, d, b, emoji = compute_probability(md, market_key)
            if prob < 0.55 or emoji == "⚠️": continue
            
            real_odd = md.real_odds.get(market_key)
            is_est = False
            if not real_odd:
                real_odd = max(1.30, round(1.0 / prob, 2))
                is_est = True
            
            if real_odd < ODDS3_MIN_COTE_LEG: continue
            
            if not best_candidate or prob > best_candidate['proba']:
                best_candidate = {
                    "match": md, "type": label, "odds": real_odd,
                    "proba": prob * 100, "ai": md.analysis,
                    "real_odd": real_odd if not is_est else None,
                    "source": "BZZOIRO", "kickoff": md.kickoff,
                }
        
        if best_candidate:
            pool_result.append(best_candidate)
    
    pool_result.sort(key=lambda x: x['proba'], reverse=True)
    return pool_result[:3]

def build_odds3_auth_coupon(bonus_mtft, bonus_draw, third_pillar) -> Optional[Coupon]:
    # On prend TOUS les meilleurs matchs disponibles (Bonus MT-Fin + Nul + Bzzoiro)
    combined = bonus_mtft + bonus_draw + third_pillar
    if not combined: return None
    
    # On trie par la probabilité la plus haute
    combined.sort(key=lambda x: x['proba'], reverse=True)
    
    # Pour le AUTH, on veut 2 matchs max avec une cote entre 2.5 et 3.0
    for combo in itertools.combinations(combined[:10], 2):
        match_ids = [c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id for c in combo]
        if len(set(match_ids)) != len(match_ids): continue
        
        total_odds = combo[0]['odds'] * combo[1]['odds']
        if ODDS3_AUTH_MIN_COTE <= total_odds <= ODDS3_AUTH_MAX_COTE:
            legs = [Selection(
                event_id=c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id, 
                league=c['match'].league, 
                home=c['match'].home if hasattr(c['match'], 'home') else c['match'].home_team, 
                away=c['match'].away if hasattr(c['match'], 'away') else c['match'].away_team, 
                kickoff=c.get('kickoff', ''), market=c['type'], pick_label=c['type'], odds=c['odds'], 
                model_prob=c['proba']/100.0, real_odd=c['real_odd'], quality=90, analysis=c['ai']
            ) for c in combo]
            prob = (combo[0]['proba']/100.0) * (combo[1]['proba']/100.0)
            return Coupon("Odds +3 AUTH", "Sécurité - Top Probabilités", "🛡️", legs, round(total_odds, 2), round(prob, 4), show_analysis=True)
    
    return None

def build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar) -> Optional[Coupon]:
    if not bonus_mtft or not bonus_draw or not third_pillar: 
        # Fallback si on manque un pilier, on mélange tout
        combined = bonus_mtft + bonus_draw + third_pillar
        if not combined: return None
        third_pillar = combined # On autorise le mélange total si un pilier manque
        
    combined = bonus_mtft + bonus_draw + third_pillar
    
    for r in range(3, min(len(combined), 5) + 1):
        for combo in itertools.combinations(combined, r):
            match_ids = [c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id for c in combo]
            if len(set(match_ids)) != len(match_ids): continue
            
            # Règle de mixité
            has_mtft = any(c.get('source') == 'MT-FIN' for c in combo)
            has_draw = any(c.get('source') == 'MATCH-NUL' for c in combo)
            has_third = any(c.get('source') == 'BZZOIRO' for c in combo)
            
            if not (has_mtft and has_draw and has_third): continue
            
            all_not_started = all(is_match_not_started(c.get('kickoff', '')) for c in combo)
            if not all_not_started: continue
            
            total_odds = 1.0
            for x in combo: total_odds *= x['odds']
            
            if ODDS3_HYB_MIN_COTE <= total_odds <= ODDS3_HYB_MAX_COTE:
                legs = []
                total_prob = 1.0
                for c in combo:
                    total_prob *= (c['proba'] / 100.0)
                    legs.append(Selection(
                        event_id=c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id, 
                        league=c['match'].league, 
                        home=c['match'].home if hasattr(c['match'], 'home') else c['match'].home_team, 
                        away=c['match'].away if hasattr(c['match'], 'away') else c['match'].away_team, 
                        kickoff=c.get('kickoff', ''), market=c['type'], pick_label=c['type'], odds=c['odds'], 
                        model_prob=c['proba']/100.0, real_odd=c['real_odd'], quality=90, analysis=c['ai']
                    ))
                
                return Coupon("Odds +3 HYBRIDE", "Mélange MT-Fin + Nul + Bzzoiro", "🎯", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)
    
    return None

# =====================================================================
# 🔧 ASSEMBLAGE FINAL
# =====================================================================
async def enrich_and_analyze(pool):
    async with httpx.AsyncClient(timeout=70.0) as client:
        for idx, md in enumerate(pool[:GEMINI_MAX_CALLS_PER_SCAN]):
            try:
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
                
                if STATE.get("dixon_model"):
                    try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                    except: pass
                
                await asyncio.sleep(2.0)
            except Exception as e:
                print(f"  ⚠️ {e}")

async def scan():
    async with STATE["scan_lock"]:
        bzz_preds = []
        if fetch_bzzoiro_predictions_direct and BZZOIRO_API_KEY:
            try: bzz_preds = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            except Exception as e: print(f"⚠️ Bzz: {e}")

        infer_odds = {}
        if bzz_preds and fetch_infer_odds:
            matches_for_infer = [{"event_id": b["event_id"], "home": b["home"], "away": b["away"], "kickoff": b.get("kickoff", "")} for b in bzz_preds]
            try: infer_odds = await fetch_infer_odds(matches_for_infer)
            except Exception as e: print(f"⚠️ Infer crash: {e}")

        pool = []
        if bzz_preds:
            for bz in bzz_preds:
                md = MatchData(event_id=bz["event_id"], league=bz.get("league", ""), home=bz["home"], away=bz["away"], kickoff=bz.get("kickoff", ""), real_odds=infer_odds.get(bz["event_id"], {}), bzz_markets=bz.get("bzz_markets", {}), bzz_confidence=bz.get("bzz_confidence", 0))
                if STATE.get("dixon_model"):
                    try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                    except: pass
                pool.append(md)

            before = len(pool)
            pool = [md for md in pool if is_match_not_started(md.kickoff)]
            if before - len(pool) > 0: print(f"🚫 {before - len(pool)} matchs retirés (déjà commencés)")

            pool.sort(key=prescore_match, reverse=True)
            top_pool = pool[:GEMINI_MAX_CALLS_PER_SCAN]

            await enrich_and_analyze(top_pool)

        coupon_mtft, bonus_mtft = build_mtft_coupon_and_bonus(top_pool) if pool else (None, [])
        coupon_draw, bonus_draw = build_draw_coupon_and_bonus(top_pool) if pool else (None, [])
        third_pillar = build_third_pillar(top_pool) if pool else []

        coupons = []
        if coupon_mtft: coupons.append(coupon_mtft)
        if coupon_draw: coupons.append(coupon_draw)

        # 1. AUTHENTIQUE (Sécurité - Top Probabilités)
        coupon_auth = build_odds3_auth_coupon(bonus_mtft, bonus_draw, third_pillar)
        if coupon_auth: 
            coupons.insert(0, coupon_auth)
            print(f"  → Odds +3 AUTH OK : {len(coupon_auth.legs)} legs, cote {coupon_auth.combined_odds}")
        
        # 2. HYBRIDE (Value - Mélange des 3 sources)
        coupon_hybrid = build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar)
        if coupon_hybrid: 
            coupons.insert(1, coupon_hybrid)
            print(f"  → Odds +3 HYBRIDE OK : {len(coupon_hybrid.legs)} legs, cote {coupon_hybrid.combined_odds}")

        STATE["coupons"] = coupons
        STATE["last_scan_date"] = datetime.now(TZ).strftime("%Y-%m-%d")
        
        print(f"──────── RÉSUMÉ ────────")
        print(f"Coupons : {len(STATE['coupons'])}")
        for c in STATE["coupons"]: print(f"  • {c.name} : {len(c.legs)} legs, cote {c.combined_odds}")
        print("────────────────────────")

# =====================================================================
# 🛡️ FORMATAGE & HANDLERS
# =====================================================================
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
        except Exception as e: 
            if "chat not found" not in str(e).lower(): print(f"⚠️ {e}")

async def broadcast_after_scan():
    if not STATE["coupons"]:
        msg = f"<b>📭 Aucun coupon aujourd'hui</b>"
    else:
        lignes = [f"{c.emoji} <b>{c.name}</b> • {len(c.legs)} paris • Cote <b>{c.combined_odds}</b>" for c in STATE["coupons"]]
        msg = f"<b>🔔 COUPONS DU JOUR !</b>\n\n" + "\n\n".join(lignes)
        
    for cid in TARGETS:
        await safe_send(cid, msg)
        await asyncio.sleep(0.5)

def format_header():
    return f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n     <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n<i>{BRAND_TAGLINE}</i>\n<i>📅 {today_pretty()} • 🇨🇮 Côte d’Ivoire</i>"

def format_selection_block(s, idx, show_analysis):
    lines = [f"<b>┌─ #{idx} {s.home} vs {s.away}</b>", f"│ 📅 <b>{format_kickoff(s.kickoff)}</b> • {s.league}", f"│ 🎯 <b>{s.pick_label}</b>", f"│ 📊 Proba : <b>{s.model_prob*100:.1f}%</b> {prob_bar(s.model_prob)}"]
    if s.quality > 0: lines.append(f"│ ⭐ Qualité : <b>{s.quality}/100</b> {quality_bar(s.quality)}")
    if s.htft_pick: lines.append(f"│ ⏱ MT/Fin : <b>{s.htft_pick}</b>")
    if s.real_odd: lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b>")
    else: lines.append(f"│ 💰 Cote : <b>{s.odds}</b> <i>(estimée)</i>")
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

async def send_coupon(m, name):
    c = next((x for x in STATE["coupons"] if x.name == name), None)
    if c: await safe_answer(m, format_coupon(c))
    else: await safe_answer(m, f"Aucun coupon {name} aujourd'hui.")

# --- COMMANDES ADMIN ---
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

# --- COMMANDES ABONNÉS ---
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

# --- HANDLERS BOUTONS ---
@dp.message(Command("start"))
async def start_cmd(m):
    kb = ADMIN_KEYBOARD if m.from_user.id == ADMIN_ID else USER_KEYBOARD
    msg = f"{format_header()}\n\n<b>👋 Admin</b>\n/scan pour analyser." if m.from_user.id == ADMIN_ID else f"{format_header()}\n\n{get_subscription_message(m.from_user.id)}"
    await safe_answer(m, msg, reply_markup=kb)

@dp.message(F.text == BTN_ODDS3)
async def b_odds3(m):
    if m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        await m.answer(f"❌ {get_subscription_message(m.from_user.id)}"); return
    
    # On envoie le AUTH (Sécurité) d'abord
    c_auth = next((x for x in STATE["coupons"] if x.name == "Odds +3 AUTH"), None)
    if c_auth: await safe_answer(m, format_coupon(c_auth))
    else: await safe_answer(m, "Aucun coupon AUTHENTIQUE aujourd'hui.")
    
    await asyncio.sleep(1)
    
    # Puis le HYBRIDE (Value)
    c_hyb = next((x for x in STATE["coupons"] if x.name == "Odds +3 HYBRIDE"), None)
    if c_hyb: await safe_answer(m, format_coupon(c_hyb))
    else: await safe_answer(m, "Aucun coupon HYBRIDE aujourd'hui.")

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
    await safe_answer(m, "Bilan en construction.")

@dp.message(F.text == BTN_SCAN)
async def b_scan(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, "⏳ Analyse des matchs en cours...")
    await scan()
    await broadcast_after_scan()

@dp.message(F.text == BTN_ACTIVATE)
async def b_act(m): await m.answer("🔑 /activate VOTRE_CLE")

@dp.message(F.text == "⚙️ Debug")
async def b_debug(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, f"<b>🔍 DEBUG</b>\n\nCoupons : {len(STATE['coupons'])}")

@dp.message(Command("scan"))
async def scan_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, "⏳ Analyse des matchs en cours...")
    await scan()
    await broadcast_after_scan()

async def daily_broadcast():
    try:
        await scan()
        record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"⚠️ Erreur record_coupons (non bloquant) : {e}")
    
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
    scheduler.add_job(
        daily_broadcast, 
        CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ), 
        id="daily", 
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True
    )
    scheduler.start()
    bot_task = asyncio.create_task(dp.start_polling(bot))
    print(f"✅ Scan programmé à {NOTIFY_HOUR:02d}h{NOTIFY_MINUTE:02d}")

    async def bootstrap():
        try:
            if download_and_train:
                STATE["dixon_model"] = await download_and_train()
                print(f"✅ Dixon chargé")
            await asyncio.sleep(5)
            
            today_str = datetime.now(TZ).strftime("%Y-%m-%d")
            last_scan_date = STATE.get("last_scan_date", "")
            
            if last_scan_date != today_str:
                if NOTIFY_ON_STARTUP:
                    for cid in TARGETS: 
                        await safe_send(cid, f"{format_header()}\n\n⏳ Bot démarré. Premier scan du jour en cours...")
                await daily_broadcast()
                STATE["last_scan_date"] = today_str
            else:
                if NOTIFY_ON_STARTUP:
                    for cid in TARGETS: 
                        await safe_send(cid, f"{format_header()}\n\n⏳ Bot redémarré. Scan déjà fait aujourd'hui. Prochain scan à {NOTIFY_HOUR:02d}h00...")
                
        except Exception as e: print(f"⚠️ {e}")

    asyncio.create_task(bootstrap())
    yield
    scheduler.shutdown(wait=False); bot_task.cancel()
    try: await bot.session.close()
    except: pass

app = FastAPI(title="Value Bot", lifespan=lifespan)

@app.get("/")
async def root(): return {"status": "ok", "coupons": [c.name for c in STATE["coupons"]]}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
