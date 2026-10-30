"""
API-Football v3 — Source principale de fixtures + cotes réelles.
Plan gratuit : 100 requêtes/jour, tous endpoints accessibles.
Base URL : https://v3.football.api-sports.io
Auth : header x-apisports-key
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
_CACHE: dict = {}


def _headers() -> dict:
    return {"x-apisports-key": API_FOOTBALL_KEY}


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except (ValueError, TypeError):
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    try:
        r = await client.get(f"{API_FOOTBALL_BASE}{path}", headers=_headers(),
                             params=params or {}, timeout=20.0)
        if r.status_code >= 400:
            print(f"⚠️ API-Football {path} -> {r.status_code} | {r.text[:150]}")
            return {}
        return r.json() if r.text else {}
    except Exception as e:
        print(f"⚠️ API-Football {path} crash: {e}")
        return {}


async def fetch_fixtures_today(date_str: str) -> list:
    """Retourne les fixtures du jour NON commencées (NS)."""
    cache_key = f"fixtures_{date_str}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    async with httpx.AsyncClient(timeout=25.0) as client:
        data = await _get(client, "/fixtures", {"date": date_str, "timezone": "UTC"})

    response = data.get("response", []) if isinstance(data, dict) else []
    fixtures = []

    for f in response:
        fx = f.get("fixture", {})
        status = fx.get("status", {}).get("short", "")
        # Garder UNIQUEMENT les matchs "Not Started"
        if status != "NS":
            continue

        fixture_id = fx.get("id")
        teams = f.get("teams", {})
        league = f.get("league", {})
        home = teams.get("home", {}).get("name", "")
        away = teams.get("away", {}).get("name", "")
        if not home or not away or not fixture_id:
            continue

        fixtures.append({
            "event_id": f"af_{fixture_id}",
            "fixture_id": fixture_id,
            "home": home,
            "away": away,
            "league": league.get("name", ""),
            "country": league.get("country", ""),
            "kickoff": fx.get("date", ""),
        })

    _CACHE[cache_key] = fixtures
    print(f"📊 API-Football : {len(fixtures)} fixtures NS le {date_str}")
    return fixtures


async def fetch_fixture_odds(fixture_id: int) -> dict:
    """Récupère les cotes pour un fixture. Prend le 1er bookmaker complet."""
    cache_key = f"odds_{fixture_id}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    async with httpx.AsyncClient(timeout=20.0) as client:
        data = await _get(client, "/odds", {"fixture": fixture_id})

    result = {
        "home": None, "draw": None, "away": None,
        "over_2.5": None, "under_2.5": None,
        "btts_yes": None, "btts_no": None,
    }

    response = data.get("response", []) if isinstance(data, dict) else []
    if not response:
        _CACHE[cache_key] = {}
        return {}

    for item in response:
        for bk in item.get("bookmakers", []):
            for bet in bk.get("bets", []):
                bet_name = (bet.get("name") or "").lower()
                values = bet.get("values", [])

                if "match winner" in bet_name:
                    for v in values:
                        val = (v.get("value") or "").lower()
                        odd = _safe_float(v.get("odd"))
                        if val == "home" and result["home"] is None:
                            result["home"] = odd
                        elif val == "draw" and result["draw"] is None:
                            result["draw"] = odd
                        elif val == "away" and result["away"] is None:
                            result["away"] = odd

                elif "over/under" in bet_name and "2.5" in bet_name:
                    for v in values:
                        val = (v.get("value") or "").lower()
                        odd = _safe_float(v.get("odd"))
                        if val == "over" and result["over_2.5"] is None:
                            result["over_2.5"] = odd
                        elif val == "under" and result["under_2.5"] is None:
                            result["under_2.5"] = odd
                    # Essayer aussi avec "Over 2.5" / "Under 2.5"
                    for v in values:
                        val = (v.get("value") or "").lower()
                        odd = _safe_float(v.get("odd"))
                        if "over 2.5" in val and result["over_2.5"] is None:
                            result["over_2.5"] = odd
                        elif "under 2.5" in val and result["under_2.5"] is None:
                            result["under_2.5"] = odd

                elif "both teams" in bet_name:
                    for v in values:
                        val = (v.get("value") or "").lower()
                        odd = _safe_float(v.get("odd"))
                        if val == "yes" and result["btts_yes"] is None:
                            result["btts_yes"] = odd
                        elif val == "no" and result["btts_no"] is None:
                            result["btts_no"] = odd

            if result["home"] and result["draw"] and result["away"]:
                _CACHE[cache_key] = result
                return result

    _CACHE[cache_key] = result
    return result if any(result.values()) else {}


async def fetch_all_odds(fixtures: list[dict]) -> dict:
    """Récupère les cotes pour une liste de fixtures (limite 40 pour quota)."""
    if not API_FOOTBALL_KEY:
        print("⚠️ API-Football : pas de clé API_FOOTBALL_KEY")
        return {}

    results = {}
    for fx in fixtures[:40]:
        fid = fx.get("fixture_id")
        if not fid:
            continue
        odds = await fetch_fixture_odds(fid)
        if odds and any(v for v in odds.values()):
            results[fx["event_id"]] = odds
        await asyncio.sleep(0.1)

    print(f"📊 API-Football odds : {len(results)}/{len(fixtures[:40])} matchs avec cotes")
    return results
