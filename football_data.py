"""
API-Football : fixtures du jour + résultats de matchs.
Compatible avec l'ancien main.py.
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from typing import Optional

import httpx

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_BASE = "https://v3.football.api-sports.io"


def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _headers() -> dict:
    return {"x-apisports-key": API_FOOTBALL_KEY}


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    try:
        r = await client.get(f"{API_BASE}{path}", headers=_headers(),
                              params=params, timeout=15.0)
        if r.status_code >= 400:
            return {}
        data = r.json()
        errors = data.get("errors", {})
        if errors and errors != []:
            return {}
        return data
    except Exception:
        return {}


# =========================================================
# FIXTURES DU JOUR
# =========================================================
async def fetch_all_today_fixtures() -> list[dict]:
    """
    Récupère TOUS les matchs du jour via API-Football.
    Retourne une liste de dicts :
    {"event_id", "home", "away", "league", "kickoff"}
    """
    if not API_FOOTBALL_KEY:
        return []

    today = datetime.now().strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            data = await _get(client, "/fixtures", {
                "date": today,
                "timezone": "Africa/Abidjan",
            })
            fixtures = data.get("response", [])
            result = []

            for fx in fixtures:
                try:
                    info = fx.get("fixture", {})
                    teams = fx.get("teams", {})
                    league = fx.get("league", {})

                    home = teams.get("home", {}).get("name", "")
                    away = teams.get("away", {}).get("name", "")
                    date_str = info.get("date", "")
                    status = info.get("status", {}).get("short", "")

                    if not home or not away or not date_str:
                        continue
                    if status not in ("NS", "TBD", ""):
                        continue

                    result.append({
                        "event_id": f"af_{info.get('id')}",
                        "home": home,
                        "away": away,
                        "league": league.get("name", ""),
                        "kickoff": date_str,
                    })
                except Exception:
                    continue

            print(f"📡 API-Football : {len(fixtures)} bruts → {len(result)} à venir")
            return result
        except Exception as e:
            print(f"⚠️ API-Football fixtures crash: {e}")
            return []


# =========================================================
# RÉSULTAT D'UN MATCH (règlement des coupons)
# =========================================================
async def get_fixture_result(home: str, away: str, date_iso: str) -> Optional[dict]:
    """
    Cherche le résultat d'un match passé.
    Retourne {"home_goals": X, "away_goals": Y, "status": "FT"} ou None.
    """
    if not API_FOOTBALL_KEY:
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            data = await _get(client, "/fixtures", {
                "date": date_iso,
                "timezone": "Africa/Abidjan",
            })
            fixtures = data.get("response", [])
            if not fixtures:
                return None

            h_norm = _norm(home)
            a_norm = _norm(away)

            for fx in fixtures:
                teams = fx.get("teams", {})
                hn = _norm(teams.get("home", {}).get("name", ""))
                an = _norm(teams.get("away", {}).get("name", ""))

                if not ((h_norm in hn or hn in h_norm) and (a_norm in an or an in a_norm)):
                    continue

                fixture_info = fx.get("fixture", {})
                status = fixture_info.get("status", {}).get("short", "")
                goals = fx.get("goals", {})

                if status not in ("FT", "AET", "PEN"):
                    return None

                return {
                    "home_goals": goals.get("home"),
                    "away_goals": goals.get("away"),
                    "status": status,
                }
            return None
        except Exception as e:
            print(f"⚠️ get_fixture_result crash: {e}")
            return None
