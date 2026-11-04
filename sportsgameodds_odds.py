"""
SportsGameOdds — Source de cotes réelles gratuite (alternative à The Odds API).
API Base : https://api.sportsgameodds.com/v2
Auth : Header X-API-Key
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx

SGO_API_KEY = os.getenv("SPORTSGAMEODDS_API_KEY", "")
SGO_BASE = "https://api.sportsgameodds.com/v2"
_CACHE: dict = {}


def _headers() -> dict:
    return {"X-API-Key": SGO_API_KEY}


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        # L'API renvoie parfois des cotes américaines (ex: -150, +200)
        # On ne garde que les cotes décimales (> 1.01)
        return f if f > 1.01 else None
    except (ValueError, TypeError):
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    try:
        r = await client.get(f"{SGO_BASE}{path}", headers=_headers(),
                             params=params or {}, timeout=20.0)
        if r.status_code >= 400:
            if r.status_code != 404:
                print(f"⚠️ SportsGameOdds {path} -> {r.status_code}")
            return {}
        return r.json() if r.text else {}
    except Exception as e:
        print(f"⚠️ SportsGameOdds {path} crash: {e}")
        return {}


def _parse_odds(event: dict) -> dict:
    """
    Parse les cotes d'un événement SportsGameOdds.
    Format attendu : {home, draw, away, over_2.5, under_2.5, btts_yes, btts_no}
    """
    result = {"home": None, "draw": None, "away": None,
              "over_2.5": None, "under_2.5": None,
              "btts_yes": None, "btts_no": None}

    odds_list = event.get("odds") or []
    if not isinstance(odds_list, list):
        return result

    for line in odds_list:
        if not isinstance(line, dict):
            continue
        market = (line.get("market") or "").lower()
        period = (line.get("period") or "full_game").lower()

        # On ne garde que le temps réglementaire
        if period not in ("full_game", "regulation", ""):
            continue

        # Moneyline (1X2)
        if market in ("moneyline", "1x2", "match_winner"):
            if result["home"] is None:
                result["home"] = _safe_float(line.get("homeOdds"))
            if result["draw"] is None:
                # Parfois le draw est dans un champ séparé
                result["draw"] = _safe_float(line.get("drawOdds") or line.get("tieOdds"))
            if result["away"] is None:
                result["away"] = _safe_float(line.get("awayOdds"))

        # Totals (Over/Under)
        elif market in ("total", "totals", "over_under"):
            line_val = line.get("line")
            try:
                line_val = float(line_val) if line_val is not None else None
            except (ValueError, TypeError):
                line_val = None
            if line_val == 2.5:
                if result["over_2.5"] is None:
                    result["over_2.5"] = _safe_float(line.get("homeOdds"))  # home = over
                if result["under_2.5"] is None:
                    result["under_2.5"] = _safe_float(line.get("awayOdds"))  # away = under

        # BTTS (Both Teams to Score) — si disponible
        elif market in ("btts", "both_teams_to_score"):
            if result["btts_yes"] is None:
                result["btts_yes"] = _safe_float(line.get("homeOdds"))  # home = yes
            if result["btts_no"] is None:
                result["btts_no"] = _safe_float(line.get("awayOdds"))  # away = no

    return result


async def fetch_sgo_odds(date_iso: str) -> dict:
    """
    Récupère les cotes pour une date donnée.
    Retourne {event_id: {home, draw, away, over_2.5, under_2.5, btts_yes, btts_no}}
    """
    if not SGO_API_KEY:
        print("⚠️ SportsGameOdds : pas de clé API")
        return {}

    cache_key = f"sgo_{date_iso}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    results = {}
    async with httpx.AsyncClient(timeout=25.0) as client:
        # On demande les événements de football pour cette date
        data = await _get(client, "/events", {
            "sport": "football",
            "date": date_iso,
            "limit": 200,  # selon la doc, limite max
        })

        events = data.get("data") or data.get("events") or data.get("results") or []
        if not isinstance(events, list):
            events = []

        print(f"🔎 SportsGameOdds : {len(events)} events pour le {date_iso}")

        for ev in events:
            if not isinstance(ev, dict):
                continue
            status = (ev.get("status") or "").lower()
            if status in ("completed", "cancelled", "live"):
                continue

            event_id = ev.get("eventId") or ev.get("id")
            if not event_id:
                continue

            home_team = ev.get("homeTeam")
            away_team = ev.get("awayTeam")
            home_name = home_team.get("name") if isinstance(home_team, dict) else str(home_team or "")
            away_name = away_team.get("name") if isinstance(away_team, dict) else str(away_team or "")

            if not home_name or not away_name:
                continue

            odds_data = _parse_odds(ev)
            if odds_data and any(odds_data.values()):
                results[event_id] = odds_data

        print(f"📊 SportsGameOdds odds : {len(results)}/{len(events)} matchs avec cotes")

    _CACHE[cache_key] = results
    return results
