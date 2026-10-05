from datetime import datetime, timezone, timedelta
from typing import List, Optional
import httpx

from app.core import settings, logger
from app.models import MatchData, SportType, TeamFormSnapshot


class OddsProvider:
    async def fetch_upcoming_matches(self) -> List[MatchData]:
        if not settings.ODDS_API_KEY:
            logger.error("ODDS_API_KEY manquante")
            return []

        url = (
            f"https://api.the-odds-api.com/v4/sports/soccer/odds/"
            f"?apiKey={settings.ODDS_API_KEY}&regions=eu&markets=h2h"
        )

        matches = []
        now_utc = datetime.now(timezone.utc)
        max_time = now_utc + timedelta(hours=24)

        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=25.0)
                r.raise_for_status()
                data = r.json()

            for m in data:
                commence_time = m.get("commence_time")
                if not commence_time:
                    continue

                try:
                    kickoff = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                except Exception:
                    continue

                if not (now_utc <= kickoff <= max_time):
                    continue

                bookmakers = m.get("bookmakers", [])
                if not bookmakers:
                    continue

                first = bookmakers[0]
                markets = first.get("markets", [])
                if not markets:
                    continue

                outcomes = markets[0].get("outcomes", [])
                home = m.get("home_team")
                away = m.get("away_team")
                if not home or not away:
                    continue

                odds_map = {x["name"]: x["price"] for x in outcomes if "name" in x and "price" in x}
                if home not in odds_map or away not in odds_map or "Draw" not in odds_map:
                    continue

                matches.append(
                    MatchData(
                        match_id=m["id"],
                        sport=SportType.SOCCER,
                        league=m.get("sport_title", "Soccer"),
                        kickoff_at=kickoff,
                        home_team=home,
                        away_team=away,
                        home_odds=float(odds_map[home]),
                        draw_odds=float(odds_map["Draw"]),
                        away_odds=float(odds_map[away]),
                        bookmaker=first.get("title"),
                    )
                )

                if len(matches) >= settings.MAX_MATCHES_PER_SCAN:
                    break

        except Exception as e:
            logger.exception(f"Erreur OddsProvider: {e}")

        return matches


class FootballDataProvider:
    BASE_URL = "https://v3.football.api-sports.io"

    async def _request(self, endpoint: str, params: dict):
        if not settings.API_FOOTBALL_KEY:
            return None

        headers = {"x-apisports-key": settings.API_FOOTBALL_KEY}
        url = f"{self.BASE_URL}{endpoint}"

        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, headers=headers, params=params, timeout=20.0)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.exception(f"Erreur API-Football {endpoint}: {e}")
            return None

    async def get_team_form(self, team_name: str) -> TeamFormSnapshot:
        search = await self._request("/teams", {"search": team_name})
        if not search or not search.get("response"):
            return TeamFormSnapshot(team_name=team_name)

        team = search["response"][0]
        team_id = team["team"]["id"]

        fixtures = await self._request("/fixtures", {"team": team_id, "last": 5})
        if not fixtures or not fixtures.get("response"):
            return TeamFormSnapshot(team_name=team_name)

        rows = fixtures["response"]

        points = 0
        gf = 0
        ga = 0
        home_gf = 0
        home_ga = 0
        away_gf = 0
        away_ga = 0
        home_n = 0
        away_n = 0

        for fx in rows:
            teams = fx["teams"]
            goals = fx["goals"]

            is_home = teams["home"]["name"].lower() == team_name.lower()
            is_away = teams["away"]["name"].lower() == team_name.lower()

            if not (is_home or is_away):
                continue

            scored = goals["home"] if is_home else goals["away"]
            conceded = goals["away"] if is_home else goals["home"]

            scored = scored or 0
            conceded = conceded or 0

            gf += scored
            ga += conceded

            if scored > conceded:
                points += 3
            elif scored == conceded:
                points += 1

            if is_home:
                home_gf += scored
                home_ga += conceded
                home_n += 1
            else:
                away_gf += scored
                away_ga += conceded
                away_n += 1

        n = max(1, len(rows))

        return TeamFormSnapshot(
            team_name=team_name,
            last5_points=points,
            last5_goals_for=gf,
            last5_goals_against=ga,
            avg_goals_for=round(gf / n, 2),
            avg_goals_against=round(ga / n, 2),
            home_avg_goals_for=round(home_gf / max(1, home_n), 2),
            home_avg_goals_against=round(home_ga / max(1, home_n), 2),
            away_avg_goals_for=round(away_gf / max(1, away_n), 2),
            away_avg_goals_against=round(away_ga / max(1, away_n), 2),
            matches_sampled=n,
        )
