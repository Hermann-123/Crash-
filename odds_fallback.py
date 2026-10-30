"""
Odds Fallback — InferSports (parser adapté au vrai format API)
Format réel : {odds: [{market_type: '1x2', prices: {home, draw, away}}, ...]}
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
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except (ValueError, TypeError):
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    try:
        r = await client.get(f"{INFER_BASE}{path}", headers=_headers(),
                             params=params or {}, timeout=20.0)
        if r.status_code >= 400:
            if r.status_code != 404:
                # Log utile pour debug
                print(f"⚠️ InferSports {path} ({params}) -> {r.status_code} | {r.text[:200]}")
            return {}
        return r.json() if r.text else {}
    except Exception as e:
        print(f"⚠️ InferSports {path} crash: {e}")
        return {}


def _match_event(events_list: list, home: str, away: str) -> Optional[str]:
    """Cherche un event_id correspondant dans une liste d'events."""
    target_home = _norm(home)
    target_away = _norm(away)
    if not target_home or not target_away:
        return None
    
    best_id, best_score = None, 0
    for ev in events_list:
        if not isinstance(ev, dict):
            continue
        ht = ev.get("home_team")
        at = ev.get("away_team")
        h_raw = ht.get("name", "") if isinstance(ht, dict) else (ht or "")
        a_raw = at.get("name", "") if isinstance(at, dict) else (at or "")
        
        h_norm = _norm(h_raw)
        a_norm = _norm(a_raw)
        
        score = 0
        if target_home and h_norm and (target_home == h_norm or target_home in h_norm or h_norm in target_home):
            score += 1
        if target_away and a_norm and (target_away == a_norm or target_away in a_norm or a_norm in target_away):
            score += 1
        
        if score > best_score:
            best_score = score
            best_id = ev.get("id")
            if score == 2:
                return str(best_id) if best_id else None
    
    return str(best_id) if best_score >= 2 and best_id else None


async def _fetch_odds(client: httpx.AsyncClient, event_id: str) -> dict:
    """Parse les cotes au VRAI format InferSports."""
    global _DEBUG_ODDS_DONE
    
    data = await _get(client, f"/v1/events/{event_id}/odds", {"format": "decimal"})
    if not data:
        return {}
    
    result = {
        "home": None, "draw": None, "away": None,
        "over_2.5": None, "under_2.5": None,
        "btts_yes": None, "btts_no": None,
    }
    
    # ✅ VRAI FORMAT : data['odds'] est une LISTE
    odds_list = data.get("odds", [])
    if not isinstance(odds_list, list):
        return result
    
    market_types_found = set()
    for item in odds_list:
        if not isinstance(item, dict):
            continue
        mt = (item.get("market_type") or "").lower().strip()
        market_types_found.add(mt)
        prices = item.get("prices", {})
        if not isinstance(prices, dict):
            continue
        
        # 1X2
        if mt == "1x2":
            if result["home"] is None:
                result["home"] = _safe_float(prices.get("home"))
            if result["draw"] is None:
                result["draw"] = _safe_float(prices.get("draw"))
            if result["away"] is None:
                result["away"] = _safe_float(prices.get("away"))
        
        # Totals (Over/Under 2.5)
        elif mt in ("totals", "total", "over_under", "ou"):
            line = item.get("line")
            try:
                line_f = float(line) if line is not None else None
            except (ValueError, TypeError):
                line_f = None
            if line_f == 2.5:
                if result["over_2.5"] is None:
                    result["over_2.5"] = _safe_float(prices.get("over"))
                if result["under_2.5"] is None:
                    result["under_2.5"] = _safe_float(prices.get("under"))
        
        # BTTS
        elif mt in ("btts", "both_teams_to_score"):
            if result["btts_yes"] is None:
                result["btts_yes"] = _safe_float(prices.get("yes"))
            if result["btts_no"] is None:
                result["btts_no"] = _safe_float(prices.get("no"))
    
    if not _DEBUG_ODDS_DONE:
        print(f"🔎 InferSports PARSED (event {event_id}):")
        print(f"   1X2 : {result['home']} / {result['draw']} / {result['away']}")
        print(f"   O/U 2.5 : {result['over_2.5']} / {result['under_2.5']}")
        print(f"   BTTS : {result['btts_yes']} / {result['btts_no']}")
        print(f"   market_types : {market_types_found}")
        _DEBUG_ODDS_DONE = True
    
    return result


async def fetch_all_odds(matches: list[dict]) -> dict:
    """Récupère toutes les cotes. Charge les events UNE FOIS par date."""
    global _DEBUG_EVENTS_DONE
    
    if not INFER_API_KEY:
        print("⚠️ InferSports : pas de clé API")
        return {}
    
    results = {}
    async with httpx.AsyncClient(timeout=25.0) as client:
        # === 1. Charger les events par date ===
        dates = set()
        for m in matches:
            date = (m.get("kickoff") or "")[:10]
            if date:
                dates.add(date)
        
        all_events = []
        for date in sorted(dates):
            # ✅ SANS 'limit' — c'est ce qui causait le 400
            data = await _get(client, "/v1/events", {"date": date, "sport": "football"})
            events = data.get("data", []) if isinstance(data, dict) else []
            print(f"🔎 InferSports {date} : {len(events)} events")
            all_events.extend(events)
            await asyncio.sleep(0.15)
        
        print(f"🔎 InferSports TOTAL : {len(all_events)} events sur {len(dates)} date(s)")
        
        if not _DEBUG_EVENTS_DONE and all_events:
            sample = []
            for ev in all_events[:5]:
                ht = ev.get("home_team", {}) if isinstance(ev.get("home_team"), dict) else {}
                at = ev.get("away_team", {}) if isinstance(ev.get("away_team"), dict) else {}
                lg = ev.get("league", {}) if isinstance(ev.get("league"), dict) else {}
                sample.append(f"{ht.get('name','?')} vs {at.get('name','?')} ({lg.get('name','?')})")
            print(f"🔎 InferSports exemples : {sample}")
            _DEBUG_EVENTS_DONE = True
        
        # === 2. Matcher et récupérer les cotes ===
        matched_count = 0
        for m in matches[:40]:
            date = (m.get("kickoff") or "")[:10]
            if not date:
                continue
            
            cache_key = f"{_norm(m['home'])}_{_norm(m['away'])}_{date}"
            if cache_key in CACHE:
                cached = CACHE[cache_key]
                if cached:
                    results[m["event_id"]] = cached
                continue
            
            infer_id = _match_event(all_events, m["home"], m["away"])
            if not infer_id:
                CACHE[cache_key] = {}
                continue
            
            matched_count += 1
            odds = await _fetch_odds(client, infer_id)
            if odds and any(v for v in odds.values()):
                results[m["event_id"]] = odds
                CACHE[cache_key] = odds
            else:
                CACHE[cache_key] = {}
            
            await asyncio.sleep(0.15)
        
        print(f"🔎 InferSports : {matched_count} events matchés sur {len(matches)}")
    
    print(f"📊 InferSports odds : {len(results)}/{len(matches)} matchs avec cotes")
    return results
