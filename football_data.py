"""
API-Football : fixtures, stats équipes, corners/cartons, H2H, blessés, 
+ NOUVEAU : stats BTTS+Over2.5 sur les N derniers matchs.
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from datetime import datetime
from typing import Optional

import httpx

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_BASE = "https://v3.football.api-sports.io"

LEAGUE_MAP = {
    "soccer_epl": 39, "soccer_spain_la_liga": 140, "soccer_italy_serie_a": 135,
    "soccer_germany_bundesliga": 78, "soccer_france_ligue_one": 61,
    "soccer_uefa_champs_league": 2, "soccer_uefa_europa_league": 3,
    "soccer_efl_champ": 40, "soccer_netherlands_eredivisie": 88,
    "soccer_portugal_primeira_liga": 94, "soccer_turkey_super_league": 203,
    "soccer_belgium_first_div": 144, "soccer_scotland_premiership": 179,
    "soccer_brazil_campeonato": 71, "soccer_argentina_primera_division": 128,
}

_TEAM_ID_CACHE: dict = {}
_STATS_CACHE: dict = {}
_H2H_CACHE: dict = {}
_INJ_CACHE: dict = {}
_BTTS_STATS_CACHE: dict = {}


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
        r = await client.get(f"{API_BASE}{path}", headers=_headers(), params=params, timeout=15.0)
        if r.status_code >= 400:
            return {}
        data = r.json()
        errors = data.get("errors", {})
        if errors and errors != []:
            return {}
        return data
    except Exception:
        return {}


async def fetch_all_today_fixtures() -> list[dict]:
    if not API_FOOTBALL_KEY:
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            data = await _get(client, "/fixtures", {"date": today, "timezone": "Africa/Abidjan"})
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
                        "home": home, "away": away,
                        "league": league.get("name", ""),
                        "kickoff": date_str, "af_id": info.get("id"),
                    })
                except Exception:
                    continue
            print(f"📡 API-Football : {len(fixtures)} bruts → {len(result)} à venir")
            return result
        except Exception as e:
            print(f"⚠️ API-Football fixtures crash: {e}")
            return []


async def find_team_id(client: httpx.AsyncClient, name: str) -> Optional[int]:
    key = _norm(name)
    if not key:
        return None
    if key in _TEAM_ID_CACHE:
        return _TEAM_ID_CACHE[key]
    data = await _get(client, "/teams", {"search": name})
    teams = data.get("response", [])
    if not teams:
        _TEAM_ID_CACHE[key] = None
        return None
    for t in teams:
        team = t.get("team", {})
        tid = team.get("id")
        tname = team.get("name", "")
        if tid and (_norm(tname) == key or key in _norm(tname) or _norm(tname) in key):
            _TEAM_ID_CACHE[key] = tid
            return tid
    tid = teams[0].get("team", {}).get("id")
    _TEAM_ID_CACHE[key] = tid
    return tid


async def get_team_stats(client: httpx.AsyncClient, team_id: int, league_id: int, season: int = None) -> dict:
    if season is None:
        season = datetime.now().year
    key = f"{team_id}_{league_id}_{season}"
    if key in _STATS_CACHE:
        return _STATS_CACHE[key]
    data = await _get(client, "/teams/statistics", {"team": team_id, "league": league_id, "season": season})
    stats = data.get("response", {})
    if not stats:
        _STATS_CACHE[key] = {}
        return {}
    fixtures = stats.get("fixtures", {})
    goals = stats.get("goals", {})
    cards = stats.get("cards", {})
    corners = stats.get("corners", {})

    def avg(obj):
        try:
            v = obj.get("average")
            if v is None or v == "": return 0.0
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    result = {
        "matches_played": fixtures.get("played", {}).get("total", 0),
        "form": stats.get("form", ""),
        "goals_for_avg": avg(goals.get("for", {}).get("average", {})),
        "goals_against_avg": avg(goals.get("against", {}).get("average", {})),
        "corners_for_avg": avg(corners.get("for", {}).get("average", {})),
        "corners_against_avg": avg(corners.get("against", {}).get("average", {})),
        "cards_yellow_avg": avg(cards.get("yellow", {}).get("average", {})),
        "cards_red_avg": avg(cards.get("red", {}).get("average", {})),
    }
    _STATS_CACHE[key] = result
    return result


async def get_h2h(client: httpx.AsyncClient, team1_id: int, team2_id: int, last: int = 5) -> list[dict]:
    key = (team1_id, team2_id)
    if key in _H2H_CACHE:
        return _H2H_CACHE[key]
    data = await _get(client, "/fixtures/headtohead", {"h2h": f"{team1_id}-{team2_id}", "last": str(last)})
    fixtures = data.get("response", [])
    results = []
    for fx in fixtures:
        try:
            teams = fx.get("teams", {})
            goals = fx.get("goals", {})
            fixture = fx.get("fixture", {})
            results.append({
                "date": fixture.get("date", "")[:10],
                "home": teams.get("home", {}).get("name", ""),
                "away": teams.get("away", {}).get("name", ""),
                "home_goals": goals.get("home"),
                "away_goals": goals.get("away"),
            })
        except Exception:
            continue
    _H2H_CACHE[key] = results
    return results


async def get_injuries(client: httpx.AsyncClient, team_id: int, season: int = None) -> list[dict]:
    if season is None:
        season = datetime.now().year
    key = team_id
    if key in _INJ_CACHE:
        return _INJ_CACHE[key]
    data = await _get(client, "/injuries", {"team": team_id, "season": season})
    items = data.get("response", [])
    result = []
    seen_players = set()
    for it in items[:20]:
        try:
            player = it.get("player", {})
            pid = player.get("id")
            if pid in seen_players: continue
            seen_players.add(pid)
            result.append({
                "player": player.get("name", ""),
                "type": it.get("type", ""),
                "reason": it.get("reason", ""),
            })
        except Exception:
            continue
    _INJ_CACHE[key] = result[:10]
    return result[:10]


async def get_corners_cards(home: str, away: str, odds_sport_key: str = "") -> dict:
    if not API_FOOTBALL_KEY:
        return {"available": False}
    league_id = LEAGUE_MAP.get(odds_sport_key) if odds_sport_key else None
    if not league_id:
        return {"available": False}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            home_id, away_id = await asyncio.gather(
                find_team_id(client, home), find_team_id(client, away))
            if not home_id or not away_id:
                return {"available": False}
            home_stats, away_stats = await asyncio.gather(
                get_team_stats(client, home_id, league_id),
                get_team_stats(client, away_id, league_id))
            if not home_stats and not away_stats:
                return {"available": False}
            return {
                "available": True,
                "home_corners_for": home_stats.get("corners_for_avg", 0),
                "home_corners_against": home_stats.get("corners_against_avg", 0),
                "away_corners_for": away_stats.get("corners_for_avg", 0),
                "away_corners_against": away_stats.get("corners_against_avg", 0),
                "home_cards_avg": home_stats.get("cards_yellow_avg", 0),
                "away_cards_avg": away_stats.get("cards_yellow_avg", 0),
            }
        except Exception as e:
            print(f"⚠️ corners/cards crash {home}-{away}: {e}")
            return {"available": False}


async def get_h2h_by_names(home: str, away: str) -> dict:
    if not API_FOOTBALL_KEY:
        return {"available": False}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            home_id, away_id = await asyncio.gather(
                find_team_id(client, home), find_team_id(client, away))
            if not home_id or not away_id:
                return {"available": False}
            h2h_raw = await get_h2h(client, home_id, away_id, last=5)
            home_wins = away_wins = draws = total_goals = 0
            recent = []
            for m in h2h_raw:
                hg, ag = m.get("home_goals"), m.get("away_goals")
                if hg is None or ag is None: continue
                h_name, a_name = m.get("home", ""), m.get("away", "")
                if _norm(h_name) == _norm(home) or _norm(home) in _norm(h_name):
                    if hg > ag: home_wins += 1
                    elif hg == ag: draws += 1
                    else: away_wins += 1
                elif _norm(a_name) == _norm(home) or _norm(home) in _norm(a_name):
                    if ag > hg: home_wins += 1
                    elif ag == hg: draws += 1
                    else: away_wins += 1
                total_goals += (hg + ag)
                recent.append({"home": h_name, "away": a_name, "score": f"{hg}-{ag}"})
            total = len(h2h_raw)
            avg_goals = round(total_goals / total, 2) if total else 0
            return {
                "available": True, "total_matches": total,
                "home_wins": home_wins, "draws": draws, "away_wins": away_wins,
                "avg_total_goals": avg_goals, "recent_matches": recent[:5],
            }
        except Exception as e:
            print(f"⚠️ H2H API-Football crash {home}-{away}: {e}")
            return {"available": False}


async def get_full_context_api_football(home: str, away: str, league_name: str = "") -> dict:
    if not API_FOOTBALL_KEY:
        return {"available": False}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            home_id, away_id = await asyncio.gather(
                find_team_id(client, home), find_team_id(client, away))
            if not home_id or not away_id:
                return {"available": False}
            h2h = await get_h2h_by_names(home, away)
            home_inj = await get_injuries(client, home_id)
            away_inj = await get_injuries(client, away_id)
            if not h2h.get("available"):
                return {"available": False}
            return {
                "available": True, "h2h": h2h,
                "home_form": "?", "away_form": "?",
                "home_injuries": home_inj, "away_injuries": away_inj,
                "source": "api-football",
            }
        except Exception as e:
            print(f"⚠️ get_full_context crash {home}-{away}: {e}")
            return {"available": False}


async def enrich_match(home: str, away: str, odds_api_sport_key: str) -> dict:
    if not API_FOOTBALL_KEY:
        return {"available": False}
    league_id = LEAGUE_MAP.get(odds_api_sport_key)
    if not league_id:
        return {"available": False}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            home_id, away_id = await asyncio.gather(
                find_team_id(client, home), find_team_id(client, away))
            if not home_id or not away_id:
                return {"available": False}
            home_stats, away_stats = await asyncio.gather(
                get_team_stats(client, home_id, league_id),
                get_team_stats(client, away_id, league_id))
            h2h = await get_h2h(client, home_id, away_id)
            home_inj, away_inj = await asyncio.gather(
                get_injuries(client, home_id), get_injuries(client, away_id))
            return {
                "available": True,
                "home_stats": home_stats, "away_stats": away_stats,
                "h2h": h2h, "home_injuries": home_inj, "away_injuries": away_inj,
            }
        except Exception as e:
            print(f"⚠️ enrich_match crash {home}-{away}: {e}")
            return {"available": False}


async def get_fixture_result(home: str, away: str, date_iso: str) -> Optional[dict]:
    if not API_FOOTBALL_KEY:
        return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            data = await _get(client, "/fixtures", {"date": date_iso, "timezone": "Africa/Abidjan"})
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
                return {"home_goals": goals.get("home"), "away_goals": goals.get("away"), "status": status}
            return None
        except Exception as e:
            print(f"⚠️ get_fixture_result crash: {e}")
            return None


# =========================================================
# NOUVEAU : STATS BTTS+OVER2.5 SUR LES N DERNIERS MATCHS
# =========================================================
async def get_team_btts_stats(team_id: int, last: int = 10) -> dict:
    """
    Récupère les stats BTTS/O2.5 d'une équipe sur ses N derniers matchs.
    Retourne :
        {
            "matches_played": int,
            "goals_for_avg": float,
            "goals_against_avg": float,
            "scored_pct": float (0-1),      # % matchs où l'équipe a marqué
            "conceded_pct": float (0-1),    # % matchs où l'équipe a encaissé
            "btts_pct": float (0-1),
            "over_25_pct": float (0-1),
            "btts_over_25_pct": float (0-1) # Le graal
        }
    """
    if not API_FOOTBALL_KEY or not team_id:
        return {}

    cache_key = f"btts_{team_id}_{last}"
    if cache_key in _BTTS_STATS_CACHE:
        return _BTTS_STATS_CACHE[cache_key]

    async with httpx.AsyncClient(timeout=15.0) as client:
        data = await _get(client, "/fixtures", {
            "team": team_id,
            "last": str(last),
            "timezone": "Africa/Abidjan",
        })
        fixtures = data.get("response", [])

    if not fixtures:
        _BTTS_STATS_CACHE[cache_key] = {}
        return {}

    total = 0
    goals_for = 0
    goals_against = 0
    scored_count = 0
    conceded_count = 0
    btts_count = 0
    over_25_count = 0
    btts_over_25_count = 0

    for fx in fixtures:
        goals = fx.get("goals", {})
        hg = goals.get("home")
        ag = goals.get("away")
        if hg is None or ag is None:
            continue
        status = fx.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("FT", "AET", "PEN"):
            continue

        teams = fx.get("teams", {})
        home_id = teams.get("home", {}).get("id")

        if home_id == team_id:
            gf, ga = hg, ag
        else:
            gf, ga = ag, hg

        total += 1
        goals_for += gf
        goals_against += ga
        if gf >= 1: scored_count += 1
        if ga >= 1: conceded_count += 1
        if gf >= 1 and ga >= 1: btts_count += 1
        if (gf + ga) >= 3: over_25_count += 1
        if gf >= 1 and ga >= 1 and (gf + ga) >= 3: btts_over_25_count += 1

    if total == 0:
        _BTTS_STATS_CACHE[cache_key] = {}
        return {}

    result = {
        "matches_played": total,
        "goals_for_avg": round(goals_for / total, 2),
        "goals_against_avg": round(goals_against / total, 2),
        "scored_pct": round(scored_count / total, 3),
        "conceded_pct": round(conceded_count / total, 3),
        "btts_pct": round(btts_count / total, 3),
        "over_25_pct": round(over_25_count / total, 3),
        "btts_over_25_pct": round(btts_over_25_count / total, 3),
    }
    _BTTS_STATS_CACHE[cache_key] = result
    return result
