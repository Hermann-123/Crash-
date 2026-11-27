from __future__ import annotations

importer asyncio
importer du JSON
importer les mathématiques
importer os
importer aléatoire
importer re
secrets d'importation
import unicodedata
importer itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from zoneinfo import ZoneInfo

importer httpx
import numpy as np
from scipy.stats import poisson
importer uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
à partir d'aiogram.types, importez KeyboardButton, ReplyKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
depuis apscheduler.triggers.cron import CronTrigger
depuis apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

# --- IMPORTATIONS ---
essayer:
    from gemini_predictor import predict_match_probas
sauf exception :
    predict_match_probas = Aucun

essayer:
    from gemini_predictor import explain_selected_pick_with_gemini
sauf exception :
    explain_selected_pick_with_gemini = None

essayez : depuis bzzoiro_data import enrich_match_with_bzzoiro
sauf : enrich_match_with_bzzoiro = None

essayez : depuis bzzoiro_predictions_direct import fetch_bzzoiro_predictions_direct
sauf : fetch_bzzoiro_predictions_direct = None

try: from odds_fallback import fetch_all_odds as fetch_infer_odds
sauf : fetch_infer_odds = None

essayer:
    from ml_engine import train_model_if_needed, predict_ml, fuse_probas
    print("✅ ml_engine chargé")
sauf Exception comme e :
    train_model_si_nécessaire = predict_ml = fuse_probas = Aucun
    print(f"❌ ml_engine: {e}")

essayez : depuis rapidoddsapi_data import fetch_all_odds as fetch_rapidodds_odds_data
sauf : fetch_rapidodds_odds_data = None

essayez : from dixon_coles import download_and_train
sauf : download_and_train = None

essayer:
    from weekly_analyzer import analyze_weekly_top_matches
    print("✅week_analyzer chargé")
sauf Exception comme e :
    analyze_weekly_top_matches = Aucun
    print(f"⚠️week_analyzer non chargé : {e}")

# --- VARIABLES ET ENVIRONNEMENT ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CLÉ_API_GEMINI = os.getenv("CLÉ_API_GEMINI", "")
BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
ID_ADMIN_TELEGRAM = os.getenv("ID_ADMIN_TELEGRAM", "5968288964")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "-1003982738017")
RAPIDODDSAPI_API_KEY = os.getenv("RAPIDODDSAPI_API_KEY", "")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")

essayez : ADMIN_ID = int(TELEGRAM_ADMIN_ID)
sauf : ADMIN_ID = 0

HEURE_NOTIFY = int(os.getenv("HEURE_NOTIFY", "0"))
NOTIFY_MINUTE = int(os.getenv("NOTIFY_MINUTE", "0"))
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")
LOCAL_DB = os.getenv("LOCAL_DB", "/tmp/tracker.json")
KEYS_DB = os.getenv("KEYS_DB", "/tmp/keys.json")
USERS_DB = os.getenv("USERS_DB", "/tmp/users.json")
COUPONS_CACHE_DB = os.getenv("COUPONS_CACHE_DB", "/tmp/coupons_today.json")

NOM_MARQUE = os.getenv("NOM_MARQUE", "Indice de volatilité")
SLOGAN_MARQUE = os.getenv("SLOGAN_MARQUE", "Analyse IA • VIP")
PUBLIC_FOOTER = os.getenv("PUBLIC_FOOTER", "⚠️ Analyse statistique. Gestion de bankroll recommandée (1-3% par ticket).")
NOTIFY_ON_STARTUP = os.getenv("NOTIFY_ON_STARTUP", "true").lower() == "true"

GEMINI_MAX_CALLS_PER_SCAN = int(os.getenv("GEMINI_MAX_CALLS_PER_SCAN", "20"))
BZZ_NATIVE_CONFIDENCE = float(os.getenv("BZZ_NATIVE_CONFIDENCE", "65.0"))

POIDS_ALGO = float(os.getenv("WEIGHT_ALGO", "0.35"))
POIDS_GEMINI = float(os.getenv("POIDS_GEMINI", "0.35"))
POIDS_BZZOIRO = float(os.getenv("POIDS_BZZOIRO","0.30"))

# ✅ CALIBRAGE PRO GLOBAL — PLAGE OFFICIELLE 2.00 À 3.50 (BATTRE LA MARGE BOOKMAKER)
MIN_EDGE = float(os.getenv("MIN_EDGE", "-0.03"))
MIN_COUPON_EV = float(os.getenv("MIN_COUPON_EV", "0.96"))
MIN_REAL_COUPON_EV = float(os.getenv("MIN_REAL_COUPON_EV", "0.97"))

# ✅ CALIBRAGE MOTEUR VIP (3 MATCHS VARIÉS ENTRE 2.00 ET 3.50)
AUTH_PROBA_MIN = float(os.getenv("AUTH_PROBA_MIN", "62.0"))
AUTH_PROBA_MAX = float(os.getenv("AUTH_PROBA_MAX", "88.0"))
AUTH_MIN_COMBINED_PROB = 0,26

TAILLE_DOUBLE_SCAN_POOL = int(os.getenv("TAILLE_DOUBLE_SCAN_POOL", "12"))
DOUBLE_SCAN_STABILITY_DELTA = float(os.getenv("DOUBLE_SCAN_STABILITY_DELTA", "0.16"))
DOUBLE_SCAN_MIN_STABLE = int(os.getenv("DOUBLE_SCAN_MIN_STABLE", "3"))
DOUBLE_SCAN_PAUSE_SECONDS = int(os.getenv("DOUBLE_SCAN_PAUSE_SECONDS", "3"))

MTFIN_MIN_SCORE = 70
MTFIN_MIN_PROB = 0,42
MTFIN_SIMULATIONS = 2000
MTFIN_FIRST_HALF_RATIO = 0,45

SCORE_MIN_DRAW = 70
DRAW_MAX_ECART = 0,20
DRAW_MIN_PROB = 0,30

ODDS3_AUTH_MIN_COTE = 2,00
ODDS3_AUTH_MAX_COTE = 3,50
ODDS3_HYB_MIN_COTE = 3,55
ODDS3_HYB_MAX_COTE = 6,00
ODDS3_MIN_COTE_LEG = 1,15

MATCH_START_BUFFER_MINUTES = 10
MOTS-CLÉS DE RÉSERVE = ["jong", " fc b", " ii", " u21", " u19", " castilla", " réserves", " équipe b"]

PRO_TRAP_PHRASES = [
    "piège à allumettes",
    "risque élevé",
    "effectif remanié",
    "équipe bis",
    "équipe b",
    "nomreux absents",
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
    "démobiliser",
]

MOTS-CLÉS DE LA LIGUE À FAIBLES ENJEUX = [
    "amical", "amical", "club friendlylies", "efl trophy", "checkatrade",
    "Coupe de la Premier League", "Premier League 2", "U21", "U23", "U19", "Jeunesse",
    "réserve", "oberliga", "regionalliga", "taca da liga", "coupe de la ligue",
]

MOTS-CLÉS_DE_LA_LIGUE_À_MISE_ÉLEVÉE = [
    "Ligue des champions", "Ligue Europa", "Ligue de conférence",
    "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1",
    "championnat", "eredivisie", "primeira liga", "pro league", "super lig",
    "coupe du monde", "coupe du monde", "euro", "can", "coupe d'afrique", "copa america",
    "libertadores", "sudamericana", "playoff", "barrage",
]

PHRASES_D'ANALYSE_À_JEUX_ÉLEVÉS = [
    "qualification", "qualifier", "course au titre", "titre", "podium",
    "maintien", "relégation", "zone rouge", "top 4", "top 8", "place européenne",
    "Europe", "montée", "promotion", "play-off", "barrage", "derby",
    "revanche", "impératif", "victoire obligatoire", "doit gagner", "enjeu majeur",
    "crucial", "décisif", "capital", "sous pression",
]

ESPN_LEAGUE_SLUGS = [
    "eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1", "gre.1",
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
]

TELEGRAM_MAX_LEN = 3800
TZ = ZoneInfo(NOTIFY_TZ)

si ce n'est pas BOT_TOKEN ou GEMINI_API_KEY ou ADMIN_ID :
    raise RuntimeError("Variables d'environnement manquantes")

# =====================================================================
# ⚙️ MOTEURS
# =====================================================================
@dataclass
classe WS_MatchData :
    match_id: str; league: str; home_team: str; away_team: str
    cotes domicile : Optionnel[float] ; cotes match nul : Optionnel[float] ; cotes extérieur : Optionnel[float]
    over_2_5 : Optional[float] = None ; under_2_5 : Optional[float] = None
    btts_yes : Optionnel[float] = None ; btts_no : Optionnel[float] = None
    commencer_time: str = ""

@dataclass
classe WS_SimulationResult :
    match_id : str ; proba_home : float ; proba_draw : float ; proba_away : float
    most_likely_score : str ; proba_btts : float ; proba_over_1_5 : float ; proba_over_2_5 : float ; proba_over_3_5 : float

classe DixonColesEngine :
    def __init__(self): self.max_goals = 6
    def simuler(self, match: WS_MatchData) -> Optional[WS_SimulationResult]:
        Si les cotes à domicile ou à l'extérieur ne correspondent pas : retourner None
        lx = (1,0 / match.home_odds) * 1,8 * 1,15 ; my = (1,0 / match.away_odds) * 1,8
        matrice = np.zeros((self.max_goals, self.max_goals))
        pour i dans la plage(self.max_goals) :
            pour j dans la plage(self.max_goals) : matrix[i, j] = poisson.pmf(i, lx) * poisson.pmf(j, my)
        matrice /= np.sum(matrice)
        p_h = float(np.sum(np.tril(matrix, -1))) * 100; p_d = float(np.sum(np.diag(matrix))) * 100; p_a = float(np.sum(np.triu(matrix, 1))) * 100
        best_idx = np.argmax(matrix); sx, sy = np.unravel_index(best_idx, matrix.shape)
        p_btts = float(np.sum(matrix[1:, 1:])) * 100
        p_o15 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 1])) * 100
        p_o25 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 2])) * 100
        renvoie WS_SimulationResult(match.match_id, p_h, p_d, p_a, f"{sx}-{sy}", p_btts, p_o15, p_o25, 0)

dixon_ws = DixonColesEngine()

@dataclass
classe MatchData :
    event_id: str; league: str; home: str; away: str; kickoff: str
    cotes_réelles : dict ; marchés_bzz : dict ; confiance_bzz : float
    gemini_markets : dict = None ; dixon_pred : dict = None ; analysis : str = "" ; has_h2h : bool = False ; stability_flag : str = "inconnu"

@dataclass
Sélection de classe :
    event_id: str; league: str; home: str; away: str; kickoff: str; market: str; pick_label: str
    cotes : flottant ; probabilité_modèle : flottant ; cote_réelle : Optionnel[flottant] = None ; valeur : flottant = -1,0
    qualité : int = 0 ; analyse : str = "" ; htft_pick : str = ""

@dataclass
Coupon de classe :
    nom : str ; sous-titre : str ; emoji : str ; jambes : liste ; cotes_combinées : float ; probabilité_combinées : float
    is_list : bool = False ; show_analysis : bool = False

bot = Bot(BOT_TOKEN); dp = Dispatcher()
BTN_ODDS3 = "🎯 Cote +3"; BTN_MTFT = "⏱ MT-Fin"; BTN_DRAW = "🤝 Match nul"
BTN_WEEKLY = "📅 Meilleure Semaine"; BTN_SCAN = "🔄 Actualiser" ; BTN_BILAN = "📊Bilan"

CLAVIER_UTILISATEUR = ReplyKeyboardMarkup(clavier=[[KeyboardButton(texte=BTN_ODDS3)]], redimensionner_clavier=Vrai)
ADMIN_KEYBOARD = RépondreKeyboardMarkup(
    clavier=[
        [BoutonClavier(texte=BTN_ODDS3), BoutonClavier(texte=BTN_MTFT)],
        [BoutonClavier(texte=BTN_DRAW), BoutonClavier(texte=BTN_HEBDOMADAIRE)],
        [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_BILAN), KeyboardButton(text="⚙️ Débogage")],
    ],
    redimensionner_clavier=Vrai,
)

ÉTAT = {
    "prêt" : Faux, "coupons" : [], "dernière_analyse" : Aucun, "débogage" : {},
    "tracker": {"coupons": []}, "keys": {}, "users": {},
    "scan_lock": asyncio.Lock(), "verify_lock": asyncio.Lock(),
    "dixon_model": None, "last_scan_date": "", "last_score_check": None,
    "double_scan_status": "", "double_scan_stats": {},
    "rejets" : {"valeur" : 0, "coupon_ev" : 0, "auth_proba" : 0, "pro_trap" : 0, "instable" : 0, "no_real_odds" : 0},
}

CIBLES = []
pour les données brutes dans (TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL) :
    si cru :
        essayez : TARGETS.append(int(raw))
        sauf ValueError : TARGETS.append(raw)

# =====================================================================
#GESTION DES CLÉS VIP & ABONNÉS
# =====================================================================
def generate_vip_key_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    p1 = "".join(secrets.choice(alphabet) for _ in range(4))
    p2 = "".join(secrets.choice(alphabet) for _ in range(4))
    retourner f"VIP-{p1}-{p2}"

def créer_clés_d'accès(jours: int = 30, nombre: int = 1) -> liste[str]:
    créé = []
    maintenant_iso = datetime.now(TZ).isoformat()
    pour _ dans la plage(count):
        code = générer_clé_vip()
        tant que le code dans STATE["keys"] :
            code = générer_clé_vip()
        ÉTAT["clés"][code] = {
            "jours" : jours,
            "created_at": now_iso,
            "utilisé" : Faux,
            "utilisé_par": Aucun,
            "used_at": Aucun,
        }
        créé.ajouter(code)
    enregistrer_json(KEYS_DB, STATE["keys"])
    retour créé

def redeem_access_key(user, raw_code: str) -> Tuple[bool, str]:
    code = raw_code.strip().upper()
    key_info = STATE["keys"].get(code)
    si key_info n'est pas :
        return False, "❌ Clé invalide ou introuvable."
    si key_info.get("used"):
        return False, "❌ Cette clé a déjà été utilisée."

    uid = str(user.id)
    maintenant = datetime.now(TZ)
    jours = int(key_info.get("jours", 30))

    base_dt = maintenant
    si uid est dans STATE["users"] :
        essayer:
            ancienne_expiration = datetime.fromisoformat(STATE["users"][uid].get("expires_at", ""))
            si old_exp > now :
                base_dt = ancienne_exp
        sauf exception :
            passer

    nouvelle_exp = base_dt + timedelta(jours=jours)
    ÉTAT["utilisateurs"][uid] = {
        "user_id": user.id,
        "nom d'utilisateur": utilisateur.nom d'utilisateur ou "",
        "full_name": user.full_name ou "",
        "expires_at": new_exp.isoformat(),
        "last_key": code,
        "updated_at": now.isoformat(),
    }
    key_info["utilisé"] = Vrai
    key_info["used_by"] = utilisateur.id
    key_info["used_at"] = maintenant.isoformat()

    enregistrer_json(USERS_DB, STATE["users"])
    enregistrer_json(KEYS_DB, STATE["keys"])

    exp_str = new_exp.strftime("%d/%m/%Y à %H:%M")
    return True, f"✅ <b>Accès VIP activé ({days} jours) !</b>\n📅 Valide jusqu'au <b>{exp_str}</b>.\n\nAppuie sur <b>{BTN_ODDS3}</b> pour voir le ticket du jour."

def est_abonné(id_utilisateur):
    si user_id == ADMIN_ID : renvoyer True
    uid = str(user_id)
    si l'UID n'est pas dans STATE['users'] : retourner Faux
    exp = STATE["users"][uid].get("expires_at")
    si exp n'est pas : retourner Faux
    essayer : retourner datetime.now(TZ) < datetime.fromisoformat(exp)
    sauf : renvoyer Faux

def obtenir_message_d'abonnement(user_id):
    si user_id == ADMIN_ID : renvoyer « ✅ Admin »
    si is_subscribed(user_id) :
        return f"✅ Actif jusqu'au {datetime.fromisoformat(STATE['users'][str(user_id)]['expires_at']).strftime('%d/%m/%Y à %H:%M')}."
    return "❌ Abonnement expiré ou inactif.\n🔑 <i>Colle directement ta clé d'accès dans ce chat pour activer ton VIP, ou contacte @hermann1123.</i>"

# =====================================================================
# UTILITAIRES & CACHE JOURNALIER
# =====================================================================
def load_json(chemin, default=None):
    si la valeur par défaut est None : valeur par défaut = {}
    essayer:
        avec open(path, "r", encoding="utf-8") as f: return json.load(f)
    sauf : renvoyer la valeur par défaut

def save_json(chemin, données):
    essayer:
        avec open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    sauf : passer

def serialize_coupon(c: Coupon) -> dict:
    retour {
        "nom": c.nom,
        "sous-titre" : c.sous-titre,
        "emoji": c.emoji,
        "combined_odds" : c.combined_odds,
        "combined_prob): c.combined_prob,
        "is_list": c.is_list,
        "show_analysis": c.show_analysis,
        "jambes" : [asdict(s) pour s dans c.jambes],
    }

def deserialize_coupon(d: dict) -> Coupon :
    jambes = [Sélection(**jambe) pour jambe dans d.get("jambes", [])]
    coupon de retour(
        nom=d["nom"],
        sous-titre=d["sous-titre"],
        emoji=d["emoji"],
        jambes=jambes,
        cotes_combinées=d["cotes_combinées"],
        probabilité_combinée=d["probabilité_combinée"],
        est_liste=d.get("est_liste", False),
        afficher_analyse=d.get("afficher_analyse", False),
    )

def save_daily_cache(date_str: str):
    charge utile = {
        "date" : date_str,
        "saved_at": datetime.now(TZ).isoformat(),
        "odds_api_mode": STATE.get("odds_api_mode", "inconnu"),
        "double_scan_status": STATE.get("double_scan_status", ""),
        "double_scan_stats" : STATE.get("double_scan_stats", {}),
        "coupons" : [serialize_coupon(c) pour c dans STATE.get("coupons", [])],
    }
    enregistrer_json(COUPONS_CACHE_DB, charge utile)
    print(f"💾 Cache journalier sauvegardé ({len(payload['coupons'])} coupons figés)")

def load_daily_cache(date_str: str) -> bool:
    données = charger_json(COUPONS_CACHE_DB, {})
    si les données ne sont pas ou si data.get("date") n'est pas une date_str :
        renvoyer Faux
    essayer:
        ÉTAT["coupons"] = [désérialiser_coupon(c) pour c dans data.get("coupons", [])]
        ÉTAT["last_scan_date"] = date_str
        ÉTAT["dernière_analyse"] = données.get("enregistré_à")
        si data.get("odds_api_mode"):
            ÉTAT["odds_api_mode"] = données["odds_api_mode"]
        si data.get("double_scan_status"):
            ÉTAT["double_scan_status"] = données["double_scan_status"]
        si data.get("double_scan_stats"):
            ÉTAT["double_scan_stats"] = données["double_scan_stats"]
        renvoyer Vrai
    sauf Exception comme e :
        print(f"⚠️ Erreur lecture cache journalier : {e}")
        renvoyer Faux

def today_pretty():
    d = datetime.now(TZ); mois = {1:"janvier",2:"février",3:"mars",4:"avril",5:"mai",6:"juin",7:"juillet",8:"août",9:"septembre",10:"octobre",11:"novembre",12:"décembre"}
    renvoie f"{d.day} {mois[d.month]} {d.year}"

def normalize_name(s: str) -> str:
    si non s : retourner ""
    s = str(s).lower().strip()
    s = "".join(c pour c dans unicodedata.normalize("NFD", s) si unicodedata.category(c) != "Mn")
    pour sfx dans [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"] : s = s.replace(sfx, "")
    retourner re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s)).strip()

def teams_match(name1: str, name2: str) -> bool:
    n1, n2 = normaliser_nom(nom1), normaliser_nom(nom2)
    si ce n'est pas n1 ou ce n'est pas n2 :
        renvoyer Faux
    si n1 == n2 :
        renvoyer Vrai
    si len(n1) >= 4 et len(n2) >= 4 et (n1 dans n2 ou n2 dans n1) :
        renvoyer Vrai
    renvoyer Faux

def is_reserve_team(name: str) -> bool: return any(kw in name.lower() for kw in RESERVE_KEYWORDS)

def is_low_stakes_competition(league_name: str) -> bool:
    si ce n'est pas league_name :
        renvoyer Faux
    bas = nom_de_la_ligue.plus_bas()
    retourner n'importe quel(kw dans low pour kw dans LOW_STAKES_LEAGUE_KEYWORDS)

def calculer_score_enjeux(nom_ligue: str, texte_analyse: str = "") -> int:
    """Évalue l'enjeu sportif réel du match (bonus positif si fort enjeu, pénalité si faible)."""
    score = 0
    low_lg = (nom_de_la_ligue ou "").lower()
    low_an = (texte_analyse ou "").lower()
    si un mot-clé appartient à low_lg pour HIGH_STAKES_LEAGUE_KEYWORDS :
        score += 20
    si une phrase (ph) appartient à low_an pour chaque phrase (ph) appartenant à HIGH_STAKES_ANALYSIS_PHRASES :
        score += 15
    score de retour

def has_pro_red_flags(analysis_text: str, league_name: str = "") -> Tuple[bool, str]:
    si is_low_stakes_competition(league_name):
        return True, f"compétition à faible enjeu ({league_name})"
    si ce n'est pas analysis_text :
        renvoyer Faux, ""
    bas = texte_analyse.lower()
    pour chaque phrase dans PRO_TRAP_PHRASES :
        si la phrase est en bas :
            renvoyer Vrai, phrase
    renvoyer Faux, ""

def clean_analysis_text(text: str, max_len: int = 560) -> str:
    sinon texte :
        retour ""
    texte = re.sub(r"\s+", " ", texte).strip()
    si la longueur de (texte) <= longueur_max :
        texte de retour
    couper = texte[:longueur_max]
    pour sep dans (". ", "! ", "? "):
        idx = cut.rfind(sep)
        si idx > 100 :
            retourner cut[:idx + 1]
    return cut.rsplit(" ", 1)[0] + "..."

def parse_dt_safe(s) -> Optional[datetime]:
    si s n'est pas renseigné : renvoyer None
    essayer:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        si dt.tzinfo est None : dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        renvoyer dt.astimezone(TZ)
    sauf : retourner None

def format_kickoff(kickoff_str) -> str:
    si kickoff_str n'est pas renvoyé : retourner "Heure non conf."
    dt = parse_dt_safe(kickoff_str)
    si dt : retourner dt.strftime("%d/%m %H:%M")
    renvoie "Heure non conf."

def is_match_not_started(kickoff_str) -> booléen :
    si kickoff_str n'est pas vrai : retourner Vrai
    dt = parse_dt_safe(kickoff_str)
    si dt n'est pas défini ou (dt.heure == 0 et dt.minute == 0) : retourner Vrai
    retourner dt > (datetime.now(TZ) + timedelta(minutes=MATCH_START_BUFFER_MINUTES))

def is_match_today(kickoff_str) -> booléen :
    """Vérifie strictement que le match se joue aujourd'hui (dans le fuseau TZ) et n'a pas encore commencé."""
    si kickoff_str n'est pas :
        renvoyer Faux
    dt = parse_dt_safe(kickoff_str)
    si ce n'est pas dt :
        renvoyer Faux
    maintenant = datetime.now(TZ)
    si dt.date() != now.date():
        renvoyer Faux
    si dt.hour == 0 et dt.minute == 0 :
        renvoyer Vrai
    retourner dt > (maintenant + timedelta(minutes=MATCH_START_BUFFER_MINUTES))

def calibrated_odd(prob: float, real_odd: Optional[float]) -> float:
    """Strictement vraies cotes bookmaker uniquement — zéro estimation artificielle."""
    si real_odd et float(real_odd) > 1.01 :
        retourner arrondi(float(real_odd), 2)
    renvoyer 0,0

def prob_bar(p: float) -> str:
    si p >= 0,75 : renvoyer "🟢🟢🟢"
    si p >= 0,67 : renvoyer "🟢🟢⚪"
    si p >= 0,62 : renvoyer "🟢⚪⚪"
    renvoyer "⚪⚪⚪"

def split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    si len(texte) <= max_len: retourner [texte]
    parties, courant = [], ""
    pour chaque ligne dans text.split("\n"):
        Si la longueur de la ligne actuelle + la longueur de la ligne + 1 est supérieure à la longueur maximale : parts.append(current.rstrip()); current = line + "\n"
        sinon : courant += ligne + "\n"
    si current.strip(): parts.append(current.rstrip())
    pièces de retour

def enregistrer_coupons(coupons, date_str):
    tracker = STATE["tracker"]; existant = {c["id"] pour c dans tracker["coupons"]}
    pour c dans les coupons :
        cid = f"{date_str}-{c.name}"
        si cid existe déjà : continuer
        jambes = [{"event_id": s.event_id, "home": s.home, "away": s.away, "market": s.market, "pick_label": s.pick_label, "odds": s.odds, "kickoff": s.kickoff, "result": "pending", "score": ""} pour s dans c.legs]
        tracker["coupons"].append({"id": cid, "date": date_str, "name": c.name, "combined_odds": c.combined_odds, "legs": legs, "status": "pending"})
    tracker["coupons"] = tracker["coupons"][-500:]; save_json(LOCAL_DB, tracker)

# =====================================================================
# ✅ VÉRIFICATION AUTOMATIQUE DES SCORES (BZZOIRO + SECOURS ESPN GRATUIT)
# =====================================================================
def evaluate_pick_outcome(pick_label: str, ft_h: int, ft_a: int, ht_h: Optional[int] = None, ht_a: Optional[int] = None) -> Optional[str]:
    lbl = (pick_label ou "").lower().strip()
    buts_total = ft_h + ft_a

    si "mt/fin" dans lbl ou "v1/v1" dans lbl ou "v2/v2" dans lbl :
        si ht_h est None ou ht_a est None :
            renvoyer Aucun
        ht_res = "v1" si ht_h > ht_a sinon ("x" si ht_h == ht_a sinon "v2")
        ft_res = "v1" si ft_h > ft_a sinon ("x" si ft_h == ft_a sinon "v2")
        réel = f"{ht_res}/{ft_res}"
        si "v1/v1" est dans lbl :
            Renvoie « gagné » si le résultat est « v1/v1 », sinon « perdu ».
        si "v2/v2" dans lbl :
            Renvoie « gagné » si le résultat est « v2/v2 », sinon « perdu ».
        renvoyer Aucun

    si "(1x)" dans lbl ou "1x" dans lbl :
        renvoyer « gagné » si ft_h >= ft_a sinon « perdu »
    si "(x2)" dans lbl ou "x2" dans lbl :
        renvoyer « gagné » si ft_a >= ft_h sinon « perdu »
    si « double chance 12 » dans lbl ou « (12) » dans lbl :
        renvoyer « gagné » si ft_h != ft_a sinon « perdu »

    si "match nul" dans lbl ou "(x)" dans lbl :
        renvoyer « gagné » si ft_h == ft_a sinon « perdu »

    si lbl.endswith("(1)") ou ("victoire" dans lbl et "(1)" dans lbl) :
        renvoyer « gagné » si ft_h > ft_a sinon « perdu »
    si lbl.endswith("(2)") ou ("victoire" dans lbl et "(2)" dans lbl) :
        renvoyer « gagné » si ft_a > ft_h sinon « perdu »

    si "plus de 1,5" en lbl ou "over 1,5" en lbl :
        Renvoyer « gagné » si total_goals >= 2 sinon « perdu »
    si "moins de 1,5" en lbl ou "under 1,5" en lbl :
        Renvoyer « gagné » si total_goals <= 1 sinon « perdu »
    si "plus de 2,5" en lbl ou "over 2,5" en lbl :
        Renvoyer « gagné » si total_goals >= 3 sinon « perdu »
    si "moins de 2,5" en lbl ou "under 2,5" en lbl :
        Renvoyer "gagné" si total_buts <= 2 sinon "perdu"
    si "plus de 3,5" en lbl ou "over 3,5" en lbl :
        Renvoyer « gagné » si total_goals >= 4 sinon « perdu »
    si "moins de 3,5" en lbl ou "under 3,5" en lbl :
        Renvoyer « gagné » si total_buts <= 3 sinon « perdu »

    si "btts oui" en lbl ou "2 équipes marquent" en lbl :
        renvoyer « gagné » si (ft_h > 0 et ft_a > 0) sinon « perdu »
    si "btts non" dans lbl :
        renvoyer « gagné » si (ft_h == 0 ou ft_a == 0) sinon « perdu »

    renvoyer Aucun

def _extract_bzzoiro_score(ev: dict) -> Optional[dict]:
    h = ev.get("home_team") ou ev.get("home") ou (ev.get("home_team_obj") ou {}).get("name") ou ""
    a = ev.get("away_team") ou ev.get("away") ou (ev.get("away_team_obj") ou {}).get("name") ou ""
    si h est une instance de dict : h = h.get("name", "")
    si isinstance(a, dict): a = a.get("name", "")
    si ce n'est pas h ou ce n'est pas a :
        renvoyer Aucun

    raw_st = str(ev.get("status") or ev.get("event_status") or "").lower()
    ft_h = ev.get("home_score") si ev.get("home_score") n'est pas None sinon ev.get("home_goals")
    ft_a = ev.get("away_score") si ev.get("away_score") n'est pas None sinon ev.get("away_goals")
    ht_h = ev.get("home_score_ht") si ev.get("home_score_ht") n'est pas None sinon ev.get("ht_home_score")
    ht_a = ev.get("away_score_ht") si ev.get("away_score_ht") n'est pas None sinon ev.get("ht_away_score")

    si un k appartient à raw_st pour chaque k appartenant à ("postponed", "cancel", "abandon", "pst", "canc")) :
        return {"home": h, "away": a, "status": "VOID", "ft_h": None, "ft_a": None, "ht_h": None, "ht_a": None}

    si un k appartient à raw_st pour chaque k appartenant à ("fin", "ft", "ended", "complet", "full")) et que ft_h n'est pas None et ft_a n'est pas None :
        retour {
            "domicile" : h, "absent" : a, "statut" : "FT",
            "ft_h": int(ft_h), "ft_a": int(ft_a),
            "ht_h": int(ht_h) si ht_h n'est pas None sinon None,
            "ht_a": int(ht_a) si ht_a n'est pas None sinon None,
        }
    renvoyer Aucun

async def fetch_bzzoiro_finished_scores(date_iso: str) -> list[dict]:
    sinon BZZOIRO_API_KEY :
        retour []
    résultats = []
    headers = {"Authorization": f"Token {BZZOIRO_API_KEY}"}
    URL = [
        ("https://sports.bzzoiro.com/api/events/", {"date": date_iso}),
        ("https://sports.bzzoiro.com/api/events/", {"date_from": date_iso, "date_to": date_iso}),
    ]
    asynchrone avec httpx.AsyncClient() comme client :
        pour l'URL, paramètres dans les URL :
            essayer:
                r = await client.get(url, headers=headers, params=params, timeout=15.0)
                si r.status_code != 200 :
                    continuer
                payload = r.json()
                items = payload.get("results", payload) si isinstance(payload, dict) sinon payload
                si les éléments ne sont pas une instance de la liste :
                    continuer
                pour ev dans les articles :
                    analysé = _extraire_score_bzzoiro(ev)
                    si analysé :
                        résultats.append(analysé)
                si résultats :
                    casser
            sauf Exception comme e :
                print(f"⚠️ Scores de Bzzoiro ({date_iso}) : {e}")
    résultats de retour

async def fetch_espn_finished_scores(date_iso: str) -> list[dict]:
    date_compact = date_iso.replace("-", "")
    résultats = []
    asynchrone avec httpx.AsyncClient() comme client :
        pour slug dans ESPN_LEAGUE_SLUGS :
            essayer:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
                r = await client.get(url, params={"dates": date_compact}, timeout=10.0)
                si r.status_code != 200 :
                    continuer
                pour ev dans r.json().get("events", []):
                    comps = ev.get("competitions", [])
                    sinon compétitions :
                        continuer
                    comp = comps[0]
                    st_obj = comp.get("status", {}).get("type", {})
                    st_name = (st_obj.get("name") ou "").upper()
                    terminé = booléen(st_obj.get("completed"))

                    concurrents = comp.get("concurrents", [])
                    home_c = next((c pour c dans les concurrents si c.get("homeAway") == "home"), None)
                    away_c = next((c pour c dans les concurrents si c.get("homeAway") == "away"), None)
                    si ce n'est pas home_c ou away_c :
                        continuer

                    h_name = home_c.get("team", {}).get("displayName") ou home_c.get("team", {}).get("name") ou ""
                    a_name = away_c.get("team", {}).get("displayName") ou away_c.get("team", {}).get("name") ou ""
                    si ce n'est pas h_name ou ce n'est pas a_name :
                        continuer

                    si « POSTPONED » ou « CANCELED » figure dans st_name :
                        results.append({"home": h_name, "away": a_name, "status": "VOID", "ft_h": None, "ft_a": None, "ht_h": None, "ht_a": None})
                    elif terminé :
                        essayer:
                            ft_h = int(float(home_c.get("score", 0)))
                            ft_a = int(float(away_c.get("score", 0)))
                            results.append({"home": h_name, "away": a_name, "status": "FT", "ft_h": ft_h, "ft_a": ft_a, "ht_h": None, "ht_a": None})
                        sauf exception :
                            passer
            sauf exception :
                continuer
    résultats de retour

async def verify_pending_scores(notify: bool = True) -> dict:
    asynchrone avec STATE["verify_lock"] :
        coupons = STATE["tracker"].get("coupons", [])
        coupons_en_attente = [c pour c dans coupons si c.get("statut") == "en_attente"]
        si les coupons ne sont pas en attente :
            ÉTAT["last_score_check"] = datetime.now(TZ).isoformat()
            return {"checked": 0, "updated_legs": 0, "settled_coupons": 0}

        dates_à_récupérer = ensemble()
        pour c dans pending_coupons :
            d = c.get("date")
            si d :
                dates_à_récupérer.ajouter(d)
                essayer:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    dates_to_fetch.add((dt + timedelta(days=1)).strftime("%Y-%m-%d"))
                sauf exception :
                    passer

        tous_résultats = []
        pour d dans sorted(dates_to_fetch) :
            bzz_res = await fetch_bzzoiro_finished_scores(d)
            espn_res = await fetch_espn_finished_scores(d)
            tous_résultats.étendre(bzz_res)
            tous_résultats.étendre(espn_res)
            attendre asyncio.sleep(0.3)

        si tous les résultats ne sont pas disponibles :
            ÉTAT["last_score_check"] = datetime.now(TZ).isoformat()
            return {"checked": len(pending_coupons), "updated_legs": 0, "settled_coupons": 0}

        jambes_mises_à_jour = 0
        nouvellement_installés = []

        pour c dans pending_coupons :
            jambes = c.get("jambes", [])
            modifié = Faux

            pour jambe dans jambes :
                si leg.get("result") != "pending" :
                    continuer
                lh, la = leg.get("home", ""), leg.get("away", "")
                matched_res = Aucun
                pour chaque élément dans all_results :
                    si teams_match(lh, item["home"]) et teams_match(la, item["away"]):
                        matched_res = élément
                        si item.get("ht_h") n'est pas None :
                            casser

                si la réponse correspondante n'est pas trouvée :
                    continuer

                si matched_res["status"] == "FT):
                    ft_h, ft_a = matched_res["ft_h"], matched_res["ft_a"]
                    ht_h, ht_a = matched_res.get("ht_h"), matched_res.get("ht_a")
                    résultat = évaluer_sélectionner_résultat(leg.get("sélectionner_étiquette", ""), ft_h, ft_a, ht_h, ht_a)
                    si résultat :
                        jambe["résultat"] = résultat
                        score_txt = f"{ft_h}-{ft_a}"
                        si ht_h n'est pas None et ht_a n'est pas None et "mt/fin" dans leg.get("pick_label", "").lower():
                            score_txt += f" (MT {ht_h}-{ht_a})"
                        jambe["score"] = score_txt
                        jambes_mis_à_jour += 1
                        modifié = Vrai

                elif matched_res["status"] == "VOID":
                    jambe["résultat"] = "void"
                    jambe["odds"] = 1.0
                    leg["score"] = "Reporté/Annulé"
                    jambes_mis_à_jour += 1
                    modifié = Vrai

            si modifié :
                résultats = [l.get("résultat") pour l dans jambes]
                si « perdu » dans les résultats :
                    c["statut"] = "perdu"
                    c["settled_at"] = datetime.now(TZ).isoformat()
                    nouvellement_installé.ajouter(c)
                elif all(r in ("won", "void") for r in results):
                    cotes_efficaces = arrondi(math.prod(float(l.get("cotes", 1.0)) pour l dans jambes), 2)
                    c["combined_odds"] = eff_odds
                    c["status"] = "void" si tous les résultats sont vides (r == "void") sinon "won"
                    c["settled_at"] = datetime.now(TZ).isoformat()
                    nouvellement_installé.ajouter(c)

        si updated_legs > 0 ou newly_settled :
            enregistrer_json(BASE_DE_BASE_LOCALE, ÉTAT["tracker"])

        ÉTAT["last_score_check"] = datetime.now(TZ).isoformat()

        si notifier et nouvellement_réglé :
            pour c dans newly_settled :
                attendre notify_settled_coupon(c)

        return {"checked": len(pending_coupons), "updated_legs": updated_legs, "settled_coupons": len(newly_settled)}

async def notify_settled_coupon(c: dict):
    statut = c.get("statut")
    si le statut n'est pas « gagné », « perdu » :
        retour
    is_win = statut == "gagné"
    header = "✅ <b>BILLET VIP VALIDE !</b>" if is_win else "❌ <b>BILLET BILAN</b>"
    lignes = [
        en-tête,
        f"🎫 <b>{c.get('name')}</b> ({c.get('date')}) • Cote <b>{c.get('combed_odds')}</b>",
        "",
    ]
    pour l dans c.get("legs", []):
        r = l.get("result")
        ico = "✅" si r == "gagné" sinon ("❌" si r == "perdu" sinon "⚪")
        sc = f" <b>[{l.get('score')}]</b>" si l.get("score") sinon ""
        lignes.append(f"{ico} {l.get('home')} vs {l.get('away')} — <i>{l.get('pick_label')}</i>{sc}")

    msg = "\n".join(lines)
    si is_win et c.get("name") == "Odds +3 AUTH":
        pour cid dans CIBLES :
            attendre safe_send(cid, msg)
    elif ADMIN_ID :
        attendre safe_send(ADMIN_ID, msg)

def compute_roi_stats(coupons: list) -> dict:
    gagné = [c pour c dans coupons si c.get("statut") == "gagné"]
    perdu = [c pour c dans coupons si c.get("statut") == "perdu"]
    en attente = [c pour c dans coupons si c.get("statut") == "en attente"]
    réglé = longueur(gagné) + longueur(perdu)
    taux_de_victoires = (len(gagné) / réglé * 100,0) si réglé > 0 sinon 0,0
    profit = somme(float(c.get("combined_odds", 0)) - 1.0 pour c dans won) - float(len(lost))
    roi = (profit / soldé * 100,0) si soldé > 0 sinon 0,0
    avg_win_odd = (sum(float(c.get("combined_odds", 0)) for c in won) / len(won)) if won else 0.0
    retour {
        "gagné" : len(gagné),
        "perdu" : len(perdu),
        "en attente" : longueur(en attente),
        "réglé" : réglé,
        "taux_de_victoire" : taux_de_victoire,
        "profit" : profit,
        "roi": roi,
        "avg_win_odd" : avg_win_odd,
    }

def format_bilan_report() -> str :
    all_c = STATE["tracker"].get("coupons", [])
    vip_c = [c pour c dans all_c si c.get("name") == "Odds +3 AUTH"]

    s_vip = calculer_roi_stats(vip_c)
    s_all = calculer_roi_stats(all_c)

    lignes = [
        f"<b>📊 PERFORMANCE ET ROI BILAN</b>",
        f"<i>Mise fixe : 1.00 unité / ticket</i>",
        "",
        f"<b>🛡️ VIP — COTES +3 AUTH</b>",
        f"• Terminés : <b>{s_vip['settled']}</b> (✅ {s_vip['won']} / ❌ {s_vip['lost']} / ⏳ {s_vip['pending']})",
        f"• Taux de réussite : <b>{s_vip['win_rate']:.1f}%</b>",
        f"• Côte moyenne gagnante : <b>{s_vip['avg_win_odd']:.2f}</b>",
        f"• Bénéfice net : <b>{s_vip['profit']:+.2f} u</b>",
        f"• ROI Réel : <b>{s_vip['roi']:+.1f}%</b>",
        "",
        f"<b>📈GLOBAL (TOUS COUPONS)</b>",
        f"• Terminés : <b>{s_all['settled']}</b> (✅ {s_all['won']} / ❌ {s_all['lost']} / ⏳ {s_all['pending']})",
        f"• Réussite : <b>{s_all['win_rate']:.1f}%</b> • Profit : <b>{s_all['profit']:+.2f} u</b> (ROI <b>{s_all['roi']:+.1f}%</b>)",
    ]

    récent = liste(inversé(tous_c[-5:]))
    si récent :
        lines.append("\n<b>🕒 5 BILLETS DERNIERS :</b>")
        pour c dans récent :
            st = c.get("statut")
            badge = "✅" si st == "gagné" sinon ("❌" si st == "perdu" sinon ("⚪" si st == "annulé" sinon "⏳"))
            lignes.append(f"\n{badge} <b>{c.get('name')}</b> ({c.get('date')}) • Cote <b>{c.get('combed_odds')}</b>")
            pour l dans c.get("legs", []):
                lr = l.get("resultat")
                lico = "✅" si lr == "gagné" sinon ("❌" si lr == "perdu" sinon ("⚪" si lr == "annulé" sinon "⏳"))
                sc = f" [{l.get('score')}]" si l.get("score") sinon ""
                lignes.append(f" {lico} {l.get('home')} - {l.get('away')} ({l.get('pick_label')}){sc}")

    retourner "\n".join(lignes)

def prescore_match(md: MatchData) -> int:
    si is_low_stakes_competition(md.league) :
        retour -999
    conf_score = int(md.bzz_confidence ou 0)
    real_bonus = 40 si (md.real_odds.get("home") ou md.real_odds.get("away")) sinon (20 si md.real_odds sinon 0)
    bzz_bonus = 15 si md.bzz_markets sinon 0
    dixon_bonus = 25 si md.dixon_pred sinon 0
    h2h_bonus = 10 si md.has_h2h sinon 0
    stakes_bonus = compute_stakes_score(md.league, md.analysis)
    renvoie conf_score + real_bonus + bzz_bonus + dixon_bonus + h2h_bonus + stakes_bonus

def _get_match_id(item: dict) -> str:
    m = élément["correspondance"]
    retourner str(m.event_id si hasattr(m, "event_id") sinon m.match_id)

def _ensure_dixon_pred(md: MatchData):
    """Garantit une modélisation Dixon-Coles / Poisson (xG et probas) même hors Top 5 championnats."""
    si md.dixon_pred :
        retour
    si STATE.get("dixon_model"):
        essayer:
            md.dixon_pred = STATE["dixon_model"].predict_all(md.home, md.away)
        sauf exception :
            passer
    si md.dixon_pred et md.real_odds.get("home") et md.real_odds.get("away") ne sont pas définis :
        essayer:
            h_o = float(md.real_odds["home"])
            a_o = float(md.real_odds["away"])
            d_o = float(md.real_odds["draw"]) si md.real_odds.get("draw") sinon None
            sim = dixon_ws.simulate(WS_MatchData(md.event_id, md.league, md.home, md.away, h_o, d_o, a_o))
            si sim :
                lx = arrondi((1.0 / h_o) * 1.8 * 1.15, 2)
                mon = arrondi((1.0 / a_o) * 1.8, 2)
                md.dixon_pred = {
                    "lambda_home": lx,
                    "lambda_away" : mon,
                    "p_home": arrondi(sim.proba_home / 100.0, 4),
                    "p_draw": round(sim.proba_draw / 100.0, 4),
                    "p_away": round(sim.proba_away / 100.0, 4),
                    "over_1.5": round(sim.proba_over_1_5 / 100.0, 4),
                    "over_2.5": round(sim.proba_over_2_5 / 100.0, 4),
                    "under_2.5": round(max(0.01, 1.0 - sim.proba_over_2_5 / 100.0), 4),
                    "btts_yes" : arrondi(sim.proba_btts / 100.0, 4),
                    "btts_no": round(max(0.01, 1.0 - sim.proba_btts / 100.0), 4),
                }
        sauf exception :
            passer

# =====================================================================
# CALCUL PROBABILITÉ (Dixon + ML + Gémeaux + Bzzoiro + Double Chance)
# =====================================================================
def calculer_probabilité(md, clé_marché):
    _assurer_dixon_pred(md)
    dixon_pred = md.dixon_pred si md.dixon_pred sinon {}
    ml_probas = predict_ml(dixon_pred, md.bzz_confidence) si predict_ml sinon None
    fused = fuse_probas(dixon_pred, ml_probas) si fuse_probas et ml_probas sinon dixon_pred
    gemme = dix = bzz = 0.0; gemme_disponible = dix_disponible = bzz_disponible = Faux

    si md.gemini_markets :
        si md.gemini_markets.get(market_key) n'est pas None :
            try: gem = float(md.gemini_markets[market_key]) / 100.0; gem_avail = True
            sauf : passer
        elif market_key == "double_chance_1X" and md.gemini_markets.get("victoire_home") is not None and md.gemini_markets.get("match_nul") is not None:
            try: gem = (float(md.gemini_markets["victoire_home"]) + float(md.gemini_markets["match_nul"])) / 100.0; gem_avail = True
            sauf : passer
        elif market_key == "double_chance_X2" and md.gemini_markets.get("victoire_away") is not None and md.gemini_markets.get("match_nul") is not None:
            try: gem = (float(md.gemini_markets["victoire_away"]) + float(md.gemini_markets["match_nul"])) / 100.0; gem_avail = True
            sauf : passer
        elif market_key == "double_chance_12" and md.gemini_markets.get("victoire_home") is not None and md.gemini_markets.get("victoire_away") is not None:
            try: gem = (float(md.gemini_markets["victoire_home"]) + float(md.gemini_markets["victoire_away"])) / 100.0; gem_avail = True
            sauf : passer

    si fusionné :
        si market_key == "double_chance_1X" et fused.get("p_home") n'est pas None et fused.get("p_draw") n'est pas None :
            essay: dix = float(fused["p_home"]) + float(fused["p_draw"]); dix_avail = True
            sauf : passer
        elif market_key == "double_chance_X2" and fused.get("p_away") is not None and fused.get("p_draw") is not None:
            essay: dix = float(fused["p_away"]) + float(fused["p_draw"]); dix_avail = True
            sauf : passer
        elif market_key == "double_chance_12" and fused.get("p_home") is not None and fused.get("p_away") is not None:
            essay: dix = float(fused["p_home"]) + float(fused["p_away"]); dix_avail = True
            sauf : passer
        autre:
            dk = {"victoire_home":"p_home","match_nul":"p_draw","victoire_away":"p_away","over_2.5":"over_2.5","under_2.5":"under_2.5","over_3.5":"over_3.5","over_1.5":"over_1.5","under_1.5":"under_1.5","btts_yes":"btts_yes","btts_no":"btts_no"}.get(market_key)
            si dk et fused.get(dk) n'est pas None :
                essayer : dix = float(fused.get(dk)); dix_avail = True
                sauf : passer

    si md.bzz_markets et md.bzz_markets.get(market_key) n'est pas None :
        try: bzz = float(md.bzz_markets[market_key]) / 100.0; bzz_avail = True
        sauf : passer

    total_w = 0,0; somme_pondérée = 0,0
    si dix_avail : total_w += WEIGHT_ALGO ; weighted_sum += dix * WEIGHT_ALGO
    si gem_avail : total_w += POIDS_GEMINI ; somme_pondérée += gem * POIDS_GEMINI
    si bzz_avail : total_w += WEIGHT_BZZOIRO ; weighted_sum += bzz * WEIGHT_BZZOIRO
    si total_w <= 0 : retourner 0.0, gem, dix, bzz, ""
    final = min(0,95, somme_pondérée / poids_total)
    sources = [x pour x dans (gem, dix, bzz) si x > 0]
    emoji = ""
    si len(sources) >= 2 :
        delta = max(sources) - min(sources)
        emoji = "🤝🤝" si len(sources) == 3 et delta <= 0,12 sinon "🤝" si delta <= 0,15 sinon "⚠️" si delta >= 0,20 sinon ""
    retour final, gemme, dix, bzz, emoji

def passes_value_filter(proba, odd, is_real, consensus_emoji=""):
    """Verrou global : filtrer les paris sans valeur en tenant compte de la marge bookmaker."""
    si proba <= 0 ou odd <= 0 :
        renvoyer Faux
    si consensus_emoji == "⚠️):
        ÉTAT["rejets"]["valeur"] += 1
        renvoyer Faux
    si is_real :
        ok = (probabilité * impair) >= (1.0 + MIN_EDGE)
        si ce n'est pas correct :
            ÉTAT["rejets"]["valeur"] += 1
        retour ok
    ok = (probabilité * impair) >= 0,995
    si ce n'est pas correct :
        ÉTAT["rejets"]["valeur"] += 1
    retour ok

def coupon_passes_ev(odds, prob, has_real_odds: bool = False):
    """Verrou global coupon : vérifier l'EV combinée minimale."""
    si la cote est inférieure ou égale à 0 ou si la probabilité est inférieure ou égale à 0 :
        renvoyer Faux
    min_ev = MIN_REAL_COUPON_EV si has_real_odds sinon MIN_COUPON_EV
    ok = (odds * prob) >= min_ev
    si ce n'est pas correct :
        ÉTAT["rejets"]["coupon_ev"] += 1
    retour ok

def passes_auth_proba_filter(proba):
    si proba <= 0 : retourner Faux
    ok = AUTH_PROBA_MIN <= proba <= AUTH_PROBA_MAX
    Si non ok : STATE["rejections"]["auth_proba"] += 1
    retour ok

# =====================================================================
# COTES APIS (ESPN + Bzzoiro + The Odds API + RapidOddsAPI)
# =====================================================================
def _american_to_decimal(ml) -> Optional[float]:
    """Convertit une cote américaine ESPN (+150 / -200 / EVEN) en cote décimale européenne."""
    si ml est None :
        renvoyer Aucun
    essayer:
        s = str(ml).strip().upper()
        si s est dans ("EVEN", "EV", "PK") :
            retour 2.00
        val = float(s.replace("+", ""))
        si val == 0 :
            renvoyer Aucun
        si val > 0 :
            déc = 1,0 + (valeur / 100,0)
        autre:
            déc = 1,0 + (100,0 / abs(val))
        retourner round(dec, 2) si dec > 1.01 sinon None
    sauf exception :
        renvoyer Aucun

def _parse_espn_odds_list(odds_list: list) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    h_impair = d_impair = a_impair = o25 = u25 = Aucun
    pour od dans (odds_list ou []):
        si ce n'est pas une instance de od, dict :
            continuer
        h_ml = (od.get("homeTeamOdds") ou {}).get("moneyLine")
        a_ml = (od.get("awayTeamOdds") ou {}).get("moneyLine")
        d_ml = (od.get("drawOdds") ou {}).get("moneyLine")
        si h_ml n'est pas None et n'est pas h_odd :
            h_odd = _american_to_decimal(h_ml)
        si a_ml n'est pas None et n'est pas a_odd :
            a_odd = _american_to_decimal(a_ml)
        si d_ml n'est pas None et n'est pas d_odd :
            d_odd = _american_to_decimal(d_ml)

        ou_line = od.get("overUnder")
        si ou_line n'est pas None et abs(float(ou_line) - 2.5) < 0.01 :
            ov_dec = _american_to_decimal(od.get("overOdds"))
            un_dec = _american_to_decimal(od.get("underOdds"))
            si ov_dec et non o25 :
                o25 = ov_dec
            si un_dec et non u25 :
                u25 = non déc

        si h_odd et a_odd et d_odd :
            casser
    renvoyer h_odd, d_odd, a_odd, o25, u25

ESPN_ODDS_SLUGS = [
    "tous",
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
    "eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "fra.1",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1",
    "conmebol.libertadores", "usa.1", "mex.1", "bra.1", "arg.1",
]

async def _fetch_espn_slug_date(client: httpx.AsyncClient, slug: str, d_str: str) -> list[dict]:
    articles = []
    essayer:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
        r = await client.get(url, params={"dates": d_str, "limit": 200})
        si r.status_code != 200 :
            retour []
        données = r.json()
        ligue_par_défaut = (data.get("leagues") ou [{}])[0].get("name") ou slug
        pour ev dans data.get("events", []):
            comps = ev.get("competitions", [])
            sinon compétitions :
                continuer
            comp = comps[0]
            st_type = comp.get("status", {}).get("type", {})
            si st_type.get("completed") ou st_type.get("state") dans ("in", "post") :
                continuer
            concurrents = comp.get("concurrents", [])
            home_c = next((c pour c dans les concurrents si c.get("homeAway") == "home"), None)
            away_c = next((c pour c dans les concurrents si c.get("homeAway") == "away"), None)
            si ce n'est pas home_c ou away_c :
                continuer
            home_name = home_c.get("team", {}).get("displayName") ou home_c.get("team", {}).get("name") ou ""
            away_name = away_c.get("team", {}).get("displayName") ou away_c.get("team", {}).get("name") ou ""
            si ce n'est pas home_name ou away_name :
                continuer
            lg_name = (ev.get("league") ou {}).get("name") ou default_league
            articles.append({
                "raw_id": str(ev.get("id", "")),
                "limace" : limace,
                "ligue" : nom_lg,
                "home": nom_du_home,
                "absent" : nom_absent,
                "date" : ev.get("date", ""),
                "odds" : comp.get("odds") ou [],
            })
    sauf exception :
        passer
    articles de retour

async def _fetch_espn_event_summary_odds(client: httpx.AsyncClient, slug: str, raw_id: str) -> liste:
    essayer:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary"
        r = await client.get(url, params={"event": raw_id})
        si r.status_code != 200 :
            retour []
        données = r.json()
        renvoyer data.get("pickcenter") ou data.get("odds") ou []
    sauf exception :
        retour []

async def fetch_espn_prematch_odds(days_ahead: int = 3) -> list[WS_MatchData]:
    """Récupère gratuitement les vraies cotes bookmaker d'avant-match via ESPN (scoreboard + pickcenter, 0 €)."""
    maintenant = datetime.now(TZ)
    date_strings = [(now + timedelta(days=d)).strftime("%Y%m%d") for d in range(max(1, days_ahead + 1))]
    ws_map = {}
    en-têtes = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
        "Accepter": "application/json",
    }

    asynchrone avec httpx.AsyncClient(timeout=12.0, headers=headers) comme client :
        tâches = [_fetch_espn_slug_date(client, slug, d_str) pour slug dans ESPN_ODDS_SLUGS pour d_str dans date_strings]
        lots = await asyncio.gather(*tâches, return_exceptions=True)

        candidats_sans_cotes = []
        pour lot par lots :
            si ce n'est pas une instance de (batch, list) :
                continuer
            pour l'article du lot :
                ev_id = f"espn_{item['raw_id']}"
                si ev_id est dans ws_map :
                    continuer
                h_odd, d_odd, a_odd, o25, u25 = _parse_espn_odds_list(item["odds"])
                si h_odd et a_odd :
                    ws_map[ev_id] = WS_MatchData(
                        match_id=ev_id,
                        ligue=élément["ligue"],
                        équipe_domicile=élément["domicile"],
                        équipe_extérieure=élément["extérieur"],
                        cotes_domicile=h_odd,
                        cotes_tirage=d_impair,
                        cotes_à_l'écart=a_impair,
                        over_2_5=o25,
                        sous_2_5=u25,
                        commencer_heure=élément["date"],
                    )
                elif item["raw_id"] and item["slug"] != "all":
                    candidats_sans_cotes.ajouter(élément)

        si candidats_sans_chances :
            vu_raw = set()
            unique_missing = []
            pour cela dans candidats_sans_chances :
                si it["raw_id"] n'est pas dans seen_raw et f"espn_{it['raw_id']}" n'est pas dans ws_map :
                    vu_raw.ajouter(it["raw_id"])
                    unique_missing.append(it)
            summary_tasks = [_fetch_espn_event_summary_odds(client, it["slug"], it["raw_id"]) for it in unique_missing[:35]]
            summary_results = await asyncio.gather(*summary_tasks, return_exceptions=True)
            pour cela, s_odds dans zip(unique_missing[:35], summary_results) :
                si ce n'est pas une instance de s_odds, liste, ou si s_odds n'est pas :
                    continuer
                h_odd, d_odd, a_odd, o25, u25 = _parse_espn_odds_list(s_odds)
                si h_odd et a_odd :
                    ev_id = f"espn_{it['raw_id']}"
                    ws_map[ev_id] = WS_MatchData(
                        match_id=ev_id,
                        ligue=it["ligue"],
                        équipe_domicile=it["domicile"],
                        équipe_extérieure=elle["extérieur"],
                        cotes_domicile=h_odd,
                        cotes_tirage=d_impair,
                        cotes_à_l'écart=a_impair,
                        over_2_5=o25,
                        sous_2_5=u25,
                        commencer_heure=it["date"],
                    )

    res = liste(ws_map.values())
    print(f"🆓 ESPN Vraies Cotes Gratuites : {len(res)} matchs récupérés (0 €)")
    retour res

async def fetch_bzzoiro_free_odds(days_ahead: int = 3) -> list[WS_MatchData]:
    """Récupère les vraies cotes bookmaker gratuites déjà incluses dans l'API Bzzoiro (/api/events/ et /api/predictions/)."""
    sinon BZZOIRO_API_KEY :
        retour []
    maintenant = datetime.now(TZ)
    d_from = maintenant.strftime("%Y-%m-%d")
    d_to = (maintenant + timedelta(jours=jours_à_l'avance)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"Token {BZZOIRO_API_KEY}"}
    ws_map = {}

    asynchrone avec httpx.AsyncClient(timeout=15.0) comme client :
        pour chaque point de terminaison dans ("https://sports.bzzoiro.com/api/events/", "https://sports.bzzoiro.com/api/predictions/") :
            essayer:
                r = await client.get(endpoint, headers=headers, params={"date_from": d_from, "date_to": d_to, "limit": 250})
                si r.status_code != 200 :
                    continuer
                payload = r.json()
                items = payload.get("results", payload) si isinstance(payload, dict) sinon payload
                si les éléments ne sont pas une instance de la liste :
                    continuer
                pour les matières premières dans les articles :
                    ev = raw.get("event") si isinstance(raw.get("event"), dict) sinon raw
                    h = ev.get("home_team") ou raw.get("home_team") ou ""
                    a = ev.get("away_team") ou raw.get("away_team") ou ""
                    si h est une instance de dict : h = h.get("name", "")
                    si isinstance(a, dict): a = a.get("name", "")
                    si ce n'est pas h ou ce n'est pas a :
                        continuer
                    lg = ev.get("league") ou raw.get("league") ou ""
                    si lg est une instance de dict : lg = lg.get("name", "")

                    def _f(v):
                        essayer:
                            fv = float(v)
                            retourner round(fv, 2) si fv > 1.01 sinon None
                        sauf exception :
                            renvoyer Aucun

                    h_odd = _f(ev.get("odds_home") ou raw.get("odds_home") ou ev.get("home_odds"))
                    d_odd = _f(ev.get("odds_draw") ou raw.get("odds_draw") ou ev.get("draw_odds"))
                    a_odd = _f(ev.get("odds_away") ou raw.get("odds_away") ou ev.get("away_odds"))
                    o25 = _f(ev.get("odds_over_25") or raw.get("odds_over_25") or ev.get("over_2_5_odds"))
                    u25 = _f(ev.get("odds_under_25") or raw.get("odds_under_25") or ev.get("under_2_5_odds"))
                    b_yes = _f(ev.get("odds_btts_yes") ou raw.get("odds_btts_yes"))
                    b_no = _f(ev.get("odds_btts_no") ou raw.get("odds_btts_no"))

                    si h_odd et a_odd :
                        nk = f"{normalize_name(h)}__{normalize_name(a)}"
                        si nk n'est pas dans ws_map :
                            ws_map[nk] = WS_MatchData(
                                match_id=str(ev.get("id") ou raw.get("id") ou nk),
                                ligue=str(lg),
                                équipe_domicile=str(h),
                                équipe_extérieure=str(a),
                                cotes_domicile=h_odd,
                                cotes_tirage=d_impair,
                                cotes_à_l'écart=a_impair,
                                over_2_5=o25,
                                sous_2_5=u25,
                                btts_oui=b_oui,
                                btts_no=b_no,
                                commencer_time=str(ev.get("event_date") or ev.get("commence_time") or ""),
                            )
            sauf exception :
                continuer

    res = liste(ws_map.values())
    print(f"🆓 Bzzoiro Vraies Cotes Gratuites : {len(res)} matchs récupérés (0 €)")
    retour res

async def fetch_rapidodds_odds() -> Tuple[list, str]:
    """Fusionne ESPN (scoreboard + pickcenter) + Bzzoiro gratuit + The Odds API gratuit (1 crédit/jour)."""
    ws_by_key = {}

    essayer:
        espn_ws = await fetch_espn_prematch_odds(days_ahead=1)
        pour chaque élément dans espn_ws :
            nk = f"{normalize_name(item.home_team)}__{normalize_name(item.away_team)}"
            ws_by_key[nk] = élément
    sauf Exception comme e :
        print(f"⚠️ Erreur de cote ESPN : {e}")

    essayer:
        bzz_ws = await fetch_bzzoiro_free_odds(days_ahead=1)
        pour chaque élément dans bzz_ws :
            nk = f"{normalize_name(item.home_team)}__{normalize_name(item.away_team)}"
            existant = ws_by_key.get(nk)
            s'il existe :
                Si existing.draw_odds et item.draw_odds ne sont pas présents : existing.draw_odds = item.draw_odds
                Si les valeurs `existing.over_2_5` et `item.over_2_5` ne sont pas présentes : `existing.over_2_5` = `item.over_2_5`
                Si les valeurs `existing.under_2_5` et `item.under_2_5` ne sont pas présentes : `existing.under_2_5` = `item.under_2_5`
                Si existing.btts_yes et item.btts_yes ne sont pas présents : existing.btts_yes = item.btts_yes
                Si existing.btts_no et item.btts_no ne sont pas renseignés : existing.btts_no = item.btts_no
            autre:
                ws_by_key[nk] = élément
    sauf Exception comme e :
        print(f"⚠️ Bzzoiro Odds erreur : {e}")

    si THE_ODDS_API_KEY :
        essayer:
            asynchrone avec httpx.AsyncClient(timeout=15.0) comme client :
                r = await client.get(
                    "https://api.the-odds-api.com/v4/sports/upcoming/odds",
                    params={
                        "apiKey" : THE_ODDS_API_KEY,
                        "régions" : "eu,uk",
                        "marchés" : "h2h,totaux",
                        "oddsFormat": "décimal",
                    },
                )
                si r.status_code == 200 :
                    rem = r.headers.get("x-requests-remaining", "?")
                    ajouté = 0
                    pour ev dans r.json():
                        si la chaîne `ev.get("sport_key", "")` ne commence pas par `soccer_` :
                            continuer
                        domicile = ev.get("équipe_domicile", "")
                        loin = ev.get("équipe_extérieure", "")
                        h_impair = d_impair = a_impair = o25 = u25 = Aucun

                        pour bk dans ev.get("bookmakers", []):
                            pour mkt dans bk.get("markets", []):
                                si mkt.get("key") == "h2h":
                                    pour out dans mkt.get("outcomes", []):
                                        si out.get("name") == home et non h_odd:
                                            h_odd = float(out["price"])
                                        elif out.get("name") == away and not a_odd:
                                            a_odd = float(out["price"])
                                        elif out.get("name") == "Draw" and not d_odd:
                                            d_odd = float(out["price"])
                                elif mkt.get("key") == "totals":
                                    pour out dans mkt.get("outcomes", []):
                                        si float(out.get("point", 0)) == 2.5:
                                            si out.get("name") == "Over" et non o25 :
                                                o25 = float(out["price"])
                                            elif out.get("name") == "Under" and not u25:
                                                u25 = float(out["price"])
                            si h_odd et a_odd et d_odd et o25 et u25 :
                                casser

                        si h_odd et a_odd :
                            nk = f"{normalize_name(home)}__{normalize_name(away)}"
                            existant = ws_by_key.get(nk)
                            s'il existe :
                                Si existing.draw_odds et d_odd ne sont pas définis : existing.draw_odds = d_odd
                                Si existing.over_2_5 n'existe pas et o25 : existing.over_2_5 = o25
                                Si existing.under_2_5 et u25 n'existent pas : existing.under_2_5 = u25
                            autre:
                                ws_by_key[nk] = WS_MatchData(
                                    match_id=str(ev.get("id", "")),
                                    ligue=ev.get("sport_title", ""),
                                    équipe à domicile=domicile,
                                    équipe_extérieure=extérieur,
                                    cotes_domicile=h_odd,
                                    cotes_tirage=d_impair,
                                    cotes_à_l'écart=a_impair,
                                    over_2_5=o25,
                                    sous_2_5=u25,
                                    commencer_time=ev.get("commence_time", ""),
                                )
                                ajouté += 1
                    print(f"✅ L'API Odds (1 req/jour) : +{added} matchs (crédits restants : {rem})")
        sauf Exception comme e :
            print(f"⚠️ Erreur de l'API Odds : {e}")

    si RAPIDODDSAPI_API_KEY et fetch_rapidodds_odds_data :
        essayer:
            événements = attendre fetch_rapidodds_odds_data()
            pour e dans (événements ou []):
                nk = f"{normalize_name(e['home_team'])}__{normalize_name(e['away_team'])}"
                si nk n'est pas dans ws_by_key et e.get("home_odds") et e.get("away_odds") :
                    ws_by_key[nk] = WS_MatchData(e["match_id"], e.get("league", ""), e["home_team"], e["away_team"], e.get("home_odds"), e.get("draw_odds"), e.get("away_odds"), e.get("over_2_5"), e.get("under_2_5"), e.get("btts_yes"), e.get("btts_no"), e.get("commence_time", ""))
        sauf Exception comme e :
            print(f"⚠️ Erreur RapidOddsAPI : {e}")

    ws = liste(ws_by_key.values())
    renvoyer ws, ("live" si ws sinon "fallback")

def inject_all_real_odds(pool, ws_matches):
    correspondant = 0
    pour md dans la piscine :
        had_initial_odds = bool(md.real_odds)
        pour ws dans ws_matches :
            si teams_match(md.home, ws.home_team) et teams_match(md.away, ws.away_team) :
                si ws.home_odds : md.real_odds["home"] = ws.home_odds
                si ws.draw_odds : md.real_odds["draw"] = ws.draw_odds
                si ws.away_odds : md.real_odds["away"] = ws.away_odds
                si ws.over_2_5 : md.real_odds["over_2.5"] = ws.over_2_5
                si ws.under_2_5 : md.real_odds["under_2.5"] = ws.under_2_5
                si ws.btts_yes : md.real_odds["btts_yes"] = ws.btts_yes
                si ws.btts_no : md.real_odds["btts_no"] = ws.btts_no
                si ws.commence_time : md.kickoff = ws.commence_time
                casser
        _assurer_dixon_pred(md)
        si bool(md.real_odds) ou had_initial_odds :
            apparié += 1
    retour correspondant

# =====================================================================
# DOUBLE SCAN & ENRICHISSEMENT IA COMPLET
# =====================================================================
def _build_quant_analysis(md: MatchData, pick_label: str, proba_pct: float, odd: float) -> str:
    """Génère une synthèse statistique chiffrée (xG, Dixon-Coles, Bzzoiro, EV) si Gemini est indisponible."""
    _assurer_dixon_pred(md)
    dp = md.dixon_pred ou {}
    lh = dp.get("lambda_home")
    la = dp.get("lambda_away")
    ev = arrondi((proba_pct / 100.0) * impair, 2)
    parties = []
    si lh n'est pas None et la n'est pas None :
        xg_tot = arrondi(float(lh) + float(la), 2)
        si float(lh) >= float(la) + 0,35 :
            parts.append(f"{md.home} affiche un avantage statistique net à domicile (xG {float(lh):.2f} contre {float(la):.2f} pour {md.away}, total attendu {xg_tot:.2f} buts).")
        elif float(la) >= float(lh) + 0.35:
            parts.append(f"{md.away} présente une supériorité offensive marquée en déplacement (xG {float(la):.2f} contre {float(lh):.2f} pour {md.home}, total attendu {xg_tot:.2f} buts).")
        autre:
            parts.append(f"Duel équilibré sur les projections Dixon-Coles avec un rythme attendu de {xg_tot:.2f} buts (xG {float(lh):.2f} - {float(la):.2f}) en {md.league}.")
    autre:
        parts.append(f"Analyse croisée Dixon-Coles et Bzzoiro sur cette affiche de {md.league} entre {md.home} et {md.away}.")

    bzz_note = "avec convergence des modèles Bzzoiro" if md.bzz_markets else "selon la modélisation Poisson et les cotes du marché"
    h2h_note = " et l'historique des confrontations directes" if md.has_h2h else ""
    parts.append(f"La probabilité probable à {proba_pct:.1f}% {bzz_note}{h2h_note} confirme la solidité du scénario.")
    parts.append(f"À une cote bookmaker de {odd:.2f} (EV {ev:.2f}), l'option {pick_label} offre une valeur statistique cohérente.")
    retourner " ".join(parts)

async def _fetch_gemini_analysis_for_match(client: httpx.AsyncClient, md: MatchData) -> Optional[dict]:
    _assurer_dixon_pred(md)
    ctx = {}
    si enrich_match_with_bzzoiro :
        essayer:
            date_iso = parse_dt_safe(md.kickoff).strftime("%Y-%m-%d") si parse_dt_safe(md.kickoff) sinon datetime.now(TZ).strftime("%Y-%m-%d")
            bzz_ctx = await enrich_match_with_bzzoiro(md.home, md.away, date_iso)
            si isinstance(bzz_ctx, dict) :
                ctx.update(bzz_ctx)
                si bzz_ctx.get("disponible"):
                    md.has_h2h = Vrai
        sauf exception :
            passer
    si md.dixon_pred :
        ctx["dixon_coles"] = md.dixon_pred
    si md.real_odds :
        ctx["bookmaker_odds"] = md.real_odds
    si md.bzz_markets :
        ctx["bzzoiro_probas"] = md.bzz_markets

    si predict_match_probas :
        pred = await predict_match_probas(client, md.home, md.away, md.league, md.kickoff, context=ctx)
        si pred et isinstance(pred, dict) et ("markets" dans pred ou pred.get("analysis")) :
            return {"markets": pred.get("markets", {}), "analysis": str(pred.get("analysis") or "").strip()}
    renvoyer Aucun

async def _enrich_selected_legs_ai(combo: tuple | list) -> bool:
    ""
    Enrichit et valide chaque sélection retenue dans le ticket final via Gemini
    en alignant l'analyse à 100 % avec le pari exact choisi. Retourne False si un piège est détecté.
    ""
    asynchrone avec httpx.AsyncClient(timeout=55.0) comme client :
        pour c dans combo :
            md : Optional[MatchData] = c.get("_md") ou c.get("match")
            sinon médecin :
                continuer
            étiquette_choisir = c.get("type", "")
            proba_pct = float(c.get("proba", 70.0))
            impair = float(c.get("odds", 1.35))

            si explain_selected_pick_with_gemini :
                essayer:
                    _assurer_dixon_pred(md)
                    ctx = {}
                    si md.dixon_pred :
                        ctx["dixon_coles"] = md.dixon_pred
                    si md.real_odds :
                        ctx["bookmaker_odds"] = md.real_odds
                    si md.bzz_markets :
                        ctx["bzzoiro_probas"] = md.bzz_markets

                    enrichi = attendre explain_selected_pick_with_gemini(
                        client=client,
                        domicile=md.home,
                        loin=md.loin,
                        ligue=md.ligue,
                        coup d'envoi=md.coup d'envoi,
                        étiquette_choisir=étiquette_choisir,
                        proba_pct=proba_pct,
                        impair = impair,
                        contexte=ctx,
                    )
                    si enrichi :
                        si enriched.get("is_safe", True) :
                            print(f" 🚩 Gemini rejette le pari '{pick_label}' sur {md.home} vs {md.away} (piège détecté)")
                            renvoyer Faux
                        analysis_txt = enrichi.get("analysis", "").strip()
                        si has_pro_red_flags(analysis_txt, md.league)[0] :
                            print(f" 🚩 Signal rouge dans l'analyse finale sur {md.home} vs {md.away}")
                            renvoyer Faux
                        si la longueur de analysis_txt est supérieure ou égale à 80 :
                            c["ai"] = texte d'analyse
                            print(f"🧠 Analyse Gemini ciblée validée : {md.home} vs {md.away} -> {pick_label}")
                            attendre asyncio.sleep(1.2)
                            continuer
                sauf Exception comme e :
                    print(f"⚠️ Enrichissement ciblé ({md.home} vs {md.away}) : {e}")

            si c.get("ai") et len(str(c.get("ai")).strip()) >= 60 :
                si has_pro_red_flags(str(c.get("ai")), md.league)[0]:
                    renvoyer Faux
                continuer

            si md.analysis et len(md.analysis.strip()) >= 60 :
                si has_pro_red_flags(md.analysis, md.league)[0] :
                    renvoyer Faux
                c["ai"] = md.analysis.strip()
                continuer

            essayer:
                pred = await _fetch_gemini_analysis_for_match(client, md)
                si pred et pred.get("analysis") et len(pred["analysis"].strip()) >= 60 :
                    md.analyse = pred["analyse"].strip()
                    si pred.get("markets") et non md.gemini_markets :
                        md.gemini_markets = pred["markets"]
                    si has_pro_red_flags(md.analysis, md.league)[0] :
                        renvoyer Faux
                    c["ai"] = analyse MD
                    print(f"🧠 Enrichissement IA ciblé réussi : {md.home} vs {md.away}")
                    attendre asyncio.sleep(1.5)
                    continuer
            sauf Exception comme e :
                print(f"⚠️ Enrichissement IA ciblé ({md.home} vs {md.away}) : {e}")

            c["ai"] = _build_quant_analysis(md, pick_label, proba_pct, odd)
    renvoyer Vrai

async def _enrich_single_pass(targets, pass_num):
    résultats = {}
    erreurs = 0
    asynchrone avec httpx.AsyncClient(timeout=70.0) comme client :
        pour md dans les cibles :
            essayer:
                pred = await _fetch_gemini_analysis_for_match(client, md)
                si prédateur :
                    résultats[md.event_id] = prédit
                    erreurs = 0
                autre:
                    erreurs += 1
                attendre asyncio.sleep(2.0)
            sauf Exception comme e :
                erreurs += 1
                print(f"⚠️ Gemini Pass {pass_num} ({md.home} vs {md.away}) : {e}")
                si erreurs >= 3 :
                    print(f"🛑 Gemini Pass {pass_num} : arrêt anticipé (erreurs consécutives).")
                    casser
    print(f"🤖 Gemini Pass {pass_num} : {len(results)}/{len(targets)} matchs analysés")
    résultats de retour

def _compare_two_passes(targets, r1, r2):
    stable_list, enriched_list, stats = [], [], {"stable": 0, "unstable": 0, "single": 0, "none": 0}
    pour md dans les cibles :
        p1, p2 = r1.get(md.event_id), r2.get(md.event_id)
        Si ni p1 ni p2 ne sont vrais : stats["none"] += 1 ; md.stability_flag = "no_gemini" ; continuer
        Si p1 et non p2 : md.gemini_markets = p1["markets"] ; md.analysis = p1["analysis"] ; md.stability_flag = "single" ; stats["single"] += 1 ; enriched_list.append(md) ; continuer
        Si p2 et non p1 : md.gemini_markets = p2["markets"] ; md.analysis = p2["analysis"] ; md.stability_flag = "single" ; stats["single"] += 1 ; enriched_list.append(md) ; continuer
        est_stable, fusionné = Vrai, {}
        pour k dans set(p1["markets"].keys()) | set(p2["markets"].keys()):
            v1, v2 = p1["markets"].get(k), p2["markets"].get(k)
            si v1 est None et v2 n'est pas None : merged[k] = v2 ; is_stable = False ; continuer
            si v2 est None et v1 n'est pas None : merged[k] = v1 ; is_stable = False ; continuer
            si v1 est None et v2 est None : continuer
            essayer : f1, f2 = float(v1), float(v2)
            sauf : merged[k] = v1 ; continuer
            si abs(f1 - f2) / 100.0 > DOUBLE_SCAN_STABILITY_DELTA : merged[k] = min(f1, f2) ; is_stable = False
            sinon : fusionné[k] = (f1 + f2) / 2.0
        md.gemini_markets = fusionné
        a1, a2 = p1.get("analysis", "") ou "", p2.get("analysis", "") ou ""
        base = a1 si len(a1) >= len(a2) sinon a2
        Si is_stable : md.analysis = base ; md.stability_flag = "stable" ; stats["stable"] += 1 ; stable_list.append(md)
        sinon : md.analysis = f"⚠️ Variable. {base[:280]}"; md.stability_flag = "instable"; stats["instable"] += 1
        liste_enrichie.ajouter(md)
    renvoie stable_list, enriched_list, stats

async def enrichir_avec_double_passe(pool):
    Si la piscine n'est pas vide : STATE["double_scan_status"] = "EMPTY" ; retourner la piscine
    cibles = pool[:max(DOUBLE_SCAN_POOL_SIZE, min(len(pool), GEMINI_MAX_CALLS_PER_SCAN))]
    r1 = await _enrich_single_pass(targets, 1)
    attendre asyncio.sleep(DOUBLE_SCAN_PAUSE_SECONDS)
    r2 = await _enrich_single_pass(targets, 2)
    stable, enrichi, stats = _compare_two_passes(targets, r1, r2)
    ÉTAT["double_scan_stats"] = stats
    print(f"🔬 Statistiques de double analyse : {stats}")
    si len(stable) >= DOUBLE_SCAN_MIN_STABLE :
        ÉTAT["double_scan_status"] = f"STRICT ({len(stable)})"
        retour stable
    si enrichi :
        ÉTAT["double_scan_status"] = f"FALLBACK ({len(stable)}/{len(enriched)})"
        retour enrichi
    ÉTAT["double_scan_status"] = "GEMINI_KO"
    objectifs de retour

# =====================================================================
# MOTEUR EXCLUSIF VIP : COTES +3 AUTH (PLAGE 2.00 - 3.50 • DIVERSITÉ PRO)
# =====================================================================
def _build_auth_coupon(combo, subtitle, quality) -> Coupon :
    sorted_combo = sorted(combo, key=lambda c: parse_dt_safe(c.get("kickoff")) or datetime.max.replace(tzinfo=TZ))
    cotes_totales = math.prod(c["cotes"] pour c dans combinaison_triée)
    total_prob = math.prod(c["proba"] / 100.0 pour c dans sorted_combo)
    jambes = [
        Sélection(
            event_id=c["event_id"],
            ligue=c["ligue"],
            maison=c["maison"],
            loin=c["loin"],
            coup d'envoi=c["coup d'envoi"],
            marché=c["type"],
            étiquette_choisir=c["type"],
            cotes=c["cotes"],
            model_prob=c["proba"] / 100.0,
            réel_odd=c["réel_odd"],
            qualité = qualité,
            analyse=clean_analysis_text(c["ai"], 560),
        )
        pour c dans sorted_combo
    ]
    return Coupon("Odds +3 AUTH", f"{subtitle} • {len(legs)} sélections", "🛡️", legs, round(total_odds, 2), round(total_prob, 4), show_analysis=True)

def _gemini_alignment_bonus(md: MatchData, mk: str) -> int:
    """Privilégie les marchés confirmés en tête des probabilités Gemini."""
    si ce n'est pas md.gemini_markets ou si ce n'est pas une instance de dict :
        renvoyer 0
    val = md.gemini_markets.get(mk)
    si val est None :
        renvoyer 0
    essayer:
        v = float(val)
    sauf (ValueError, TypeError) :
        renvoyer 0
    si v >= 74,0 :
        retour 2
    si v >= 66,0 :
        retour 1
    renvoyer 0

def _valid_pro_combo(combo, require_anchor: bool = True, strict_diversity: bool = True) -> bool:
    si len({c["norm_key"] pour c dans combo}) != len(combo):
        renvoyer Faux
    # Jamais 2 fois exactement le même type de pari dans un même ticket
    si len({c.get("mk") for c in combo}) != len(combo):
        renvoyer Faux
    si la somme(1 pour c dans combo si c["famille"] == "objectifs") > 1 :
        renvoyer Faux
    si la somme(1 pour c dans combo si c["famille"] == "btts") > 1 :
        renvoyer Faux
    si la somme(1 pour c dans combo si c.get("mk") == "victoire_away") > 1 :
        renvoyer Faux
    si la diversité stricte :
        # Exige des pronostics variés (au moins 2 familles différentes : Résultat sec + Buts / Double Chance / BTTS)
        si len(combo) >= 2 et len({c["family"] pour c dans combo}) < 2 :
            renvoyer Faux
        si la somme(1 pour c dans combo si c["famille"] == "dc") > 1 :
            renvoyer Faux
    si require_anchor et aucun(c["proba"] >= 69.0 ou (c["family"] == "result" et c["proba"] >= 64.0) pour c dans combo):
        renvoyer Faux
    renvoyer Vrai

async def build_exclusive_vip_auth_coupon(enriched_pool: List[MatchData], ws_matches: List[WS_MatchData]) -> Tuple[Optional[Coupon], set]:
    vip_candidates = []

    cartes_des_marchés = [
        ("victoire_home", "home", "result", lambda m: f"Victoire {m.home} (1)"),
        ("victoire_away", "away", "result", lambda m: f"Victoire {m.away} (2)"),
        ("over_2.5", "over_2.5", "goals", lambda m: "Plus de 2.5 buts"),
        ("under_2.5", "under_2.5", "goals", lambda m : "Moins de 2.5 buts"),
        ("btts_yes", "btts_yes", "btts", lambda m: "Les 2 équipes marquent (BTTS Oui)"),
        ("double_chance_1X", "dc_1x", "dc", lambda m: f"Double Chance {m.home} ou Nul (1X)"),
        ("double_chance_X2", "dc_x2", "dc", lambda m: f"Double Chance Nul ou {m.away} (X2)"),
        ("over_1.5", "over_1.5", "goals", lambda m: "Plus de 1.5 buts"),
        ("double_chance_12", "dc_12", "dc12", lambda m: "Une équipe gagne (12)"),
    ]

    pour md dans enriched_pool :
        si ce n'est pas md.home ou ce n'est pas md.away ou ce n'est pas is_match_today(md.kickoff) :
            continuer
        si is_reserve_team(md.home) ou is_reserve_team(md.away) :
            continuer
        _assurer_dixon_pred(md)
        si ce n'est ni md.dixon_pred, ni md.gemini_markets, ni md.bzz_markets, ni md.real_odds :
            continuer

        si getattr(md, "stability_flag", "") == "unstable":
            ÉTAT["rejets"]["instable"] += 1
            print(f" ⚠️ Double-Scan rejet ({md.home} vs {md.away}) : analyse instable (>16% d'écart)")
            continuer

        est_piège, raison_piège = a_des_signaux_rouges_pro(md.analyse, md.ligue)
        si is_trap :
            ÉTAT["rejets"]["pro_trap"] += 1
            print(f" 🚩 Pro-Filter rejet ({md.home} vs {md.away}) : signal '{trap_reason}'")
            continuer

        si md.real_odds.get("home") et md.real_odds.get("draw") et non md.real_odds.get("dc_1x") :
            inv_1x = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["draw"]))
            si inv_1x > 0 :
                md.real_odds["dc_1x"] = round(max(1.03, (1.0 / inv_1x) * 0.96), 2)
        si md.real_odds.get("away") et md.real_odds.get("draw") et non md.real_odds.get("dc_x2") :
            inv_x2 = (1.0 / float(md.real_odds["away"])) + (1.0 / float(md.real_odds["draw"]))
            si inv_x2 > 0 :
                md.real_odds["dc_x2"] = round(max(1.03, (1.0 / inv_x2) * 0.96), 2)
        si md.real_odds.get("home") et md.real_odds.get("away") et non md.real_odds.get("dc_12") :
            inv_12 = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["away"]))
            si inv_12 > 0 :
                md.real_odds["dc_12"] = round(max(1.03, (1.0 / inv_12) * 0.96), 2)

        p_home_est, g_h, _, _, em_h = calculer_probabilité(md, "victoire_home")
        p_away_est, g_a, _, _, em_a = calculer_probabilité(md, "victoire_away")
        match_unilatéral = (p_domicile_est >= 0,74) ou (p_extérieur_est >= 0,74)

        clear_home_win = (p_home_est >= 0,62 et (p_home_est - p_away_est) >= 0,28 et em_h != "⚠️")
        clear_away_win = (p_away_est >= 0,63 et (p_away_est - p_home_est) >= 0,30 et em_a != "⚠️")

        options_match = []
        pour mk, odd_key, family, label_fn dans markets_map :
            si mk == "btts_yes" et one_sided_match :
                continuer

            si mk == "double_chance_1X" :
                si g_a > 0 et g_h > 0 et g_a > (g_h + 0,05) :
                    continuer
                si p_away_est > (p_home_est + 0.05) :
                    continuer
            elif mk == "double_chance_X2":
                si g_h > 0 et g_a > 0 et g_h > (g_a + 0,05) :
                    continuer
                si p_home_est > (p_away_est + 0.05) :
                    continuer
            elif mk == "double_chance_12":
                si clear_home_win ou clear_away_win :
                    continuer
                p_draw_est, _, _, _, _ = compute_probability(md, "match_nul")
                si p_draw_est >= 0,27 :
                    continuer

            prob, g, d, b, emoji = calculer_probabilité(md, mk)
            proba_pct = prob * 100.0

            si emoji == "⚠️":
                continuer

            si mk dans ("victoire_home", "victoire_away") et (clear_home_win ou clear_away_win) :
                si proba_pct < 61,0 ou proba_pct > AUTH_PROBA_MAX :
                    ÉTAT["rejets"]["auth_proba"] += 1
                    continuer
            autre:
                si pas passes_auth_proba_filter(proba_pct) :
                    continuer

            si mk == "victoire_away" et p_away_est < 0,64 :
                continuer

            cote_réelle = md.cotes_réelles.get(clé_impaire)
            si ce n'est pas real_odd ou float(real_odd) <= 1.01 :
                ÉTAT["rejets"]["aucune_vrais_chance"] += 1
                continuer
            impair = arrondi(float(réel_impair), 2)
            est_réel = Vrai

            si impair < ​​ODDS3_MIN_COTE_LEG :
                continuer
            si pas passes_value_filter(prob, odd, is_real, emoji) :
                continuer

            consensus_bonus = 2 si emoji == "🤝🤝" sinon (1 si emoji == "🤝" sinon 0)
            bonus_gemini = bonus_alignement_gemini(md, mk)
            stakes_bonus = compute_stakes_score(md.league, md.analysis)
            priorité_marché = 3 si famille == "résultat" sinon (2 si famille dans ("objectifs", "btts", "dc") sinon 1)
            score_rang = (bonus_gemini, priorité_marché, bonus_consensus, bonus_enjeux, arrondi(pourcentage_proba, 1))

            match_options.append({
                "event_id": md.event_id,
                "norm_key": f"{normalize_name(md.home)}__{normalize_name(md.away)}",
                "ligue" : md.ligue,
                "home": md.home,
                "absent" : md.absent,
                "coup d'envoi" : md.coup d'envoi,
                "mk": mk,
                "type": label_fn(md),
                "famille" : famille,
                "odds" : impair,
                "proba": proba_pct,
                "real_odd" : real_odd,
                "consensus" : emoji ou "🤝",
                "ai": (md.analysis ou "").strip(),
                "_md): md,
                "_rang" : score_rang,
            })

        match_options.sort(clé=lambda x: x["_rank"], reverse=True)
        familles_gardées = ensemble()
        pour l'option match_options :
            si opt["family"] est dans kept_families :
                continuer
            si opt.get("mk") == "double_chance_12" et len(kept_families) >= 1 :
                continuer
            kept_families.add(opt["famille"])
            opt.pop("_rank", None)
            vip_candidates.append(opt)
            si len(kept_families) >= 2 :
                casser

    vip_candidates.sort(
        clé=lambda x: (
            _gemini_alignment_bonus(x["_md"], x["mk"]),
            3 si x["famille"] == "résultat" sinon (2 si x["famille"] dans ("objectifs", "btts", "dc") sinon 1),
            calculer_score_enjeux(x["ligue"], x["ai"]),
            x["proba"],
        ),
        inverse=Vrai,
    )
    top_pool = vip_candidates[:30]

    pour strict_div dans (Vrai, Faux) :
        pour r dans (3, 2, 4) :
            si len(top_pool) < r :
                continuer
            combinaisons_valides = []
            pour chaque combinaison dans itertools.combinations(top_pool, r) :
                si ce n'est pas _valid_pro_combo(combo, require_anchor=True, strict_diversity=strict_div) :
                    continuer
                tot_o = round(math.prod(c["odds"] pour c dans combo), 2)
                tot_p = math.prod(c["proba"] / 100.0 pour c dans combo)
                a_réel = n'importe quel(c["réel_impair"] n'est pas None pour c dans combo)
                si ODDS3_AUTH_MIN_COTE <= tot_o <= ODDS3_AUTH_MAX_COTE et tot_p >= AUTH_MIN_COMBINED_PROB :
                    si coupon_passes_ev(tot_o, tot_p, has_real_odds=has_real) :
                        continuer
                    valid_combos.append((tot_p, combo))

            valid_combos.sort(key=lambda item: item[0], reverse=True)
            pour _, combo dans valid_combos :
                is_clean = await _enrich_selected_legs_ai(combo)
                si ce n'est pas is_clean :
                    print(" 🚩 Combo rejeté après enrichissement Gemini ciblé (match piège détecté)")
                    continuer
                touches_verrouillées = {c["touche_norme"] pour c dans combo}
                label = "Pyramide Sécurisée" si r == 3 sinon ("Duo Haute Confiance" si r == 2 sinon "Quatuor Haute Confiance")
                return _build_auth_coupon(combo, f"Sélection Pro VIP • {label}", 96), locked_keys

    print(" ℹ️ AUTH VIP : Aucune combinaison valide trouvée dans la plage 2.00 - 3.50 aujourd'hui (protection bankroll).")
    renvoie None, set()

# =====================================================================
# MOTEURS SECONDAIRES (ALIGNÉS SUR LA MÊME RIGUEUR PRO)
# =====================================================================
def _poisson_sample(lam):
    si lam <= 0 : retourner 0
    L = math.exp(-lam); k, p = 0, 1.0
    tant que p > L et k < 20 : k += 1 ; p *= random.random()
    retourner k - 1

def _binomial_sample(n, p): return sum(1 for _ in range(n) if random.random() < p)

def simuler_htft(l_h, l_a, n_sims=2000):
    comptes = {}
    pour _ dans la plage(n_sims) :
        hg, ag = _poisson_sample(l_h), _poisson_sample(l_a)
        ht_hg, ht_ag = _binomial_sample(hg, 0.45), _binomial_sample(ag, 0.45)
        clé = f"{'V1' si ht_hg > ht_ag sinon ('X' si ht_hg == ht_ag sinon 'V2')}/{'V1' si hg > ag sinon ('X' si hg == ag sinon 'V2')}"
        counts[clé] = counts.get(clé, 0) + 1
    retourner {k: arrondi(v / n_sims, 4) pour k, v dans counts.items()}

def construire_coupon_et_bonus_mtft(pool, clés_verrouillées: set):
    choix, bonus_pool = [], []
    pour md dans la piscine :
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        si nkey est dans locked_keys : continuer
        si ce n'est ni md.home ni md.away ni md.dixon_pred ni is_match_today(md.kickoff) : continuer
        si is_reserve_team(md.home) ou is_reserve_team(md.away) : continuer
        est_piège, _ = a_pro_drapeaux_rouges(md.analyse, md.ligue)
        si is_trap : continuer

        htft = simuler_htft(md.dixon_pred.get("lambda_home", 1.3), md.dixon_pred.get("lambda_away", 1.1))
        best_p, best_prob = None, 0.0
        pour c dans ("V1/V1", "V2/V2") :
            si htft.get(c, 0) > best_prob : best_prob, best_p = htft.get(c, 0), c
        si best_p ou best_prob < MTFIN_MIN_PROB : continuer

        fav_mk = "victoire_home" si best_p == "V1/V1" sinon "victoire_away"
        fav_consensus, _, _, _, emoji = calculer_probabilité(md, fav_mk)
        si emoji == "⚠️" ou fav_consensus < 0,62 :
            continuer

        impair = impair_calibré(meilleure_probabilité, Aucun)
        si pas passes_value_filter(best_prob, odd, False, emoji) :
            continuer

        pts = 15 + (10 si md.has_h2h sinon 0) + 10 + (30 si best_prob >= 0,65 sinon 20) + 15 + 10
        si pts < MTFIN_MIN_SCORE : continuer
        picks.append({"md": md, "pick": best_p, "prob": best_prob, "score": pts, "odd": odd, "htft": htft})

    coupon = Aucun
    picks.sort(key=lambda x: (x["prob"], x["score"]), reverse=True)
    top3 = choix[:3]
    si top3 :
        jambes = [Sélection(p["md"].event_id, p["md"].league, p["md"].home, p["md"].away, p["md"].kickoff, "MT-Fin", f"MT/Fin : {p['pick']}", p["odd"], p["prob"], None, -1.0, p["score"], htft_pick=p["pick"]) pour p dans top3]
        co = round(math.prod(s.odds pour s dans legs), 2)
        cp = round(math.prod(s.model_prob pour s dans legs), 4)
        si coupon_passes_ev(co, cp, has_real_odds=False) :
            coupon = Coupon("MT-Fin", f"Sélection Pro • {len(legs)} matchs", "⏱", legs, co, cp, is_list=True)

    pour p dans top3 :
        md = p["md"]
        fav_market = f"Victoire {md.home} (1)" si p["pick"] == "V1/V1" sinon f"Victoire {md.away} (2)"
        real_odd = md.real_odds.get("home") si p["pick"] == "V1/V1" sinon md.real_odds.get("away")
        ft_res = p["pick"].split("/")[1]
        fav_prob = somme(p["htft"].get(f"{ht}/{ft_res}", 0) pour ht dans ("V1", "V2", "X"))
        si real_odd et float(real_odd) >= ODDS3_MIN_COTE_LEG et passes_value_filter(fav_prob, float(real_odd), True) :
            r_val = arrondi(float(real_odd), 2)
            bonus_pool.append({"match": md, "type": fav_market, "odds": r_val, "proba": fav_prob * 100, "ai": md.analysis, "real_odd": r_val, "source": "MT-FIN", "kickoff": md.kickoff})
    coupon de retour, bonus_pool

def construire_tirer_coupon_et_bonus(pool, clés_verrouillées: ensemble):
    candidats, bonus_pool = [], []
    pour md dans la piscine :
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        si nkey est dans locked_keys : continuer
        si ce n'est pas md.home ou ce n'est pas md.away ou ce n'est pas is_match_today(md.kickoff) : continuer
        si is_reserve_team(md.home) ou is_reserve_team(md.away) : continuer
        est_piège, _ = a_pro_drapeaux_rouges(md.analyse, md.ligue)
        si is_trap : continuer

        prob, g, d, b, emoji = calculer_probabilité(md, "match_nul")
        si prob < DRAW_MIN_PROB ou (g > 0 et g < 0,25) ou emoji == "⚠️): continuer
        tirage_impair = md.vrais_odds.get("tirage")
        si draw_odd n'est pas ou float(draw_odd) < 2.95 :
            ÉTAT["rejets"]["aucune_vrais_chance"] += 1
            continuer
        tirage_impair = arrondi(float(tirage_impair), 2)
        si (md.real_odds.get("home") et md.real_odds.get("home") < 1,80) ou (md.real_odds.get("away") et md.real_odds.get("away") < 1,80) : continuer
        si pass_value_filter(prob, draw_odd, True, emoji) : continuer
        pts = 30 + (25 si prob >= 0,33 sinon 18) + (20 si g >= 0,30 sinon 12) + 10 + (5 si md.has_h2h sinon 0) + 10
        si pts >= DRAW_MIN_SCORE : candidates.append(Selection(md.event_id, md.league, md.home, md.away, md.kickoff, "Match Nul", "Match Nul (X)", draw_odd, prob, draw_odd, (prob*draw_odd)-1, pts))

    coupon = Aucun
    candidats.sort(clé=lambda x: (x.qualité, x.probabilité_modèle), reverse=True)
    top3 = candidats[:3]
    si top3 :
        co = round(math.prod(s.odds pour s dans top3), 2)
        cp = round(math.prod(s.model_prob pour s dans top3), 4)
        si coupon_passes_ev(co, cp, has_real_odds=True) :
            coupon = Coupon("Match Nul", f"Sélection Pro • {len(top3)} nuls", "🤝", top3, co, cp, is_list=True)

    candidats.sort(clé=lambda x: x.probabilité_modèle)
    pour c dans candidats[:3] :
        md = next((m pour m dans pool si m.event_id == c.event_id), None)
        si ce n'est pas md ou si ce n'est pas md.real_odds.get("home") ou si ce n'est pas md.real_odds.get("away") : continuer
        p_h, _, _, _, _ = compute_probability(md, "victoire_home"); p_a, _, _, _, _ = compute_probability(md, "victoire_away")
        p_12 = p_h + p_a
        inv_12 = (1.0 / float(md.real_odds["home"])) + (1.0 / float(md.real_odds["away"]))
        odd_12 = arrondi(max(1.03, (1.0 / inv_12) * 0.96), 2) si inv_12 > 0 sinon 0.0
        si odd_12 >= ODDS3_MIN_COTE_LEG et passes_value_filter(p_12, odd_12, True) :
            bonus_pool.append({"match": md, "type": "Double Chance 12", "odds": odd_12, "proba": p_12 * 100, "ai": md.analysis, "real_odd": odd_12, "source": "MATCH-NUL", "kickoff": md.kickoff})
    coupon de retour, bonus_pool

def construire_troisième_pilier(pool, clés_verrouillées: set):
    pool_result = []
    pour md dans la piscine :
        nkey = f"{normalize_name(md.home)}__{normalize_name(md.away)}"
        si nkey est dans locked_keys : continuer
        si ce n'est ni md.home ni md.away ni is_match_today(md.kickoff) ni is_reserve_team(md.home) ni is_reserve_team(md.away) : continuer
        est_piège, _ = a_pro_drapeaux_rouges(md.analyse, md.ligue)
        si is_trap : continuer

        meilleur = Aucun
        pour mk, lbl dans {"btts_yes": "BTTS Oui", "btts_no": "BTTS Non", "over_1.5": "Plus de 1,5", "under_2.5": "Moins de 2,5", "over_2.5": "Plus de 2,5"}.items():
            prob, g, d, b, emoji = calculer_probabilité(md, mk)
            si la probabilité est inférieure à 0,62 ou si l'emoji est égal à "⚠️": continuer
            cote_réelle = md.cotes_réelles.get(mk)
            si ce n'est pas real_odd ou float(real_odd) <= 1.01 :
                continuer
            impair = arrondi(float(réel_impair), 2)
            si odd >= ODDS3_MIN_COTE_LEG et passes_value_filter(prob, odd, True, emoji) :
                score = (1, prob * impair, prob)
                si ce n'est pas le meilleur ou si le score est supérieur à best["_score"] :
                    meilleur = {"match": md, "type": lbl, "odds": odd, "proba": prob * 100, "ai": md.analysis, "real_odd": odd, "source": "BZZOIRO", "kickoff": md.kickoff, "_score": score}
        si c'est le mieux :
            best.pop("_score", None)
            pool_result.append(meilleur)
    pool_result.sort(key=lambda x: (1 if x["real_odd"] else 0, x["odds"] * x["proba"] / 100.0, x["proba"]), reverse=True)
    renvoyer pool_result[:5]

async def build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar, locked_keys: set):
    combiné = [c pour c dans (bonus_mtft + bonus_draw + third_pillar) si f"{normalize_name(c['match'].home)}__{normalize_name(c['match'].away)}" n'est pas dans locked_keys]
    si non combiné : renvoyer None
    combined.sort(key=lambda x: (1 if x.get('real_odd') else 0, x['odds'] * x['proba'] / 100.0, x['proba']), reverse=True)

    si bonus_mtft et bonus_draw et third_pillar :
        pour r dans la plage(3, min(len(combinée), 5) + 1) :
            pour chaque combinaison dans itertools.combinations(combined[:15], r) :
                ids = [_get_match_id(c) pour c dans combo]
                si la longueur de l'ensemble des identifiants (set(ids)) est différente de la longueur de l'ensemble des identifiants (ids) : continuer
                si aucune des valeurs de `c` dans `combine` n'est égale à `'MT-FIN'`, `'MATCH-NUL'` ou `'BZZOIRO'`, alors continuer
                si tous les matchs ne sont pas aujourd'hui (c.get('kickoff', '')) pour c dans combo : continuer
                tot_o = math.prod(x['odds'] pour x dans combo)
                si ODDS3_HYB_MIN_COTE <= tot_o <= ODDS3_HYB_MAX_COTE :
                    tot_p = math.prod(c['proba'] / 100.0 pour c dans combo)
                    has_real = any(c.get('real_odd') is not None for c in combo)
                    si coupon_passes_ev(tot_o, tot_p, has_real_odds=has_real) n'est pas vrai : continuer
                    si la fonction `enrich_selected_legs_ai(combo)` n'est pas appelée, continuez.
                    jambes = [Sélection(_get_match_id(c), c['match'].league, c['match'].home, c['match'].away, c.get('kickoff', ''), c['type'], c['type'], c['odds'], c['proba']/100.0, c['real_odd'], 90, analysis=clean_analysis_text(c['ai'], 560)) pour c dans combo]
                    return Coupon("Odds +3 HYBRIDE", f"Mélange 3 sources • {len(legs)} sélections", "🎯", legs, round(tot_o, 2), round(tot_p, 4), show_analysis=True)

    pour r dans (3, 4) :
        si len(combiné) < r : break
        pour chaque combinaison dans itertools.combinations(combined[:12], r) :
            ids = [_get_match_id(c) pour c dans combo]
            si la longueur de l'ensemble des identifiants (set(ids)) est différente de la longueur de l'ensemble des identifiants (ids) : continuer
            si tous les matchs ne sont pas aujourd'hui (c.get('kickoff', '')) pour c dans combo : continuer
            tot_o = math.prod(x['odds'] pour x dans combo)
            si ODDS3_HYB_MIN_COTE <= tot_o <= ODDS3_HYB_MAX_COTE :
                tot_p = math.prod(c['proba'] / 100.0 pour c dans combo)
                has_real = any(c.get('real_odd') is not None for c in combo)
                si coupon_passes_ev(tot_o, tot_p, has_real_odds=has_real) n'est pas vrai : continuer
                si la fonction `enrich_selected_legs_ai(combo)` n'est pas appelée, continuez.
                jambes = [Sélection(_get_match_id(c), c['match'].league, c['match'].home, c['match'].away, c.get('kickoff', ''), c['type'], c['type'], c['odds'], c['proba']/100.0, c['real_odd'], 85, analysis=clean_analysis_text(c['ai'], 560)) pour c dans combo]
                return Coupon("Odds +3 HYBRIDE", f"Meilleure valeur Multi-IA • {len(legs)} sélections", "🎯", legs, round(tot_o, 2), round(tot_p, 4), show_analysis=True)
    renvoyer Aucun

# =====================================================================
# SCAN GLOBAL (DOUBLE SCAN + CACHE JOURNALIER)
# =====================================================================
async def scan(force: bool = False) -> bool:
    asynchrone avec STATE["scan_lock"] :
        aujourd_str = datetime.now(TZ).strftime("%Y-%m-%d")
        si force et load_daily_cache(today_str) ne sont pas exécutés :
            print(f"📦 Cache journalier actif ({today_str}) : {len(STATE['coupons'])} coupons chargés sans rescan.")
            renvoyer Faux

        ÉTAT["rejets"] = {"valeur": 0, "coupon_ev": 0, "auth_proba": 0, "pro_trap": 0, "instable": 0, "no_real_odds": 0}
        ws_matches, odds_status = await fetch_rapidodds_odds()
        
        bzz_preds = []
        si fetch_bzzoiro_predictions_direct et BZZOIRO_API_KEY :
            essayer : bzz_preds = await fetch_bzzoiro_predictions_direct(min_confidence=BZZ_NATIVE_CONFIDENCE)
            sauf Exception as e: print(f"⚠️ Erreur Bzzoiro : {e}")
        
        inférer_les_chances = {}
        si bzz_preds et fetch_infer_odds :
            try: infer_odds = await fetch_infer_odds([{"event_id": b["event_id"], "home": b["home"], "away": b["away"], "kickoff": b.get("kickoff", "")} for b in bzz_preds])
            sauf : passer

        pool_enrichi = []
        correspondant = 0
        pool = []
        clés_pool_vues = set()
        pour bz dans (bzz_preds ou []):
            cotes_de_base = dict(inférer_cotes.get(bz["id_événement"], {}))
            base_odds.update(bz.get("real_odds", {}))
            md = MatchData(bz["event_id"], bz.get("league", ""), bz["home"], bz["away"], bz.get("kickoff", ""), base_odds, bz.get("bzz_markets", {}), bz.get("bzz_confidence", 0))
            _assurer_dixon_pred(md)
            pool.append(md)
            seen_pool_keys.add(f"{normalize_name(md.home)}__{normalize_name(md.away)}")

        pour ws dans ws_matches :
            nk = f"{normalize_name(ws.home_team)}__{normalize_name(ws.away_team)}"
            si nk est dans seen_pool_keys :
                continuer
            r_odds = {}
            si ws.home_odds : r_odds["home"] = ws.home_odds
            si ws.draw_odds : r_odds["draw"] = ws.draw_odds
            si ws.away_odds : r_odds["away"] = ws.away_odds
            si ws.over_2_5 : r_odds["over_2.5"] = ws.over_2_5
            si ws.under_2_5 : r_odds["under_2.5"] = ws.under_2_5
            md = MatchData(ws.match_id, ws.league, ws.home_team, ws.away_team, ws.commence_time, r_odds, {}, 70.0)
            _assurer_dixon_pred(md)
            pool.append(md)
            seen_pool_keys.add(nk)

        si piscine :
            matched = inject_all_real_odds(pool, ws_matches)
            print(f"⏰ Total correspondances avec vraies cotes : {matched}/{len(pool)}")
            pool = [
                médecin pour médecin dans la piscine
                si is_match_today(md.kickoff)
                et non is_low_stakes_competition(md.league)
                et booléen(md.real_odds)
            ]
            print(f"💰 Matchs retenus après verrouillage VRAIES COTES uniquement : {len(pool)}")
            pool.sort(clé=prescore_match, reverse=True)
            print(f"🎯 Top {min(len(pool), DOUBLE_SCAN_POOL_SIZE)} matchs sélectionnés pour Double Analyse Gemini")
            pool_enrichi = await enrichi_avec_double_passe(pool[:GEMINI_MAX_CALLS_PER_SCAN])

        si ws_matches ou matched > 0 :
            odds_status = "live"
        ÉTAT["odds_api_mode"] = odds_status

        coupon_auth, locked_vip_keys = await build_exclusive_vip_auth_coupon(enriched_pool, ws_matches)
        si les clés VIP sont verrouillées :
            print(f"🔒 Correspond aux verrous pour VIP AUTH : {locked_vip_keys}")

        coupon_mtft, bonus_mtft = build_mtft_coupon_and_bonus(enriched_pool, locked_vip_keys) si enriched_pool sinon (None, [])
        coupon_draw, bonus_draw = build_draw_coupon_and_bonus(enriched_pool, locked_vip_keys) if enriched_pool else (None, [])
        troisième_pilier = construire_troisième_pilier(pool_enrichi, clés_vip_verrouillées) si pool_enrichi sinon []
        coupon_hybride = await build_odds3_hybrid_coupon(bonus_mtft, bonus_draw, third_pillar, locked_vip_keys)

        coupons = []
        si coupon_auth : coupons.append(coupon_auth)
        si coupon_hybrid : coupons.append(coupon_hybrid)
        si coupon_mtft : coupons.append(coupon_mtft)
        si coupon_draw : coupons.append(coupon_draw)

        ÉTAT["coupons"] = coupons
        ÉTAT["last_scan_date"] = aujourd'hui_str
        ÉTAT["dernière_analyse"] = datetime.now(TZ).isoformat()
        ÉTAT["debug"]["mode"] = odds_status
        si coupons :
            enregistrer_le_cache_quotidien(aujourd_jour)
        autre:
            print("⚠️ Cache journalier non figé (0 coupon généré) : prochain /scan pourra réanalyser.")

        print(f"──────── RÉSUMÉ ────────")
        print(f"Rejets filtres : {STATE['rejections']}")
        print(f"Coupons validés : {len(STATE['coupons'])}")
        pour c dans STATE["coupons"] : print(f" • {c.name} : {len(c.legs)} matchs, cote {c.combined_odds}, proba {c.combined_prob*100:.1f}%, EV {c.combined_odds*c.combined_prob:.2f}")
        print("────────────────────────")
        renvoyer Vrai

# =====================================================================
# FORMATAGE ET GESTIONNAIRES
# =====================================================================
async def safe_answer(msg, txt, reply_markup=None):
    pour i, p dans enumerate(split_message(txt)):
        essayer:
            si i == 0 : attendre msg.answer(p, parse_mode="HTML", reply_markup=reply_markup)
            sinon : attendre msg.answer(p, parse_mode="HTML")
            attendre asyncio.sleep(0.3)
        sauf : passer

fonction asynchrone safe_send(cid, txt):
    pour p dans split_message(txt) :
        essayer:
            await bot.send_message(chat_id=cid, text=p, parse_mode="HTML")
            attendre asyncio.sleep(0.3)
        sauf : passer

async def broadcast_after_scan():
    mode = STATE.get("odds_api_mode", "inconnu")
    mode_label = "🟢 EN DIRECT" si mode == "live" sinon "🟡 IA CALIBRÉ" si mode == "fallback" sinon "⚪ ?"
    if not STATE["coupons"]: msg = f"<b>📭 Aucun coupon fiable aujourd'hui</b>\n<i>(Critères Pro stricts : protection de bankroll)</i>"
    autre:
        lignes = [f"{c.emoji} <b>{c.name}</b> • {len(c.legs)} paris • Cote <b>{c.combed_odds}</b>" pour c dans STATE["coupons"]]
        msg = f"<b>🔔 COUPONS DU JOUR !</b>\n<i>Mode : {mode_label}</i>\n\n" + "\n\n".join(lignes)
    pour cid dans TARGETS : attendre safe_send(cid, msg)

def format_header():
    mode = STATE.get("odds_api_mode", "inconnu")
    mode_icon = "🟢" si mode == "live" sinon "🟡" si mode == "fallback" sinon "⚪"
    mode_txt = "LIVE" si mode == "live" sinon "FALLBACK" si mode == "fallback" sinon "?"
    return f"<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n <b>⚡ {BRAND_NAME.upper()} ⚡</b>\n<b>━━━━━━━━━━━━━━━━━━━━━━━</b>\n<i>{BRAND_TAGLINE}</i>\n<i>📅 {today_pretty()}</i>\n<i>{mode_icon} Mode : {mode_txt}</i>"

def format_selection_block(s, idx, show_analysis):
    lignes = [f"<b>┌─ #{idx} {s.home} vs {s.away}</b>", f"│ 📅 <b>{format_kickoff(s.kickoff)}</b> • {s.league}", f"│ 🎯 <b>{s.pick_label}</b>", f"│ 📊 Confiance : <b>{s.model_prob*100:.1f}%</b> {prob_bar(s.model_prob)}"]
    if s.real_odd: lines.append(f"│ 💰 Cote : <b>{s.real_odd}</b>")
    sinon : lignes.append(f"│ 💰 Cote : <b>{s.odds}</b>")
    Si show_analysis et s.analysis : lines.append(f"│ 🧠 <i>{clean_analysis_text(s.analysis, 560)}</i>")
    lignes.append(f"<b>└─────────────</b>")
    retourner "\n".join(lignes)

def format_coupon(c):
    jambes_actives = [s pour s dans c.jambes si le match n'a pas commencé(s.coup_d'envoi)]
    si les jambes ne sont pas actives :
        retourner "\n".join([
            format_header(), "",
            f"<b>{c.emoji} {c.name.upper()}</b>",
            f"<i>{c.subtitle}</i>", "",
            "⏳ <i>Tous les matchs de ce coupon ont déjà démarré.</i>", "",
            f"<i>{PUBLIC_FOOTER}</i>",
        ])

    si len(active_legs) < len(c.legs) :
        disp_odds = round(math.prod(s.odds for s in active_legs), 2)
        disp_prob = round(math.prod(s.model_prob pour s dans active_legs), 4)
        disp_sub = re.sub(r"\d+\s+(sélections|matchs|nuls)", f"{len(active_legs)} \\1", c.subtitle)
    autre:
        cotes_affichées = c.cotes_combinées
        disp_prob = c.prob_combinée
        disp_sub = c.subtitle

    si c.est_liste :
        lignes = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{disp_sub}</i>", ""]
        pour i, s dans enumerate(active_legs, 1): lines.append(format_selection_block(s, i, False) + "\n")
    autre:
        ev = disp_odds * disp_prob si (disp_odds > 0 et disp_prob > 0) sinon 0
        ev_badge = f"<b>┃ 💎 EV : {ev:.2f}</b>" si ev > 0 sinon ""
        lignes = [format_header(), "", f"<b>{c.emoji} {c.name.upper()}</b>", f"<i>{disp_sub}</i>", "", f"<b>┏━━━━━━━━━━━━━━━┓</b>", f"<b>┃ 🎫 Cote : {disp_odds}</b>", f"<b>┃ 📈 Réussite est. : {disp_prob*100:.1f}%</b>", ev_badge, f"<b>┗━━━━━━━━━━━━━━━┛</b>", ""]
        pour i, s dans enumerate(active_legs, 1): lines.append(format_selection_block(s, i, True) + "\n")
    lignes.append(f"<i>{PIED_DE_FEUILLE_PUBLIC}</i>")
    retourner "\n".join(lignes)

def _parse_date_range_args(parts: list[str]) -> tuple[Optional[datetime], Optional[datetime], int, int, str]:
    """Analyser '/semaine 24/11-25/11 15', '/semaine 24/11 25/11 15', '/semaine 24/11 15', '/semaine 15' ou '/semaine 3 15'."""
    maintenant = datetime.now(TZ)
    jours = 3
    limite = 15
    start_dt = Aucun
    end_dt = Aucun

    def _make_dt(day_s: str, month_s: str, end_of_day: bool = False) -> datetime:
        d, mois = int(jour_s), int(mois_s)
        année = maintenant.année + (1 si mois < maintenant.mois et (maintenant.mois - mois) > 6 sinon 0)
        si fin_de_journée :
            renvoie datetime(yr, mo, d, 23, 59, 59, tzinfo=TZ)
        renvoie datetime(yr, mo, d, 0, 0, 0, tzinfo=TZ)

    args = parts[1:]
    si arguments :
        m_range = re.match(r"^(\d{1,2})/(\d{1,2})\s*[-–]\s*(\d{1,2})/(\d{1,2})$", args[0])
        si m_range :
            start_dt = _make_dt(m_range.group(1), m_range.group(2), end_of_day=False)
            fin_dt = _make_dt(m_range.group(3), m_range.group(4), fin_de_journée=True)
            jours = max(1, (end_dt.date() - start_dt.date()).days + 1)
            period_label = f"Du {start_dt.strftime('%d/%m')} au {end_dt.strftime('%d/%m')}"
            si len(args) >= 2 et args[1].isdigit():
                limite = max(3, min(30, int(args[1])))
            renvoie start_dt, end_dt, jours, limite, étiquette_période

        si len(args) >= 2 :
            m1 = re.match(r"^(\d{1,2})/(\d{1,2})$", args[0])
            m2 = re.match(r"^(\d{1,2})/(\d{1,2})$", args[1])
            si m1 et m2 :
                start_dt = _make_dt(m1.group(1), m1.group(2), end_of_day=False)
                fin_dt = _make_dt(m2.group(1), m2.group(2), fin_de_journée=True)
                jours = max(1, (end_dt.date() - start_dt.date()).days + 1)
                period_label = f"Du {start_dt.strftime('%d/%m')} au {end_dt.strftime('%d/%m')}"
                si len(args) >= 3 et args[2].isdigit():
                    limite = max(3, min(30, int(args[2])))
                renvoie start_dt, end_dt, jours, limite, étiquette_période

        m_single = re.match(r"^(\d{1,2})/(\d{1,2})$", args[0])
        si m_single :
            start_dt = _make_dt(m_single.group(1), m_single.group(2), end_of_day=False)
            fin_dt = _make_dt(m_single.group(1), m_single.group(2), fin_de_journée=True)
            jours = 1
            period_label = f"Le {start_dt.strftime('%d/%m')}"
            si len(args) >= 2 et args[1].isdigit():
                limite = max(3, min(30, int(args[1])))
            renvoie start_dt, end_dt, jours, limite, étiquette_période

        si args[0].isdigit():
            val0 = int(args[0])
            si len(args) == 1 et val0 >= 8 :
                limite = max(3, min(30, val0))
            autre:
                jours = max(1, min(7, val0))
        si len(args) >= 2 et args[1].isdigit():
            limite = max(3, min(30, int(args[1])))

    renvoie None, None, jours, limite, f"{jours} jours"

# --- COMMANDES UTILISATEUR & ADMIN ---
@dp.message(Commande("démarrer"))
async def start_cmd(m: Message):
    kb = ADMIN_KEYBOARD si m.from_user.id == ADMIN_ID sinon USER_KEYBOARD
    si m.from_user.id == ADMIN_ID :
        msg = (
            f"{format_header()}\n\n<b>👋 Mode Admin</b>\n"
            f"• /semaine <code>24/11-25/11 15</code> — Top matchs sur dates précises\n"
            f"• /semaine <code>24/11 10</code> — Top matchs sur une date unique\n"
            f"• /semaine <code>[jours] [matchs]</code> — Top matchs sur N jours\n"
            f"• /genkey <code>[jours] [nb]</code> — Créer clé(s) VIP\n"
            f"• /vip — Liste des abonnés & clés\n"
            f"• /delvip <code>ID</code> — Révoquer un abonné\n"
            f"• /bilan — Vérifier les scores & voir le ROI\n"
            f"• /scan • /rescan"
        )
    elif is_subscribed(m.from_user.id):
        msg = f"{format_header()}\n\n<b>🎉 Bienvenue VIP !</b>\n\n{get_subscription_message(m.from_user.id)}\n\nAppuie sur <b>{BTN_ODDS3}</b> ci-dessous."
    autre:
        msg = f"{format_header()}\n\n<b>🔒 Accès VIP requis</b>\n\n{get_subscription_message(m.from_user.id)}"
    attendre une réponse sûre(m, msg, reply_markup=kb)

@dp.message(F.text == BTN_WEEKLY)
@dp.message(Command("semaine", "week", "top"))
async def semaine_cmd(m: Message):
    si m.from_user.id != ADMIN_ID :
        retour
    si la fonction analyze_weekly_top_matches n'est pas activée :
        wait safe_answer(m, "⚠️ Module <code>weekly_analyzer.py</code> introuvable sur le serveur.")
        retour

    parties = (m.text ou "").split()
    start_dt, end_dt, jours, limite, libellé_période = _parse_date_range_args(parts)

    attendre une réponse sûre(
        m,
        f"⏳ <b>Analyse Pro Multi-Jours en cours...</b>\n"
        f"📅 Période : <b>{period_label}</b> • 🎯 Objectif : <b>Top {limit} matchs</b>\n"
        f"<i>Croisement Dixon-Coles + XGBoost + Bzzoiro + Gemini (patiente ~30-45 sec)...</i>",
    )

    sélections = attendre analyse_meilleur_match_hebdomadaire(
        jours=jours,
        limite=limite,
        match_cls=MatchData,
        dixon_model=STATE.get("dixon_model"),
        fetch_odds_fn=fetch_rapidodds_odds,
        inject_odds_fn=inject_all_real_odds,
        prescore_fn=prescore_match,
        enrich_pass_fn=_enrich_single_pass,
        calculer_prob_fn=calculer_probabilité,
        fallback_bzz_fn=fetch_bzzoiro_predictions_direct,
        start_dt=start_dt,
        fin_dt=fin_dt,
    )

    sélections_filtrées = []
    pour p dans (picks ou []):
        is_trap, _ = has_pro_red_flags(p.get("ai", ""), p.get("league", ""))
        si is_trap :
            continuer
        prob = float(p.get("proba", 0)) / 100.0
        est_réel = booléen(p.get("est_réel"))
        si ce n'est pas réel :
            continuer
        impair = arrondi(float(p.get("impair", 0)), 2)
        si passes_value_filter(prob, odd, True) :
            p["impair"] = impair
            p["_stakes"] = calculer_score_enjeux(p.get("league", ""), p.get("ai", ""))
            filtered_picks.append(p)
    filtered_picks.sort(key=lambda x: (x.get("_stakes", 0), x.get("proba", 0)), reverse=True)

    si les sélections filtrées ne sont pas :
        wait safe_answer(m, f"📭 Aucun match fiable à EV positif trouvé pour la période <b>{period_label}</b>.")
        retour

    total_odd = round(math.prod(p["odd"] for p in filtered_picks), 2)
    total_prob = round(math.prod(p["proba"] / 100.0 for p in filtered_picks), 4)
    espérance totale = probabilité totale impaire * probabilité totale
    lignes = [
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>",
        f"<b>📅 TOP {len(filtered_picks)} MATLES • {period_label.upper()}</b>",
        f"<b>🎫 COTE COMBINÉE : {total_odd} • 💎 EV : {total_ev:.2f}</b>",
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>",
        f"<i>Stratégie Pro (20 ans) : Fidélité au scénario & haute confiance</i>\n",
    ]
    pour i, p dans énumérer(filtered_picks, 1) :
        odd_tag ​​= f"<b>{p['odd']}</b>"
        lignes.ajouter(
            f"<b>#{i} {p['domicile']} vs {p['extérieur']}</b>\n"
            f"📅 {format_kickoff(p['kickoff'])} • {p['league']}\n"
            f"🎯 <b>{p['pick']}</b> | 📊 <b>{p['proba']:.1f}%</b> | 💰 {odd_tag}\n"
            f"🧠 <i>{p['ai']}</i>\n"
        )
    lignes.append(f"<i>{PIED_DE_FEUILLE_PUBLIC}</i>")
    attendre une réponse sûre(m, "\n".join(lignes))

@dp.message(Commande("genkey", "gen", "newkey"))
async def genkey_cmd(m: Message):
    si m.from_user.id != ADMIN_ID :
        retour
    parties = (m.text ou "").split()
    jours = 30
    nombre = 1
    essayer:
        si len(parts) >= 2 :
            jours = max(1, min(3650, int(parts[1])))
        si len(parts) >= 3 :
            nombre = max(1, min(50, int(parts[2])))
    sauf ValueError :
        await safe_answer(m, "⚠️ Format : <code>/genkey 30</code> ou <code>/genkey 30 5</code>")
        retour

    clés = créer_clés_d'accès(jours=jours, nombre=nombre)
    keys_fmt = "\n".join(f"• <code>{k}</code>" pour k dans keys)
    attendre une réponse sûre(
        m,
        f"🔑 <b>{count} clé(s) VIP générée(s) ({days} jours) :</b>\n\n{keys_fmt}\n\n"
        f"<i>L'abonné n'a qu'à coller la clé dans le bot pour activer son accès.</i>",
    )

@dp.message(Commande("vip", "utilisateurs", "clés"))
async def vip_list_cmd(m: Message):
    si m.from_user.id != ADMIN_ID :
        retour
    maintenant = datetime.now(TZ)
    lignes_actives = []
    pour uid, info dans STATE["users"].items():
        essayer:
            exp = datetime.fromisoformat(info.get("expires_at", ""))
            si exp > maintenant :
                uname = f"@{info['username']}" si info.get("username") sinon info.get("full_name", "Sans nom")
                active_lines.append(f"• <b>{uname}</b> (<code>{uid}</code>) → {exp.strftime('%d/%m/%Y %H:%M')}")
        sauf exception :
            passer

    clés_inutilisées = [
        f"• <code>{k}</code> ({v.get('days', 30)}j)"
        pour k, v dans STATE["keys"].items()
        si v.get("used")
    ]

    txt = f"<b>👥 ACTIFS VIP ABONNÉS ({len(active_lines)}) :</b>\n"
    txt += ("\n".join(active_lines) if active_lines else "<i>Aucun abonné actif.</i>")
    txt += f"\n\n<b>🔑 CLÉS DISPONIBLES ({len(unused_keys)}) :</b>\n"
    txt += ("\n".join(unused_keys[:25]) if unused_keys else "<i>Aucune clé libre (/genkey pour en créer).</i>")
    attendre une réponse sûre(m, txt)

@dp.message(Commande("delvip", "révoquer"))
async def delvip_cmd(m: Message):
    si m.from_user.id != ADMIN_ID :
        retour
    parties = (m.text ou "").split()
    si len(parts) < 2 :
        await safe_answer(m, "⚠️ Format : <code>/delvip ID_TELEGRAM</code>")
        retour
    uid = parts[1].strip()
    si uid est dans STATE["users"] :
        del STATE["users"][uid]
        enregistrer_json(USERS_DB, STATE["users"])
        wait safe_answer(m, f"🗑️ Abonnement révoqué pour <code>{uid}</code>.")
    autre:
        wait safe_answer(m, f"❌ Utilisateur <code>{uid}</code> introuvable.")

@dp.message(F.text == BTN_ODDS3)
async def b_odds3(m: Message):
    si m.from_user.id != ADMIN_ID et que m.from_user.id n'est pas abonné :
        attendre une réponse sûre(
            m,
            f"<b>🔒 Accès VIP requis</b>\n\n{get_subscription_message(m.from_user.id)}",
            reply_markup=CLAVIER_UTILISATEUR,
        )
        retour
    c = next((x pour x dans STATE["coupons"] si x.name == "Odds +3 AUTH"), None)
    si c :
        attendre une réponse sûre(m, format_coupon(c))
    autre:
        wait safe_answer(m, "📭 Aucun ticket VIP aujourd'hui : aucun match ne passe tous nos filtres de sécurité pro (protection de bankroll).")
    si m.from_user.id == ADMIN_ID :
        c_hyb = next((x for x in STATE["coupons"] if x.name == "Odds +3 HYBRIDE"), None)
        si c_hyb :
            attendre une réponse sûre(m, format_coupon(c_hyb))

@dp.message(F.text == BTN_MTFT)
async def b_mtft(m: Message):
    si m.from_user.id == ADMIN_ID :
        c = suivant((x pour x dans STATE["coupons"] si x.name == "MT-Fin"), None)
        si c : attendre safe_answer(m, format_coupon(c))
        else: wait safe_answer(m, "📭 Aucun coupon MT-Fin disponible.")

@dp.message(F.text == BTN_DRAW)
async def b_draw(m: Message):
    si m.from_user.id == ADMIN_ID :
        c = next((x pour x dans STATE["coupons"] si x.name == "Match Nul"), None)
        si c : attendre safe_answer(m, format_coupon(c))
        else: wait safe_answer(m, "📭 Aucun coupon Match Nul disponible.")

@dp.message(F.text == BTN_BILAN)
@dp.message(Commande("bilan", "roi", "checkscores"))
async def b_bilan(m: Message):
    si m.from_user.id != ADMIN_ID :
        retour
    await safe_answer(m, "⏳ <i>Vérification des scores (Bzzoiro + ESPN) en cours...</i>")
    attendre la vérification des scores en attente (notification=False)
    attendre safe_answer(m, format_bilan_report())

@dp.message(F.text == "⚙️ Débogage")
async def b_debug(m: Message):
    si m.from_user.id == ADMIN_ID :
        attendre une réponse sûre(
            m,
            f"⚙️ <b>Débogage</b>\n"
            f"• Mode cotes : <code>{STATE.get('odds_api_mode', '?')}</code>\n"
            f"• Double analyse : <code>{STATE.get('double_scan_status', '?')}</code>\n"
            f"• Statistiques : <code>{STATE.get('double_scan_stats', {})}</code>\n"
            f"• Rejets : <code>{STATE.get('rejections', {})}</code>\n"
            f"• Dernier score de vérification : <code>{STATE.get('last_score_check') or 'N/A'}</code>\n"
            f"• Clés libres : <b>{sum(1 for v in STATE['keys'].values() if not v.get('used'))}</b>",
        )

@dp.message(F.text == BTN_SCAN)
async def b_scan(m: Message):
    si m.from_user.id == ADMIN_ID :
        wait safe_answer(m, "⏳ Vérification des coupons...")
        ran_fresh = await scan(force=False)
        si ran_fresh :
            enregistrer_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
            attendre broadcast_after_scan()
        autre:
            wait safe_answer(m, f"📦 <b>Cache journalier actif</b> ({len(STATE['coupons'])} coupon(s) figé(s) pour aujourd'hui).\n<i>Utilise /rescan pour forcer une nouvelle double analyse.</i>")

@dp.message(Commande("scan"))
async def scan_cmd(m: Message):
    si m.from_user.id == ADMIN_ID :
        wait safe_answer(m, "⏳ Vérification des coupons...")
        ran_fresh = await scan(force=False)
        si ran_fresh :
            enregistrer_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
            attendre broadcast_after_scan()
        autre:
            wait safe_answer(m, f"📦 <b>Cache journalier actif</b> ({len(STATE['coupons'])} coupon(s) figé(s) pour aujourd'hui).\n<i>Utilise /rescan pour forcer une nouvelle double analyse.</i>")

@dp.message(Commande("rescan"))
async def rescan_cmd(m: Message):
    si m.from_user.id == ADMIN_ID :
        wait safe_answer(m, "🔄 <b>Rescan forcé en cours</b> (Analyse Pro + mise à jour du cache)...")
        attendre scan(force=True)
        enregistrer_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
        attendre broadcast_after_scan()

# --- ACTIVATION AUTOMATIQUE QUAND UN ABONNÉ COLLE SA CLÉ ---
@dp.message(F.text)
async def handle_key_redeem(m: Message):
    brut = (m.text ou "").strip()
    si ce n'est pas brut ou brut commence par "/" :
        retour
    candidat = brut.haut()
    si le candidat se trouve dans l'ÉTAT["keys"] ou si le candidat commence par "VIP-" :
        ok, réponse = redeem_access_key(m.from_user, candidat)
        kb = ADMIN_KEYBOARD si m.from_user.id == ADMIN_ID sinon USER_KEYBOARD
        attendre une réponse sécurisée(m, réponse, balisage_réponse=kb)
        si ok et ADMIN_ID et m.from_user.id != ADMIN_ID :
            uname = f"@{m.from_user.username}" si m.from_user.username sinon m.from_user.full_name
            attendre safe_send(
                ID_ADMIN,
                f"🔔 <b>Nouvelle activation VIP !</b>\n👤 {uname} (<code>{m.from_user.id}</code>)\n🔑 Clé : <code>{candidate}</code>",
            )
    elif m.from_user.id != ADMIN_ID and not is_subscribed(m.from_user.id):
        attendre une réponse sûre(
            m,
            f"<b>🔒 Accès VIP requis</b>\n\n{get_subscription_message(m.from_user.id)}",
            reply_markup=CLAVIER_UTILISATEUR,
        )

async def daily_broadcast():
    essayer:
        attendre la vérification des scores en attente (notification=Vrai)
        attendre scan(force=True)
        enregistrer_coupons(STATE["coupons"], datetime.now(TZ).strftime("%Y-%m-%d"))
    sauf Exception comme e :
        print(f"❌ Erreur daily_broadcast : {e}")
    attendre broadcast_after_scan()

async def periodic_score_check():
    essayer:
        res = await verify_pending_scores(notify=True)
        si res.get("updated_legs"):
            print(f"⚽ Score check : {res['updated_legs']} match(s) mis à jour, {res['settled_coupons']} coupon(s) réglé(s)")
    sauf Exception comme e :
        print(f"⚠️ Erreur periodic_score_check : {e}")

@asynccontextmanager
async def durée de vie(app):
    essayer:
        await bot.delete_webhook(drop_pending_updates=True)
        moi = await bot.get_me()
        print(f"🤖 Bot Telegram connecté : @{me.username} (ID: {me.id})")
    sauf Exception comme e :
        print(f"❌ TÉLÉGRAMME DE CONNEXION ERREUR : {e}")

    ÉTAT["tracker"] = charger_json(BASE_DE_BASE_DE_BASE_DE_BROCHES_LOCALE, {"coupons": []})
    ÉTAT["clés"] = charger_json(KEYS_DB, {})
    ÉTAT["utilisateurs"] = charger_json(USERS_DB, {})
    ÉTAT["prêt"] = Vrai
    aujourd_str = datetime.now(TZ).strftime("%Y-%m-%d")
    si load_daily_cache(today_str):
        print(f"📦 Cache du jour restauré au démarrage ({len(STATE['coupons'])} coupons)")
    si train_model_if_needed :
        asyncio.create_task(asyncio.to_thread(train_model_if_needed))

    planificateur = AsyncIOScheduler(fuseau horaire=TZ)
    scheduler.add_job(daily_broadcast, CronTrigger(hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE, timezone=TZ), id="daily")
    scheduler.add_job(periodic_score_check, IntervalTrigger(minutes=45), id="score_check")
    planificateur.démarrer()

    async def run_polling():
        essayer:
            print("📡 Démarrage de l'écoute Telegram (polling)...")
            attendre dp.start_polling(bot, handle_signals=False)
        sauf asyncio.CancelledError :
            passer
        sauf Exception comme e :
            print(f"❌ CRASH SONDAGE TELEGRAM : {e}")

    bot_task = asyncio.create_task(run_polling())

    async def bootstrap():
        essayer:
            si télécharger_et_s'entraîner :
                ÉTAT["dixon_model"] = attendre le téléchargement et l'entraînement()
                imprimer("✅ Dixon chargé")
            attendre asyncio.sleep(5)
            attendre periodic_score_check()
            curr_today = datetime.now(TZ).strftime("%Y-%m-%d")
            si load_daily_cache(curr_today) n'est pas chargé :
                attendre la diffusion quotidienne()
                ÉTAT["last_scan_date"] = aujourd'hui
        sauf Exception comme e :
            print(f"⚠️ Erreur bootstrap : {e}")

    asyncio.create_task(bootstrap())
    rendement
    planificateur.arrêt(attente=Faux)
    bot_tâche.annuler()

app = FastAPI(title="Value Bot", lifespan=lifespan)

@app.get("/")
async def santé():
    return {"status": "ok", "bot": BRAND_NAME, "cached_date": STATE.get("last_scan_date", "")}

si __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")), reload=False)
