"""
Moteur Dixon-Coles avec matching d'équipes ULTRA-ROBUSTE et TIME DECAY.
"""
from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
import datetime
from typing import Optional

import httpx

LEAGUES = {
    "England Premier League 25/26":     "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
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
    "Portugal Primeira Liga 25/26":     "https://www.football-data.co.uk/mmz4281/2526/P1.csv",
    "Belgium Jupiler Pro League 25/26": "https://www.football-data.co.uk/mmz4281/2526/B1.csv",
    "Turkey Super Lig 25/26":           "https://www.football-data.co.uk/mmz4281/2526/T1.csv",
    "Greece Super League 25/26":        "https://www.football-data.co.uk/mmz4281/2526/G1.csv",
    "Scotland Premiership 25/26":       "https://www.football-data.co.uk/mmz4281/2526/SC0.csv",

    "England Premier League 26/27":     "https://www.football-data.co.uk/mmz4281/2627/E0.csv",
    "England Championship 26/27":       "https://www.football-data.co.uk/mmz4281/2627/E1.csv",
    "Spain La Liga 26/27":              "https://www.football-data.co.uk/mmz4281/2627/SP1.csv",
    "Italy Serie A 26/27":              "https://www.football-data.co.uk/mmz4281/2627/I1.csv",
    "Germany Bundesliga 26/27":         "https://www.football-data.co.uk/mmz4281/2627/D1.csv",
    "France Ligue 1 26/27":             "https://www.football-data.co.uk/mmz4281/2627/F1.csv",
    "Netherlands Eredivisie 26/27":     "https://www.football-data.co.uk/mmz4281/2627/N1.csv",
    "Portugal Primeira Liga 26/27":     "https://www.football-data.co.uk/mmz4281/2627/P1.csv",
    "Belgium Jupiler Pro League 26/27": "https://www.football-data.co.uk/mmz4281/2627/B1.csv",
    "Turkey Super Lig 26/27":           "https://www.football-data.co.uk/mmz4281/2627/T1.csv",
    "Greece Super League 26/27":        "https://www.football-data.co.uk/mmz4281/2627/G1.csv",
    "Scotland Premiership 26/27":       "https://www.football-data.co.uk/mmz4281/2627/SC0.csv",
}

IGNORE_WORDS = {
    "fc", "cf", "sc", "ac", "afc", "as", "ss", "ssc", "rc", "cd", "fk",
    "sk", "jk", "sd", "ud", "ca", "cs", "us", "sv", "bv", "bsc", "fsv",
    "vfb", "vfl", "tsg", "spvgg", "kfc", "krc", "kv", "kaa",
    "club", "de", "futbol", "futebol", "calcio", "voetbal",
    "the", "and", "of",
}

TEAM_ALIASES = {
    "atletico madrid": "ath madrid", "atletico de madrid": "ath madrid",
    "atletic bilbao": "ath bilbao", "real betis": "betis",
    "celta vigo": "celta", "real sociedad": "sociedad",
    "deportivo alaves": "alaves", "rayo vallecano": "vallecano",
    "fc barcelona": "barcelona", "real madrid": "real madrid",
    "bayern munich": "bayern munich", "bayern munchen": "bayern munich",
    "borussia dortmund": "dortmund", "bayer leverkusen": "leverkusen",
    "eintracht frankfurt": "eintracht", "rb leipzig": "rb leipzig",
    "inter milan": "inter", "internazionale": "inter", "ac milan": "milan",
    "as roma": "roma", "ss lazio": "lazio", "ssc napoli": "napoli",
    "manchester city": "man city", "manchester united": "man united",
    "newcastle united": "newcastle", "tottenham hotspur": "tottenham",
    "brighton hove albion": "brighton", "nottingham forest": "nott'm forest",
    "wolverhampton wanderers": "wolves", "west ham united": "west ham",
    "leicester city": "leicester", "paris saint germain": "paris sg",
    "olympique de marseille": "marseille", "olympique lyonnais": "lyon",
    "as monaco": "monaco", "losc lille": "lille", "stade rennais": "rennes",
    "ajax amsterdam": "ajax", "psv eindhoven": "psv", "feyenoord rotterdam": "feyenoord",
    "fc porto": "porto", "sl benfica": "benfica", "sporting cp": "sp lisbon",
    "galatasaray sk": "galatasaray", "fenerbahce sk": "fenerbahce", "besiktas jk": "besiktas",
    "olympiacos": "olympiacos", "panathinaikos": "panathinaikos",
    "celtic fc": "celtic", "rangers fc": "rangers",
}

def _normalize_team_name(s: str) -> str:
    if not s: return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    words = s.split()
    filtered = [w for w in words if w not in IGNORE_WORDS and len(w) > 1]
    return " ".join(filtered).strip() if filtered else " ".join(words).strip()

def _similarity_score(name_a: str, name_b: str) -> float:
    a = _normalize_team_name(name_a)
    b = _normalize_team_name(name_b)
    if not a or not b: return 0.0
    if a == b: return 1.0
    if a in b or b in a:
        shorter, longer = min(len(a), len(b)), max(len(a), len(b))
        return 0.70 + 0.25 * (shorter / longer if longer > 0 else 0)
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if not tokens_a or not tokens_b: return 0.0
    inter, union = tokens_a & tokens_b, tokens_a | tokens_b
    jaccard = len(inter) / len(union) if union else 0.0
    first_match = 0.20 if (a.split()[0] == b.split()[0]) else 0.0
    return min(1.0, jaccard * 0.75 + first_match)

class DixonColesModel:
    def __init__(self):
        self.teams = {}
        self.home_advantage = 1.15
        self.avg_goals = 1.35
        self._trained = False
        self._normalized_index = {}

    def train(self, results):
        if len(results) < 100:
            return False
        now = datetime.datetime.now()
        def get_weight(date_str):
            if not date_str: return 1.0
            try:
                if len(date_str.split('/')[-1]) == 2:
                    dt = datetime.datetime.strptime(date_str, "%d/%m/%y")
                else:
                    dt = datetime.datetime.strptime(date_str, "%d/%m/%Y")
                days = (now - dt).days
                return math.exp(-0.005 * max(0, days))
            except:
                return 1.0
        total_weight = 0.0
        total_goals = 0.0
        total_home_goals = 0.0
        total_away_goals = 0.0
        team_stats = {}
        for r in results:
            w = get_weight(r.get("date", ""))
            hg, ag = r["home_goals"], r["away_goals"]
            total_weight += w
            total_goals += (hg + ag) * w
            total_home_goals += hg * w
            total_away_goals += ag * w
            h, a = r["home"], r["away"]
            lg = r["league"]
            for t in (h, a):
                if t not in team_stats:
                    team_stats[t] = {"gf": 0, "ga": 0, "n": 0, "league": lg}
            team_stats[h]["gf"] += hg * w; team_stats[h]["ga"] += ag * w; team_stats[h]["n"] += w
            team_stats[a]["gf"] += ag * w; team_stats[a]["ga"] += hg * w; team_stats[a]["n"] += w
        self.avg_goals = max(0.5, total_goals / (2 * total_weight)) if total_weight > 0 else 1.35
        self.home_advantage = max(1.0, min(1.40, total_home_goals / max(1.0, total_away_goals)))
        for t, s in team_stats.items():
            if s["n"] < 3: continue
            att = (s["gf"] / s["n"]) / self.avg_goals
            dfn = (s["ga"] / s["n"]) / self.avg_goals
            self.teams[t] = {"att": max(0.4, min(2.5, att)), "def": max(0.4, min(2.5, dfn)), "league": s["league"]}
        self._trained = len(self.teams) >= 20
        self._build_indexes()
        return self._trained

    def _build_indexes(self):
        self._normalized_index = {_normalize_team_name(c): c for c in self.teams if _normalize_team_name(c)}

    def _poisson(self, lam, k):
        return (lam ** k) * math.exp(-lam) / math.factorial(k)

    def predict_all(self, home, away, max_goals=8):
        h_key, a_key = self._find_team(home), self._find_team(away)
        if not h_key or not a_key: return None
        h_data, a_data = self.teams[h_key], self.teams[a_key]
        lambda_home = h_data["att"] * a_data["def"] * self.avg_goals * self.home_advantage
        lambda_away = a_data["att"] * h_data["def"] * self.avg_goals
        p_home = p_draw = p_away = 0.0
        p_over_15 = p_over_25 = p_over_35 = p_btts_yes = total = 0.0
        p_btts_over_25 = 0.0
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = self._poisson(lambda_home, i) * self._poisson(lambda_away, j)
                total += p
                if i > j: p_home += p
                elif i == j: p_draw += p
                else: p_away += p
                if (i + j) >= 2: p_over_15 += p
                if (i + j) >= 3: p_over_25 += p
                if (i + j) >= 4: p_over_35 += p
                if i >= 1 and j >= 1:
                    p_btts_yes += p
                    if (i + j) >= 3:
                        p_btts_over_25 += p
        if total <= 0: return None
        return {
            "p_home": round(p_home / total, 4), "p_draw": round(p_draw / total, 4), "p_away": round(p_away / total, 4),
            "over_1.5": round(p_over_15 / total, 4), "under_1.5": round((total - p_over_15) / total, 4),
            "over_2.5": round(p_over_25 / total, 4), "under_2.5": round((total - p_over_25) / total, 4),
            "over_3.5": round(p_over_35 / total, 4), "under_3.5": round((total - p_over_35) / total, 4),
            "btts_yes": round(p_btts_yes / total, 4), "btts_no": round((total - p_btts_yes) / total, 4),
            "btts_over_25": round(p_btts_over_25 / total, 4),
            "lambda_home": round(lambda_home, 2),
            "lambda_away": round(lambda_away, 2),
        }

    def _find_team(self, name):
        if not name or not self.teams: return None
        raw = str(name).lower().strip()
        alias = TEAM_ALIASES.get(raw)
        if alias:
            target_norm = _normalize_team_name(alias)
            for csv_name in self.teams:
                if _normalize_team_name(csv_name) == target_norm: return csv_name
        n_norm = _normalize_team_name(name)
        if n_norm and n_norm in self._normalized_index:
            return self._normalized_index[n_norm]
        best_team, best_score = None, 0.0
        for csv_name in self.teams:
            score = _similarity_score(name, csv_name)
            if score > best_score:
                best_score, best_team = score, csv_name
                if score >= 0.95: break
        if best_team and best_score >= 0.75:
            return best_team
        return None

async def download_and_train():
    results = []
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for league, url in LEAGUES.items():
            try:
                r = await client.get(url)
                if r.status_code != 200: continue
                rows = list(csv.DictReader(io.StringIO(r.text)))
                for row in rows:
                    try: hg, ag = int(row.get("FTHG", "")), int(row.get("FTAG", ""))
                    except (ValueError, TypeError): continue
                    h, a = row.get("HomeTeam", "").strip(), row.get("AwayTeam", "").strip()
                    if not h or not a: continue
                    results.append({"home": h, "away": a, "home_goals": hg, "away_goals": ag,
                                    "league": league, "date": row.get("Date", "")})
            except Exception: pass
    if not results: return None
    model = DixonColesModel()
    return model if model.train(results) else None
