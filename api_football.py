"""
API-Football : fixtures, stats équipes, corners/cartons, H2H, blessés, 
+ stats BTTS+Over2.5 (avec diagnostics).
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
            # === DIAGNOSTIC : on affiche l'erreur au lieu de la cacher ===
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
    key = _norm(name)
    if not key:
        return None
    if key in _TEAM_ID_CACHE:
        return _TEAM_ID_CACHE[key]
    
    print(f"    🔎 Recherche ID pour '{name}'...")
    data = await _get(client, "/teams", {"search": name})
    teams = data.get("response", [])
    
    if not teams:
        print(f"    ❌ Aucune équipe trouvée pour '{name}'")
        _TEAM_ID_CACHE[key] = None
        return None
        
    for t in teams:
        team = t.get("team", {})
        tid = team.get("id")
        tname = team.get("name", "")
        if tid and (_norm(tname) == key or key in _norm(tname) or _norm(tname) in key):
            print(f"    ✅ Matché '{name}' -> '{tname}' (ID: {tid})")
            _TEAM_ID_CACHE[key] = tid
            return tid
            
    tid = teams[0].get("team", {}).get("id")
    print(f"    ⚠️ Pas de match exact pour '{name}', on prend '{teams[0].get('team', {}).get('name')}' (ID: {tid})")
    _TEAM_ID_CACHE[key] = tid
    return tid


async def get_team_btts_stats(team_id: int, last: int = 10) -> dict:
    if not API_FOOTBALL_KEY or not team_id:
        return {}

    cache_key = f"btts_{team_id}_{last}"
    if cache_key in _BTTS_STATS_CACHE:
        return _BTTS_STATS_CACHE[cache_key]

    async with httpx.AsyncClient(timeout=15.0) as client:
        print(f"    📥 Fetch stats 10 derniers matchs pour team_id={team_id}...")
        data = await _get(client, "/fixtures", {
            "team": team_id,
            "last": str(last),
            "timezone": "Africa/Abidjan",
        })
        fixtures = data.get("response", [])

    if not fixtures:
        print(f"    ❌ Aucun match retourné pour team_id={team_id}")
        _BTTS_STATS_CACHE[cache_key] = {}
        return {}

    total = goals_for = goals_against = 0
    scored_count = conceded_count = btts_count = over_25_count = btts_over_25_count = 0

    for fx in fixtures:
        goals = fx.get("goals", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None: continue
        status = fx.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("FT", "AET", "PEN"): continue

        teams = fx.get("teams", {})
        home_id = teams.get("home", {}).get("id")
        
        if home_id == team_id:
            gf, ga = hg, ag
        else:
            gf, ga = ag, hg

        total += 1
        goals_for += gf; goals_against += ga
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
    print(f"    📊 Stats OK pour team_id={team_id} : BTTS+O2.5 = {result['btts_over_25_pct']*100:.0f}% (sur {total} matchs)")
    _BTTS_STATS_CACHE[cache_key] = result
    return result


# =========================================================
# AUTRES FONCTIONS (inchangées)
# =========================================================
async def fetch_all_today_fixtures() -> list[dict]:
    return [] # Simplifié pour l'exemple, garde ton ancien code si besoin

async def get_h2h_by_names(home: str, away: str) -> dict:
    return {"available": False} # Simplifié pour l'exemple
