"""
Module Bzzoiro Sports Data — H2H + Prédictions ML + Forme.
Free tier illimité.
Version corrigée avec matching ultra-robuste.
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


# =========================================================
# MOTS À IGNORER
# =========================================================
IGNORE_WORDS = {
    "fc", "cf", "sc", "ac", "afc", "as", "ss", "ssc", "rc", "cd", "fk",
    "sk", "jk", "sd", "ud", "ca", "cs", "us", "sv", "bv", "bsc", "fsv",
    "vfb", "vfl", "tsg", "spvgg", "kfc", "krc", "kv", "kaa", "vv", "cfc",
    "if", "bk", "ff", "ik",
    "stade", "olympique", "sporting", "sport", "club", "de", "of",
    "the", "and", "of",
    "united", "utd", "city", "town", "albion", "rovers", "wanderers",
}


def _headers() -> dict:
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


def _norm(s: str) -> str:
    """Normalisation avancée des noms d'équipes."""
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    words = s.split()
    filtered = [w for w in words if w not in IGNORE_WORDS and len(w) > 1]
    if not filtered:
        return " ".join(words).strip()
    return " ".join(filtered).strip()


def _similarity_score(name_a: str, name_b: str) -> float:
    """Score de similarité 0.0 à 1.0."""
    a = _norm(name_a)
    b = _norm(name_b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        ratio = shorter / longer if longer > 0 else 0
        return 0.70 + 0.25 * ratio
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(inter) / len(union) if union else 0.0
    first_a = a.split()[0] if a.split() else ""
    first_b = b.split()[0] if b.split() else ""
    first_match = 0.20 if (first_a and first_a == first_b) else 0.0
    return min(1.0, jaccard * 0.75 + first_match)


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
# ÉVÉNEMENTS (recherche par date)
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
        "limit": "300",
    })
    events = data.get("results", []) or data.get("data", []) or []

    if not events:
        _CACHE[cache_key] = None
        return None

    best_event = None
    best_score = 0.0
    THRESHOLD = 0.55

    for ev in events:
        h = ev.get("home_team", "") or ev.get("home", "") or ev.get("team_home", "")
        a = ev.get("away_team", "") or ev.get("away", "") or ev.get("team_away", "")
        if not h or not a:
            continue

        score_h = _similarity_score(home, h)
        score_a = _similarity_score(away, a)
        combined = min(score_h, score_a)
        if score_h > 0.75 and score_a > 0.75:
            combined += 0.15

        if combined > best_score:
            best_score = combined
            best_event = ev

    if best_event and best_score >= THRESHOLD:
        _CACHE[cache_key] = best_event
        return best_event

    _CACHE[cache_key] = None
    return None


# =========================================================
# ENRICHISSEMENT COMPLET
# =========================================================
async def enrich_match_with_bzzoiro(home: str, away: str,
                                      date_iso: str) -> dict:
    """
    Cherche le match sur Bzzoiro, puis récupère H2H + prédictions.
    Retourne un contexte compatible avec le format Gemini.
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

            # 3. Extraire les infos utiles du H2H
            h2h_clean = {}
            if h2h:
                h2h_clean = {
                    "total_matches": h2h.get("total_matches", 0),
                    "home_wins": h2h.get("home_wins", 0),
                    "draws": h2h.get("draws", 0),
                    "away_wins": h2h.get("away_wins", 0),
                    "avg_total_goals": h2h.get("avg_total_goals", 0),
                    "recent_matches": h2h.get("recent_matches", []),
                }

            return {
                "available": True,
                "bzzoiro_id": event_id,
                "h2h": h2h_clean,
                "prediction": prediction,
                "source": "bzzoiro",
                "home_form": event.get("home_form", "?"),
                "away_form": event.get("away_form", "?"),
            }
        except Exception as e:
            print(f"⚠️ Bzzoiro enrich crash {home}-{away}: {e}")
            return {"available": False}
