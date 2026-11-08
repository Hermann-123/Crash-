"""
RapidOddsAPI Data — Cotes réelles de bookmakers
Docs : https://rapidoddsapi.com
Auth : query parameter `api_key` (clé commençant par `oa_`)
Base URL : https://api.rapidoddsapi.com
Free tier : 250 crédits/mois, pas de carte requise[reference:2]
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
import httpx
from typing import Optional
from collections import defaultdict
from datetime import datetime, timezone, timedelta

RAPIDODDS_BASE_URL = "https://api.rapidoddsapi.com"
RAPIDODDS_API_KEY = os.getenv("RAPIDODDSAPI_API_KEY", "")

_DEBUG_DONE = False


def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city", " club"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except (ValueError, TypeError):
        return None


def _is_in_future(date_str: str, buffer_min: int = 10) -> bool:
    if not date_str:
        return True
    try:
        s = str(date_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > (datetime.now(timezone.utc) + timedelta(minutes=buffer_min))
    except Exception:
        return True


async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    params = params or {}
    params["api_key"] = RAPIDODDS_API_KEY

    try:
        r = await client.get(f"{RAPIDODDS_BASE_URL}{path}", params=params, timeout=25.0)
        if r.status_code == 429:
            print("⚠️ RapidOddsAPI RATE LIMIT (30 req/s)")
            return {}
        if r.status_code >= 400:
            print(f"⚠️ RapidOddsAPI {path} -> {r.status_code} | {r.text[:150]}")
            return {}
        return r.json() if r.text else {}
    except Exception as e:
        print(f"⚠️ RapidOddsAPI {path} crash: {e}")
        return {}


async def fetch_all_odds(matches: list[dict] = None) -> object:
    """
    Récupère les cotes 1X2 et Totals pour le soccer.
    Retourne :
      - sans matches -> list[dict] (tous les matchs avec cotes)
      - avec matches -> {event_id: odds_dict} matché par noms
    """
    if not RAPIDODDS_API_KEY:
        print("⚠️ RapidOddsAPI : RAPIDODDSAPI_API_KEY manquante")
        return [] if matches is None else {}

    async with httpx.AsyncClient(timeout=25.0) as client:
        # 1. Récupérer les matchs de soccer avec les marchés 1X2 et Totals
        # Le free tier permet 5 bookmakers par défaut. On demande les 5 les plus courants.
        data = await _get(client, "/sports/soccer/markets", {
            "market_types": "head_to_head_3_way,totals",
            "bookmakers": "bet365,pinnacle,draftkings,betmgm,fanduel",
        })

        if not data:
            print("⚠️ RapidOddsAPI : aucune donnée retournée")
            return [] if matches is None else {}

        # Debug : afficher la structure globale une seule fois
        global _DEBUG_DONE
        if not _DEBUG_DONE:
            print(f"🔎 RapidOddsAPI — clés racine : {list(data.keys())}")
            if "games" in data and data["games"]:
                sample_game = data["games"][0]
                print(f"🔎 Exemple game : home={sample_game.get('game', {}).get('home_team')} "
                      f"away={sample_game.get('game', {}).get('away_team')}")
                if sample_game.get("bookmakers"):
                    sample_bk = sample_game["bookmakers"][0]
                    print(f"🔎 Bookmaker exemple : {sample_bk.get('name')}, "
                          f"marchés : {[m.get('market_type') for m in sample_bk.get('markets', [])]}")
            _DEBUG_DONE = True

        games = data.get("games", [])
        if not isinstance(games, list):
            print("⚠️ RapidOddsAPI : 'games' n'est pas une liste")
            return [] if matches is None else {}

        print(f"🔎 RapidOddsAPI : {len(games)} matchs bruts reçus")

        # 2. Construire les events avec cotes
        events_data = []
        for entry in games:
            game = entry.get("game", {})
            home_team = game.get("home_team", "")
            away_team = game.get("away_team", "")
            commence_time = game.get("start_time") or game.get("commence_time", "")
            league = game.get("league", "Soccer")

            if not home_team or not away_team:
                continue
            if not _is_in_future(commence_time, buffer_min=10):
                continue

            home_odds = draw_odds = away_odds = None
            over_25 = under_25 = None

            for book in entry.get("bookmakers", []):
                for market in book.get("markets", []):
                    mtype = (market.get("market_type") or "").lower()
                    outcomes = market.get("outcomes", [])

                    if mtype == "head_to_head_3_way":
                        for out in outcomes:
                            name = (out.get("name") or "").lower()
                            price = _safe_float(out.get("price"))
                            if not price:
                                continue
                            if name in ("home", "1") or _norm(home_team) in name:
                                if home_odds is None: home_odds = price
                            elif name in ("draw", "x", "tie"):
                                if draw_odds is None: draw_odds = price
                            elif name in ("away", "2") or _norm(away_team) in name:
                                if away_odds is None: away_odds = price

                    elif mtype == "totals":
                        for out in outcomes:
                            name = (out.get("name") or "").lower()
                            price = _safe_float(out.get("price"))
                            line = out.get("line") or out.get("handicap")
                            try:
                                line_f = float(line) if line is not None else None
                            except (ValueError, TypeError):
                                line_f = None
                            if line_f != 2.5:
                                continue
                            if "over" in name and over_25 is None:
                                over_25 = price
                            elif "under" in name and under_25 is None:
                                under_25 = price

                if home_odds and draw_odds and away_odds:
                    break

            if home_odds and draw_odds and away_odds:
                events_data.append({
                    "match_id": f"rapid_{_norm(home_team)}__{_norm(away_team)}",
                    "league": league,
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_odds": home_odds,
                    "draw_odds": draw_odds,
                    "away_odds": away_odds,
                    "over_2_5": over_25,
                    "under_2_5": under_25,
                    "btts_yes": None,
                    "btts_no": None,
                    "commence_time": commence_time,
                })

        print(f"✅ RapidOddsAPI : {len(events_data)} matchs avec cotes 1X2 complètes")

        if matches is None:
            return events_data

        # 3. Matching par noms (pour l'injection dans le pool Bzzoiro)
        results = {}
        for m in matches:
            target_home = _norm(m.get("home", ""))
            target_away = _norm(m.get("away", ""))
            if not target_home or not target_away:
                continue

            best, best_score = None, 0
            for ev in events_data:
                h = _norm(ev["home_team"])
                a = _norm(ev["away_team"])
                score = 0
                if target_home in h or h in target_home: score += 1
                if target_away in a or a in target_away: score += 1
                if score > best_score:
                    best_score, best = score, ev

            if best and best_score >= 2:
                results[m["event_id"]] = {
                    "home": best["home_odds"],
                    "draw": best["draw_odds"],
                    "away": best["away_odds"],
                    "over_2.5": best["over_2_5"],
                    "under_2.5": best["under_2_5"],
                    "btts_yes": best["btts_yes"],
                    "btts_no": best["btts_no"],
                }

        print(f"📊 RapidOddsAPI matching : {len(results)}/{len(matches)} matchs avec cotes")
        return results
