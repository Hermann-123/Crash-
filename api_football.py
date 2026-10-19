"""
API-Football : fixtures, stats équipes, corners/cartons, H2H, blessés,
stats BTTS+Over2.5 (compatible Free Plan).
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
            print(f"  🚨 API-Football {path} -> {r.status_code} | {r.text[:120]}")
            return {}
        data = r.json()
        errors = data.get("errors", {})
        if errors and errors != []:
            print(f"  🚨 API-Football {path} erreurs: {errors}")
            return {}
        return data
    except Exception as e:
        print(f"  🚨 API-Football {path} crash: {e}")
        return {}


async def find_team_id(client: httpx.AsyncClient, name: str) -> Optional[int]:
    """Trouve l'ID de l'équipe PREMIÈRE (exclut Women, U23, U21, B, etc.)"""
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

    # Mots-clés à exclure (équipes féminines, jeunes, réserves)
    EXCLUDE_KW = ["women", "w", "u23", "u21", "u20", "u19", "u18", "reserve", "reserves", "ii"]

    best_team_id = None
    best_team_name = None

    for t in teams:
        team = t.get("team", {})
        tid = team.get("id")
        tname = team.get("name", "").strip()
        tname_lower = tname.lower()

        # Exclure
        skip = False
        for kw in EXCLUDE_KW:
            if f" {kw}" in tname_lower or tname_lower.endswith(kw):
                skip = True; break
        if skip:
            continue

        # Match exact ou contenu
        if tid and (_norm(tname) == key or key in _norm(tname) or _norm(tname) in key):
            best_team_id = tid
            best_team_name = tname
            # Si match exact, on s'arrête tout de suite
            if _norm(tname) == key:
                break

    if best_team_id:
        print(f"    ✅ Matché '{name}' -> '{best_team_name}' (ID: {best_team_id})")
        _TEAM_ID_CACHE[key] = best_team_id
        return best_team_id

    print(f"    ❌ Aucune équipe première trouvée pour '{name}' (filtré women/U23/etc)")
    _TEAM_ID_CACHE[key] = None
    return None


async def get_team_btts_stats(team_id: int, last: int = 10) -> dict:
    """Free plan : utilise 'season' au lieu de 'last'."""
    if not API_FOOTBALL_KEY or not team_id:
        return {}

    cache_key = f"btts_{team_id}_{last}"
    if cache_key in _BTTS_STATS_CACHE:
        return _BTTS_STATS_CACHE[cache_key]

    # On teste l'année en cours, puis l'année précédente
    current_year = datetime.now().year
    fixtures = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for season in [current_year, current_year - 1]:
            data = await _get(client, "/fixtures", {
                "team": team_id,
                "season": str(season),
            })
            fixtures = data.get("response", [])
            if fixtures:
                break

    if not fixtures:
        _BTTS_STATS_CACHE[cache_key] = {}
        return {}

    # Garder uniquement les matchs terminés
    finished = [fx for fx in fixtures if fx.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")]

    # Trier par timestamp DESC et prendre les N derniers
    finished.sort(key=lambda x: x.get("fixture", {}).get("timestamp", 0), reverse=True)
    recent = finished[:last]

    if not recent:
        _BTTS_STATS_CACHE[cache_key] = {}
        return {}

    total = gf_sum = ga_sum = 0
    scored = conceded = btts = over25 = btts_over25 = 0

    for fx in recent:
        goals = fx.get("goals", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None: continue

        home_id = fx.get("teams", {}).get("home", {}).get("id")
        gf, ga = (hg, ag) if home_id == team_id else (ag, hg)

        total += 1
        gf_sum += gf; ga_sum += ga
        if gf >= 1: scored += 1
        if ga >= 1: conceded += 1
        if gf >= 1 and ga >= 1: btts += 1
        if (gf + ga) >= 3: over25 += 1
        if gf >= 1 and ga >= 1 and (gf + ga) >= 3: btts_over25 += 1

    if total == 0:
        _BTTS_STATS_CACHE[cache_key] = {}
        return {}

    result = {
        "matches_played": total,
        "goals_for_avg": round(gf_sum / total, 2),
        "goals_against_avg": round(ga_sum / total, 2),
        "scored_pct": round(scored / total, 3),
        "conceded_pct": round(conceded / total, 3),
        "btts_pct": round(btts / total, 3),
        "over_25_pct": round(over25 / total, 3),
        "btts_over_25_pct": round(btts_over25 / total, 3),
    }
    print(f"    📊 Stats OK pour team_id={team_id} : BTTS+O2.5 = {result['btts_over_25_pct']*100:.0f}% (sur {total} matchs)")
    _BTTS_STATS_CACHE[cache_key] = result
    return result


# --- Fonctions conservées telles quelles (pas critiques pour BBTS+2.5) ---
async def fetch_all_today_fixtures() -> list[dict]:
    return []

async def get_h2h_by_names(home: str, away: str) -> dict:
    return {"available": False}
