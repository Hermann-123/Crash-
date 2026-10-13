"""
Moteur Dixon-Coles avec matching d'équipes ULTRA-ROBUSTE.
Gère toutes les variations de noms d'équipes entre les APIs.
"""
from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
from typing import Optional

import httpx

LEAGUES = {
    # ===== SAISON 2025-2026 =====
    "England Premier League 25/26":    "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
    "England Championship 25/26":       "https://www.football-data.co.uk/mmz4281/2526/E1.csv",
    "England League One 25/26":         "https://www.football-data.co.uk/mmz4281/2526/E2.csv",
    "England League Two 25/26":         "https://www.football-data.co.uk/mmz4281/2526/E3.csv",
    "Spain La Liga 25/26":              "https://www.football-data.co.uk/mmz4281/2526/SP1.csv",
    "Spain Segunda 25/26":              "https://www.football-data.co.uk/mmz4281/2526/SP2.csv",
    "Italy Serie A 25/26":              "https://www.football-data.co.uk/mmz4281/2526/I1.csv",
    "Italy Serie B 25/26":              "https://www.football-data.co.uk/mmz4281/2526/I2.csv",
    "Germany Bundesliga 25/26":         "https://www.football-data.co.uk/mmz4281/2526/D1.csv",
    "Germany 2. Bundesliga 25/26":      "https://www.football-data.co.uk/mmz4281/2526/D2.csv",
    "France Ligue 1 25/26":             "https://www.football-data.co.uk/mmz4281/2526/F1.csv",
    "France Ligue 2 25/26":             "https://www.football-data.co.uk/mmz4281/2526/F2.csv",
    "Netherlands Eredivisie 25/26":     "https://www.football-data.co.uk/mmz4281/2526/N1.csv",
    "Netherlands Eerste Divisie 25/26": "https://www.football-data.co.uk/mmz4281/2526/N2.csv",
    "Portugal Primeira Liga 25/26":     "https://www.football-data.co.uk/mmz4281/2526/P1.csv",
    "Belgium Jupiler Pro League 25/26": "https://www.football-data.co.uk/mmz4281/2526/B1.csv",
    "Turkey Super Lig 25/26":           "https://www.football-data.co.uk/mmz4281/2526/T1.csv",
    "Turkey 1. Lig 25/26":              "https://www.football-data.co.uk/mmz4281/2526/T2.csv",
    "Greece Super League 25/26":        "https://www.football-data.co.uk/mmz4281/2526/G1.csv",
    "Scotland Premiership 25/26":       "https://www.football-data.co.uk/mmz4281/2526/SC0.csv",

    # ===== SAISON 2026-2027 =====
    "England Premier League 26/27":    "https://www.football-data.co.uk/mmz4281/2627/E0.csv",
    "England Championship 26/27":       "https://www.football-data.co.uk/mmz4281/2627/E1.csv",
    "England League One 26/27":         "https://www.football-data.co.uk/mmz4281/2627/E2.csv",
    "England League Two 26/27":         "https://www.football-data.co.uk/mmz4281/2627/E3.csv",
    "Spain La Liga 26/27":              "https://www.football-data.co.uk/mmz4281/2627/SP1.csv",
    "Spain Segunda 26/27":              "https://www.football-data.co.uk/mmz4281/2627/SP2.csv",
    "Italy Serie A 26/27":              "https://www.football-data.co.uk/mmz4281/2627/I1.csv",
    "Italy Serie B 26/27":              "https://www.football-data.co.uk/mmz4281/2627/I2.csv",
    "Germany Bundesliga 26/27":         "https://www.football-data.co.uk/mmz4281/2627/D1.csv",
    "Germany 2. Bundesliga 26/27":      "https://www.football-data.co.uk/mmz4281/2627/D2.csv",
    "France Ligue 1 26/27":             "https://www.football-data.co.uk/mmz4281/2627/F1.csv",
    "France Ligue 2 26/27":             "https://www.football-data.co.uk/mmz4281/2627/F2.csv",
    "Netherlands Eredivisie 26/27":     "https://www.football-data.co.uk/mmz4281/2627/N1.csv",
    "Netherlands Eerste Divisie 26/27": "https://www.football-data.co.uk/mmz4281/2627/N2.csv",
    "Portugal Primeira Liga 26/27":     "https://www.football-data.co.uk/mmz4281/2627/P1.csv",
    "Belgium Jupiler Pro League 26/27": "https://www.football-data.co.uk/mmz4281/2627/B1.csv",
    "Turkey Super Lig 26/27":           "https://www.football-data.co.uk/mmz4281/2627/T1.csv",
    "Turkey 1. Lig 26/27":              "https://www.football-data.co.uk/mmz4281/2627/T2.csv",
    "Greece Super League 26/27":        "https://www.football-data.co.uk/mmz4281/2627/G1.csv",
    "Scotland Premiership 26/27":       "https://www.football-data.co.uk/mmz4281/2627/SC0.csv",
}


# =========================================================
# MOTS À IGNORER dans le matching (préfixes/suffixes communs)
# =========================================================
IGNORE_WORDS = {
    # Types de clubs
    "fc", "cf", "sc", "ac", "afc", "as", "ss", "ssc", "rc", "cd", "fk",
    "sk", "jk", "sd", "ud", "ca", "cs", "us", "sv", "bv", "bsc", "fsv",
    "vfb", "vfl", "tsg", "spvgg", "kfc", "krc", "kv", "kaa",
    "club", "de", "futbol", "futebol", "calcio", "voetbal",
    # Suffixes génériques
    "united", "utd", "city", "town", "albion", "rovers", "wanderers",
    "athletic", "sporting", "sport", "deportivo",
    # Chiffres/années
    "1", "2", "3", "04", "05", "07", "09", "1899", "1900", "1919", "1927",
    # Anglais
    "the", "and", "of",
}


# =========================================================
# DICTIONNAIRE D'ALIAS — Mapping API → CSV
# =========================================================
TEAM_ALIASES = {
    # === Espagne ===
    "atletico madrid": "ath madrid", "atletico de madrid": "ath madrid",
    "atlético madrid": "ath madrid", "atletico": "ath madrid",
    "athletic bilbao": "ath bilbao", "athletic club": "ath bilbao", "athletic": "ath bilbao",
    "real betis": "betis", "betis": "betis",
    "celta vigo": "celta", "celta de vigo": "celta",
    "real sociedad": "sociedad", "sociedad": "sociedad",
    "deportivo alaves": "alaves", "deportivo alavés": "alaves",
    "rayo vallecano": "vallecano",
    "cadiz cf": "cadiz", "cadiz": "cadiz",
    "ud las palmas": "las palmas", "las palmas": "las palmas",
    "girona fc": "girona", "girona": "girona",
    "valencia cf": "valencia", "valencia": "valencia",
    "villarreal cf": "villarreal", "villarreal": "villarreal",
    "sevilla fc": "sevilla", "sevilla": "sevilla",
    "fc barcelona": "barcelona", "barcelona": "barcelona", "barca": "barcelona",
    "real madrid": "real madrid", "madrid": "real madrid",
    "osasuna": "osasuna", "ca osasuna": "osasuna",
    "mallorca": "mallorca", "rcd mallorca": "mallorca",
    "getafe": "getafe", "getafe cf": "getafe",
    
    # === Allemagne ===
    "bayern munich": "bayern munich", "bayern munchen": "bayern munich",
    "bayern münchen": "bayern munich", "fc bayern": "bayern munich",
    "fc bayern munich": "bayern munich", "fc bayern munchen": "bayern munich",
    "borussia dortmund": "dortmund", "bvb dortmund": "dortmund", "dortmund": "dortmund",
    "borussia monchengladbach": "m'gladbach", "b. monchengladbach": "m'gladbach",
    "monchengladbach": "m'gladbach", "mönchengladbach": "m'gladbach",
    "bayer leverkusen": "leverkusen", "bayer 04 leverkusen": "leverkusen",
    "eintracht frankfurt": "eintracht", "frankfurt": "eintracht",
    "rb leipzig": "rb leipzig", "rasenballsport leipzig": "rb leipzig", "leipzig": "rb leipzig",
    "vfb stuttgart": "stuttgart", "stuttgart": "stuttgart",
    "sc freiburg": "freiburg", "sport club freiburg": "freiburg",
    "mainz 05": "mainz", "1. fsv mainz 05": "mainz", "mainz": "mainz",
    "vfl wolfsburg": "wolfsburg", "wolfsburg": "wolfsburg",
    "fc augsburg": "augsburg", "augsburg": "augsburg",
    "werder bremen": "werder", "sv werder bremen": "werder",
    "union berlin": "union berlin", "1. fc union berlin": "union berlin",
    "vfl bochum": "bochum", "bochum": "bochum",
    "tsg hoffenheim": "hoffenheim", "1899 hoffenheim": "hoffenheim", "hoffenheim": "hoffenheim",
    
    # === Italie ===
    "inter milan": "inter", "internazionale": "inter", "fc internazionale": "inter",
    "inter": "inter", "internazionale milano": "inter",
    "ac milan": "milan", "milan": "milan", "acmilan": "milan",
    "as roma": "roma", "roma": "roma",
    "ss lazio": "lazio", "lazio": "lazio",
    "ssc napoli": "napoli", "napoli": "napoli",
    "juventus fc": "juventus", "juventus": "juventus", "juve": "juventus",
    "atalanta bc": "atalanta", "atalanta": "atalanta",
    "fiorentina": "fiorentina", "acf fiorentina": "fiorentina", "ac fiorentina": "fiorentina",
    "bologna": "bologna", "bologna fc": "bologna",
    "torino fc": "torino", "torino": "torino",
    "udinese": "udinese", "udinese calcio": "udinese",
    "genoa": "genoa", "genoa cfc": "genoa",
    "cagliari": "cagliari", "cagliari calcio": "cagliari",
    "verona": "verona", "hellas verona": "verona",
    "lecce": "lecce", "us lecce": "lecce",
    "empoli": "empoli", "empoli fc": "empoli",
    "monza": "monza", "ac monza": "monza",
    "como": "como", "como 1907": "como",
    "parma": "parma", "parma calcio": "parma",
    "venezia": "venezia", "venezia fc": "venezia",
    
    # === Angleterre ===
    "manchester city": "man city", "man city": "man city",
    "manchester united": "man united", "man united": "man united",
    "man utd": "man united", "manchester utd": "man united",
    "newcastle united": "newcastle", "newcastle": "newcastle", "newcastle utd": "newcastle",
    "tottenham hotspur": "tottenham", "tottenham": "tottenham", "spurs": "tottenham",
    "brighton hove albion": "brighton", "brighton & hove albion": "brighton", "brighton": "brighton",
    "nottingham forest": "nott'm forest", "nottm forest": "nott'm forest", "forest": "nott'm forest",
    "wolverhampton wanderers": "wolves", "wolves": "wolves",
    "west ham united": "west ham", "west ham": "west ham",
    "crystal palace": "crystal palace", "palace": "crystal palace",
    "leicester city": "leicester", "leicester": "leicester",
    "everton fc": "everton", "everton": "everton",
    "aston villa": "aston villa", "villa": "aston villa",
    "afc bournemouth": "bournemouth", "fc bournemouth": "bournemouth", "bournemouth": "bournemouth",
    "brentford fc": "brentford", "brentford": "brentford",
    "fulham fc": "fulham", "fulham": "fulham",
    "ipswich town": "ipswich", "ipswich": "ipswich",
    "southampton fc": "southampton", "southampton": "southampton",
    "luton town": "luton", "luton": "luton",
    "sheffield united": "sheffield utd", "sheffield utd": "sheffield utd",
    "sheffield wednesday": "sheffield weds", "sheffield weds": "sheffield weds",
    "queens park rangers": "qpr", "qpr": "qpr",
    "west bromwich albion": "west brom", "west brom": "west brom", "west bromwich": "west brom",
    "norwich city": "norwich", "norwich": "norwich",
    "swansea city": "swansea", "swansea": "swansea",
    "cardiff city": "cardiff", "cardiff": "cardiff",
    "hull city": "hull", "hull": "hull",
    "coventry city": "coventry", "coventry": "coventry",
    "stoke city": "stoke", "stoke": "stoke",
    "preston north end": "preston", "preston": "preston",
    "blackburn rovers": "blackburn", "blackburn": "blackburn",
    "bristol city": "bristol city", "bristol": "bristol city",
    "millwall": "millwall", "millwall fc": "millwall",
    "watford fc": "watford", "watford": "watford",
    "sunderland afc": "sunderland", "sunderland": "sunderland",
    "burnley fc": "burnley", "burnley": "burnley",
    "charlton athletic": "charlton", "charlton": "charlton",
    "birmingham city": "birmingham", "birmingham": "birmingham",
    
    # === France ===
    "paris saint germain": "paris sg", "paris saint-germain": "paris sg", "psg": "paris sg",
    "olympique de marseille": "marseille", "olympique marseille": "marseille", "marseille": "marseille",
    "olympique lyonnais": "lyon", "lyon": "lyon", "olympique lyon": "lyon",
    "as monaco": "monaco", "monaco": "monaco",
    "as saint-etienne": "saint-etienne", "as saint etienne": "saint-etienne", "saint-etienne": "saint-etienne",
    "rc lens": "lens", "lens": "lens",
    "losc lille": "lille", "lille osc": "lille", "lille": "lille",
    "stade rennais": "rennes", "rennes": "rennes",
    "stade brestois": "brest", "brest": "brest",
    "rc strasbourg": "strasbourg", "rc strasbourg alsace": "strasbourg", "strasbourg": "strasbourg",
    "fc nantes": "nantes", "nantes": "nantes",
    "toulouse fc": "toulouse", "toulouse": "toulouse",
    "montpellier hsc": "montpellier", "montpellier": "montpellier",
    "le havre": "le havre", "le havre ac": "le havre",
    "fc metz": "metz", "metz": "metz",
    "stade de reims": "reims", "reims": "reims",
    "aj auxerre": "auxerre", "auxerre": "auxerre",
    "angers sco": "angers", "angers": "angers",
    
    # === Pays-Bas ===
    "ajax amsterdam": "ajax", "afc ajax": "ajax", "ajax": "ajax",
    "psv eindhoven": "psv", "psv": "psv",
    "feyenoord rotterdam": "feyenoord", "feyenoord": "feyenoord",
    "az alkmaar": "az alkmaar", "az": "az alkmaar",
    "fc utrecht": "utrecht", "utrecht": "utrecht",
    "fc twente": "twente", "twente": "twente",
    "sc heerenveen": "heerenveen", "heerenveen": "heerenveen",
    "vitesse arnhem": "vitesse", "vitesse": "vitesse",
    "fc groningen": "groningen", "groningen": "groningen",
    
    # === Portugal ===
    "fc porto": "porto", "porto": "porto",
    "sl benfica": "benfica", "benfica": "benfica",
    "sporting cp": "sp lisbon", "sporting lisbon": "sp lisbon",
    "sporting clube de portugal": "sp lisbon", "sporting": "sp lisbon",
    "sc braga": "braga", "braga": "braga",
    "vitoria guimaraes": "vitoria", "vitoria sc": "vitoria", "vitoria": "vitoria",
    "boavista fc": "boavista", "boavista": "boavista",
    "rio ave": "rio ave", "rio ave fc": "rio ave",
    "famalicao": "famalicao", "fc famalicao": "famalicao",
    
    # === Turquie ===
    "galatasaray sk": "galatasaray", "galatasaray": "galatasaray",
    "fenerbahce sk": "fenerbahce", "fenerbahce": "fenerbahce",
    "besiktas jk": "besiktas", "besiktas": "besiktas",
    "trabzonspor": "trabzonspor", "trabzonspor k": "trabzonspor",
    "basaksehir": "istanbul bb", "istanbul basaksehir": "istanbul bb",
    "istanbul bb": "istanbul bb",
    
    # === Grèce ===
    "olympiacos": "olympiacos", "olympiacos piraeus": "olympiacos",
    "panathinaikos": "panathinaikos", "panathinaikos fc": "panathinaikos",
    "aek athens": "aek", "aek": "aek",
    "paok thessaloniki": "paok", "paok": "paok",
    
    # === Écosse ===
    "celtic fc": "celtic", "celtic": "celtic",
    "rangers fc": "rangers", "rangers": "rangers",
    "aberdeen fc": "aberdeen", "aberdeen": "aberdeen",
    "heart of midlothian": "hearts", "hearts": "hearts",
    "hibernian": "hibernian", "hibernian fc": "hibernian",
}


# =========================================================
# NORMALISATION ULTRA-ROBUSTE
# =========================================================
def _normalize_team_name(s: str) -> str:
    """
    Normalise un nom d'équipe : enlève accents, ponctuation, 
    préfixes/suffixes communs, chiffres d'année.
    """
    if not s:
        return ""
    s = str(s).lower().strip()
    # Accents
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    # Ponctuation
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    # Découpe en mots
    words = s.split()
    # Filtre les mots à ignorer
    filtered = []
    for w in words:
        if w in IGNORE_WORDS:
            continue
        if len(w) <= 1:  # ignore "a", "b", "1", etc.
            continue
        filtered.append(w)
    if not filtered:
        # Si tout est filtré, on garde les mots originaux
        return " ".join(words).strip()
    return " ".join(filtered).strip()


def _similarity_score(name_a: str, name_b: str) -> float:
    """
    Score de similarité entre deux noms d'équipes (0.0 à 1.0).
    Combine plusieurs métriques : exact, containment, Jaccard, first-word.
    """
    a = _normalize_team_name(name_a)
    b = _normalize_team_name(name_b)
    
    if not a or not b:
        return 0.0
    
    # Match exact après normalisation
    if a == b:
        return 1.0
    
    # Containment : l'un contient l'autre en entier
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        ratio = shorter / longer if longer > 0 else 0
        return 0.70 + 0.25 * ratio  # Entre 0.70 et 0.95
    
    # Token Jaccard
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    
    if not tokens_a or not tokens_b:
        return 0.0
    
    inter = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(inter) / len(union) if union else 0.0
    
    # Premier mot identique (souvent le plus significatif)
    first_a = a.split()[0] if a.split() else ""
    first_b = b.split()[0] if b.split() else ""
    first_match = 0.20 if (first_a and first_a == first_b) else 0.0
    
    # Dernier mot identique
    last_a = a.split()[-1] if a.split() else ""
    last_b = b.split()[-1] if b.split() else ""
    last_match = 0.10 if (last_a and last_a == last_b and last_a != first_a) else 0.0
    
    return min(1.0, jaccard * 0.70 + first_match + last_match)


# =========================================================
# MODÈLE DIXON-COLES
# =========================================================
class DixonColesModel:
    def __init__(self):
        self.teams: dict = {}
        self.home_advantage = 1.15
        self.avg_goals = 1.35
        self._trained = False
        self._normalized_index: dict = {}  # cache {normalized_name: csv_name}
        self._alias_index: dict = {}       # cache {alias: csv_name}

    def train(self, results: list[dict]) -> bool:
        if len(results) < 100:
            return False
        total_goals = sum(r["home_goals"] + r["away_goals"] for r in results)
        self.avg_goals = max(0.5, total_goals / (2 * len(results)))

        team_stats: dict = {}
        for r in results:
            h, a = r["home"], r["away"]
            hg, ag = r["home_goals"], r["away_goals"]
            lg = r["league"]
            for t in (h, a):
                if t not in team_stats:
                    team_stats[t] = {"gf": 0, "ga": 0, "n": 0, "league": lg}
            team_stats[h]["gf"] += hg
            team_stats[h]["ga"] += ag
            team_stats[h]["n"] += 1
            team_stats[a]["gf"] += ag
            team_stats[a]["ga"] += hg
            team_stats[a]["n"] += 1

        for t, s in team_stats.items():
            if s["n"] < 3:
                continue
            att = (s["gf"] / s["n"]) / self.avg_goals
            dfn = (s["ga"] / s["n"]) / self.avg_goals
            self.teams[t] = {
                "att": max(0.4, min(2.5, att)),
                "def": max(0.4, min(2.5, dfn)),
                "league": s["league"],
            }
        self._trained = len(self.teams) >= 20
        
        # Pré-calcul des index pour accélérer le matching
        self._build_indexes()
        return self._trained

    def _build_indexes(self):
        """Pré-calcule les indexes de normalisation et d'alias pour perf."""
        self._normalized_index = {}
        self._alias_index = {}
        
        # Index normalisé : "barcelona" → "Barcelona"
        for csv_name in self.teams:
            n = _normalize_team_name(csv_name)
            if n:
                self._normalized_index[n] = csv_name
        
        # Index alias : "fc barcelona" → "Barcelona"
        for alias, target in TEAM_ALIASES.items():
            target_norm = _normalize_team_name(target)
            for csv_name in self.teams:
                if _normalize_team_name(csv_name) == target_norm:
                    self._alias_index[alias] = csv_name
                    break

    def _poisson(self, lam: float, k: int) -> float:
        return (lam ** k) * math.exp(-lam) / math.factorial(k)

    def predict(self, home: str, away: str, max_goals: int = 6) -> Optional[dict]:
        full = self.predict_all(home, away, max_goals)
        if not full:
            return None
        return {
            "home": full["p_home"],
            "draw": full["p_draw"],
            "away": full["p_away"],
            "lambda_home": full["lambda_home"],
            "lambda_away": full["lambda_away"],
        }

    def predict_all(self, home: str, away: str, max_goals: int = 8) -> Optional[dict]:
        h_key = self._find_team(home)
        a_key = self._find_team(away)
        if not h_key or not a_key:
            return None

        h_data = self.teams[h_key]
        a_data = self.teams[a_key]

        lambda_home = h_data["att"] * a_data["def"] * self.avg_goals * self.home_advantage
        lambda_away = a_data["att"] * h_data["def"] * self.avg_goals

        p_home = p_draw = p_away = 0.0
        p_over_15 = p_over_25 = p_over_35 = 0.0
        p_btts_yes = 0.0
        total = 0.0

        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = self._poisson(lambda_home, i) * self._poisson(lambda_away, j)
                total += p
                if i > j:
                    p_home += p
                elif i == j:
                    p_draw += p
                else:
                    p_away += p
                if (i + j) >= 2:
                    p_over_15 += p
                if (i + j) >= 3:
                    p_over_25 += p
                if (i + j) >= 4:
                    p_over_35 += p
                if i >= 1 and j >= 1:
                    p_btts_yes += p

        if total <= 0:
            return None

        return {
            "p_home": round(p_home / total, 4),
            "p_draw": round(p_draw / total, 4),
            "p_away": round(p_away / total, 4),
            "over_1.5": round(p_over_15 / total, 4),
            "under_1.5": round((total - p_over_15) / total, 4),
            "over_2.5": round(p_over_25 / total, 4),
            "under_2.5": round((total - p_over_25) / total, 4),
            "over_3.5": round(p_over_35 / total, 4),
            "under_3.5": round((total - p_over_35) / total, 4),
            "btts_yes": round(p_btts_yes / total, 4),
            "btts_no": round((total - p_btts_yes) / total, 4),
            "lambda_home": round(lambda_home, 2),
            "lambda_away": round(lambda_away, 2),
        }

    def _find_team(self, name: str) -> Optional[str]:
        """
        Trouve une équipe CSV à partir d'un nom d'API.
        Utilise 5 niveaux de matching, du plus strict au plus permissif.
        """
        if not name or not self.teams:
            return None
        
        raw = str(name).lower().strip()
        
        # === Niveau 1 : Alias direct ===
        alias = TEAM_ALIASES.get(raw)
        if alias:
            target_norm = _normalize_team_name(alias)
            for csv_name in self.teams:
                if _normalize_team_name(csv_name) == target_norm:
                    return csv_name
        
        # === Niveau 2 : Match exact après normalisation ===
        n_norm = _normalize_team_name(name)
        if n_norm and n_norm in self._normalized_index:
            return self._normalized_index[n_norm]
        
        # === Niveau 3 : Containment (l'un contient l'autre) ===
        if n_norm:
            for csv_name in self.teams:
                csv_norm = _normalize_team_name(csv_name)
                if csv_norm and (n_norm in csv_norm or csv_norm in n_norm):
                    # Vérifie que la longueur est raisonnable
                    if min(len(n_norm), len(csv_norm)) >= 4:
                        return csv_name
        
        # === Niveau 4 : Scoring de similarité ===
        best_team = None
        best_score = 0.0
        THRESHOLD = 0.60
        
        for csv_name in self.teams:
            score = _similarity_score(name, csv_name)
            if score > best_score:
                best_score = score
                best_team = csv_name
                if score >= 0.95:  # Optimisation : on arrête si match quasi-parfait
                    break
        
        if best_team and best_score >= THRESHOLD:
            return best_team
        
        return None


async def download_and_train() -> Optional[DixonColesModel]:
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for league, url in LEAGUES.items():
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    print(f"  ⚠️ {league}: HTTP {r.status_code}")
                    continue
                rows = list(csv.DictReader(io.StringIO(r.text)))
                added = 0
                for row in rows:
                    try:
                        hg = int(row.get("FTHG", ""))
                        ag = int(row.get("FTAG", ""))
                    except (ValueError, TypeError):
                        continue
                    h = row.get("HomeTeam", "").strip()
                    a = row.get("AwayTeam", "").strip()
                    if not h or not a:
                        continue
                    results.append({
                        "home": h, "away": a,
                        "home_goals": hg, "away_goals": ag,
                        "league": league,
                    })
                    added += 1
                print(f"📊 Dixon-Coles : {league} → {added} matchs")
            except Exception as e:
                print(f"⚠️ Dixon-Coles {league}: {e}")

    if not results:
        print("❌ Dixon-Coles : aucune donnée")
        return None

    model = DixonColesModel()
    ok = model.train(results)
    if ok:
        print(f"✅ Dixon-Coles entraîné sur {len(results)} matchs ({len(model.teams)} équipes)")
    return model if ok else None
