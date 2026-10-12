"""
Module Bzzoiro Sports Data — H2H + Prédictions ML + Forme.
Free tier illimité.
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

import httpx

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"

_CACHE: dict = {}


def _headers() -> dict:
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


def _norm(s: str) -> str:
    """Normalisation avancée des noms d'équipes pour un meilleur matching."""
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    try:
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=15.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro {path} -> {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro {path} crash: {e}")
        return {}


# =========================================================
# TEST CONNEXION
# =========================================================
async def test_connection() -> bool:
    """Teste si la clé Bzzoiro fonctionne."""
    if not BZZOIRO_API_KEY:
        return False
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{BZZOIRO_BASE}/events/", headers=_headers(),
                                  params={"limit": "1"}, timeout=10.0)
            print(f"🔑 Bzzoiro test -> {r.status_code}")
            return r.status_code == 200
        except Exception as e:
            print(f"⚠️ Bzzoiro test crash: {e}")
            return False


# =========================================================
# H2H
# =========================================================
async def get_h2h(client: httpx.AsyncClient, event_id: int) -> dict:
    """Récupère le H2H d'un match."""
    cache_key = f"h2h_{event_id}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    data = await _get(client, f"/events/{event_id}/h2h/")
    if not data:
        return {}

    # On essaie de parser plusieurs formats possibles
    if isinstance(data, dict):
        result = {
            "total_matches": data.get("total_matches", len(data.get("matches", []))),
            "home_wins": data.get("home_wins", data.get("team_a_wins", 0)),
            "draws": data.get("draws", 0),
            "away_wins": data.get("away_wins", data.get("team_b_wins", 0)),
            "avg_total_goals": data.get("avg_total_goals", data.get("average_goals", 0)),
            "recent_matches": data.get("matches", data.get("recent_matches", []))[:5],
            "raw": data,
        }
    else:
        result = {"raw": data}
        
    _CACHE[cache_key] = result
    return result


# =========================================================
# PRÉDICTIONS ML
# =========================================================
async def get_prediction(client: httpx.AsyncClient, event_id: int) -> dict:
    """Récupère les prédictions ML CatBoost."""
    cache_key = f"pred_{event_id}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    data = await _get(client, f"/events/{event_id}/prediction/")
    if data:
        _CACHE[cache_key] = data
    return data if data else {}


# =========================================================
# ÉVÉNEMENTS (pour retrouver l'ID Bzzoiro)
# =========================================================
async def find_bzzoiro_event(client: httpx.AsyncClient, home: str, away: str,
                                date_iso: str) -> Optional[dict]:
    """Cherche un match par nom d'équipes + date."""
    cache_key = f"event_{_norm(home)}_{_norm(away)}_{date_iso}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    data = await _get(client, "/events/", {
        "date_from": date_iso,
        "date_to": date_iso,
        "limit": "200",
    })
    events = data.get("results", []) or data.get("data", []) or []

    h_norm = _norm(home)
    a_norm = _norm(away)

    for ev in events:
        h = ev.get("home_team", "") or ev.get("home", "")
        a = ev.get("away_team", "") or ev.get("away", "")
        if not h or not a:
            continue
        hn = _norm(h)
        an = _norm(a)
        if (h_norm in hn or hn in h_norm) and (a_norm in an or an in a_norm):
            _CACHE[cache_key] = ev
            return ev
            
    _CACHE[cache_key] = None
    return None


# =========================================================
# ENRICHISSEMENT COMPLET
# =========================================================
async def enrich_match_with_bzzoiro(home: str, away: str,
                                      date_iso: str) -> dict:
    """
    Cherche le match sur Bzzoiro, puis récupère H2H + prédictions.
    """
    if not BZZOIRO_API_KEY:
        return {"available": False}

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            # 1. Retrouver l'event
            event = await find_bzzoiro_event(client, home, away, date_iso)
            if not event:
                return {"available": False}

            event_id = event.get("id")
            if not event_id:
                return {"available": False}

            # 2. Récupérer H2H + prédictions en parallèle
            h2h, prediction = await asyncio.gather(
                get_h2h(client, event_id),
                get_prediction(client, event_id),
            )

            return {
                "available": True,
                "bzzoiro_id": event_id,
                "h2h": h2h,
                "prediction": prediction,
                "source": "bzzoiro"
            }
        except Exception as e:
            print(f"⚠️ Bzzoiro enrich crash {home}-{away}: {e}")
            return {"available": False}
