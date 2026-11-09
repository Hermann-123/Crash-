from __future__ import annotations
import asyncio, os, re, unicodedata, httpx
from typing import Optional
from datetime import datetime, timezone, timedelta

RAPIDODDS_BASE_URL = "https://api.rapidoddsapi.com"
RAPIDODDS_API_KEY = os.getenv("RAPIDODDSAPI_API_KEY", "")
SOCCER_LEAGUES = ["EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "MLS"]
DEFAULT_BOOKMAKER = "bet365"

def _norm(s: str) -> str:
    if not s: return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city", " club"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def _safe_float(v) -> Optional[float]:
    if v is None: return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except: return None

def _is_in_future(date_str: str, buffer_min: int = 10) -> bool:
    if not date_str: return True
    try:
        s = str(date_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt > (datetime.now(timezone.utc) + timedelta(minutes=buffer_min))
    except: return True

async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    params = params or {}
    params["api_key"] = RAPIDODDS_API_KEY
    try:
        r = await client.get(f"{RAPIDODDS_BASE_URL}{path}", params=params, timeout=25.0)
        if r.status_code >= 400: return {}
        return r.json() if r.text else {}
    except: return {}

async def _fetch_league_markets(client: httpx.AsyncClient, league: str, market_type: str) -> list[dict]:
    data = await _get(client, f"/sports/{league}/markets", {"market_type": market_type, "bookmaker": DEFAULT_BOOKMAKER})
    return data.get("games", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

async def fetch_all_odds(matches: list[dict] = None) -> object:
    if not RAPIDODDS_API_KEY: return [] if matches is None else {}
    all_games_1x2, all_games_totals = [], []
    async with httpx.AsyncClient(timeout=25.0) as client:
        for league in SOCCER_LEAGUES:
            all_games_1x2.extend(await _fetch_league_markets(client, league, "head_to_head_3_way"))
            await asyncio.sleep(1.0)
            all_games_totals.extend(await _fetch_league_markets(client, league, "totals"))
            await asyncio.sleep(1.0)

        merged = {}
        for entry in all_games_1x2:
            game = entry.get("game", {})
            home, away = game.get("home_team", ""), game.get("away_team", "")
            if not home or not away: continue
            key = f"{_norm(home)}__{_norm(away)}"
            if key not in merged:
                merged[key] = {"home_team": home, "away_team": away, "commence_time": game.get("start_time") or game.get("commence_time", ""), "league": game.get("league", "Soccer"), "home_odds": None, "draw_odds": None, "away_odds": None, "over_2_5": None, "under_2_5": None}
            for book in entry.get("bookmakers", []):
                for market in book.get("markets", []):
                    if (market.get("market_type") or "").lower() == "head_to_head_3_way":
                        for out in market.get("outcomes", []):
                            name = (out.get("name") or "").lower()
                            price = _safe_float(out.get("price"))
                            if not price: continue
                            if name in ("home", "1") or _norm(home) in name: merged[key]["home_odds"] = merged[key]["home_odds"] or price
                            elif name in ("draw", "x", "tie"): merged[key]["draw_odds"] = merged[key]["draw_odds"] or price
                            elif name in ("away", "2") or _norm(away) in name: merged[key]["away_odds"] = merged[key]["away_odds"] or price

        for entry in all_games_totals:
            game = entry.get("game", {})
            home, away = game.get("home_team", ""), game.get("away_team", "")
            if not home or not away: continue
            key = f"{_norm(home)}__{_norm(away)}"
            if key not in merged: continue
            for book in entry.get("bookmakers", []):
                for market in book.get("markets", []):
                    if (market.get("market_type") or "").lower() == "totals":
                        for out in market.get("outcomes", []):
                            name = (out.get("name") or "").lower()
                            price = _safe_float(out.get("price"))
                            try: line_f = float(out.get("line") or out.get("handicap"))
                            except: line_f = None
                            if line_f == 2.5:
                                if "over" in name and merged[key]["over_2_5"] is None: merged[key]["over_2_5"] = price
                                elif "under" in name and merged[key]["under_2_5"] is None: merged[key]["under_2_5"] = price

        events_data = []
        for key, data in merged.items():
            if data["home_odds"] and data["draw_odds"] and data["away_odds"] and _is_in_future(data["commence_time"]):
                events_data.append({"match_id": f"rapid_{key}", **data, "btts_yes": None, "btts_no": None})

    if matches is None: return events_data
    results = {}
    for m in matches:
        th, ta = _norm(m.get("home", "")), _norm(m.get("away", ""))
        if not th or not ta: continue
        best, best_score = None, 0
        for ev in events_data:
            score = (1 if th in _norm(ev["home_team"]) or _norm(ev["home_team"]) in th else 0) + (1 if ta in _norm(ev["away_team"]) or _norm(ev["away_team"]) in ta else 0)
            if score > best_score: best_score, best = score, ev
        if best and best_score >= 2:
            results[m["event_id"]] = {"home": best["home_odds"], "draw": best["draw_odds"], "away": best["away_odds"], "over_2.5": best["over_2_5"], "under_2.5": best["under_2_5"], "btts_yes": None, "btts_no": None}
    return results
