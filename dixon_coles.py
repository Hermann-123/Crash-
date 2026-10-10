"""
Moteur Dixon-Coles simplifié.
Télécharge l'historique des 5 grands championnats (football-data.co.uk),
calcule les forces offensives/défensives et prédit les probas 1X2.
"""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime
from typing import Optional

import httpx

# URLs football-data.co.uk (CSV publics, ~20 Mo au total)
LEAGUES = {
    "England Premier League": "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
    "England Championship":    "https://www.football-data.co.uk/mmz4281/2526/E1.csv",
    "Spain La Liga":           "https://www.football-data.co.uk/mmz4281/2526/SP1.csv",
    "Italy Serie A":           "https://www.football-data.co.uk/mmz4281/2526/I1.csv",
    "Germany Bundesliga":      "https://www.football-data.co.uk/mmz4281/2526/D1.csv",
    "France Ligue 1":          "https://www.football-data.co.uk/mmz4281/2526/F1.csv",
}
# Saison précédente en secours
LEAGUES_PREV = {
    "England Premier League": "https://www.football-data.co.uk/mmz4281/2425/E0.csv",
    "Spain La Liga":           "https://www.football-data.co.uk/mmz4281/2425/SP1.csv",
    "Italy Serie A":           "https://www.football-data.co.uk/mmz4281/2425/I1.csv",
    "Germany Bundesliga":      "https://www.football-data.co.uk/mmz4281/2425/D1.csv",
    "France Ligue 1":          "https://www.football-data.co.uk/mmz4281/2425/F1.csv",
}


class DixonColesModel:
    """
    Modèle Poisson bivarié simplifié.
    Force attaque/défense + avantage domicile.
    """

    def __init__(self):
        self.teams: dict = {}          # {team: {"att": float, "def": float, "league": str}}
        self.home_advantage = 1.15     # multiplicateur domicile
        self.avg_goals = 1.35          # moyenne buts par équipe
        self._trained = False

    def train(self, results: list[dict]) -> bool:
        """
        results = [{"home": str, "away": str, "home_goals": int, "away_goals": int, "league": str}]
        """
        if len(results) < 100:
            return False

        # 1. Moyenne de buts par match (home + away)
        total_goals = sum(r["home_goals"] + r["away_goals"] for r in results)
        self.avg_goals = max(0.5, total_goals / (2 * len(results)))

        # 2. Calcul des forces par équipe
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

        # 3. Forces normalisées
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
        """Retourne {"home": p, "draw": p, "away": p} ou None."""
        h_key = self._find_team(home)
        a_key = self._find_team(away)
        if not h_key or not a_key:
            return None

        h_data = self.teams[h_key]
        a_data = self.teams[a_key]

        # Expected goals (buts attendus)
        lambda_home = h_data["att"] * a_data["def"] * self.avg_goals * self.home_advantage
        lambda_away = a_data["att"] * h_data["def"] * self.avg_goals

        # Proba de chaque score
        p_home = p_draw = p_away = 0.0
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = self._poisson(lambda_home, i) * self._poisson(lambda_away, j)
                if i > j:
                    p_home += p
                elif i == j:
                    p_draw += p
                else:
                    p_away += p

        total = p_home + p_draw + p_away
        if total <= 0:
            return None

        return {
            "home": round(p_home / total, 4),
            "draw": round(p_draw / total, 4),
            "away": round(p_away / total, 4),
            "lambda_home": round(lambda_home, 2),
            "lambda_away": round(lambda_away, 2),
        }

    def _find_team(self, name: str) -> Optional[str]:
        """Match approximatif du nom."""
        if not name:
            return None
        n = name.lower().strip()
        # 1. Exact
        for t in self.teams:
            if t.lower().strip() == n:
                return t
        # 2. Inclusion
        for t in self.teams:
            tl = t.lower().strip()
            if n in tl or tl in n:
                return t
        # 3. Premier mot
        first = n.split()[0] if n.split() else ""
        if len(first) > 3:
            for t in self.teams:
                if t.lower().startswith(first):
                    return t
        return None


async def download_and_train() -> Optional[DixonColesModel]:
    """Télécharge les CSV et entraîne le modèle."""
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for league, url in LEAGUES.items():
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                rows = list(csv.DictReader(io.StringIO(r.text)))
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
                print(f"📊 Dixon-Coles : {league} → {len(rows)} matchs")
            except Exception as e:
                print(f"⚠️ Dixon-Coles {league}: {e}")

    if not results:
        print("❌ Dixon-Coles : aucune donnée téléchargée")
        return None

    model = DixonColesModel()
    ok = model.train(results)
    if ok:
        print(f"✅ Dixon-Coles entraîné sur {len(results)} matchs ({len(model.teams)} équipes)")
    else:
        print("⚠️ Dixon-Coles : entraînement insuffisant")
    return model if ok else None
