"""
Moteur Dixon-Coles + marchés dérivés (1X2, O/U, BTTS).
Avec 20 championnats et mapping d'alias pour les noms d'équipes.
"""
from __future__ import annotations

import csv
import io
import math
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
# MAPPING D'ALIAS — noms API → noms football-data.co.uk
# =========================================================
TEAM_ALIASES = {
    # Espagne
    "atletico madrid": "ath madrid",
    "atlético madrid": "ath madrid",
    "athletic bilbao": "ath bilbao",
    "athletic club": "ath bilbao",
    "real betis": "betis",
    "celta vigo": "celta",
    "real sociedad": "sociedad",
    "deportivo alaves": "alaves",
    "deportivo alavés": "alaves",
    "rayo vallecano": "vallecano",
    "cadiz cf": "cadiz",
    "ud las palmas": "las palmas",
    "girona fc": "girona",
    "osasuna": "osasuna",
    "mallorca": "mallorca",
    "getafe": "getafe",
    "valencia cf": "valencia",
    "villarreal cf": "villarreal",
    "sevilla fc": "sevilla",
    # Allemagne
    "bayern munich": "bayern munich",
    "bayern munchen": "bayern munich",
    "bayern münchen": "bayern munich",
    "borussia dortmund": "dortmund",
    "borussia monchengladbach": "m'gladbach",
    "borussia mönchengladbach": "m'gladbach",
    "bayer leverkusen": "leverkusen",
    "bayer 04 leverkusen": "leverkusen",
    "eintracht frankfurt": "eintracht",
    "rb leipzig": "rb leipzig",
    "rasenballsport leipzig": "rb leipzig",
    "vfb stuttgart": "stuttgart",
    "sc freiburg": "freiburg",
    "mainz 05": "mainz",
    "1. fsv mainz 05": "mainz",
    "vfl wolfsburg": "wolfsburg",
    "fc augsburg": "augsburg",
    "werder bremen": "werder",
    "union berlin": "union berlin",
    "vfl bochum": "bochum",
    "tsg hoffenheim": "hoffenheim",
    "1899 hoffenheim": "hoffenheim",
    # Italie
    "inter milan": "inter",
    "internazionale": "inter",
    "fc internazionale": "inter",
    "ac milan": "milan",
    "as roma": "roma",
    "ss lazio": "lazio",
    "ssc napoli": "napoli",
    "juventus fc": "juventus",
    "atalanta bc": "atalanta",
    "fiorentina": "fiorentina",
    "acf fiorentina": "fiorentina",
    "bologna": "bologna",
    "torino fc": "torino",
    "udinese": "udinese",
    "genoa": "genoa",
    "cagliari": "cagliari",
    "verona": "verona",
    "hellas verona": "verona",
    "lecce": "lecce",
    "empoli": "empoli",
    "monza": "monza",
    "como": "como",
    "parma": "parma",
    "venezia": "venezia",
    # Angleterre
    "manchester city": "man city",
    "manchester united": "man united",
    "man utd": "man united",
    "newcastle united": "newcastle",
    "tottenham hotspur": "tottenham",
    "spurs": "tottenham",
    "brighton hove albion": "brighton",
    "brighton & hove albion": "brighton",
    "nottingham forest": "nott'm forest",
    "nottm forest": "nott'm forest",
    "wolverhampton wanderers": "wolves",
    "west ham united": "west ham",
    "crystal palace": "crystal palace",
    "leicester city": "leicester",
    "everton fc": "everton",
    "aston villa": "aston villa",
    "afc bournemouth": "bournemouth",
    "fc bournemouth": "bournemouth",
    "brentford fc": "brentford",
    "fulham fc": "fulham",
    "ipswich town": "ipswich",
    "southampton fc": "southampton",
    "luton town": "luton",
    "sheffield united": "sheffield utd",
    "sheffield wednesday": "sheffield weds",
    "queens park rangers": "qpr",
    "west bromwich albion": "west brom",
    "norwich city": "norwich",
    "swansea city": "swansea",
    "cardiff city": "cardiff",
    "hull city": "hull",
    "coventry city": "coventry",
    "stoke city": "stoke",
    "preston north end": "preston",
    "blackburn rovers": "blackburn",
    "bristol city": "bristol city",
    "millwall": "millwall",
    "watford fc": "watford",
    "sunderland afc": "sunderland",
    # France
    "paris saint germain": "paris sg",
    "paris saint-germain": "paris sg",
    "psg": "paris sg",
    "olympique de marseille": "marseille",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "as monaco": "monaco",
    "as saint-etienne": "saint-etienne",
    "as saint etienne": "saint-etienne",
    "rc lens": "lens",
    "losc lille": "lille",
    "lille osc": "lille",
    "stade rennais": "rennes",
    "stade brestois": "brest",
    "rc strasbourg": "strasbourg",
    "rc strasbourg alsace": "strasbourg",
    "fc nantes": "nantes",
    "toulouse fc": "toulouse",
    "montpellier hsc": "montpellier",
    "le havre": "le havre",
    "fc metz": "metz",
    "reims": "reims",
    "stade de reims": "reims",
    "auxerre": "auxerre",
    "aj auxerre": "auxerre",
    "angers sco": "angers",
    # Pays-Bas
    "ajax amsterdam": "ajax",
    "afc ajax": "ajax",
    "psv eindhoven": "psv",
    "psv": "psv",
    "feyenoord rotterdam": "feyenoord",
    "feyenoord": "feyenoord",
    "az alkmaar": "az alkmaar",
    "az": "az alkmaar",
    "fc utrecht": "utrecht",
    "fc twente": "twente",
    "sc heerenveen": "heerenveen",
    "vitesse arnhem": "vitesse",
    "fc groningen": "groningen",
    "sparta rotterdam": "sparta rotterdam",
    # Portugal
    "fc porto": "porto",
    "sl benfica": "benfica",
    "sporting cp": "sp lisbon",
    "sporting lisbon": "sp lisbon",
    "sporting clube de portugal": "sp lisbon",
    "sc braga": "braga",
    "vitoria guimaraes": "vitoria",
    "vitoria sc": "vitoria",
    "boavista fc": "boavista",
    "rio ave": "rio ave",
    "famalicao": "famalicao",
    "fc famalicao": "famalicao",
    # Turquie
    "galatasaray sk": "galatasaray",
    "galatasaray": "galatasaray",
    "fenerbahce sk": "fenerbahce",
    "fenerbahce": "fenerbahce",
    "besiktas jk": "besiktas",
    "besiktas": "besiktas",
    "trabzonspor": "trabzonspor",
    "basaksehir": "istanbul bb",
    "istanbul basaksehir": "istanbul bb",
    # Grèce
    "olympiacos": "olympiacos",
    "olympiacos piraeus": "olympiacos",
    "panathinaikos": "panathinaikos",
    "aek athens": "aek",
    "paok thessaloniki": "paok",
    "paok": "paok",
    # Écosse
    "celtic fc": "celtic",
    "rangers fc": "rangers",
    "aberdeen fc": "aberdeen",
    "heart of midlothian": "hearts",
    "hearts": "hearts",
    "hibernian": "hibernian",
}


class DixonColesModel:
    def __init__(self):
        self.teams: dict = {}
        self.home_advantage = 1.15
        self.avg_goals = 1.35
        self._trained = False

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
        return self._trained

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
        if not name:
            return None
        n = name.lower().strip()
        
        # Vérifier les alias en premier
        alias = TEAM_ALIASES.get(n)
        if alias:
            n = alias

        # Match exact
        for t in self.teams:
            if t.lower().strip() == n:
                return t
        # Match partiel
        for t in self.teams:
            tl = t.lower().strip()
            if n in tl or tl in n:
                return t
        # Match par premier mot
        first = n.split()[0] if n.split() else ""
        if len(first) > 3:
            for t in self.teams:
                if t.lower().startswith(first):
                    return t
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
