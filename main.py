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

# --- VARIABLES ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
INFERSPORTS_API_KEY = os.getenv("INFERSPORTS_API_KEY", "")

TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "5968288964")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "-1003982738017")

API_KEY_ODDS = os.getenv("ODDS_API_KEY", "af11eb9ae715ca3585f4abdb00227bd6")

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

# MOTEUR MT-FIN
MTFIN_MIN_SCORE = 70
MTFIN_MIN_PROB = 0.38
MTFIN_TOP_N = 3
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45

# MOTEUR MATCH NUL
DRAW_MIN_SCORE = 70
DRAW_MAX_ECART = 0.20
DRAW_MIN_PROB = 0.28
DRAW_TOP_N = 3

# MOTEUR ODDS +3
ODDS3_MIN_COTE_LEG = 1.15
ODDS3_MIN_COTE_TOTAL = 3.0
ODDS3_MAX_COTE_TOTAL = 5.0  # Modifié à 5.0

TELEGRAM_MAX_LEN = 3800
TZ = ZoneInfo(NOTIFY_TZ)

if not BOT_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise RuntimeError("Variables d'environnement manquantes")

# =====================================================================
# ⚙️ MOTEUR WALLSTREET
# =====================================================================

@dataclass
class WS_MatchData:
    match_id: str
    league: str
    home_team: str
    away_team: str
    home_odds: float
    draw_odds: float
    away_odds: float

@dataclass
class WS_SimulationResult:
    match_id: str
    proba_home: float
    proba_draw: float
    proba_away: float
    most_likely_score: str
    proba_btts: float
    proba_over_1_5: float
    proba_over_2_5: float
    proba_over_3_5: float

class DixonColesEngine:
    def __init__(self, rho: float = -0.15, home_advantage: float = 1.15):
        self.rho = rho
        self.home_advantage = home_advantage
        self.max_goals = 6

    def simulate(self, match: WS_MatchData) -> WS_SimulationResult:
        lambda_x = (1.0 / match.home_odds) * 1.8 * self.home_advantage
        mu_y = (1.0 / match.away_odds) * 1.8
        matrix = np.zeros((self.max_goals, self.max_goals))

        for i in range(self.max_goals):
            for j in range(self.max_goals):
                matrix[i, j] = poisson.pmf(i, lambda_x) * poisson.pmf(j, mu_y)
        
        matrix /= np.sum(matrix)
        p_home = float(np.sum(np.tril(matrix, -1))) * 100
        p_draw = float(np.sum(np.diag(matrix))) * 100
        p_away = float(np.sum(np.triu(matrix, 1))) * 100

        best_idx = np.argmax(matrix)
        score_x, score_y = np.unravel_index(best_idx, matrix.shape)

        p_btts = float(np.sum(matrix[1:, 1:])) * 100
        p_o15 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 1])) * 100
        p_o25 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 2])) * 100
        p_o35 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 3])) * 100

        return WS_SimulationResult(
            match_id=match.match_id, proba_home=p_home, proba_draw=p_draw, proba_away=p_away, 
            most_likely_score=f"{score_x}-{score_y}", proba_btts=p_btts, 
            proba_over_1_5=p_o15, proba_over_2_5=p_o25, proba_over_3_5=p_o35
        )

dixon_ws = DixonColesEngine()

# =====================================================================
# 🎫 ARCHITECTURE VOLATILITY INDEX
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

def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
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

# =====================================================================
# 🎯 MOTEUR 1 : MT-FIN (Top 1-2 pour Odds +3)
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

    # On trie et on garde les 3 meilleurs pour le ticket MT-Fin
    coupon = None
    if len(picks) >= MTFIN_TOP_N:
        picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
        top3 = picks[:MTFIN_TOP_N]
        legs = [Selection(p["md"].event_id, p["md"].league, p["md"].home, p["md"].away, p["md"].kickoff, "MT-Fin", f"MT/Fin : {p['pick']}", p["odd"], p["prob"], None, -1.0, p["score"], htft_pick=p["pick"]) for p in top3]
        co, cp = 1.0, 1.0
        for s in legs: co *= s.odds; cp *= s.model_prob
        coupon = Coupon("MT-Fin", "3 matchs Bookmaker Blind", "⏱", legs, round(co, 2), round(cp, 4), is_list=True, show_analysis=False)

    # ⭐ BONUS ODDS +3 : On prend les 2 MEILLEURS (Top 1-2)
    top_bonus = picks[:2]
    for p in top_bonus:
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
            real_odd = round(1.0 / fav_ft_prob, 2) if fav_ft_prob > 0 else 2.0
            is_est = True

        bonus_pool.append({
            "match": md,
            "type": fav_market,
            "odds": real_odd,
            "proba": fav_ft_prob * 100,
            "ai": md.analysis,
            "real_odd": real_odd if not is_est else None,
            "source": "MT-FIN",
        })

    return coupon, bonus_pool

# =====================================================================
# 🤝 MOTEUR 2 : MATCH NUL (Bottom 1-2 pour Odds +3)
# =====================================================================
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

def build_draw_coupon_and_bonus(pool):
    candidates = []
    bonus_pool = []

    for md in pool:
        if not md.home or not md.away: continue
        
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

    # Ticket Match Nul : les 3 meilleurs
    coupon = None
    if len(candidates) >= DRAW_TOP_N:
        candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
        top3 = candidates[:DRAW_TOP_N]
        co, cp = 1.0, 1.0
        for s in top3: co *= s.odds; cp *= s.model_prob
        coupon = Coupon("Match Nul", "3 nuls combinés", "🤝", top3, round(co, 2), round(cp, 4), is_list=True, show_analysis=False)

    # ⭐ BONUS ODDS +3 : On prend les 2 MOINS PROBABLES (Bottom 1-2)
    # On trie par prob_draw croissant
    candidates.sort(key=lambda x: x.model_prob, reverse=False)  # Ordre croissant
    bottom_candidates = candidates[:2]
    
    for c in bottom_candidates:
        md = next((m for m in pool if m.event_id == c.event_id), None)
        if not md: continue
        
        prob_home, _, _, _, _ = compute_probability(md, "victoire_home")
        prob_away, _, _, _, _ = compute_probability(md, "victoire_away")
        prob_dc_12 = prob_home + prob_away

        dc12_odd = round(1.0 / prob_dc_12, 2) if prob_dc_12 > 0 else 1.30

        bonus_pool.append({
            "match": md,
            "type": "Double Chance 12 (Pas de Nul)",
            "odds": dc12_odd,
            "proba": prob_dc_12 * 100,
            "ai": md.analysis,
            "real_odd": None,
            "source": "MATCH-NUL",
        })

    return coupon, bonus_pool

# =====================================================================
# 🎯 MOTEUR 3 : ODDS +3 (Assemblage Top MT-Fin + Bottom Match Nul + Top WS)
# =====================================================================
async def fetch_real_odds_matches() -> list:
    url = f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={API_KEY_ODDS}&regions=eu&markets=h2h"
    matches = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=20.0)
            if response.status_code == 200:
                data = response.json()
                for m in data:
                    commence_time = m.get('commence_time', '')
                    if not commence_time.startswith(today_str): continue
                    if 'bookmakers' in m and len(m['bookmakers']) > 0:
                        cotes = {c['name']: c['price'] for c in m['bookmakers'][0]['markets'][0]['outcomes']}
                        home, away = m['home_team'], m['away_team']
                        
                        if home in cotes and away in cotes and 'Draw' in cotes:
                            matches.append(WS_MatchData(
                                match_id=m['id'], league=m['sport_title'],
                                home_team=home, away_team=away,
                                home_odds=cotes[home], draw_odds=cotes['Draw'], away_odds=cotes[away]
                            ))
                            if len(matches) >= 100: break
    except Exception as e:
        print(f"⚠️ Erreur API Odds: {e}")
    return matches

def build_ws_best(ws_matches) -> list:
    """
    Retourne le TOP 1 match de WallStreet (The Odds API).
    """
    pool = []
    for match in ws_matches:
        sim = dixon_ws.simulate(match)
        base_conf = max(sim.proba_home, sim.proba_draw, sim.proba_away)
        if base_conf < 50.0: continue
        
        p_home, p_draw, p_away = sim.proba_home, sim.proba_draw, sim.proba_away
        
        if p_home >= 55.0 and match.home_odds >= 1.15:
            pool.append({"match": match, "type": f"Victoire {match.home_team} (1)", "odds": match.home_odds, "proba": p_home, "ai": "Analyse mathématique solide.", "real_odd": match.home_odds, "source": "WALLSTREET"})
        if p_away >= 55.0 and match.away_odds >= 1.15:
            pool.append({"match": match, "type": f"Victoire {match.away_team} (2)", "odds": match.away_odds, "proba": p_away, "ai": "Analyse mathématique solide.", "real_odd": match.away_odds, "source": "WALLSTREET"})
    
    # On trie et on garde le TOP 1
    pool.sort(key=lambda x: x['proba'], reverse=True)
    return pool[:1]

def build_odds3_coupon(bonus_mtft, bonus_draw, ws_best) -> Optional[Coupon]:
    """
    Assemble le ticket Odds +3 avec :
    - Top 1-2 MT-Fin
    - Bottom 1-2 Match Nul
    - Top 1 WallStreet
    Cote totale entre 3.0 et 5.0.
    """
    # On combine tous les éléments
    combined = bonus_mtft + bonus_draw + ws_best
    
    if len(combined) < 2:
        print(f"  → Odds +3 : pas assez de matchs ({len(combined)})")
        return None
    
    # On cherche une combinaison qui donne une cote entre 3.0 et 5.0
    for r in range(2, min(len(combined), 5) + 1):
        for combo in itertools.combinations(combined, r):
            # Anti-doublon intra-ticket
            match_ids = [c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id for c in combo]
            if len(set(match_ids)) != len(match_ids): continue
            
            total_odds = 1.0
            for x in combo: total_odds *= x['odds']
            
            if ODDS3_MIN_COTE_TOTAL <= total_odds <= ODDS3_MAX_COTE_TOTAL:
                legs = []
                total_prob = 1.0
                for c in combo:
                    total_prob *= (c['proba'] / 100.0)
                    legs.append(Selection(
                        event_id=c['match'].event_id if hasattr(c['match'], 'event_id') else c['match'].match_id, 
                        league=c['match'].league, 
                        home=c['match'].home if hasattr(c['match'], 'home') else c['match'].home_team, 
                        away=c['match'].away if hasattr(c['match'], 'away') else c['match'].away_team, 
                        kickoff="", market=c['type'], pick_label=c['type'], odds=c['odds'], 
                        model_prob=c['proba']/100.0, real_odd=c['real_odd'], quality=90, analysis=c['ai']
                    ))
                
                n_mtft = sum(1 for c in combo if c.get('source') == 'MT-FIN')
                n_nul = sum(1 for c in combo if c.get('source') == 'MATCH-NUL')
                n_ws = sum(1 for c in combo if c.get('source') == 'WALLSTREET')
                print(f"  → Odds +3 OK : {len(legs)} legs ({n_mtft} MT-Fin + {n_nul} Nul + {n_ws} WS), cote {total_odds:.2f}")
                return Coupon("Odds +3", "Moteur WallStreet Fusionné", "🎯", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)
    
    print(f"  → Odds +3 : 0 combinaison possible")
    return None

# =====================================================================
# 🔧 ASSEMBLAGE FINAL
# =====================================================================
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
                
                if STATE.get("dixon_model"):
                    try: md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
                    except: pass
                
                await asyncio.sleep(2.0)
            except Exception as e:
                print(f"  ⚠️ {e}")

async def scan():
    async with STATE["scan_lock"]:
        # ÉTAPE 1 : Données Bzzoiro + InferSports
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

            pool.sort(key=prescore_match, reverse=True)
            top_pool = pool[:GEMINI_MAX_CALLS_PER_SCAN]

            await enrich_and_analyze(top_pool)

        # ÉTAPE 2 : MT-Fin et Match Nul (tickets + bonus Top/Bottom)
        coupon_mtft, bonus_mtft = build_mtft_coupon_and_bonus(top_pool) if pool else (None, [])
        coupon_draw, bonus_draw = build_draw_coupon_and_bonus(top_pool) if pool else (None, [])

        coupons = []
        if coupon_mtft: coupons.append(coupon_mtft)
        if coupon_draw: coupons.append(coupon_draw)

        # ÉTAPE 3 : Odds +3 (Top MT-Fin + Bottom Match Nul + Top WS)
        print("🎯 Construction Odds +3 (Top MT-Fin + Bottom Nul + Top WS)...")
        print(f"  → Bonus MT-Fin : {len(bonus_mtft)} matchs")
        print(f"  → Bonus Match-Nul : {len(bonus_draw)} matchs")
        
        ws_matches = await fetch_real_odds_matches()
        ws_best = build_ws_best(ws_matches) if ws_matches else []
        print(f"  → WS Best : {len(ws_best)} matchs")
        
        coupon_odds3 = build_odds3_coupon(bonus_mtft, bonus_draw, ws_best)
        if coupon_odds3:
            coupons.insert(0, coupon_odds3)

        STATE["coupons"] = coupons
        STATE["last_scan_date"] = datetime.now(TZ).strftime("%Y-%m-%d")
        
        print(f"──────── RÉSUMÉ ────────")
        print(f"Coupons : {len(STATE['coupons'])}")
        for c in STATE["coupons"]:
            print(f"  • {c.name} : {len(c.legs)} legs, cote {c.combined_odds}")
        print("────────────────────────")

# --- FORMATAGE & HANDLERS ---
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

@dp.message(Command("start"))
async def start_cmd(m):
    kb = ADMIN_KEYBOARD if m.from_user.id == ADMIN_ID else USER_KEYBOARD
    msg = f"{format_header()}\n\n<b>👋 Admin</b>\n/scan pour analyser." if m.from_user.id == ADMIN_ID else f"{format_header()}\n\nBienvenue."
    await safe_answer(m, msg, reply_markup=kb)

@dp.message(Command("scan"))
async def scan_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    await safe_answer(m, "⏳ Analyse des matchs en cours (Patientez 3-5 min)...")
    await scan()
    await broadcast_after_scan()

@dp.message(F.text == BTN_ODDS3)
async def b_odds3(m): await send_coupon(m, "Odds +3")
@dp.message(F.text == BTN_MTFT)
async def b_mtft(m): await send_coupon(m, "MT-Fin")
@dp.message(F.text == BTN_DRAW)
async def b_draw(m): await send_coupon(m, "Match Nul")
@dp.message(F.text == BTN_BILAN)
async def b_bilan(m): await safe_answer(m, "Bilan en construction.")
@dp.message(F.text == BTN_SCAN)
async def b_scan(m): await scan_cmd(m)
@dp.message(F.text == BTN_ACTIVATE)
async def b_act(m): await m.answer("🔑 /activate VOTRE_CLE")
@dp.message(F.text == "⚙️ Debug")
async def b_debug(m): await safe_answer(m, "Debug")

async def daily_broadcast():
    await scan()
    record_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    await broadcast_after_scan()

@asynccontextmanager
async def lifespan(app):
    try: await bot.delete_webhook(drop_pending_updates=True)
    except: pass
    STATE["tracker"] = load_json(LOCAL_DB, {"coupons": []})
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
