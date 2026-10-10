"""
Moteur Dixon-Coles + marchés dérivés (1X2, O/U, BTTS).
"""
from __future__ import annotations

import csv
import io
import math
from typing import Optional

import httpx

LEAGUES = {
    "England Premier League": "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
    "England Championship":    "https://www.football-data.co.uk/mmz4281/2526/E1.csv",
    "Spain La Liga":           "https://www.football-data.co.uk/mmz4281/2526/SP1.csv",
    "Italy Serie A":           "https://www.football-data.co.uk/mmz4281/2526/I1.csv",
    "Germany Bundesliga":      "https://www.football-data.co.uk/mmz4281/2526/D1.csv",
    "France Ligue 1":          "https://www.football-data.co.uk/mmz4281/2526/F1.csv",
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
        """Retourne 1X2 uniquement (compatibilité)."""
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
        """Retourne toutes les probas : 1X2 + O/U 1.5/2.5/3.5 + BTTS."""
        h_key = self._find_team(home)
        a_key = self._find_team(away)
        if not h_key or not a_key:
            return None

        h_data = self.teams[h_key]
        a_data = self.teams[a_key]

        lambda_home = h_data["att"] * a_data["def"] * self.avg_goals * self.home_advantage
        lambda_away = a_data["att"] * h_data["def"] * self.avg_goals

        # Distribution complète des scores
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
        for t in self.teams:
            if t.lower().strip() == n:
                return t
        for t in self.teams:
            tl = t.lower().strip()
            if n in tl or tl in n:
                return t
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
        print("❌ Dixon-Coles : aucune donnée")
        return None

    model = DixonColesModel()
    ok = model.train(results)
    if ok:
        print(f"✅ Dixon-Coles entraîné sur {len(results)} matchs ({len(model.teams)} équipes)")
    return model if ok else None
