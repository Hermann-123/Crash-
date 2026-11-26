from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import secrets
import unicodedata
import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from scipy.stats import poisson
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

# =====================================================================
# IMPORTATIONS OPTIONNELLES DES MODULES EXTERNES DU PROJET
# =====================================================================
try:
    from gemini_predictor import predict_match_probas
except Exception:
    predict_match_probas = None

try:
    from gemini_predictor import explain_selected_pick_with_gemini
except Exception:
    explain_selected_pick_with_gemini = None

try:
    from bzzoiro_data import enrich_match_with_bzzoiro
except Exception:
    enrich_match_with_bzzoiro = None

try:
    from bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
except Exception:
    fetch_bzzoiro_predictions_direct = None

try:
    from odds_fallback import fetch_all_odds as fetch_infer_odds
except Exception:
    fetch_infer_odds = None

try:
    from ml_engine import train_model_if_needed, predict_ml, fuse_probas
    print("✅ ml_engine chargé")
except Exception as e:
    train_model_if_needed = predict_ml = fuse_probas = None
    print(f"⚠️ ml_engine non chargé : {e}")

try:
    from rapidoddsapi_data import fetch_all_odds as fetch_rapidodds_odds_data
except Exception:
    fetch_rapidodds_odds_data = None

try:
    from dixon_coles import download_and_train
except Exception:
    download_and_train = None

try:
    from weekly_analyzer import analyze_weekly_top_matches
    print("✅ weekly_analyzer chargé")
except Exception as e:
    analyze_weekly_top_matches = None
    print(f"⚠️ weekly_analyzer non chargé : {e}")

# =====================================================================
# VARIABLES D'ENVIRONNEMENT & CONFIGURATION
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("CLÉ_API_GEMINI", ""))
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", os.getenv("ID_ADMIN_TELEGRAM", "5968288964"))
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "-1003982738017")
RAPIDODDSAPI_API_KEY = os.getenv("RAPIDODDSAPI_API_KEY", "")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")

try:
    ADMIN_ID = int(TELEGRAM_ADMIN_ID)
except Exception:
    ADMIN_ID = 0

NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", os.getenv("HEURE_NOTIFY", "8")))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")
LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")
COUPONS_CACHE_DB = os.getenv("COUPONS_CACHE_DB", "/tmp/coupons_today.json")

BRAND_NAME = os.getenv("BRAND_NAME", os.getenv("NOM_MARQUE", "VOLATILITY INDEX"))
BRAND_SLOGAN = os.getenv("BRAND_SLOGAN", os.getenv("SLOGAN_MARQUE", "Analyse IA • VIP"))
PUBLIC_FOOTER = os.getenv(
    "PUBLIC_FOOTER",
    "⚠️ Analyse statistique. Gestion de bankroll recommandée (1-3% par ticket).",
)
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "20"))
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

WEIGHT_ALGO = float(os.getenv("WEIGHT_ALGO", "0.35"))
WEIGHT_GEMINI = float(os.getenv("WEIGHT_GEMINI", "0.35"))
WEIGHT_BZZOIRO = float(os.getenv("WEIGHT_BZZOIRO", "0.30"))

# ✅ CALIBRAGE BAYÉSIEN & BOUCLIER ANTI-SURCONFIANCE (V2 PRO)
MARKET_SHRINKAGE_WEIGHT = float(os.getenv("MARKET_SHRINKAGE_WEIGHT", "0.65"))  # 65% marché sans marge / 35% modèle
MAX_SINGLE_EDGE = float(os.getenv("MAX_SINGLE_EDGE", "0.11"))                    # Rejette écart > +11% vs bookmaker
MIN_EDGE = float(os.getenv("MIN_EDGE", "-0.03"))
MAX_SINGLE_EV = float(os.getenv("MAX_SINGLE_EV", "1.18"))                        # Rejette EV irréaliste (> 1.18)
MIN_COUPON_EV = float(os.getenv("MIN_COUPON_EV", "0.94"))
MIN_REAL_COUPON_EV = float(os.getenv("MIN_REAL_COUPON_EV", "0.95"))
MAX_COUPON_EV = float(os.getenv("MAX_COUPON_EV", "1.28"))

# ✅ CALIBRAGE MOTEUR VIP ODDS +3 (DUO HAUTE CONFIANCE -> TRIO SÉCURITÉ)
AUTH_PROBA_MIN = float(os.getenv("AUTH_PROBA_MIN", "61.0"))
AUTH_PROBA_MAX = float(os.getenv("AUTH_PROBA_MAX", "88.0"))
AUTH_MIN_COMBINED_PROB = float(os.getenv("AUTH_MIN_COMBINED_PROB", "0.30"))
ODDS3_AUTH_MIN_COTE = float(os.getenv("ODDS3_AUTH_MIN_COTE", "2.40"))
ODDS3_AUTH_MAX_COTE = float(os.getenv("ODDS3_AUTH_MAX_COTE", "3.45"))
ODDS3_AUTH_MIN_LEG_ODD = float(os.getenv("ODDS3_AUTH_MIN_LEG_ODD", "1.22"))
ODDS3_AUTH_MAX_LEG_ODD = float(os.getenv("ODDS3_AUTH_MAX_LEG_ODD", "1.92"))

ODDS3_HYB_MIN_COTE = float(os.getenv("ODDS3_HYB_MIN_COTE", "3.50"))
ODDS3_HYB_MAX_COTE = float(os.getenv("ODDS3_HYB_MAX_COTE", "5.80"))
ODDS3_MIN_COTE_LEG = float(os.getenv("ODDS3_MIN_COTE_LEG", "1.22"))

# ✅ VERROUS SPÉCIFIQUES MARCHÉS DE BUTS & BLESSURES ESPN
OVER15_MIN_ODD = float(os.getenv("OVER15_MIN_ODD", "1.28"))
OVER15_MAX_ODD = float(os.getenv("OVER15_MAX_ODD", "1.52"))
OVER15_MIN_TOTAL_XG = float(os.getenv("OVER15_MIN_TOTAL_XG", "2.65"))
OVER15_MIN_TEAM_XG = float(os.getenv("OVER15_MIN_TEAM_XG", "0.65"))
UNDER25_MAX_TOTAL_XG = float(os.getenv("UNDER25_MAX_TOTAL_XG", "2.50"))
MAX_TEAM_INJURIES = int(os.getenv("MAX_TEAM_INJURIES", "4"))

DOUBLE_SCAN_POOL_SIZE = int(os.getenv("DOUBLE_SCAN_POOL_SIZE", "12"))
DOUBLE_SCAN_STABILITY_DELTA = float(os.getenv("DOUBLE_SCAN_STABILITY_DELTA", "0.16"))
DOUBLE_SCAN_MIN_STABLE = int(os.getenv("DOUBLE_SCAN_MIN_STABLE", "3"))
DOUBLE_SCAN_PAUSE_SECONDS = int(os.getenv("DOUBLE_SCAN_PAUSE_SECONDS", "3"))

MTFIN_MIN_SCORE = 70
MTFIN_MIN_PROB = 0.42
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0.45

DRAW_MIN_SCORE = 70
DRAW_MAX_ECART = 0.20
DRAW_MIN_PROB = 0.30

MATCH_START_BUFFER_MINUTES = 10
RESERVE_KEYWORDS = ["jong", " fc b", " ii", " u21", " u19", " castilla", " reserves", " réserve", " équipe b"]

PRO_TRAP_PHRASES = [
    "piège à allumettes",
    "risque élevé",
    "effectif remanié",
    "équipe bis",
    "équipe b",
    "nombreux absents",
    "blessures majeures",
    "aucun enjeu",
    "sans enjeu",
    "déjà qualifié",
    "déjà éliminé",
    "déjà champion",
    "manque de motivation",
    "match amical",
    "coupe secondaire",
    "en pleine crise",
    "démobilisé",
]

LOW_STAKES_LEAGUE_KEYWORDS = [
    "amical", "friendly", "club friendlies", "efl trophy", "checkatrade",
    "premier league cup", "premier league 2", "u21", "u23", "u19", "youth",
    "réserve", "oberliga", "regionalliga", "taca da liga", "coupe de la ligue",
]

HIGH_STAKES_LEAGUE_KEYWORDS = [
    "champions league", "ligue des champions", "europa league", "ligue europa",
    "conference league", "ligue de conférence", "premier league", "la liga", "laliga",
    "serie a", "bundesliga", "ligue 1", "championship", "eredivisie", "primeira liga",
    "pro league", "super lig", "world cup", "coupe du monde", "euro", "can",
    "copa america", "libertadores", "sudamericana", "playoff", "barrage",
]

HIGH_STAKES_ANALYSIS_PHRASES = [
    "qualification", "qualifier", "course au titre", "titre", "podium",
    "maintien", "relégation", "zone rouge", "top 4", "top 8", "place européenne",
    "europe", "montée", "promotion", "play-off", "barrage", "derby",
    "revanche", "impératif", "victoire obligatoire", "doit gagner", "enjeu majeur",
    "crucial", "décisif", "capital", "sous pression",
]

ESPN_LEAGUE_SLUGS = [
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
    "eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1", "gre.1",
    "conmebol.libertadores", "usa.1", "mex.1", "bra.1", "arg.1",
]

TELEGRAM_MAX_LEN = 3800
TZ = ZoneInfo(NOTIFY_TZ)

if not BOT_TOKEN or not ADMIN_ID:
    raise RuntimeError("Variables d'environnement critiques manquantes (BOT_TOKEN / TELEGRAM_ADMIN_ID)")


# =====================================================================
# MODÈLES DE DONNÉES & MOTEUR DIXON-COLES CALIBRÉ
# =====================================================================
@dataclass
class WS_MatchData:
    match_id: str
    league: str
    home_team: str
    away_team: str
    home_odds: Optional[float]
    draw_odds: Optional[float]
    away_odds: Optional[float]
    over_2_5: Optional[float] = None
    under_2_5: Optional[float] = None
    over_1_5: Optional[float] = None
    under_1_5: Optional[float] = None
    btts_yes: Optional[float] = None
    btts_no: Optional[float] = None
    commence_time: str = ""
    espn_slug: str = ""
    espn_event_id: str = ""


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
    lambda_home: float = 1.25
    lambda_away: float = 1.05


class DixonColesEngine:
    """
    Moteur Dixon-Coles avec :
    - Dé-vigging des cotes bookmaker (suppression de la marge avant calcul des lambdas)
    - Neutralisation automatique de l'avantage domicile sur terrain neutre (neutral_venue=True)
    - Correction de corrélation des petits scores tau(lambda, mu, rho)
    """

    def __init__(self, rho: float = -0.11, max_goals: int = 8):
        self.rho = rho
        self.max_goals = max_goals

    def simulate_from_lambdas(
        self,
        match_id: str,
        home_xg: float,
        away_xg: float,
        neutral_venue: bool = False,
    ) -> WS_SimulationResult:
        home_adv = 1.00 if neutral_venue else 1.10
        lx = max(0.20, float(home_xg) * home_adv)
        my = max(0.20, float(away_xg))

        matrix = np.zeros((self.max_goals, self.max_goals))
        for i in range(self.max_goals):
            p_i = poisson.pmf(i, lx)
            for j in range(self.max_goals):
                p_j = poisson.pmf(j, my)
                if i == 0 and j == 0:
                    tau = 1.0 - (lx * my * self.rho)
                elif i == 1 and j == 0:
                    tau = 1.0 + (my * self.rho)
                elif i == 0 and j == 1:
                    tau = 1.0 + (lx * self.rho)
                elif i == 1 and j == 1:
                    tau = 1.0 - self.rho
                else:
                    tau = 1.0
                matrix[i, j] = max(0.0, p_i * p_j * tau)

        total = float(np.sum(matrix))
        if total <= 0:
            total = 1.0
        matrix /= total

        p_h = float(np.sum(np.tril(matrix, -1))) * 100.0
        p_d = float(np.sum(np.diag(matrix))) * 100.0
        p_a = float(np.sum(np.triu(matrix, 1))) * 100.0
        best_idx = np.argmax(matrix)
        sx, sy = np.unravel_index(best_idx, matrix.shape)
        p_btts = float(np.sum(matrix[1:, 1:])) * 100.0
        p_o15 = float(sum(matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j >= 2)) * 100.0
        p_o25 = float(sum(matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j >= 3)) * 100.0
        p_o35 = float(sum(matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j >= 4)) * 100.0

        return WS_SimulationResult(
            match_id=match_id,
            proba_home=p_h,
            proba_draw=p_d,
            proba_away=p_a,
            most_likely_score=f"{sx}-{sy}",
            proba_btts=p_btts,
            proba_over_1_5=p_o15,
            proba_over_2_5=p_o25,
            proba_over_3_5=p_o35,
            lambda_home=round(lx, 2),
            lambda_away=round(my, 2),
        )

    def simulate(self, match: WS_MatchData, neutral_venue: bool = False) -> Optional[WS_SimulationResult]:
        if not match.home_odds or not match.away_odds:
            return None
        inv_h = 1.0 / float(match.home_odds)
        inv_a = 1.0 / float(match.away_odds)
        inv_d = (1.0 / float(match.draw_odds)) if match.draw_odds and float(match.draw_odds) > 1.01 else 0.27
        overround = inv_h + inv_d + inv_a

        p_h_true = inv_h / overround
        p_a_true = inv_a / overround

        base_total_goals = 2.65
        if match.over_2_5 and match.under_2_5 and match.over_2_5 > 1.01 and match.under_2_5 > 1.01:
            ov_r = (1.0 / float(match.over_2_5)) + (1.0 / float(match.under_2_5))
            p_o25_true = (1.0 / float(match.over_2_5)) / ov_r
            base_total_goals = 1.85 + (p_o25_true * 1.65)

        share_h = max(0.18, min(0.82, p_h_true / max(0.05, p_h_true + p_a_true)))
        raw_home_xg = base_total_goals * share_h
        raw_away_xg = base_total_goals * (1.0 - share_h)
        return self.simulate_from_lambdas(match.match_id, raw_home_xg, raw_away_xg, neutral_venue=True)


dixon_ws = DixonColesEngine()


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
    gemini_markets: Optional[dict] = None
    dixon_pred: Optional[dict] = None
    analysis: str = ""
    has_h2h: bool = False
    stability_flag: str = "unknown"
    espn_ctx: dict = field(default_factory=dict)


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
BTN_ODDS3 = "🎯 Cote +3"
BTN_MTFT = "⏱ MT-Fin"
BTN_DRAW = "🤝 Match nul"
BTN_WEEKLY = "📅 Meilleure Semaine"
BTN_SCAN = "🔄 Actualiser"
BTN_BILAN = "📊 Bilan"

USER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_ODDS3)]],
    resize_keyboard=True,
)
ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ODDS3), KeyboardButton(text=BTN_MTFT)],
        [KeyboardButton(text=BTN_DRAW), KeyboardButton(text=BTN_WEEKLY)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Débogage")],
    ],
    resize_keyboard=True,
)

STATE: dict[str, Any] = {
    "ready": False,
    "coupons": [],
    "last_analysis": None,
    "debug": {},
    "tracker": {"coupons": []},
    "keys": {},
    "users": {},
    "scan_lock": asyncio.Lock(),
    "verify_lock": asyncio.Lock(),
    "dixon_model": None,
    "last_scan_date": "",
    "last_score_check": None,
    "double_scan_status": "",
    "double_scan_stats": {},
    "odds_api_mode": "inconnu",
    "rejections": {
        "value": 0,
        "coupon_ev": 0,
        "auth_proba": 0,
        "pro_trap": 0,
        "unstable": 0,
        "no_real_odds": 0,
        "espn_guard": 0,
    },
}

TARGETS: list[Any] = []
for raw in (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL):
    if raw:
        try:
            TARGETS.append(int(raw))
        except ValueError:
            TARGETS.append(raw)


# =====================================================================
# GESTION DES CLÉS VIP & ABONNÉS
# =====================================================================
def generate_vip_key_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    p1 = "".join(secrets.choice(alphabet) for _ in range(4))
    p2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"VIP-{p1}-{p2}"


def create_access_keys(days: int = 30, count: int = 1) -> list[str]:
    created = []
    now_iso = datetime.now(TZ).isoformat()
    for _ in range(count):
        code = generate_vip_key_code()
        while code in STATE["keys"]:
            code = generate_vip_key_code()
        STATE["keys"][code] = {
            "days": days,
            "created_at": now_iso,
            "used": False,
            "used_by": None,
            "used_at": None,
        }
        created.append(code)
    save_json(KEYS_DB, STATE["keys"])
    return created


def redeem_access_key(user, raw_code: str) -> Tuple[bool, str]:
    code = raw_code.strip().upper()
    key_info = STATE["keys"].get(code)
    if not key_info:
        return False, "❌ Clé invalide ou introuvable."
    if key_info.get("used"):
        return False, "❌ Cette clé a déjà été utilisée."

    uid = str(user.id)
    now = datetime.now(TZ)
    days = int(key_info.get("days", 30))

    base_dt = now
    if uid in STATE["users"]:
        try:
            old_exp = datetime.fromisoformat(STATE["users"][uid].get("expires_at", ""))
            if old_exp > now:
                base_dt = old_exp
        except Exception:
            pass

    new_exp = base_dt + timedelta(days=days)
    STATE["users"][uid] = {
        "user_id": user.id,
        "username": user.username or "",
        "full_name": user.full_name or "",
        "expires_at": new_exp.isoformat(),
        "last_key": code,
        "updated_at": now.isoformat(),
    }
    key_info["used"] = True
    key_info["used_by"] = user.id
    key_info["used_at"] = now.isoformat()

    save_json(USERS_DB, STATE["users"])
    save_json(KEYS_DB, STATE["keys"])

    exp_str = new_exp.strftime("%d/%m/%Y à %H:%M")
    return (
        True,
        f"✅ <b>Accès VIP activé ({days} jours) !</b>\n📅 Valide jusqu'au <b>{exp_str}</b>.\n\nAppuie sur <b>{BTN_ODDS3}</b> pour voir le ticket du jour.",
    )


def is_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    uid = str(user_id)
    if uid not in STATE["users"]:
        return False
    exp = STATE["users"][uid].get("expires_at")
    if not exp:
        return False
    try:
        return datetime.now(TZ) < datetime.fromisoformat(exp)
    except Exception:
        return False


def get_subscription_message(user_id: int) -> str:
    if user_id == ADMIN_ID:
        return "✅ Admin"
    if is_subscribed(user_id):
        exp_fmt = datetime.fromisoformat(STATE["users"][str(user_id)]["expires_at"]).strftime("%d/%m/%Y à %H:%M")
        return f"✅ Actif jusqu'au {exp_fmt}."
    return "❌ Abonnement expiré ou inactif.\n🔑 <i>Colle directement ta clé d'accès dans ce chat pour activer ton VIP.</i>"


# =====================================================================
# UTILITAIRES & CACHE JOURNALIER
# =====================================================================
def load_json(path: str, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def serialize_coupon(c: Coupon) -> dict:
    return {
        "name": c.name,
        "subtitle": c.subtitle,
        "emoji": c.emoji,
        "combined_odds": c.combined_odds,
        "combined_prob": c.combined_prob,
        "is_list": c.is_list,
        "show_analysis": c.show_analysis,
        "legs": [asdict(s) for s in c.legs],
    }


def deserialize_coupon(d: dict) -> Coupon:
    legs = [Selection(**leg) for leg in d.get("legs", [])]
    return Coupon(
        name=d["name"],
        subtitle=d["subtitle"],
        emoji=d["emoji"],
        legs=legs,
        combined_odds=d["combined_odds"],
        combined_prob=d["combined_prob"],
        is_list=d.get("is_list", False),
        show_analysis=d.get("show_analysis", False),
    )


def save_daily_cache(date_str: str):
    payload = {
        "date": date_str,
        "saved_at": datetime.now(TZ).isoformat(),
        "odds_api_mode": STATE.get("odds_api_mode", "inconnu"),
        "double_scan_status": STATE.get("double_scan_status", ""),
        "double_scan_stats": STATE.get("double_scan_stats", {}),
        "coupons": [serialize_coupon(c) for c in STATE.get("coupons", [])],
    }
    save_json(COUPONS_CACHE_DB, payload)
    print(f"💾 Cache journalier sauvegardé ({len(payload['coupons'])} coupons figés)")


def load_daily_cache(date_str: str) -> bool:
    data = load_json(COUPONS_CACHE_DB, {})
    if not data or data.get("date") != date_str:
        return False
    try:
        STATE["coupons"] = [deserialize_coupon(c) for c in data.get("coupons", [])]
        STATE["last_scan_date"] = date_str
        STATE["last_analysis"] = data.get("saved_at")
        if data.get("odds_api_mode"):
            STATE["odds_api_mode"] = data["odds_api_mode"]
        if data.get("double_scan_status"):
            STATE["double_scan_status"] = data["double_scan_status"]
        if data.get("double_scan_stats"):
            STATE["double_scan_stats"] = data["double_scan_stats"]
        return True
    except Exception as e:
        print(f"⚠️ Erreur lecture cache journalier : {e}")
        return False


def today_pretty() -> str:
    d = datetime.now(TZ)
    mois = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
    }
    return f"{d.day} {mois[d.month]} {d.year}"


def normalize_name(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " jk", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s)).strip()


def teams_match(name1: str, name2: str) -> bool:
    n1, n2 = normalize_name(name1), normalize_name(name2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    if len(n1) >= 4 and len(n2) >= 4 and (n1 in n2 or n2 in n1):
        return True
    return False


def is_reserve_team(name: str) -> bool:
    return any(kw in name.lower() for kw in RESERVE_KEYWORDS)


def is_low_stakes_competition(league_name: str) -> bool:
    if not league_name:
        return False
    low = league_name.lower()
    return any(kw in low for kw in LOW_STAKES_LEAGUE_KEYWORDS)


def compute_stakes_score(league_name: str, analysis_text: str = "") -> int:
    score = 0
    low_lg = (league_name or "").lower()
    low_an = (analysis_text or "").lower()
    if any(kw in low_lg for kw in HIGH_STAKES_LEAGUE_KEYWORDS):
        score += 20
    if any(ph in low_an for ph in HIGH_STAKES_ANALYSIS_PHRASES):
        score += 15
    return score


def has_pro_red_flags(analysis_text: str, league_name: str = "") -> Tuple[bool, str]:
    if is_low_stakes_competition(league_name):
        return True, f"compétition à faible enjeu ({league_name})"
    if not analysis_text:
        return False, ""
    low = analysis_text.lower()
    for phrase in PRO_TRAP_PHRASES:
        if phrase in low:
            return True, phrase
    return False, ""


def clean_analysis_text(text: str, max_len: int = 560) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > 100:
            return cut[: idx + 1]
    return cut.rsplit(" ", 1)[0] + "..."


def parse_dt_safe(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None


def format_kickoff(kickoff_str) -> str:
    if not kickoff_str:
        return "Heure non conf."
    dt = parse_dt_safe(kickoff_str)
    if dt:
        return dt.strftime("%d/%m %H:%M")
    return "Heure non conf."


def is_match_not_started(kickoff_str) -> bool:
    if not kickoff_str:
        return True
    dt = parse_dt_safe(kickoff_str)
    if not dt or (dt.hour == 0 and dt.minute == 0):
        return True
    return dt > (datetime.now(TZ) + timedelta(minutes=MATCH_START_BUFFER_MINUTES))


def is_match_today(kickoff_str) -> bool:
    if not kickoff_str:
        return False
    dt = parse_dt_safe(kickoff_str)
    if not dt:
        return False
    now = datetime.now(TZ)
    if dt.date() != now.date():
        return False
    if dt.hour == 0 and dt.minute == 0:
        return True
    return dt > (now + timedelta(minutes=MATCH_START_BUFFER_MINUTES))


def calibrated_odd(prob: float, real_odd: Optional[float]) -> float:
    if real_odd and float(real_odd) > 1.01:
        return round(float(real_odd), 2)
    return 0.0


def prob_bar(p: float) -> str:
    if p >= 0.75:
        return "🟢🟢🟢"
    if p >= 0.67:
        return "🟢🟢⚪"
    if p >= 0.61:
        return "🟢⚪⚪"
    return "⚪⚪⚪"


def split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            parts.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


async def safe_send(chat_id, text: str, reply_markup=None):
    for chunk in split_message(text):
        try:
            await bot.send_message(chat_id, chunk, parse_mode="HTML", reply_markup=reply_markup)
            await asyncio.sleep(0.25)
        except Exception as e:
            print(f"⚠️ Envoi Telegram ({chat_id}) : {e}")


def record_coupons(coupons: list[Coupon], date_str: str):
    tracker = STATE["tracker"]
    existing = {c["id"] for c in tracker["coupons"]}
    for c in coupons:
        cid = f"{date_str}-{c.name}"
        if cid in existing:
            continue
        legs = [
            {
                "event_id": s.event_id,
                "home": s.home,
                "away": s.away,
                "market": s.market,
                "pick_label": s.pick_label,
                "odds": s.odds,
                "kickoff": s.kickoff,
                "result": "pending",
                "score": "",
            }
            for s in c.legs
        ]
        tracker["coupons"].append(
            {
                "id": cid,
                "date": date_str,
                "name": c.name,
                "combined_odds": c.combined_odds,
                "legs": legs,
                "status": "pending",
            }
        )
    tracker["coupons"] = tracker["coupons"][-500:]
    save_json(LOCAL_DB, tracker)


# =====================================================================
# VÉRIFICATION PRÉ-MATCH 100 % ESPN + BZZOIRO (SANS API-FOOTBALL)
# =====================================================================
async def fetch_espn_prematch_context(
    client: httpx.AsyncClient,
    slug: str,
    raw_event_id: str,
    home_team: str,
    away_team: str,
    date_yyyymmdd: str,
) -> dict[str, Any]:
    """
    Vérifie avant coup d'envoi via ESPN (0 €) :
    - Stade réel & terrain neutre (competitions[0].neutralSite)
    - Classement officiel (standings)
    - Forme récente (W/D/L)
    - Blessures / absents par équipe (summary.injuries)
    """
    ctx: dict[str, Any] = {
        "venue_name": "Inconnu",
        "venue_city": "",
        "neutral_venue": False,
        "home_rank": "N/A",
        "away_rank": "N/A",
        "home_form": "",
        "away_form": "",
        "home_injuries": [],
        "away_injuries": [],
        "verified": False,
    }
    if not slug or slug == "all":
        return ctx

    home_id, away_id = None, None
    try:
        sb_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
        r = await client.get(sb_url, params={"dates": date_yyyymmdd, "limit": 200}, timeout=10.0)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                competitors = comp.get("competitors", [])
                hc = next((c for c in competitors if c.get("homeAway") == "home"), None)
                ac = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not hc or not ac:
                    continue
                h_n = hc.get("team", {}).get("displayName") or hc.get("team", {}).get("name") or ""
                a_n = ac.get("team", {}).get("displayName") or ac.get("team", {}).get("name") or ""
                if str(ev.get("id")) == str(raw_event_id) or (teams_match(home_team, h_n) and teams_match(away_team, a_n)):
                    raw_event_id = str(ev.get("id") or raw_event_id)
                    venue = comp.get("venue") or {}
                    ctx["neutral_venue"] = bool(comp.get("neutralSite", False))
                    ctx["venue_name"] = venue.get("fullName") or "Stade officiel"
                    ctx["venue_city"] = (venue.get("address") or {}).get("city") or ""
                    ctx["home_form"] = hc.get("form") or ""
                    ctx["away_form"] = ac.get("form") or ""
                    home_id = str(hc.get("team", {}).get("id") or "")
                    away_id = str(ac.get("team", {}).get("id") or "")
                    ctx["verified"] = True
                    break
    except Exception:
        pass

    if home_id or away_id:
        try:
            st_url = f"https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings"
            st_r = await client.get(st_url, timeout=10.0)
            if st_r.status_code == 200:
                for child in st_r.json().get("children", []):
                    for entry in child.get("standings", {}).get("entries", []):
                        tid = str(entry.get("team", {}).get("id") or "")
                        stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                        rank_val = stats.get("rank") or entry.get("note", {}).get("rank")
                        if tid == home_id and rank_val:
                            ctx["home_rank"] = str(rank_val)
                        elif tid == away_id and rank_val:
                            ctx["away_rank"] = str(rank_val)
        except Exception:
            pass

    if raw_event_id:
        try:
            sum_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary"
            sum_r = await client.get(sum_url, params={"event": raw_event_id}, timeout=10.0)
            if sum_r.status_code == 200:
                for team_inj in sum_r.json().get("injuries", []):
                    t_obj = team_inj.get("team", {})
                    tid = str(t_obj.get("id") or "")
                    tname = t_obj.get("displayName") or ""
                    parsed_list = []
                    for item in team_inj.get("injuries", []):
                        ath = (item.get("athlete") or {}).get("displayName") or ""
                        st = item.get("status") or ""
                        if ath:
                            parsed_list.append(f"{ath} ({st})" if st else ath)
                    if (home_id and tid == home_id) or teams_match(home_team, tname):
                        ctx["home_injuries"] = parsed_list
                    elif (away_id and tid == away_id) or teams_match(away_team, tname):
                        ctx["away_injuries"] = parsed_list
        except Exception:
            pass

    return ctx


async def enrich_pool_with_espn_context(pool: list[MatchData], ws_matches: list[WS_MatchData]):
    ws_lookup = {}
    for ws in ws_matches:
        ws_lookup[f"{normalize_name(ws.home_team)}__{normalize_name(ws.away_team)}"] = ws

    async with httpx.AsyncClient(timeout=12.0) as client:
        for md in pool[:25]:
            if md.espn_ctx.get("verified"):
                continue
            nk = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
            ws = ws_lookup.get(nk)
            slug = ws.espn_slug if ws and ws.espn_slug else ""
            raw_id = ws.espn_event_id if ws and ws.espn_event_id else ""
            if not slug:
                lg = (md.league or "").lower()
                if "champions" in lg:
                    slug = "uefa.champions"
                elif "europa" in lg and "conference" not in lg:
                    slug = "uefa.europa"
                elif "conference" in lg:
                    slug = "uefa.europa.conf"
                elif ("premier league" in lg and "england" in lg) or lg == "premier league":
                    slug = "eng.1"
                elif "laliga" in lg or "la liga" in lg:
                    slug = "esp.1"
                elif "serie a" in lg:
                    slug = "ita.1"
                elif "bundesliga" in lg:
                    slug = "ger.1"
                elif "ligue 1" in lg:
                    slug = "fra.1"
                elif "super lig" in lg or "süper lig" in lg:
                    slug = "tur.1"
                elif "eredivisie" in lg:
                    slug = "ned.1"
                elif "primeira" in lg:
                    slug = "por.1"

            if slug:
                dt = parse_dt_safe(md.kickoff) or datetime.now(TZ)
                ctx = await fetch_espn_prematch_context(
                    client, slug, raw_id, md.home, md.away, dt.strftime("%Y%m%d")
                )
                md.espn_ctx = ctx
                if ctx.get("neutral_venue"):
                    print(f"🏟️ Terrain neutre confirmé via ESPN : {md.home} vs {md.away} à {ctx['venue_name']} ({ctx['venue_city']})")


# =====================================================================
# VÉRIFICATION AUTOMATIQUE DES SCORES (BZZOIRO + SECOURS ESPN GRATUIT)
# =====================================================================
def evaluate_pick_outcome(
    pick_label: str,
    ft_h: int,
    ft_a: int,
    ht_h: Optional[int] = None,
    ht_a: Optional[int] = None,
) -> Optional[str]:
    lbl = (pick_label or "").lower().strip()
    total_goals = ft_h + ft_a

    if "mt/fin" in lbl or "v1/v1" in lbl or "v2/v2" in lbl:
        if ht_h is None or ht_a is None:
            return None
        ht_res = "v1" if ht_h > ht_a else ("x" if ht_h == ht_a else "v2")
        ft_res = "v1" if ft_h > ft_a else ("x" if ft_h == ft_a else "v2")
        actual = f"{ht_res}/{ft_res}"
        if "v1/v1" in lbl:
            return "won" if actual == "v1/v1" else "lost"
        if "v2/v2" in lbl:
            return "won" if actual == "v2/v2" else "lost"
        return None

    if "(1x)" in lbl or "1x" in lbl:
        return "won" if ft_h >= ft_a else "lost"
    if "(x2)" in lbl or "x2" in lbl:
        return "won" if ft_a >= ft_h else "lost"
    if "double chance 12" in lbl or "(12)" in lbl:
        return "won" if ft_h != ft_a else "lost"

    if "match nul" in lbl or "(x)" in lbl:
        return "won" if ft_h == ft_a else "lost"

    if lbl.endswith("(1)") or ("victoire" in lbl and "(1)" in lbl):
        return "won" if ft_h > ft_a else "lost"
    if lbl.endswith("(2)") or ("victoire" in lbl and "(2)" in lbl):
        return "won" if ft_a > ft_h else "lost"

    if "plus de 1.5" in lbl or "plus de 1,5" in lbl or "over 1.5" in lbl:
        return "won" if total_goals >= 2 else "lost"
    if "moins de 1.5" in lbl or "moins de 1,5" in lbl or "under 1.5" in lbl:
        return "won" if total_goals <= 1 else "lost"
    if "plus de 2.5" in lbl or "plus de 2,5" in lbl or "over 2.5" in lbl:
        return "won" if total_goals >= 3 else "lost"
    if "moins de 2.5" in lbl or "moins de 2,5" in lbl or "under 2.5" in lbl:
        return "won" if total_goals <= 2 else "lost"
    if "plus de 3.5" in lbl or "plus de 3,5" in lbl or "over 3.5" in lbl:
        return "won" if total_goals >= 4 else "lost"
    if "moins de 3.5" in lbl or "moins de 3,5" in lbl or "under 3.5" in lbl:
        return "won" if total_goals <= 3 else "lost"

    if "btts oui" in lbl or "2 équipes marquent" in lbl:
        return "won" if (ft_h > 0 and ft_a > 0) else "lost"
    if "btts non" in lbl:
        return "won" if (ft_h == 0 or ft_a == 0) else "lost"

    return None


def _extract_bzzoiro_score(ev: dict) -> Optional[dict]:
    h = ev.get("home_team") or ev.get("home") or (ev.get("home_team_obj") or {}).get("name") or ""
    a = ev.get("away_team") or ev.get("away") or (ev.get("away_team_obj") or {}).get("name") or ""
    if isinstance(h, dict):
        h = h.get("name", "")
    if isinstance(a, dict):
        a = a.get("name", "")
    if not h or not a:
        return None

    raw_st = str(ev.get("status") or ev.get("event_status") or "").lower()
    ft_h = ev.get("home_score") if ev.get("home_score") is not None else ev.get("home_goals")
    ft_a = ev.get("away_score") if ev.get("away_score") is not None else ev.get("away_goals")
    ht_h = ev.get("home_score_ht") if ev.get("home_score_ht") is not None else ev.get("ht_home_score")
    ht_a = ev.get("away_score_ht") if ev.get("away_score_ht") is not None else ev.get("ht_away_score")

    if any(k in raw_st for k in ("postponed", "cancel", "abandon", "pst", "canc")):
        return {"home": h, "away": a, "status": "VOID", "ft_h": None, "ft_a": None, "ht_h": None, "ht_a": None}

    if any(k in raw_st for k in ("fin", "ft", "ended", "complet", "full")) and ft_h is not None and ft_a is not None:
        return {
            "home": h,
            "away": a,
            "status": "FT",
            "ft_h": int(ft_h),
            "ft_a": int(ft_a),
            "ht_h": int(ht_h) if ht_h is not None else None,
            "ht_a": int(ht_a) if ht_a is not None else None,
        }
    return None


async def fetch_bzzoiro_finished_scores(date_iso: str) -> list[dict]:
    if not BZZOIRO_API_KEY:
        return []
    results = []
    headers = {"Authorization": f"Token {BZZOIRO_API_KEY}"}
    urls = [
        ("https://sports.bzzoiro.com/api/events/", {"date": date_iso}),
        ("https://sports.bzzoiro.com/api/events/", {"date_from": date_iso, "date_to": date_iso}),
    ]
    async with httpx.AsyncClient() as client:
        for url, params in urls:
            try:
                r = await client.get(url, headers=headers, params=params, timeout=15.0)
                if r.status_code != 200:
                    continue
                payload = r.json()
                items = payload.get("results", payload) if isinstance(payload, dict) else payload
                if not isinstance(items, list):
                    continue
                for ev in items:
                    parsed = _extract_bzzoiro_score(ev)
                    if parsed:
                        results.append(parsed)
                if results:
                    break
            except Exception as e:
                print(f"⚠️ Scores Bzzoiro ({date_iso}) : {e}")
    return results


async def fetch_espn_finished_scores(date_iso: str) -> list[dict]:
    date_compact = date_iso.replace("-", "")
    results = []
    async with httpx.AsyncClient() as client:
        for slug in ESPN_LEAGUE_SLUGS:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
                r = await client.get(url, params={"dates": date_compact}, timeout=10.0)
                if r.status_code != 200:
                    continue
                for ev in r.json().get("events", []):
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    st_obj = comp.get("status", {}).get("type", {})
                    st_name = (st_obj.get("name") or "").upper()
                    completed = bool(st_obj.get("completed"))

                    competitors = comp.get("competitors", [])
                    home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
                    away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
                    if not home_c or not away_c:
                        continue

                    h_name = home_c.get("team", {}).get("displayName") or home_c.get("team", {}).get("name") or ""
                    a_name = away_c.get("team", {}).get("displayName") or away_c.get("team", {}).get("name") or ""
                    if not h_name or not a_name:
                        continue

                    if "POSTPONED" in st_name or "CANCELED" in st_name:
                        results.append({"home": h_name, "away": a_name, "status": "VOID", "ft_h": None, "ft_a": None, "ht_h": None, "ht_a": None})
                    elif completed:
                        try:
                            ft_h = int(float(home_c.get("score", 0)))
                            ft_a = int(float(away_c.get("score", 0)))
                            results.append({"home": h_name, "away": a_name, "status": "FT", "ft_h": ft_h, "ft_a": ft_a, "ht_h": None, "ht_a": None})
                        except Exception:
                            pass
            except Exception:
                continue
    return results


async def verify_pending_scores(notify: bool = True) -> dict:
    async with STATE["verify_lock"]:
        coupons = STATE["tracker"].get("coupons", [])
        pending_coupons = [c for c in coupons if c.get("status") == "pending"]
        if not pending_coupons:
            STATE["last_score_check"] = datetime.now(TZ).isoformat()
            return {"checked": 0, "updated_legs": 0, "settled_coupons": 0}

        dates_to_fetch = set()
        for c in pending_coupons:
            d = c.get("date")
            if d:
                dates_to_fetch.add(d)
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    dates_to_fetch.add((dt + timedelta(days=1)).strftime("%Y-%m-%d"))
                except Exception:
                    pass

        all_results = []
        for d in sorted(dates_to_fetch):
            bzz_res = await fetch_bzzoiro_finished_scores(d)
            espn_res = await fetch_espn_finished_scores(d)
            all_results.extend(bzz_res)
            all_results.extend(espn_res)
            await asyncio.sleep(0.3)

        if not all_results:
            STATE["last_score_check"] = datetime.now(TZ).isoformat()
            return {"checked": len(pending_coupons), "updated_legs": 0, "settled_coupons": 0}

        updated_legs = 0
        newly_settled = []

        for c in pending_coupons:
            legs = c.get("legs", [])
            modified = False

            for leg in legs:
                if leg.get("result") != "pending":
                    continue
                lh, la = leg.get("home", ""), leg.get("away", "")
                matched_res = None
                for item in all_results:
                    if teams_match(lh, item["home"]) and teams_match(la, item["away"]):
                        matched_res = item
                        if item.get("ht_h") is not None:
                            break

                if not matched_res:
                    continue

                if matched_res["status"] == "FT":
                    ft_h, ft_a = matched_res["ft_h"], matched_res["ft_a"]
                    ht_h, ht_a = matched_res.get("ht_h"), matched_res.get("ht_a")
                    outcome = evaluate_pick_outcome(leg.get("pick_label", ""), ft_h, ft_a, ht_h, ht_a)
                    if outcome:
                        leg["result"] = outcome
                        score_txt = f"{ft_h}-{ft_a}"
                        if ht_h is not None and ht_a is not None and "mt/fin" in leg.get("pick_label", "").lower():
                            score_txt += f" (MT {ht_h}-{ht_a})"
                        leg["score"] = score_txt
                        updated_legs += 1
                        modified = True

                elif matched_res["status"] == "VOID":
                    leg["result"] = "void"
                    leg["odds"] = 1.0
                    leg["score"] = "Reporté/Annulé"
                    updated_legs += 1
                    modified = True

            if modified:
                results = [l.get("result") for l in legs]
                if "lost" in results:
                    c["status"] = "lost"
                    c["settled_at"] = datetime.now(TZ).isoformat()
                    newly_settled.append(c)
                elif all(r in ("won", "void") for r in results):
                    eff_odds = round(math.prod(float(l.get("odds", 1.0)) for l in legs), 2)
                    c["combined_odds"] = eff_odds
                    c["status"] = "void" if all(r == "void" for r in results) else "won"
                    c["settled_at"] = datetime.now(TZ).isoformat()
                    newly_settled.append(c)

        if updated_legs > 0 or newly_settled:
            save_json(LOCAL_DB, STATE["tracker"])

        STATE["last_score_check"] = datetime.now(TZ).isoformat()

        if notify and newly_settled:
            for c in newly_settled:
                await notify_settled_coupon(c)

        return {"checked": len(pending_coupons), "updated_legs": updated_legs, "settled_coupons": len(newly_settled)}


async def notify_settled_coupon(c: dict):
    status = c.get("status")
    if status not in ("won", "lost"):
        return
    is_win = status == "won"
    header = "✅ <b>TICKET VIP VALIDÉ !</b>" if is_win else "❌ <b>BILAN TICKET</b>"
    lines = [
        header,
        f"🎫 <b>{c.get('name')}</b> ({c.get('date')}) • Cote <b>{c.get('combined_odds')}</b>",
        "",
    ]
    for l in c.get("legs", []):
        r = l.get("result")
        ico = "✅" if r == "won" else ("❌" if r == "lost" else "⚪")
        sc = f" <b>[{l.get('score')}]</b>" if l.get("score") else ""
        lines.append(f"{ico} {l.get('home')} vs {l.get('away')} — <i>{l.get('pick_label')}</i>{sc}")

    msg = "\n".join(lines)
    if is_win and c.get("name") == "Odds +3 AUTH":
        for cid in TARGETS:
            await safe_send(cid, msg)
    elif ADMIN_ID:
        await safe_send(ADMIN_ID, msg)


def compute_roi_stats(coupons: list) -> dict:
    won = [c for c in coupons if c.get("status") == "won"]
    lost = [c for c in coupons if c.get("status") == "lost"]
    pending = [c for c in coupons if c.get("status") == "pending"]
    settled = len(won) + len(lost)
    win_rate = (len(won) / settled * 100.0) if settled > 0 else 0.0
    profit = sum(float(c.get("combined_odds", 0)) - 1.0 for c in won) - float(len(lost))
    roi = (profit / settled * 100.0) if settled > 0 else 0.0
    avg_win_odd = (sum(float(c.get("combined_odds", 0)) for c in won) / len(won)) if won else 0.0
    return {
        "won": len(won),
        "lost": len(lost),
        "pending": len(pending),
        "settled": settled,
        "win_rate": win_rate,
        "profit": profit,
        "roi": roi,
        "avg_win_odd": avg_win_odd,
    }


def format_bilan_report() -> str:
    all_c = STATE["tracker"].get("coupons", [])
    vip_c = [c for c in all_c if c.get("name") == "Odds +3 AUTH"]

    s_vip = compute_roi_stats(vip_c)
    s_all = compute_roi_stats(all_c)

    lines = [
        "<b>📊 PERFORMANCE & BILAN ROI</b>",
        "<i>Mise fixe : 1.00 unité / ticket</i>",
        "",
        "<b>🛡️ VIP — ODDS +3 AUTH</b>",
        f"• Terminés : <b>{s_vip['settled']}</b> (✅ {s_vip['won']} / ❌ {s_vip['lost']} / ⏳ {s_vip['pending']})",
        f"• Taux de réussite : <b>{s_vip['win_rate']:.1f}%</b>",
        f"• Cote moyenne gagnante : <b>{s_vip['avg_win_odd']:.2f}</b>",
        f"• Bénéfice net : <b>{s_vip['profit']:+.2f} u</b>",
        f"• ROI Réel : <b>{s_vip['roi']:+.1f}%</b>",
        "",
        "<b>📈 GLOBAL (TOUS COUPONS)</b>",
        f"• Terminés : <b>{s_all['settled']}</b> (✅ {s_all['won']} / ❌ {s_all['lost']} / ⏳ {s_all['pending']})",
        f"• Réussite : <b>{s_all['win_rate']:.1f}%</b> • Profit : <b>{s_all['profit']:+.2f} u</b> (ROI <b>{s_all['roi']:+.1f}%</b>)",
    ]

    recent = list(reversed(all_c[-5:]))
    if recent:
        lines.append("\n<b>🕒 5 DERNIERS TICKETS :</b>")
        for c in recent:
            st = c.get("status")
            badge = "✅" if st == "won" else ("❌" if st == "lost" else ("⚪" if st == "void" else "⏳"))
            lines.append(f"\n{badge} <b>{c.get('name')}</b> ({c.get('date')}) • Cote <b>{c.get('combined_odds')}</b>")
            for l in c.get("legs", []):
                lr = l.get("result")
                lico = "✅" if lr == "won" else ("❌" if lr == "lost" else ("⚪" if lr == "void" else "⏳"))
                sc = f" [{l.get('score')}]" if l.get("score") else ""
                lines.append(f"  {lico} {l.get('home')} - {l.get('away')} ({l.get('pick_label')}){sc}")

    return "\n".join(lines)


def prescore_match(md: MatchData) -> int:
    if is_low_stakes_competition(md.league):
        return -999
    conf_score = int(md.bzz_confidence or 0)
    real_bonus = 40 if (md.real_odds.get("home") or md.real_odds.get("away")) else (20 if md.real_odds else 0)
    bzz_bonus = 15 if md.bzz_markets else 0
    dixon_bonus = 25 if md.dixon_pred else 0
    h2h_bonus = 10 if md.has_h2h else 0
    stakes_bonus = compute_stakes_score(md.league, md.analysis)
    return conf_score + real_bonus + bzz_bonus + dixon_bonus + h2h_bonus + stakes_bonus


def _get_match_id(item: dict) -> str:
    m = item["match"]
    return str(m.event_id if hasattr(m, "event_id") else m.match_id)


def _ensure_dixon_pred(md: MatchData):
    is_neutral = bool(md.espn_ctx.get("neutral_venue", False))
    if md.dixon_pred and md.dixon_pred.get("_neutral_applied") == is_neutral:
        return

    if STATE.get("dixon_model") and not is_neutral:
        try:
            pred = STATE["dixon_model"].predict_all(md.home, md.away)
            if pred:
                pred["_neutral_applied"] = False
                md.dixon_pred = pred
                return
        except Exception:
            pass

    if md.real_odds.get("home") and md.real_odds.get("away"):
        try:
            h_o = float(md.real_odds["home"])
            a_o = float(md.real_odds["away"])
            d_o = float(md.real_odds["draw"]) if md.real_odds.get("draw") else None
            o25 = float(md.real_odds["over_2.5"]) if md.real_odds.get("over_2.5") else None
            u25 = float(md.real_odds["under_2.5"]) if md.real_odds.get("under_2.5") else None

            sim = dixon_ws.simulate(
                WS_MatchData(md.event_id, md.league, md.home, md.away, h_o, d_o, a_o, o25, u25),
                neutral_venue=is_neutral,
            )
            if sim:
                md.dixon_pred = {
                    "_neutral_applied": is_neutral,
                    "lambda_home": sim.lambda_home,
                    "lambda_away": sim.lambda_away,
                    "p_home": round(sim.proba_home / 100.0, 4),
                    "p_draw": round(sim.proba_draw / 100.0, 4),
                    "p_away": round(sim.proba_away / 100.0, 4),
                    "over_1.5": round(sim.proba_over_1_5 / 100.0, 4),
                    "under_1.5": round(max(0.01, 1.0 - sim.proba_over_1_5 / 100.0), 4),
                    "over_2.5": round(sim.proba_over_2_5 / 100.0, 4),
                    "under_2.5": round(max(0.01, 1.0 - sim.proba_over_2_5 / 100.0), 4),
                    "btts_yes": round(sim.proba_btts / 100.0, 4),
                    "btts_no": round(max(0.01, 1.0 - sim.proba_btts / 100.0), 4),
                }
        except Exception:
            pass


# =====================================================================
# CALIBRATION BAYÉSIENNE AVEC LE MARCHÉ (ANTI-SURCONFIANCE)
# =====================================================================
def get_devigged_market_probability(md: MatchData, market_key: str) -> Optional[float]:
    ro = md.real_odds or {}
    try:
        if market_key in ("victoire_home", "match_nul", "victoire_away"):
            h, d, a = ro.get("home"), ro.get("draw"), ro.get("away")
            if h and d and a and float(h) > 1.01 and float(d) > 1.01 and float(a) > 1.01:
                ih, id_, ia = 1.0 / float(h), 1.0 / float(d), 1.0 / float(a)
                ov = ih + id_ + ia
                mapping = {"victoire_home": ih / ov, "match_nul": id_ / ov, "victoire_away": ia / ov}
                return round(mapping[market_key], 4)

        if market_key == "double_chance_1X":
            p_h = get_devigged_market_probability(md, "victoire_home")
            p_d = get_devigged_market_probability(md, "match_nul")
            if p_h is not None and p_d is not None:
                return round(p_h + p_d, 4)

        if market_key == "double_chance_X2":
            p_a = get_devigged_market_probability(md, "victoire_away")
            p_d = get_devigged_market_probability(md, "match_nul")
            if p_a is not None and p_d is not None:
                return round(p_a + p_d, 4)

        if market_key == "double_chance_12":
            p_h = get_devigged_market_probability(md, "victoire_home")
            p_a = get_devigged_market_probability(md, "victoire_away")
            if p_h is not None and p_a is not None:
                return round(p_h + p_a, 4)

        if market_key in ("over_2.5", "under_2.5"):
            o25, u25 = ro.get("over_2.5"), ro.get("under_2.5")
            if o25 and u25 and float(o25) > 1.01 and float(u25) > 1.01:
                io, iu = 1.0 / float(o25), 1.0 / float(u25)
                ov = io + iu
                return round((io / ov) if market_key == "over_2.5" else (iu / ov), 4)

        if market_key in ("over_1.5", "under_1.5"):
            o15, u15 = ro.get("over_1.5"), ro.get("under_1.5")
            if o15 and u15 and float(o15) > 1.01 and float(u15) > 1.01:
                io, iu = 1.0 / float(o15), 1.0 / float(u15)
                ov = io + iu
                return round((io / ov) if market_key == "over_1.5" else (iu / ov), 4)
            elif o15 and float(o15) > 1.01 and market_key == "over_1.5":
                return round(min(0.90, (1.0 / float(o15)) * 0.94), 4)

        if market_key in ("btts_yes", "btts_no"):
            by, bn = ro.get("btts_yes"), ro.get("btts_no")
            if by and bn and float(by) > 1.01 and float(bn) > 1.01:
                iby, ibn = 1.0 / float(by), 1.0 / float(bn)
                ov = iby + ibn
                return round((iby / ov) if market_key == "btts_yes" else (ibn / ov), 4)
    except Exception:
        pass
    return None


def compute_probability(md: MatchData, market_key: str) -> Tuple[float, float, float, float, str]:
    _ensure_dixon_pred(md)
    dixon_pred = md.dixon_pred if md.dixon_pred else {}
    ml_probas = predict_ml(dixon_pred, md.bzz_confidence) if predict_ml else None
    fused = fuse_probas(dixon_pred, ml_probas) if fuse_probas and ml_probas else dixon_pred
    gem = dix = bzz = 0.0
    gem_avail = dix_avail = bzz_avail = False

    if md.gemini_markets:
        if md.gemini_markets.get(market_key) is not None:
            try:
                gem = float(md.gemini_markets[market_key]) / 100.0
                gem_avail = True
            except Exception:
                pass
        elif market_key == "double_chance_1X" and md.gemini_markets.get("victoire_home") is not None and md.gemini_markets.get("match_nul") is not None:
            try:
                gem = (float(md.gemini_markets["victoire_home"]) + float(md.gemini_markets["match_nul"])) / 100.0
                gem_avail = True
            except Exception:
                pass
        elif market_key == "double_chance_X2" and md.gemini_markets.get("victoire_away") is not None and md.gemini_markets.get("match_nul") is not None:
            try:
                gem = (float(md.gemini_markets["victoire_away"]) + float(md.gemini_markets["match_nul"])) / 100.0
                gem_avail = True
            except Exception:
                pass
        elif market_key == "double_chance_12" and md.gemini_markets.get("victoire_home") is not None and md.gemini_markets.get("victoire_away") is not None:
            try:
                gem = (float(md.gemini_markets["victoire_home"]) + float(md.gemini_markets["victoire_away"])) / 100.0
                gem_avail = True
            except Exception:
                pass

    if fused:
        if market_key == "double_chance_1X" and fused.get("p_home") is not None and fused.get("p_draw") is not None:
            try:
                dix = float(fused["p_home"]) + float(fused["p_draw"])
                dix_avail = True
            except Exception:
                pass
        elif market_key == "double_chance_X2" and fused.get("p_away") is not None and fused.get("p_draw") is not None:
            try:
                dix = float(fused["p_away"]) + float(fused["p_draw"])
                dix_avail = True
            except Exception:
                pass
        elif market_key == "double_chance_12" and fused.get("p_home") is not None and fused.get("p_away") is not None:
            try:
                dix = float(fused["p_home"]) + float(fused["p_away"])
                dix_avail = True
            except Exception:
                pass
        else:
            dk = {
                "victoire_home": "p_home",
                "match_nul": "p_draw",
                "victoire_away": "p_away",
                "over_2.5": "over_2.5",
                "under_2.5": "under_2.5",
                "over_3.5": "over_3.5",
                "over_1.5": "over_1.5",
                "under_1.5": "under_1.5",
                "btts_yes": "btts_yes",
                "btts_no": "btts_no",
            }.get(market_key)
            if dk and fused.get(dk) is not None:
                try:
                    dix = float(fused.get(dk))
                    dix_avail = True
                except Exception:
                    pass

    if md.bzz_markets and md.bzz_markets.get(market_key) is not None:
        try:
            bzz = float(md.bzz_markets[market_key]) / 100.0
            bzz_avail = True
        except Exception:
            pass

    total_w = 0.0
    weighted_sum = 0.0
    if dix_avail:
        total_w += WEIGHT_ALGO
        weighted_sum += dix * WEIGHT_ALGO
    if gem_avail:
        total_w += WEIGHT_GEMINI
        weighted_sum += gem * WEIGHT_GEMINI
    if bzz_avail:
        total_w += WEIGHT_BZZOIRO
        weighted_sum += bzz * WEIGHT_BZZOIRO
    if total_w <= 0:
        return 0.0, gem, dix, bzz, ""

    raw_model_prob = min(0.95, weighted_sum / total_w)

    market_prob = get_devigged_market_probability(md, market_key)
    if market_prob is not None and market_prob > 0.05:
        w_mkt = max(0.0, min(0.85, MARKET_SHRINKAGE_WEIGHT))
        final = ((1.0 - w_mkt) * raw_model_prob) + (w_mkt * market_prob)
        if abs(raw_model_prob - market_prob) > 0.22:
            return final, gem, dix, bzz, "⚠️"
    else:
        final = raw_model_prob

    sources = [x for x in (gem, dix, bzz) if x > 0]
    emoji = ""
    if len(sources) >= 2:
        delta = max(sources) - min(sources)
        emoji = (
            "🤝🤝"
            if len(sources) == 3 and delta <= 0.12
            else "🤝"
            if delta <= 0.15
            else "⚠️"
            if delta >= 0.20
            else ""
        )
    return min(0.93, final), gem, dix, bzz, emoji


def passes_value_filter(proba: float, odd: float, is_real: bool, consensus_emoji: str = "", market_prob: Optional[float] = None) -> bool:
    if proba <= 0 or odd <= 0:
        return False
    if consensus_emoji == "⚠️":
        STATE["rejections"]["value"] += 1
        return False

    ev = proba * odd
    if ev > MAX_SINGLE_EV:
        STATE["rejections"]["value"] += 1
        return False

    if market_prob is not None and (proba - market_prob) > MAX_SINGLE_EDGE:
        STATE["rejections"]["value"] += 1
        return False

    if is_real:
        ok = ev >= (1.0 + MIN_EDGE)
        if not ok:
            STATE["rejections"]["value"] += 1
        return ok

    ok = ev >= 0.995
    if not ok:
        STATE["rejections"]["value"] += 1
    return ok


def coupon_passes_ev(odds: float, prob: float, has_real_odds: bool = False) -> bool:
    if odds <= 0 or prob <= 0:
        return False
    ev = odds * prob
    min_ev = MIN_REAL_COUPON_EV if has_real_odds else MIN_COUPON_EV
    ok = min_ev <= ev <= MAX_COUPON_EV
    if not ok:
        STATE["rejections"]["coupon_ev"] += 1
    return ok


def passes_auth_proba_filter(proba: float) -> bool:
    if proba <= 0:
        return False
    ok = AUTH_PROBA_MIN <= proba <= AUTH_PROBA_MAX
    if not ok:
        STATE["rejections"]["auth_proba"] += 1
    return ok


# =====================================================================
# RÉCUPÉRATION DES COTES (ESPN GRATUIT + BZZOIRO + THE ODDS API)
# =====================================================================
def _american_to_decimal(ml) -> Optional[float]:
    if ml is None:
        return None
    try:
        s = str(ml).strip().upper()
        if s in ("EVEN", "EV", "PK"):
            return 2.00
        val = float(s.replace("+", ""))
        if val == 0:
            return None
        dec = (1.0 + (val / 100.0)) if val > 0 else (1.0 + (100.0 / abs(val)))
        return round(dec, 2) if dec > 1.01 else None
    except Exception:
        return None


def _parse_espn_odds_list(odds_list: list) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    h_odd = d_odd = a_odd = o25 = u25 = o15 = u15 = None
    for od in odds_list or []:
        if not isinstance(od, dict):
            continue
        h_ml = (od.get("homeTeamOdds") or {}).get("moneyLine")
        a_ml = (od.get("awayTeamOdds") or {}).get("moneyLine")
        d_ml = (od.get("drawOdds") or {}).get("moneyLine")
        if h_ml is not None and not h_odd:
            h_odd = _american_to_decimal(h_ml)
        if a_ml is not None and not a_odd:
            a_odd = _american_to_decimal(a_ml)
        if d_ml is not None and not d_odd:
            d_odd = _american_to_decimal(d_ml)

        ou_line = od.get("overUnder")
        if ou_line is not None:
            try:
                line_val = float(ou_line)
                if abs(line_val - 2.5) < 0.01:
                    ov_dec = _american_to_decimal(od.get("overOdds"))
                    un_dec = _american_to_decimal(od.get("underOdds"))
                    if ov_dec and not o25:
                        o25 = ov_dec
                    if un_dec and not u25:
                        u25 = un_dec
                elif abs(line_val - 1.5) < 0.01:
                    ov_dec = _american_to_decimal(od.get("overOdds"))
                    un_dec = _american_to_decimal(od.get("underOdds"))
                    if ov_dec and not o15:
                        o15 = ov_dec
                    if un_dec and not u15:
                        u15 = un_dec
            except Exception:
                pass

    return h_odd, d_odd, a_odd, o25, u25, o15, u15


ESPN_ODDS_SLUGS = [
    "all",
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
    "eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1",
    "conmebol.libertadores", "usa.1", "mex.1", "bra.1", "arg.1",
]


async def _fetch_espn_slug_date(client: httpx.AsyncClient, slug: str, d_str: str) -> list[dict]:
    items = []
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
        r = await client.get(url, params={"dates": d_str, "limit": 200})
        if r.status_code != 200:
            return []
        data = r.json()
        default_league = (data.get("leagues") or [{}])[0].get("name") or slug
        for ev in data.get("events", []):
            comps = ev.get("competitions", [])
            if not comps:
                continue
            comp = comps[0]
            st_type = comp.get("status", {}).get("type", {})
            if st_type.get("completed") or st_type.get("state") in ("in", "post"):
                continue
            competitors = comp.get("competitors", [])
            home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home_c or not away_c:
                continue
            home_name = home_c.get("team", {}).get("displayName") or home_c.get("team", {}).get("name") or ""
            away_name = away_c.get("team", {}).get("displayName") or away_c.get("team", {}).get("name") or ""
            if not home_name or not away_name:
                continue
            lg_name = (ev.get("league") or {}).get("name") or default_league
            items.append(
                {
                    "raw_id": str(ev.get("id", "")),
                    "slug": slug,
                    "league": lg_name,
                    "home": home_name,
                    "away": away_name,
                    "date": ev.get("date", ""),
                    "odds": comp.get("odds") or [],
                }
            )
    except Exception:
        pass
    return items


async def _fetch_espn_event_summary_odds(client: httpx.AsyncClient, slug: str, raw_id: str) -> list:
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary"
        r = await client.get(url, params={"event": raw_id})
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("pickcenter") or data.get("odds") or []
    except Exception:
        return []


async def fetch_espn_prematch_odds(days_ahead: int = 1) -> list[WS_MatchData]:
    now = datetime.now(TZ)
    date_strings = [(now + timedelta(days=d)).strftime("%Y%m%d") for d in range(max(1, days_ahead + 1))]
    ws_map: dict[str, WS_MatchData] = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
        tasks = [_fetch_espn_slug_date(client, slug, d_str) for slug in ESPN_ODDS_SLUGS for d_str in date_strings]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

        candidates_without_odds = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                ev_id = f"espn_{item['raw_id']}"
                if ev_id in ws_map:
                    if item["slug"] != "all" and ws_map[ev_id].espn_slug == "all":
                        ws_map[ev_id].espn_slug = item["slug"]
                    continue
                h_odd, d_odd, a_odd, o25, u25, o15, u15 = _parse_espn_odds_list(item["odds"])
                if h_odd and a_odd:
                    ws_map[ev_id] = WS_MatchData(
                        match_id=ev_id,
                        league=item["league"],
                        home_team=item["home"],
                        away_team=item["away"],
                        home_odds=h_odd,
                        draw_odds=d_odd,
                        away_odds=a_odd,
                        over_2_5=o25,
                        under_2_5=u25,
                        over_1_5=o15,
                        under_1_5=u15,
                        commence_time=item["date"],
                        espn_slug=item["slug"],
                        espn_event_id=item["raw_id"],
                    )
                elif item["raw_id"] and item["slug"] != "all":
                    candidates_without_odds.append(item)

        if candidates_without_odds:
            seen_raw = set()
            unique_missing = []
            for it in candidates_without_odds:
                if it["raw_id"] not in seen_raw and f"espn_{it['raw_id']}" not in ws_map:
                    seen_raw.add(it["raw_id"])
                    unique_missing.append(it)
            summary_tasks = [_fetch_espn_event_summary_odds(client, it["slug"], it["raw_id"]) for it in unique_missing[:35]]
            summary_results = await asyncio.gather(*summary_tasks, return_exceptions=True)
            for it, s_odds in zip(unique_missing[:35], summary_results):
                if not isinstance(s_odds, list) or not s_odds:
                    continue
                h_odd, d_odd, a_odd, o25, u25, o15, u15 = _parse_espn_odds_list(s_odds)
                if h_odd and a_odd:
                    ev_id = f"espn_{it['raw_id']}"
                    ws_map[ev_id] = WS_MatchData(
                        match_id=ev_id,
                        league=it["league"],
                        home_team=it["home"],
                        away_team=it["away"],
                        home_odds=h_odd,
                        draw_odds=d_odd,
                        away_odds=a_odd,
                        over_2_5=o25,
                        under_2_5=u25,
                        over_1_5=o15,
                        under_1_5=u15,
                        commence_time=it["date"],
                        espn_slug=it["slug"],
                        espn_event_id=it["raw_id"],
                    )

    res = list(ws_map.values())
    print(f"🆓 ESPN Vraies Cotes Gratuites : {len(res)} matchs récupérés (0 €)")
    return res


async def fetch_bzzoiro_free_odds(days_ahead: int = 1) -> list[WS_MatchData]:
    if not BZZOIRO_API_KEY:
        return []
    now = datetime.now(TZ)
    d_from = now.strftime("%Y-%m-%d")
    d_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"Token {BZZOIRO_API_KEY}"}
    ws_map: dict[str, WS_MatchData] = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for endpoint in ("https://sports.bzzoiro.com/api/events/", "https://sports.bzzoiro.com/api/predictions/"):
            try:
                r = await client.get(endpoint, headers=headers, params={"date_from": d_from, "date_to": d_to, "limit": 250})
                if r.status_code != 200:
                    continue
                payload = r.json()
                items = payload.get("results", payload) if isinstance(payload, dict) else payload
                if not isinstance(items, list):
                    continue
                for raw in items:
                    ev = raw.get("event") if isinstance(raw.get("event"), dict) else raw
                    h = ev.get("home_team") or raw.get("home_team") or ""
                    a = ev.get("away_team") or raw.get("away_team") or ""
                    if isinstance(h, dict):
                        h = h.get("name", "")
                    if isinstance(a, dict):
                        a = a.get("name", "")
                    if not h or not a:
                        continue
                    lg = ev.get("league") or raw.get("league") or ""
                    if isinstance(lg, dict):
                        lg = lg.get("name", "")

                    def _f(v):
                        try:
                            fv = float(v)
                            return round(fv, 2) if fv > 1.01 else None
                        except Exception:
                            return None

                    h_odd = _f(ev.get("odds_home") or raw.get("odds_home") or ev.get("home_odds"))
                    d_odd = _f(ev.get("odds_draw") or raw.get("odds_draw") or ev.get("draw_odds"))
                    a_odd = _f(ev.get("odds_away") or raw.get("odds_away") or ev.get("away_odds"))
                    o25 = _f(ev.get("odds_over_25") or raw.get("odds_over_25") or ev.get("over_2_5_odds"))
                    u25 = _f(ev.get("odds_under_25") or raw.get("odds_under_25") or ev.get("under_2_5_odds"))
                    o15 = _f(ev.get("odds_over_15") or raw.get("odds_over_15") or ev.get("over_1_5_odds"))
                    u15 = _f(ev.get("odds_under_15") or raw.get("odds_under_15") or ev.get("under_1_5_odds"))
                    b_yes = _f(ev.get("odds_btts_yes") or raw.get("odds_btts_yes"))
                    b_no = _f(ev.get("odds_btts_no") or raw.get("odds_btts_no"))

                    if h_odd and a_odd:
                        nk = f"{normalize_name(h)}__{normalize_name(a)}"
                        if nk not in ws_map:
                            ws_map[nk] = WS_MatchData(
                                match_id=str(ev.get("id") or raw.get("id") or nk),
                                league=str(lg),
                                home_team=str(h),
                                away_team=str(a),
                                home_odds=h_odd,
                                draw_odds=d_odd,
                                away_odds=a_odd,
                                over_2_5=o25,
                                under_2_5=u25,
                                over_1_5=o15,
                                under_1_5=u15,
                                btts_yes=b_yes,
                                btts_no=b_no,
                                commence_time=str(ev.get("event_date") or ev.get("commence_time") or ""),
                            )
            except Exception:
                continue

    res = list(ws_map.values())
    print(f"🆓 Bzzoiro Vraies Cotes Gratuites : {len(res)} matchs récupérés (0 €)")
    return res


async def fetch_rapidodds_odds() -> Tuple[list[WS_MatchData], str]:
    ws_by_key: dict[str, WS_MatchData] = {}

    try:
        espn_ws = await fetch_espn_prematch_odds(days_ahead=1)
        for item in espn_ws:
            nk = f"{normalize_name(item.home_team)}__{normalize_name(item.away_team)}"
            ws_by_key[nk] = item
    except Exception as e:
        print(f"⚠️ Erreur de cote ESPN : {e}")

    try:
        bzz_ws = await fetch_bzzoiro_free_odds(days_ahead=1)
        for item in bzz_ws:
            nk = f"{normalize_name(item.home_team)}__{normalize_name(item.away_team)}"
            existing = ws_by_key.get(nk)
            if existing:
                if not existing.draw_odds and item.draw_odds:
                    existing.draw_odds = item.draw_odds
                if not existing.over_2_5 and item.over_2_5:
                    existing.over_2_5 = item.over_2_5
                if not existing.under_2_5 and item.under_2_5:
                    existing.under_2_5 = item.under_2_5
                if not existing.over_1_5 and item.over_1_5:
                    existing.over_1_5 = item.over_1_5
                if not existing.under_1_5 and item.under_1_5:
                    existing.under_1_5 = item.under_1_5
                if not existing.btts_yes and item.btts_yes:
                    existing.btts_yes = item.btts_yes
                if not existing.btts_no and item.btts_no:
                    existing.btts_no = item.btts_no
            else:
                ws_by_key[nk] = item
    except Exception as e:
        print(f"⚠️ Bzzoiro Odds erreur : {e}")

    if THE_ODDS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    "https://api.the-odds-api.com/v4/sports/upcoming/odds",
                    params={
                        "apiKey": THE_ODDS_API_KEY,
                        "regions": "eu,uk",
                        "markets": "h2h,totals",
                        "oddsFormat": "decimal",
                    },
                )
                if r.status_code == 200:
                    rem = r.headers.get("x-requests-remaining", "?")
                    added = 0
                    for ev in r.json():
                        if not str(ev.get("sport_key", "")).startswith("soccer_"):
                            continue
                        home = ev.get("home_team", "")
                        away = ev.get("away_team", "")
                        h_odd = d_odd = a_odd = o25 = u25 = o15 = u15 = None

                        for bk in ev.get("bookmakers", []):
                            for mkt in bk.get("markets", []):
                                if mkt.get("key") == "h2h":
                                    for out in mkt.get("outcomes", []):
                                        if out.get("name") == home and not h_odd:
                                            h_odd = float(out["price"])
                                        elif out.get("name") == away and not a_odd:
                                            a_odd = float(out["price"])
                                        elif out.get("name") == "Draw" and not d_odd:
                                            d_odd = float(out["price"])
                                elif mkt.get("key") == "totals":
                                    for out in mkt.get("outcomes", []):
                                        pt = float(out.get("point", 0))
                                        if pt == 2.5:
                                            if out.get("name") == "Over" and not o25:
                                                o25 = float(out["price"])
                                            elif out.get("name") == "Under" and not u25:
                                                u25 = float(out["price"])
                                        elif pt == 1.5:
                                            if out.get("name") == "Over" and not o15:
                                                o15 = float(out["price"])
                                            elif out.get("name") == "Under" and not u15:
                                                u15 = float(out["price"])
                            if h_odd and a_odd and d_odd and o25 and u25:
                                break

                        if h_odd and a_odd:
                            nk = f"{normalize_name(home)}__{normalize_name(away)}"
                            existing = ws_by_key.get(nk)
                            if existing:
                                if not existing.draw_odds and d_odd:
                                    existing.draw_odds = d_odd
                                if not existing.over_2_5 and o25:
                                    existing.over_2_5 = o25
                                if not existing.under_2_5 and u25:
                                    existing.under_2_5 = u25
                                if not existing.over_1_5 and o15:
                                    existing.over_1_5 = o15
                                if not existing.under_1_5 and u15:
                                    existing.under_1_5 = u15
                            else:
                                ws_by_key[nk] = WS_MatchData(
                                    match_id=str(ev.get("id", "")),
                                    league=ev.get("sport_title", ""),
                                    home_team=home,
                                    away_team=away,
                                    home_odds=h_odd,
                                    draw_odds=d_odd,
                                    away_odds=a_odd,
                                    over_2_5=o25,
                                    under_2_5=u25,
                                    over_1_5=o15,
                                    under_1_5=u15,
                                    commence_time=ev.get("commence_time", ""),
                                )
                                added += 1
                    print(f"✅ The Odds API : +{added} matchs (crédits restants : {rem})")
        except Exception as e:
            print(f"⚠️ Erreur The Odds API : {e}")

    if RAPIDODDSAPI_API_KEY and fetch_rapidodds_odds_data:
        try:
            events = await fetch_rapidodds_odds_data()
            for e in events or []:
                nk = f"{normalize_name(e['home_team'])}__{normalize_name(e['away_team'])}"
                if nk not in ws_by_key and e.get("home_odds") and e.get("away_odds"):
                    ws_by_key[nk] = WS_MatchData(
                        e["match_id"],
                        e.get("league", ""),
                        e["home_team"],
                        e["away_team"],
                        e.get("home_odds"),
                        e.get("draw_odds"),
                        e.get("away_odds"),
                        e.get("over_2_5"),
                        e.get("under_2_5"),
                        e.get("over_1_5"),
                        e.get("under_1_5"),
                        e.get("btts_yes"),
                        e.get("btts_no"),
                        e.get("commence_time", ""),
                    )
        except Exception as e:
            print(f"⚠️ Erreur RapidOddsAPI : {e}")

    ws = list(ws_by_key.values())
    return ws, ("live" if ws else "fallback")


def inject_all_real_odds(pool: list[MatchData], ws_matches: list[WS_MatchData]) -> int:
    matched = 0
    for md in pool:
        had_initial_odds = bool(md.real_odds)
        for ws in ws_matches:
            if teams_match(md.home, ws.home_team) and teams_match(md.away, ws.away_team):
                if ws.home_odds:
                    md.real_odds["home"] = ws.home_odds
                if ws.draw_odds:
                    md.real_odds["draw"] = ws.draw_odds
                if ws.away_odds:
                    md.real_odds["away"] = ws.away_odds
                if ws.over_2_5:
                    md.real_odds["over_2.5"] = ws.over_2_5
                if ws.under_2_5:
                    md.real_odds["under_2.5"] = ws.under_2_5
                if ws.over_1_5:
                    md.real_odds["over_1.5"] = ws.over_1_5
                if ws.under_1_5:
                    md.real_odds["under_1.5"] = ws.under_1_5
                if ws.btts_yes:
                    md.real_odds["btts_yes"] = ws.btts_yes
                if ws.btts_no:
                    md.real_odds["btts_no"] = ws.btts_no
                if ws.commence_time:
                    md.kickoff = ws.commence_time
                break
        _ensure_dixon_pred(md)
        if bool(md.real_odds) or had_initial_odds:
            matched += 1
    return matched


# =====================================================================
# DOUBLE SCAN & ENRICHISSEMENT IA VERROUILLÉ SUR LE PARI EXACT
# =====================================================================
def _build_quant_analysis(md: MatchData, pick_label: str, proba_pct: float, odd: float) -> str:
    _ensure_dixon_pred(md)
    dp = md.dixon_pred or {}
    lh = float(dp.get("lambda_home") or 1.35)
    la = float(dp.get("lambda_away") or 1.15)
    xg_tot = round(lh + la, 2)
    ev = round((proba_pct / 100.0) * odd, 2)

    espn = md.espn_ctx or {}
    neutral_note = ""
    if espn.get("neutral_venue"):
        city = f" ({espn['venue_city']})" if espn.get("venue_city") else ""
        neutral_note = f" Match délocalisé sur terrain neutre à {espn.get('venue_name', 'stade neutre')}{city}."

    rank_note = ""
    if espn.get("home_rank") not in (None, "N/A") and espn.get("away_rank") not in (None, "N/A"):
        rank_note = f" (classement : {espn['home_rank']}e vs {espn['away_rank']}e)"

    lbl_low = pick_label.lower()
    if "moins de 2.5" in lbl_low or "under 2.5" in lbl_low:
        s1 = f"Projection fermée en {md.league} entre {md.home} et {md.away}{rank_note} avec un cumul limité à {xg_tot:.2f} xG ({lh:.2f} - {la:.2f}).{neutral_note}"
    elif "plus de 1.5" in lbl_low or "plus de 2.5" in lbl_low:
        s1 = f"Configuration offensive attendue entre {md.home} et {md.away}{rank_note} avec {xg_tot:.2f} buts projetés par Dixon-Coles ({lh:.2f} xG vs {la:.2f} xG).{neutral_note}"
    elif "(2)" in lbl_low:
        s1 = f"{md.away} affiche une supériorité statistique en déplacement face à {md.home}{rank_note} (xG {la:.2f} contre {lh:.2f}).{neutral_note}"
    else:
        s1 = f"{md.home} présente un avantage statistique solide face à {md.away}{rank_note} (xG {lh:.2f} contre {la:.2f} en {md.league}).{neutral_note}"

    s2 = f"La probabilité calibrée à {proba_pct:.1f}% à la cote {odd:.2f} (EV {ev:.2f}) valide l'option {pick_label}."
    return f"{s1} {s2}"


def _validate_ai_matches_pick(analysis_txt: str, pick_label: str, md: MatchData) -> bool:
    if not analysis_txt or len(analysis_txt.strip()) < 60:
        return False
    low = analysis_txt.lower()
    lbl = pick_label.lower()

    if has_pro_red_flags(analysis_txt, md.league)[0]:
        return False

    if "moins de 2.5" in lbl or "under 2.5" in lbl:
        contradictions = [
            "festival offensif", "pluie de buts", "large victoire", "écraser",
            "ne devrait pas peser lourd face aux offensifs", "déferlante", "score fleuve",
        ]
        if any(w in low for w in contradictions):
            return False

    if "plus de 1.5" in lbl or "plus de 2.5" in lbl:
        if any(w in low for w in ("0-0", "match verrouillé", "fermé à double tour", "aucun but")):
            return False

    if md.espn_ctx.get("neutral_venue"):
        if any(w in low for w in ("à domicile", "devant son public", "ambiance incandescente", "chaudron")):
            return False

    return True


async def _generate_strict_gemini_pick_text(
    client: httpx.AsyncClient,
    md: MatchData,
    pick_label: str,
    proba_pct: float,
    odd: float,
) -> Optional[str]:
    if not GEMINI_API_KEY:
        return None

    _ensure_dixon_pred(md)
    dp = md.dixon_pred or {}
    espn = md.espn_ctx or {}

    lbl = pick_label.lower()
    if "moins de 2.5" in lbl:
        market_rule = "Justifie UNIQUEMENT un match fermé à faible score (blocs défensifs, faible volume d'occasions). INTERDICTION de parler de victoire facile ou d'attaque dominante."
    elif "plus de 1.5" in lbl or "plus de 2.5" in lbl:
        market_rule = "Justifie UNIQUEMENT le potentiel de buts (xG cumulés élevés, occasions régulières des deux équipes)."
    elif "(2)" in lbl:
        market_rule = f"Justifie UNIQUEMENT pourquoi {md.away} a l'avantage pour s'imposer."
    elif "(1)" in lbl:
        market_rule = f"Justifie UNIQUEMENT pourquoi {md.home} a l'avantage pour s'imposer."
    else:
        market_rule = f"Justifie UNIQUEMENT le pari '{pick_label}' avec les statistiques fournies."

    neutral_rule = (
        f"IMPORTANT : Le match se joue sur TERRAIN NEUTRE à {espn.get('venue_name')} ({espn.get('venue_city')}). Mentionne-le et ne parle JAMAIS d'avantage du public à domicile."
        if espn.get("neutral_venue")
        else f"Stade : {espn.get('venue_name', 'Officiel')} ({espn.get('venue_city', '')})."
    )

    prompt = f"""Tu es un analyste quantitatif en paris sportifs. Rédige une analyse concise en français (3 phrases maximum, ton direct et professionnel, sans salutation inutile).

DONNÉES VÉRIFIÉES (NE RIEN INVENTER) :
- Match : {md.home} vs {md.away} ({md.league})
- {neutral_rule}
- Classement officiel : {md.home} ({espn.get('home_rank', 'N/A')}e, forme: {espn.get('home_form', 'N/A')}) vs {md.away} ({espn.get('away_rank', 'N/A')}e, forme: {espn.get('away_form', 'N/A')})
- Pari sélectionné : {pick_label} @ {odd:.2f}
- Probabilité calibrée : {proba_pct:.1f}%
- Projections Dixon-Coles : {md.home} {dp.get('lambda_home', 'N/A')} xG vs {md.away} {dp.get('lambda_away', 'N/A')} xG
- Absents ESPN : Domicile={espn.get('home_injuries', [])[:3]} | Extérieur={espn.get('away_injuries', [])[:3]}

CONSIGNE OBLIGATOIRE : {market_rule}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent"
    try:
        r = await client.post(
            url,
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.25, "maxOutputTokens": 240},
            },
            timeout=25.0,
        )
        if r.status_code == 200:
            data = r.json()
            cand = (data.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            txt = "".join(p.get("text", "") for p in parts).strip()
            if _validate_ai_matches_pick(txt, pick_label, md):
                return txt
    except Exception as e:
        print(f"⚠️ Strict Gemini direct ({md.home} vs {md.away}) : {e}")
    return None


async def _fetch_gemini_analysis_for_match(client: httpx.AsyncClient, md: MatchData) -> Optional[dict]:
    _ensure_dixon_pred(md)
    ctx: dict[str, Any] = {}
    if enrich_match_with_bzzoiro:
        try:
            date_iso = parse_dt_safe(md.kickoff).strftime("%Y-%m-%d") if parse_dt_safe(md.kickoff) else datetime.now(TZ).strftime("%Y-%m-%d")
            bzz_ctx = await enrich_match_with_bzzoiro(md.home, md.away, date_iso)
            if isinstance(bzz_ctx, dict):
                ctx.update(bzz_ctx)
                if bzz_ctx.get("available") or bzz_ctx.get("disponible"):
                    md.has_h2h = True
        except Exception:
            pass
    if md.dixon_pred:
        ctx["dixon_coles"] = md.dixon_pred
    if md.real_odds:
        ctx["bookmaker_odds"] = md.real_odds
    if md.bzz_markets:
        ctx["bzzoiro_probas"] = md.bzz_markets
    if md.espn_ctx:
        ctx["espn_verified"] = md.espn_ctx

    if predict_match_probas:
        pred = await predict_match_probas(client, md.home, md.away, md.league, md.kickoff, context=ctx)
        if pred and isinstance(pred, dict) and ("markets" in pred or pred.get("analysis")):
            return {"markets": pred.get("markets", {}), "analysis": str(pred.get("analysis") or "").strip()}
    return None


async def _enrich_selected_legs_ai(combo: tuple | list) -> bool:
    async with httpx.AsyncClient(timeout=55.0) as client:
        for c in combo:
            md: Optional[MatchData] = c.get("_md") or c.get("match")
            if not md:
                continue
            pick_label = c.get("type", "")
            proba_pct = float(c.get("proba", 70.0))
            odd = float(c.get("odds", 1.40))

            if explain_selected_pick_with_gemini:
                try:
                    _ensure_dixon_pred(md)
                    ctx = {
                        "dixon_coles": md.dixon_pred or {},
                        "bookmaker_odds": md.real_odds or {},
                        "bzzoiro_probas": md.bzz_markets or {},
                        "espn_verified": md.espn_ctx or {},
                    }
                    enriched = await explain_selected_pick_with_gemini(
                        client=client,
                        home=md.home,
                        away=md.away,
                        league=md.league,
                        kickoff=md.kickoff,
                        pick_label=pick_label,
                        proba_pct=proba_pct,
                        odd=odd,
                        context=ctx,
                    )
                    if enriched:
                        if not enriched.get("is_safe", True):
                            print(f" 🚩 Gemini rejette le pari '{pick_label}' sur {md.home} vs {md.away}")
                            return False
                        analysis_txt = str(enriched.get("analysis") or "").strip()
                        if _validate_ai_matches_pick(analysis_txt, pick_label, md):
                            c["ai"] = analysis_txt
                            await asyncio.sleep(0.8)
                            continue
                except Exception as e:
                    print(f"⚠️ Enrichissement module externe ({md.home} vs {md.away}) : {e}")

            strict_txt = await _generate_strict_gemini_pick_text(client, md, pick_label, proba_pct, odd)
            if strict_txt:
                c["ai"] = strict_txt
                print(f"🧠 Analyse Gemini verrouillée validée : {md.home} vs {md.away} -> {pick_label}")
                await asyncio.sleep(0.8)
                continue

            c["ai"] = _build_quant_analysis(md, pick_label, proba_pct, odd)
    return True


async def _enrich_single_pass(targets: list[MatchData], pass_num: int) -> dict:
    results = {}
    errors = 0
    async with httpx.AsyncClient(timeout=70.0) as client:
        for md in targets:
            try:
                pred = await _fetch_gemini_analysis_for_match(client, md)
                if pred:
                    results[md.event_id] = pred
                    errors = 0
                else:
                    errors += 1
                await asyncio.sleep(1.5)
            except Exception as e:
                errors += 1
                print(f"⚠️ Gemini Pass {pass_num} ({md.home} vs {md.away}) : {e}")
                if errors >= 3:
                    print(f"🛑 Gemini Pass {pass_num} : arrêt anticipé (erreurs consécutives).")
                    break
    print(f"🤖 Gemini Pass {pass_num} : {len(results)}/{len(targets)} matchs analysés")
    return results


def _compare_two_passes(targets: list[MatchData], r1: dict, r2: dict):
    stable_list, enriched_list, stats = [], [], {"stable": 0, "unstable": 0, "single": 0, "none": 0}
    for md in targets:
        p1, p2 = r1.get(md.event_id), r2.get(md.event_id)
        if not p1 and not p2:
            stats["none"] += 1
            md.stability_flag = "no_gemini"
            continue
        if p1 and not p2:
            md.gemini_markets = p1["markets"]
            md.analysis = p1["analysis"]
            md.stability_flag = "single"
            stats["single"] += 1
            enriched_list.append(md)
            continue
        if p2 and not p1:
            md.gemini_markets = p2["markets"]
            md.analysis = p2["analysis"]
            md.stability_flag = "single"
            stats["single"] += 1
            enriched_list.append(md)
            continue

        is_stable, merged = True, {}
        for k in set(p1["markets"].keys()) | set(p2["markets"].keys()):
            v1, v2 = p1["markets"].get(k), p2["markets"].get(k)
            if v1 is None and v2 is not None:
                merged[k] = v2
                is_stable = False
                continue
            if v2 is None and v1 is not None:
                merged[k] = v1
                is_stable = False
                continue
            if v1 is None and v2 is None:
                continue
            try:
                f1, f2 = float(v1), float(v2)
            except Exception:
                merged[k] = v1
                continue
            if abs(f1 - f2) / 100.0 > DOUBLE_SCAN_STABILITY_DELTA:
                merged[k] = min(f1, f2)
                is_stable = False
            else:
                merged[k] = (f1 + f2) / 2.0

        md.gemini_markets = merged
        a1, a2 = p1.get("analysis", "") or "", p2.get("analysis", "") or ""
        base = a1 if len(a1) >= len(a2) else a2
        if is_stable:
            md.analysis = base
            md.stability_flag = "stable"
            stats["stable"] += 1
            stable_list.append(md)
        else:
            md.analysis = f"⚠️ Variable. {base[:280]}"
            md.stability_flag = "unstable"
            stats["unstable"] += 1
        enriched_list.append(md)
    return stable_list, enriched_list, stats


async def enrich_with_double_pass(pool: list[MatchData]) -> list[MatchData]:
    if not pool:
        STATE["double_scan_status"] = "EMPTY"
        return pool
    targets = pool[: max(DOUBLE_SCAN_POOL_SIZE, min(len(pool), GEMINI_MAX_CALLS_PER_SCAN))]
    r1 = await _enrich_single_pass(targets, 1)
    await asyncio.sleep(DOUBLE_SCAN_PAUSE_SECONDS)
    r2 = await _enrich_single_pass(targets, 2)
    stable, enriched, stats = _compare_two_passes(targets, r1, r2)
    STATE["double_scan_stats"] = stats
    print(f"🔬 Statistiques de double analyse : {stats}")
    if len(stable) >= DOUBLE_SCAN_MIN_STABLE:
        STATE["double_scan_status"] = f"STRICT ({len(stable)})"
        return stable
    if enriched:
        STATE["double_scan_status"] = f"FALLBACK ({len(stable)}/{len(enriched)})"
        return enriched
    STATE["double_scan_status"] = "GEMINI_KO"
    return targets


# =====================================================================
# MOTEUR EXCLUSIF VIP : ODDS +3 AUTH (BOUCLIER ANTI-PIÈGES + OVER 1.5)
# =====================================================================
def _build_auth_coupon(combo, subtitle: str, quality: int) -> Coupon:
    sorted_combo = sorted(combo, key=lambda c: parse_dt_safe(c.get("kickoff")) or datetime.max.replace(tzinfo=TZ))
    total_odds = math.prod(c["odds"] for c in sorted_combo)
    total_prob = math.prod(c["proba"] / 100.0 for c in sorted_combo)
    legs = [
        Selection(
            event_id=c["event_id"],
            league=c["league"],
            home=c["home"],
            away=c["away"],
            kickoff=c["kickoff"],
            market=c["type"],
            pick_label=c["type"],
            odds=c["odds"],
            model_prob=c["proba"] / 100.0,
            real_odd=c["real_odd"],
            quality=quality,
            analysis=clean_analysis_text(c["ai"], 560),
        )
        for c in sorted_combo
    ]
    return Coupon(
        "Odds +3 AUTH",
        f"{subtitle} • {len(legs)} sélections",
        "🛡️",
        legs,
        round(total_odds, 2),
        round(total_prob, 4),
        show_analysis=True,
    )


def _gemini_alignment_bonus(md: MatchData, mk: str) -> int:
    if not md.gemini_markets or not isinstance(md.gemini_markets, dict):
        return 0
    val = md.gemini_markets.get(mk)
    if val is None:
        return 0
    try:
        v = float(val)
    except (ValueError, TypeError):
        return 0
    if v >= 74.0:
        return 2
    if v >= 66.0:
        return 1
    return 0


def _valid_pro_combo(combo, require_anchor: bool = True, strict_diversity: bool = True) -> bool:
    if len({c["norm_key"] for c in combo}) != len(combo):
        return False
    if len({c.get("mk") for c in combo}) != len(combo):
        return False
    if sum(1 for c in combo if c["family"] == "goals") > 1:
        return False
    if sum(1 for c in combo if c["family"] == "btts") > 1:
        return False
    if sum(1 for c in combo if c.get("mk") == "victoire_away") > 1:
        return False
    if strict_diversity:
        if len(combo) >= 2 and len({c["family"] for c in combo}) < 2:
            return False
        if sum(1 for c in combo if c["family"] == "dc") > 1:
            return False
    if require_anchor and not any(
        c["proba"] >= 69.0 or (c["family"] == "result" and c["proba"] >= 63.0) for c in combo
    ):
        return False
    return True


async def build_exclusive_vip_auth_coupon(
    enriched_pool: List[MatchData],
    ws_matches: List[WS_MatchData],
) -> Tuple[Optional[Coupon], set]:
    vip_candidates = []

    markets_map = [
        ("victoire_home", "home", "result", lambda m: f"Victoire {m.home} (1)"),
        ("victoire_away", "away", "result", lambda m: f"Victoire {m.away} (2)"),
        ("over_1.5", "over_1.5", "goals", lambda m: "Plus de 1.5 buts"),
        ("over_2.5", "over_2.5", "goals", lambda m: "Plus de 2.5 buts"),
        ("under_2.5", "under_2.5", "goals", lambda m: "Moins de 2.5 buts"),
        ("btts_yes", "btts_yes", "btts", lambda m: "Les 2 équipes marquent (BTTS Oui)"),
        ("double_chance_1X", "dc_1x", "dc", lambda m: f"Double Chance {m.home} ou Nul (1X)"),
        ("double_chance_X2", "dc_x2", "dc", lambda m: f"Double Chance Nul ou {m.away} (X2)"),
    ]

    for md in enriched_pool:
        if not md.home or not md.away or not is_match_today(md.kickoff):
            continue
        if is_reserve_team(md.home) or is_reserve_team(md.away):
            continue
        _ensure_dixon_pred(md)
        if not md.dixon_pred and not md.gemini_markets and not md.bzz_markets and not md.real_odds:
            continue

        if getattr(md, "stability_flag", "") == "unstable":
            STATE["rejections"]["unstable"] += 1
            continue

        is_trap, trap_reason = has_pro_red_flags(md.analysis, md.league)
        if is_trap:
            STATE["rejections"]["pro_trap"] += 1
            print(f" 🚩 Pro-Filter rejet ({md.home} vs {md.away}) : '{trap_reason}'")
            continue

        if md.real_odds.get("home") and md.real_odds.get("draw") and not md.real_odds.get("dc_1x"):
            inv_1x = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["draw"]))
            if inv_1x > 0:
                md.real_odds["dc_1x"] = round(max(1.03, 1.0 / inv_1x), 2)
        if md.real_odds.get("away") and md.real_odds.get("draw") and not md.real_odds.get("dc_x2"):
            inv_x2 = (1.0 / float(md.real_odds["away"])) + (1.0 / float(md.real_odds["draw"]))
            if inv_x2 > 0:
                md.real_odds["dc_x2"] = round(max(1.03, 1.0 / inv_x2), 2)

        if not md.real_odds.get("over_1.5") and md.real_odds.get("over_2.5") and md.dixon_pred:
            p_o15_dc = float(md.dixon_pred.get("over_1.5", 0))
            if p_o15_dc >= 0.74:
                est_o15 = round((1.0 / p_o15_dc) * 0.95, 2)
                if OVER15_MIN_ODD <= est_o15 <= OVER15_MAX_ODD:
                    md.real_odds["over_1.5"] = est_o15

        dp = md.dixon_pred or {}
        lh = float(dp.get("lambda_home") or 1.30)
        la = float(dp.get("lambda_away") or 1.10)
        total_xg = lh + la
        espn = md.espn_ctx or {}

        p_home_est, g_h, _, _, em_h = compute_probability(md, "victoire_home")
        p_away_est, g_a, _, _, em_a = compute_probability(md, "victoire_away")
        one_sided_match = (p_home_est >= 0.74) or (p_away_est >= 0.74)

        clear_home_win = p_home_est >= 0.62 and (p_home_est - p_away_est) >= 0.28 and em_h != "⚠️"
        clear_away_win = p_away_est >= 0.63 and (p_away_est - p_home_est) >= 0.30 and em_a != "⚠️"

        match_options = []
        for mk, odd_key, family, label_fn in markets_map:
            if mk == "btts_yes" and one_sided_match:
                continue

            if mk == "victoire_home" and len(espn.get("home_injuries", [])) >= MAX_TEAM_INJURIES:
                STATE["rejections"]["espn_guard"] += 1
                continue
            if mk == "victoire_away" and len(espn.get("away_injuries", [])) >= MAX_TEAM_INJURIES:
                STATE["rejections"]["espn_guard"] += 1
                continue

            if mk == "over_1.5":
                if total_xg < OVER15_MIN_TOTAL_XG or lh < OVER15_MIN_TEAM_XG or la < OVER15_MIN_TEAM_XG:
                    continue

            if mk == "under_2.5":
                if total_xg > UNDER25_MAX_TOTAL_XG:
                    continue
                h_rank = int(espn["home_rank"]) if str(espn.get("home_rank")).isdigit() else 10
                a_rank = int(espn["away_rank"]) if str(espn.get("away_rank")).isdigit() else 10
                if h_rank > 24 or a_rank > 24:
                    STATE["rejections"]["espn_guard"] += 1
                    continue

            if mk == "double_chance_1X":
                if (g_a > 0 and g_h > 0 and g_a > (g_h + 0.05)) or p_away_est > (p_home_est + 0.05):
                    continue
            elif mk == "double_chance_X2":
                if (g_h > 0 and g_a > 0 and g_h > (g_a + 0.05)) or p_home_est > (p_away_est + 0.05):
                    continue

            prob, g, d, b, emoji = compute_probability(md, mk)
            proba_pct = prob * 100.0
            if emoji == "⚠️":
                continue

            if mk in ("victoire_home", "victoire_away") and (clear_home_win or clear_away_win):
                if proba_pct < 61.0 or proba_pct > AUTH_PROBA_MAX:
                    STATE["rejections"]["auth_proba"] += 1
                    continue
            else:
                if not passes_auth_proba_filter(proba_pct):
                    continue

            if mk == "victoire_away" and p_away_est < 0.64:
                continue

            real_odd = md.real_odds.get(odd_key)
            if not real_odd or float(real_odd) <= 1.01:
                STATE["rejections"]["no_real_odds"] += 1
                continue
            odd = round(float(real_odd), 2)

            if mk == "over_1.5":
                if not (OVER15_MIN_ODD <= odd <= OVER15_MAX_ODD):
                    continue
            else:
                if not (ODDS3_AUTH_MIN_LEG_ODD <= odd <= ODDS3_AUTH_MAX_LEG_ODD):
                    continue

            mkt_prob = get_devigged_market_probability(md, mk)
            if not passes_value_filter(prob, odd, True, emoji, market_prob=mkt_prob):
                continue

            consensus_bonus = 2 if emoji == "🤝🤝" else (1 if emoji == "🤝" else 0)
            gemini_bonus = _gemini_alignment_bonus(md, mk)
            stakes_bonus = compute_stakes_score(md.league, md.analysis)
            market_priority = 3 if family == "result" else (2 if family in ("goals", "btts", "dc") else 1)
            rank_score = (gemini_bonus, market_priority, consensus_bonus, stakes_bonus, round(proba_pct, 1))

            match_options.append(
                {
                    "event_id": md.event_id,
                    "norm_key": f"{normalize_name(md.home)}__{normalize_name(md.away)}",
                    "league": md.league,
                    "home": md.home,
                    "away": md.away,
                    "kickoff": md.kickoff,
                    "mk": mk,
                    "type": label_fn(md),
                    "family": family,
                    "odds": odd,
                    "proba": round(proba_pct, 1),
                    "real_odd": odd,
                    "consensus": emoji or "🤝",
                    "ai": (md.analysis or "").strip(),
                    "_md": md,
                    "_rank": rank_score,
                }
            )

        match_options.sort(key=lambda x: x["_rank"], reverse=True)
        kept_families = set()
        for opt in match_options:
            if opt["family"] in kept_families:
                continue
            kept_families.add(opt["family"])
            opt.pop("_rank", None)
            vip_candidates.append(opt)
            if len(kept_families) >= 2:
                break

    vip_candidates.sort(
        key=lambda x: (
            _gemini_alignment_bonus(x["_md"], x["mk"]),
            3 if x["family"] == "result" else (2 if x["family"] in ("goals", "btts", "dc") else 1),
            compute_stakes_score(x["league"], x["ai"]),
            x["proba"],
        ),
        reverse=True,
    )
    top_pool = vip_candidates[:30]

    for strict_div in (True, False):
        for r in (2, 3):
            if len(top_pool) < r:
                continue
            valid_combos = []
            for combo in itertools.combinations(top_pool, r):
                if not _valid_pro_combo(combo, require_anchor=True, strict_diversity=strict_div):
                    continue
                tot_o = round(math.prod(c["odds"] for c in combo), 2)
                tot_p = math.prod(c["proba"] / 100.0 for c in combo)
                if ODDS3_AUTH_MIN_COTE <= tot_o <= ODDS3_AUTH_MAX_COTE and tot_p >= AUTH_MIN_COMBINED_PROB:
                    if not coupon_passes_ev(tot_o, tot_p, has_real_odds=True):
                        continue
                    valid_combos.append((tot_p, combo))

            valid_combos.sort(key=lambda item: item[0], reverse=True)
            for _, combo in valid_combos:
                is_clean = await _enrich_selected_legs_ai(combo)
                if not is_clean:
                    continue
                locked_keys = {c["norm_key"] for c in combo}
                label = "Duo Haute Confiance" if r == 2 else "Trio Sécurité"
                return _build_auth_coupon(combo, f"Sélection Pro VIP • {label}", 96), locked_keys

    print(" ℹ️ AUTH VIP : Aucune combinaison sécurisée trouvée dans la plage 2.40 - 3.45 aujourd'hui.")
    return None, set()


# =====================================================================
# MOTEURS SECONDAIRES (MT-FIN, MATCH NUL)
# =====================================================================
def _poisson_sample(lam: float) -> int:
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L and k < 20:
        k += 1
        p *= random.random()
    return k - 1


def _binomial_sample(n: int, p: float) -> int:
    return sum(1 for _ in range(n) if random.random() < p)


def simulate_htft(l_h: float, l_a: float, n_sims: int = 2000) -> dict:
    counts: dict[str, int] = {}
    for _ in range(n_sims):
        hg, ag = _poisson_sample(l_h), _poisson_sample(l_a)
        ht_hg, ht_ag = _binomial_sample(hg, MTFIN_FIRST_HALF_RATIO), _binomial_sample(ag, MTFIN_FIRST_HALF_RATIO)
        key = f"{'V1' if ht_hg > ht_ag else ('X' if ht_hg == ht_ag else 'V2')}/{'V1' if hg > ag else ('X' if hg == ag else 'V2')}"
        counts[key] = counts.get(key, 0) + 1
    return {k: round(v / n_sims, 4) for k, v in counts.items()}


def build_mtft_coupon_and_bonus(pool: list[MatchData], locked_keys: set):
    picks, bonus_pool = [], []
    for md in pool:
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        if nkey in locked_keys:
            continue
        if not md.home or not md.away or not md.dixon_pred or not is_match_today(md.kickoff):
            continue
        if is_reserve_team(md.home) or is_reserve_team(md.away):
            continue
        if has_pro_red_flags(md.analysis, md.league)[0]:
            continue

        htft = simulate_htft(md.dixon_pred.get("lambda_home", 1.3), md.dixon_pred.get("lambda_away", 1.1))
        best_p, best_prob = None, 0.0
        for c in ("V1/V1", "V2/V2"):
            if htft.get(c, 0) > best_prob:
                best_prob, best_p = htft.get(c, 0), c
        if not best_p or best_prob < MTFIN_MIN_PROB:
            continue

        fav_mk = "victoire_home" if best_p == "V1/V1" else "victoire_away"
        fav_consensus, _, _, _, emoji = compute_probability(md, fav_mk)
        if emoji == "⚠️" or fav_consensus < 0.62:
            continue

        odd = round(1.0 / best_prob * 0.94, 2)
        pts = 15 + (10 if md.has_h2h else 0) + 10 + (30 if best_prob >= 0.65 else 20) + 25
        if pts < MTFIN_MIN_SCORE:
            continue
        picks.append({"md": md, "pick": best_p, "prob": best_prob, "score": pts, "odd": odd, "htft": htft})

    coupon = None
    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    top3 = picks[:3]
    if top3:
        legs = [
            Selection(
                p["md"].event_id, p["md"].league, p["md"].home, p["md"].away, p["md"].kickoff,
                "MT-Fin", f"MT/Fin : {p['pick']}", p["odd"], p["prob"], None, -1.0, p["score"], htft_pick=p["pick"],
            )
            for p in top3
        ]
        co = round(math.prod(s.odds for s in legs), 2)
        cp = round(math.prod(s.model_prob for s in legs), 4)
        coupon = Coupon("MT-Fin", f"Sélection Pro • {len(legs)} matchs", "⏱", legs, co, cp, is_list=True)

    for p in top3:
        md = p["md"]
        fav_market = f"Victoire {md.home} (1)" if p["pick"] == "V1/V1" else f"Victoire {md.away} (2)"
        real_odd = md.real_odds.get("home") if p["pick"] == "V1/V1" else md.real_odds.get("away")
        ft_res = p["pick"].split("/")[1]
        fav_prob = sum(p["htft"].get(f"{ht}/{ft_res}", 0) for ht in ("V1", "V2", "X"))
        if real_odd and float(real_odd) >= ODDS3_MIN_COTE_LEG and passes_value_filter(fav_prob, float(real_odd), True):
            r_val = round(float(real_odd), 2)
            bonus_pool.append({"match": md, "type": fav_market, "odds": r_val, "proba": fav_prob * 100, "ai": md.analysis, "real_odd": r_val, "source": "MT-FIN", "kickoff": md.kickoff})
    return coupon, bonus_pool


def build_draw_coupon_and_bonus(pool: list[MatchData], locked_keys: set):
    candidates, bonus_pool = [], []
    for md in pool:
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        if nkey in locked_keys:
            continue
        if not md.home or not md.away or not is_match_today(md.kickoff):
            continue
        if is_reserve_team(md.home) or is_reserve_team(md.away):
            continue
        if has_pro_red_flags(md.analysis, md.league)[0]:
            continue

        prob, g, _, _, emoji = compute_probability(md, "match_nul")
        if prob < DRAW_MIN_PROB or (g > 0 and g < 0.25) or emoji == "⚠️":
            continue
        draw_odd = md.real_odds.get("draw")
        if not draw_odd or float(draw_odd) < 2.95:
            STATE["rejections"]["no_real_odds"] += 1
            continue
        draw_odd = round(float(draw_odd), 2)
        if (md.real_odds.get("home") and float(md.real_odds["home"]) < 1.80) or (md.real_odds.get("away") and float(md.real_odds["away"]) < 1.80):
            continue
        if not passes_value_filter(prob, draw_odd, True, emoji):
            continue
        pts = 30 + (25 if prob >= 0.33 else 18) + (20 if g >= 0.30 else 12) + 15 + (5 if md.has_h2h else 0)
        if pts >= DRAW_MIN_SCORE:
            candidates.append(
                Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "Match Nul", "Match Nul (X)", draw_odd, prob, draw_odd, (prob * draw_odd) - 1, pts)
            )

    coupon = None
    candidates.sort(key=lambda x: (x.quality, x.model_prob), reverse=True)
    top3 = candidates[:3]
    if top3:
        co = round(math.prod(s.odds for s in top3), 2)
        cp = round(math.prod(s.model_prob for s in top3), 4)
        if coupon_passes_ev(co, cp, has_real_odds=True):
            coupon = Coupon("Match Nul", f"Sélection Pro • {len(top3)} nuls", "🤝", top3, co, cp, is_list=True)

    return coupon, bonus_pool


# =====================================================================
# COLLECTE DES MATCHS DU JOUR & SCAN GLOBAL
# =====================================================================
async def collect_daily_matches(ws_matches: list[WS_MatchData]) -> list[MatchData]:
    pool_map: dict[str, MatchData] = {}

    if fetch_bzzoiro_predictions_direct:
        try:
            bzz_preds = await fetch_bzzoiro_predictions_direct()
            for item in bzz_preds or []:
                home = item.get("home") or item.get("home_team") or ""
                away = item.get("away") or item.get("away_team") or ""
                if not home or not away:
                    continue
                nk = f"{normalize_name(home)}__{normalize_name(away)}"
                pool_map[nk] = MatchData(
                    event_id=str(item.get("event_id") or item.get("id") or nk),
                    league=str(item.get("league") or "Football"),
                    home=home,
                    away=away,
                    kickoff=str(item.get("kickoff") or item.get("commence_time") or ""),
                    real_odds=dict(item.get("real_odds") or {}),
                    bzz_markets=dict(item.get("bzz_markets") or item.get("markets") or {}),
                    bzz_confidence=float(item.get("bzz_confidence") or item.get("confidence") or BZZ_NATIVE_CONFIDENCE),
                    analysis=str(item.get("analysis") or ""),
                )
        except Exception as e:
            print(f"⚠️ Erreur Bzzoiro predictions direct : {e}")

    for ws in ws_matches:
        nk = f"{normalize_name(ws.home_team)}__{normalize_name(ws.away_team)}"
        if nk not in pool_map:
            ro = {}
            if ws.home_odds:
                ro["home"] = ws.home_odds
            if ws.draw_odds:
                ro["draw"] = ws.draw_odds
            if ws.away_odds:
                ro["away"] = ws.away_odds
            if ws.over_2_5:
                ro["over_2.5"] = ws.over_2_5
            if ws.under_2_5:
                ro["under_2.5"] = ws.under_2_5
            if ws.over_1_5:
                ro["over_1.5"] = ws.over_1_5
            if ws.under_1_5:
                ro["under_1.5"] = ws.under_1_5
            if ws.btts_yes:
                ro["btts_yes"] = ws.btts_yes
            if ws.btts_no:
                ro["btts_no"] = ws.btts_no

            pool_map[nk] = MatchData(
                event_id=ws.match_id,
                league=ws.league,
                home=ws.home_team,
                away=ws.away_team,
                kickoff=ws.commence_time,
                real_odds=ro,
                bzz_markets={},
                bzz_confidence=BZZ_NATIVE_CONFIDENCE,
            )

    pool = [md for md in pool_map.values() if is_match_today(md.kickoff)]
    inject_all_real_odds(pool, ws_matches)
    pool.sort(key=prescore_match, reverse=True)
    return pool


async def scan(force: bool = False) -> bool:
    async with STATE["scan_lock"]:
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        if not force and load_daily_cache(today_str):
            print(f"📦 Cache journalier actif ({today_str}) : {len(STATE['coupons'])} coupons chargés.")
            return False

        STATE["rejections"] = {
            "value": 0, "coupon_ev": 0, "auth_proba": 0,
            "pro_trap": 0, "unstable": 0, "no_real_odds": 0, "espn_guard": 0,
        }
        ws_matches, odds_mode = await fetch_rapidodds_odds()
        STATE["odds_api_mode"] = odds_mode

        pool = await collect_daily_matches(ws_matches)
        await enrich_pool_with_espn_context(pool, ws_matches)
        for md in pool:
            _ensure_dixon_pred(md)

        enriched_pool = await enrich_with_double_pass(pool)

        coupons: list[Coupon] = []
        vip_coupon, locked_keys = await build_exclusive_vip_auth_coupon(enriched_pool, ws_matches)
        if vip_coupon:
            coupons.append(vip_coupon)

        mtft_coupon, _ = build_mtft_coupon_and_bonus(enriched_pool, locked_keys)
        if mtft_coupon:
            coupons.append(mtft_coupon)

        draw_coupon, _ = build_draw_coupon_and_bonus(enriched_pool, locked_keys)
        if draw_coupon:
            coupons.append(draw_coupon)

        STATE["coupons"] = coupons
        STATE["last_scan_date"] = today_str
        STATE["last_analysis"] = datetime.now(TZ).isoformat()
        STATE["ready"] = True
        STATE["debug"] = {
            "pool_total": len(pool),
            "enriched_total": len(enriched_pool),
            "ws_odds_count": len(ws_matches),
            "rejections": dict(STATE["rejections"]),
        }

        if coupons:
            record_coupons(coupons, today_str)
        save_daily_cache(today_str)
        return True


# =====================================================================
# FORMATAGE DES MESSAGES TELEGRAM VIP
# =====================================================================
def format_coupon_message(c: Coupon) -> str:
    ev = round(c.combined_odds * c.combined_prob, 2)
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"     ⚡ <b>{BRAND_NAME}</b> ⚡",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"{BRAND_SLOGAN}",
        f"📅 {today_pretty()}",
        f"🟢 Mode : {STATE.get('odds_api_mode', 'LIVE').upper()}",
        "",
        f"{c.emoji} <b>{c.name.upper()}</b>",
        f"<i>{c.subtitle}</i>",
        "",
        "┏━━━━━━━━━━━━━━━┓",
        f"┃ 🎫 Cote : <b>{c.combined_odds:.2f}</b>",
        f"┃ 📈 Réussite est. : <b>{c.combined_prob * 100:.1f}%</b>",
        f"┃ 💎 EV : <b>{ev:.2f}</b>",
        "┗━━━━━━━━━━━━━━━┛",
        "",
    ]

    for idx, s in enumerate(c.legs, start=1):
        p_pct = s.model_prob * 100.0
        lines.extend(
            [
                f"┌─ <b>#{idx} {s.home} vs {s.away}</b>",
                f"│ 📅 {format_kickoff(s.kickoff)} • {s.league}",
                f"│ 🎯 <b>{s.pick_label}</b>",
                f"│ 📊 Confiance : <b>{p_pct:.1f}%</b> {prob_bar(s.model_prob)}",
                f"│ 💰 Cote : <b>{s.odds:.2f}</b>",
            ]
        )
        if c.show_analysis and s.analysis:
            lines.append(f"│ 🧠 {s.analysis}")
        lines.append("└─────────────\n")

    lines.append(PUBLIC_FOOTER)
    return "\n".join(lines)


async def broadcast_daily_vip():
    vip = next((c for c in STATE.get("coupons", []) if c.name == "Odds +3 AUTH"), None)
    if not vip:
        if ADMIN_ID:
            await safe_send(
                ADMIN_ID,
                "🛡️ <b>Protection Bankroll Active</b>\nAucun combiné VIP n'a passé les filtres de sécurité aujourd'hui (évitement des matchs pièges).",
            )
        return
    msg = format_coupon_message(vip)
    for cid in TARGETS:
        await safe_send(cid, msg)


# =====================================================================
# HANDLERS TELEGRAM (COMMANDES & BOUTONS)
# =====================================================================
@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    kb = ADMIN_KEYBOARD if message.from_user.id == ADMIN_ID else USER_KEYBOARD
    sub_msg = get_subscription_message(message.from_user.id)
    text = (
        f"⚡ <b>Bienvenue sur {BRAND_NAME}</b>\n"
        f"<i>{BRAND_SLOGAN}</i>\n\n"
        f"Statut VIP : {sub_msg}\n\n"
        f"Utilise le menu ci-dessous pour consulter les analyses vérifiées du jour."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(Command("genkey"))
async def cmd_genkey(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    days = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 30
    count = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
    keys = create_access_keys(days=days, count=min(count, 20))
    formatted = "\n".join(f"<code>{k}</code>" for k in keys)
    await message.answer(f"🔑 <b>{len(keys)} clé(s) VIP ({days} jours) générée(s) :</b>\n\n{formatted}", parse_mode="HTML")


@dp.message(Command("key"))
async def cmd_key(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage : <code>/key VIP-XXXX-XXXX</code>", parse_mode="HTML")
        return
    _, reply = redeem_access_key(message.from_user, parts[1])
    kb = ADMIN_KEYBOARD if message.from_user.id == ADMIN_ID else USER_KEYBOARD
    await message.answer(reply, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == BTN_ODDS3)
async def handle_odds3(message: Message):
    if not is_subscribed(message.from_user.id):
        await message.answer(get_subscription_message(message.from_user.id), parse_mode="HTML")
        return
    if not STATE.get("coupons"):
        await scan(force=False)
    vip = next((c for c in STATE.get("coupons", []) if c.name == "Odds +3 AUTH"), None)
    if not vip:
        await message.answer(
            "🛡️ <b>Aucun ticket VIP validé pour le moment.</b>\n"
            "Les filtres anti-pièges (ESPN + Bzzoiro + Dixon-Coles) n'ont détecté aucune combinaison à faible risque aujourd'hui.",
            parse_mode="HTML",
        )
        return
    for chunk in split_message(format_coupon_message(vip)):
        await message.answer(chunk, parse_mode="HTML")


@dp.message(F.text == BTN_MTFT)
async def handle_mtft(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    c = next((x for x in STATE.get("coupons", []) if x.name == "MT-Fin"), None)
    if not c:
        await message.answer("⏱ Aucun coupon MT-Fin validé aujourd'hui.")
        return
    await message.answer(format_coupon_message(c), parse_mode="HTML")


@dp.message(F.text == BTN_DRAW)
async def handle_draw(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    c = next((x for x in STATE.get("coupons", []) if x.name == "Match Nul"), None)
    if not c:
        await message.answer("🤝 Aucun coupon Match Nul validé aujourd'hui.")
        return
    await message.answer(format_coupon_message(c), parse_mode="HTML")


@dp.message(F.text == BTN_WEEKLY)
async def handle_weekly(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    if not analyze_weekly_top_matches:
        await message.answer("⚠️ Module weekly_analyzer non disponible.")
        return
    await message.answer("📅 Analyse hebdomadaire en cours...")
    res = await analyze_weekly_top_matches()
    await message.answer(str(res)[:TELEGRAM_MAX_LEN], parse_mode="HTML")


@dp.message(F.text == BTN_SCAN)
async def handle_force_scan(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔄 Actualisation complète (ESPN + Bzzoiro + Dixon-Coles + Gemini)...")
    await scan(force=True)
    await handle_odds3(message)


@dp.message(F.text == BTN_BILAN)
async def handle_bilan(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await verify_pending_scores(notify=False)
    await message.answer(format_bilan_report(), parse_mode="HTML")


@dp.message(F.text == "⚙️ Débogage")
async def handle_debug(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    dbg = STATE.get("debug", {})
    txt = (
        f"<b>⚙️ ÉTAT SYSTÈME {BRAND_NAME}</b>\n"
        f"• Date scan : <code>{STATE.get('last_scan_date')}</code>\n"
        f"• Double-Scan : <code>{STATE.get('double_scan_status')}</code>\n"
        f"• Matchs pool : <code>{dbg.get('pool_total', 0)}</code> | Enrichis : <code>{dbg.get('enriched_total', 0)}</code>\n"
        f"• Rejets : <code>{json.dumps(dbg.get('rejections', {}))}</code>\n"
        f"• Dernière vérif scores : <code>{STATE.get('last_score_check')}</code>"
    )
    await message.answer(txt, parse_mode="HTML")


@dp.message(F.text)
async def handle_text_key_or_fallback(message: Message):
    txt = (message.text or "").strip().upper()
    if txt.startswith("VIP-") and len(txt) >= 10:
        _, reply = redeem_access_key(message.from_user, txt)
        kb = ADMIN_KEYBOARD if message.from_user.id == ADMIN_ID else USER_KEYBOARD
        await message.answer(reply, parse_mode="HTML", reply_markup=kb)


# =====================================================================
# CYCLE DE VIE FASTAPI + SCHEDULER (RENDER)
# =====================================================================
scheduler = AsyncIOScheduler(timezone=TZ)


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["tracker"] = load_json(LOCAL_DB, {"coupons": []})
    STATE["keys"] = load_json(KEYS_DB, {})
    STATE["users"] = load_json(USERS_DB, {})

    if download_and_train:
        try:
            STATE["dixon_model"] = await asyncio.to_thread(download_and_train)
        except Exception as e:
            print(f"⚠️ Dixon-Coles historique non chargé : {e}")

    if train_model_if_needed:
        try:
            await asyncio.to_thread(train_model_if_needed)
        except Exception:
            pass

    scheduler.add_job(scan, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ), kwargs={"force": True})
    scheduler.add_job(broadcast_daily_vip, CronTrigger(hour=NOTIFY_HOUR, minute=min(59, NOTIFY_MINUTE + 3), timezone=TZ))
    scheduler.add_job(verify_pending_scores, IntervalTrigger(minutes=45), kwargs={"notify": True})
    scheduler.start()

    polling_task = asyncio.create_task(dp.start_polling(bot))

    if NOTIFY_ON_STARTUP:
        asyncio.create_task(scan(force=False))

    yield

    polling_task.cancel()
    scheduler.shutdown(wait=False)


app = FastAPI(title=BRAND_NAME, lifespan=lifespan)


@app.get("/")
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "brand": BRAND_NAME,
        "last_scan_date": STATE.get("last_scan_date"),
        "coupons_count": len(STATE.get("coupons", [])),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
