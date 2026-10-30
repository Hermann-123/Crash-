"""
Odds Fallback — InferSports (avec clé API)
Tente de récupérer les cotes 1X2 + O/U via API asiatique quand The Odds API et Bzzoiro sont vides.

⚠️ VERSION DEBUG : affiche la réponse brute pour identifier le bon format.
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
import httpx
from typing import Optional

INFER_BASE = "https://api.infersports.dev"
INFER_API_KEY = os.getenv("INFERSPORTS_API_KEY", "")
CACHE: dict = {}
_DEBUG_EVENTS_DONE = False
_DEBUG_ODDS_DONE = False


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if INFER_API_KEY:
        h["Authorization"] = f"Bearer {INFER_API_KEY}"
    return h


def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _safe_float(v) -> Optional[float]:
    """Retourne float si > 1.01 (vraie cote), sinon None."""
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except (ValueError, TypeError):
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    global _DEBUG_EVENTS_DONE
    try:
        url = f"{INFER_BASE}{path}"
        r = await client.get(url, headers=_headers(), params=params or {}, timeout=15.0)
        
        # Debug : afficher le statut pour la 1ère requête
        if path == "/v1/events" and not _DEBUG_EVENTS_DONE:
            print(f"🔎 InferSports {path} -> {r.status_code}")
            if r.status_code == 200:
                txt = r.text[:300]
                print(f"🔎 InferSports sample response: {txt}")
            _DEBUG_EVENTS_DONE = True
        
        if r.status_code >= 400:
            if r.status_code != 404:  # 404 = pas grave, on essaie autre chose
                print(f"⚠️ InferSports {path} -> {r.status_code}")
            return {}
        data = r.json()
        return data if isinstance(data, (dict, list)) else {}
    except Exception as e:
        print(f"⚠️ InferSports {path} crash: {e}")
        return {}


async def _find_event_id(client: httpx.AsyncClient, home: str, away: str, date: str) -> Optional[str]:
    """Cherche l'event_id InferSports pour un match donné."""
    # Essayer plusieurs endpoints (le bon dépend de leur API)
    endpoints = [
        ("/v1/events", {"date": date, "sport": "football"}),
        ("/v1/football/events", {"date": date}),
        ("/v1/fixtures", {"date": date, "sport": "football"}),
        ("/events", {"date": date, "sport": "football"}),
    ]
    
    events_list = []
    for path, params in endpoints:
        data = await _get(client, path, params)
        if isinstance(data, list):
            events_list = data
        elif isinstance(data, dict):
            events_list = data.get("events") or data.get("data") or data.get("results") or []
        
        if events_list:
            break
    
    if not events_list:
        return None
    
    target_home = _norm(home)
    target_away = _norm(away)
    if not target_home or not target_away:
        return None
    
    best_id = None
    best_score = 0.0
    for ev in events_list:
        if not isinstance(ev, dict):
            continue
        h_raw = (ev.get("home_team") or ev.get("home") or 
                 ev.get("team_home") or ev.get("home_name") or "")
        a_raw = (ev.get("away_team") or ev.get("away") or 
                 ev.get("team_away") or ev.get("away_name") or "")
        if not h_raw or not a_raw:
            # Essayer avec "name": "Home vs Away"
            name = ev.get("name") or ""
            if " vs " in name:
                parts = name.split(" vs ")
                h_raw, a_raw = parts[0], parts[1]
        
        h_norm = _norm(h_raw)
        a_norm = _norm(a_raw)
        
        # Score de matching simple
        score = 0
        if target_home in h_norm or h_norm in target_home:
            score += 1
        if target_away in a_norm or a_norm in target_away:
            score += 1
        
        if score > best_score:
            best_score = score
            best_id = ev.get("id") or ev.get("event_id")
            if score == 2:
                break
    
    if best_score == 2:
        return str(best_id) if best_id else None
    return None


async def _fetch_odds(client: httpx.AsyncClient, event_id: str) -> dict:
    """Récupère et parse les cotes pour un event donné."""
    global _DEBUG_ODDS_DONE
    
    # Essayer plusieurs endpoints d'odds
    endpoints = [
        f"/v1/events/{event_id}/odds",
        f"/v1/football/events/{event_id}/odds",
        f"/events/{event_id}/odds",
    ]
    
    data = {}
    for path in endpoints:
        d = await _get(client, path, {"format": "decimal"})
        if d:
            data = d
            break
    
    if not data:
        return {}
    
    # Debug : afficher la 1ère réponse d'odds
    if not _DEBUG_ODDS_DONE:
        print(f"🔎 InferSports ODDS SAMPLE (event {event_id}):")
        print(f"   {str(data)[:500]}")
        _DEBUG_ODDS_DONE = True
    
    result = {
        "home": None, "draw": None, "away": None,
        "over_2.5": None, "under_2.5": None,
        "btts_yes": None, "btts_no": None,
    }
    
    # Essayer plusieurs structures possibles
    markets = data
    if isinstance(data, dict):
        markets = (data.get("markets") or data.get("odds") or 
                   data.get("bookmakers") or data)
    
    if not isinstance(markets, dict):
        return result
    
    # 1X2
    for key in ("1x2", "1X2", "match_result", "moneyline", "h2h"):
        m = markets.get(key)
        if isinstance(m, dict):
            result["home"] = (_safe_float(m.get("home")) or 
                              _safe_float(m.get("HOME")) or 
                              _safe_float(m.get("1")) or
                              _safe_float(m.get("home_win")))
            result["draw"] = (_safe_float(m.get("draw")) or 
                              _safe_float(m.get("DRAW")) or 
                              _safe_float(m.get("x")) or
                              _safe_float(m.get("X")))
            result["away"] = (_safe_float(m.get("away")) or 
                              _safe_float(m.get("AWAY")) or 
                              _safe_float(m.get("2")) or
                              _safe_float(m.get("away_win")))
            if result["home"] or result["draw"] or result["away"]:
                break
    
    # Over/Under 2.5
    for key in ("totals", "over_under", "ou", "ou_25", "over_under_25"):
        m = markets.get(key)
        if isinstance(m, dict):
            result["over_2.5"] = (_safe_float(m.get("over_2.5")) or 
                                  _safe_float(m.get("over")) or
                                  _safe_float(m.get("OVER")))
            result["under_2.5"] = (_safe_float(m.get("under_2.5")) or 
                                   _safe_float(m.get("under")) or
                                   _safe_float(m.get("UNDER")))
            if result["over_2.5"] or result["under_2.5"]:
                break
    
    # BTTS
    for key in ("btts", "BTTS", "both_teams_to_score"):
        m = markets.get(key)
        if isinstance(m, dict):
            result["btts_yes"] = (_safe_float(m.get("yes")) or 
                                  _safe_float(m.get("YES")) or
                                  _safe_float(m.get("btts_yes")))
            result["btts_no"] = (_safe_float(m.get("no")) or 
                                 _safe_float(m.get("NO")) or
                                 _safe_float(m.get("btts_no")))
            if result["btts_yes"] or result["btts_no"]:
                break
    
    return result


async def fetch_all_odds(matches: list[dict]) -> dict:
    """
    matches = [{"event_id": "bzz_123", "home": "X", "away": "Y", "kickoff": "2026-10-30"}]
    Retourne {event_id: {home, draw, away, over_2.5, under_2.5, btts_yes, btts_no}}
    """
    if not INFER_API_KEY:
        print("⚠️ InferSports : pas de clé API")
        return {}
    
    results = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for m in matches[:30]:
            event_name = f"{m['home']} vs {m['away']}"
            date = (m.get("kickoff") or "")[:10]
            if not date:
                continue
            
            cache_key = f"{_norm(m['home'])}_{_norm(m['away'])}_{date}"
            if cache_key in CACHE:
                cached = CACHE[cache_key]
                if cached:
                    results[m["event_id"]] = cached
                continue
            
            # 1. Trouver l'event_id InferSports
            infer_id = await _find_event_id(client, m["home"], m["away"], date)
            if not infer_id:
                CACHE[cache_key] = {}
                await asyncio.sleep(0.2)
                continue
            
            # 2. Récupérer les cotes
            odds = await _fetch_odds(client, infer_id)
            if odds and any(v for v in odds.values()):
                results[m["event_id"]] = odds
                CACHE[cache_key] = odds
            else:
                CACHE[cache_key] = {}
            
            await asyncio.sleep(0.2)
    
    print(f"📊 InferSports odds : {len(results)}/{len(matches)} matchs avec cotes")
    return results
