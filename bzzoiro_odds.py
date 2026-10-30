"""
Bzzoiro Odds — Récupération des cotes multi-bookmakers via Bzzoiro.
Remplace The Odds API quand celui-ci est épuisé.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"


def _headers() -> dict:
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    try:
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=20.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro odds {path} -> {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro odds {path} crash: {e}")
        return {}


def _safe_float(v, default=0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        if f > 1.0:
            return round(f, 2)
        return default
    except (ValueError, TypeError):
        return default


async def fetch_event_odds(client: httpx.AsyncClient, event_id: int) -> dict:
    """
    Récupère les cotes consensus pour un event Bzzoiro.
    Retourne le format attendu par main.py :
    {home, draw, away, over_2.5, under_2.5, btts_yes, btts_no}
    """
    cache_key = f"odds_{event_id}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    data = await _get(client, f"/events/{event_id}/odds/")
    if not data:
        _CACHE[cache_key] = {}
        return {}

    result = {"home": None, "draw": None, "away": None,
              "over_2.5": None, "under_2.5": None,
              "btts_yes": None, "btts_no": None}

    # Le format peut être {markets: {...}} ou directement {1x2: {...}, ...}
    markets = data.get("markets", data) if isinstance(data, dict) else data
    if not isinstance(markets, dict):
        _CACHE[cache_key] = result
        return result

    # 1X2
    mr = markets.get("1x2") or markets.get("match_result") or {}
    if isinstance(mr, dict):
        result["home"] = _safe_float(mr.get("HOME") or mr.get("home") or mr.get("prob_home"))
        result["draw"] = _safe_float(mr.get("DRAW") or mr.get("draw") or mr.get("prob_draw"))
        result["away"] = _safe_float(mr.get("AWAY") or mr.get("away") or mr.get("prob_away"))

    # Over/Under 2.5
    ou = markets.get("over_under_25") or markets.get("over_under") or {}
    if isinstance(ou, dict):
        result["over_2.5"] = _safe_float(ou.get("over") or ou.get("OVER"))
        result["under_2.5"] = _safe_float(ou.get("under") or ou.get("UNDER"))

    # BTTS
    btts = markets.get("btts") or {}
    if isinstance(btts, dict):
        result["btts_yes"] = _safe_float(btts.get("yes") or btts.get("YES") or btts.get("prob_yes"))
        result["btts_no"] = _safe_float(btts.get("no") or btts.get("NO") or btts.get("prob_no"))

    _CACHE[cache_key] = result
    return result


async def fetch_all_odds(event_ids: list[int]) -> dict:
    """
    Récupère les cotes pour une liste d'event_ids Bzzoiro.
    Retourne {event_id: {home, draw, ...}}
    """
    if not BZZOIRO_API_KEY:
        return {}

    results = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for eid in event_ids[:40]:  # limite pour ne pas exploser le temps
            try:
                odds = await fetch_event_odds(client, eid)
                if odds and any(v is not None for v in odds.values()):
                    results[eid] = odds
                await asyncio.sleep(0.15)
            except Exception as e:
                print(f"  ⚠️ odds crash event {eid}: {e}")
    print(f"📊 Bzzoiro odds : {len(results)}/{len(event_ids)} matchs avec cotes")
    return results


# Cache global
_CACHE: dict = {}
