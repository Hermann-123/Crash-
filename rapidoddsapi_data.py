"""
RapidOddsAPI Data — Cotes réelles de bookmakers
Docs : https://rapidoddsapi.com
Auth : query parameter `api_key`
Base URL : https://api.rapidoddsapi.com
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
            print("⚠️ RapidOddsAPI RATE LIMIT")
            return {}
        if r.status_code >= 400:
            print(f"⚠️ RapidOddsAPI {path} -> {r.status_code} | {r.text[:250]}")
            return {}
        return r.json() if r.text else {}
    except Exception as e:
        print(f"⚠️ RapidOddsAPI {path} crash: {e}")
        return {}


async def _fetch_market_type(client: httpx.AsyncClient, market_type: str) -> list[dict]:
    """Récupère les cotes pour un type de marché spécifique (1X2 ou Totals)."""
    data = await _get(client, "/sports/soccer/markets", {
        "market_type": market_type,   # Singulier (exigé par l'API)
        "bookmaker": "bet365",        # Singulier (exigé par l'API)
    })
    return data.get("games", []) if isinstance(data, dict) else []


async def fetch_all_odds(matches: list[dict] = None) -> object:
    """
    Récupère les cotes 1X2 et Totals 2.5 via RapidOddsAPI.
    Retourne :
      - sans matches -> list[dict] (tous les matchs avec cotes)
      - avec matches -> {event_id: odds_dict} matché par noms
    """
    if not RAPIDODDS_API_KEY:
        print("⚠️ RapidOddsAPI : RAPIDODDSAPI_API_KEY manquante")
        return [] if matches is None else {}

    async with httpx.AsyncClient(timeout=25.0) as client:
        # 1. Récupération des cotes 1X2 (head_to_head_3_way)
        print("🔎 RapidOddsAPI : récupération 1X2...")
        games_1x2 = await _fetch_market_type(client, "head_to_head_3_way")
        await asyncio.sleep(1.0) # Pause anti rate-limit

        # 2. Récupération des cotes Over/Under 2.5 (totals)
        print("🔎 RapidOddsAPI : récupération Totals...")
        games_totals = await _fetch_market_type(client, "totals")

        if not games_1x2 and not games_totals:
            print("⚠️ RapidOddsAPI : aucune donnée retournée")
            return [] if matches is None else {}

        # Debug (une seule fois)
        global _DEBUG_DONE
        if not _DEBUG_DONE:
            if games_1x2:
                sample = games_1x2[0]
                print(f"🔎 Exemple 1X2 : home={sample.get('game', {}).get('home_team')} "
                      f"away={sample.get('game', {}).get('away_team')}")
            _DEBUG_DONE = True

        # 3. Fusion des données par match (basé sur les noms d'équipes)
        merged_matches = {}

        # Traitement du 1X2
        for entry in games_1x2:
            game = entry.get("game", {})
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            if not home or not away:
                continue

            key = f"{_norm(home)}__{_norm(away)}"
            if key not in merged_matches:
                merged_matches[key] = {
                    "home_team": home,
                    "away_team": away,
                    "commence_time": game.get("start_time") or game.get("commence_time", ""),
                    "league": game.get("league", "Soccer"),
                    "home_odds": None, "draw_odds": None, "away_odds": None,
                    "over_2_5": None, "under_2_5": None,
                }

            for book in entry.get("bookmakers", []):
                for market in book.get("markets", []):
                    if (market.get("market_type") or "").lower() == "head_to_head_3_way":
                        for out in market.get("outcomes", []):
                            name = (out.get("name") or "").lower()
                            price = _safe_float(out.get("price"))
                            if not price: continue
                            
                            if name in ("home", "1") or _norm(home) in name:
                                if merged_matches[key]["home_odds"] is None: merged_matches[key]["home_odds"] = price
                            elif name in ("draw", "x", "tie"):
                                if merged_matches[key]["draw_odds"] is None: merged_matches[key]["draw_odds"] = price
                            elif name in ("away", "2") or _norm(away) in name:
                                if merged_matches[key]["away_odds"] is None: merged_matches[key]["away_odds"] = price

        # Traitement des Totals
        for entry in games_totals:
            game = entry.get("game", {})
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            if not home or not away:
                continue

            key = f"{_norm(home)}__{_norm(away)}"
            if key not in merged_matches:
                continue # On ne garde que les matchs qui ont déjà le 1X2

            for book in entry.get("bookmakers", []):
                for market in book.get("markets", []):
                    if (market.get("market_type") or "").lower() == "totals":
                        for out in market.get("outcomes", []):
                            name = (out.get("name") or "").lower()
                            price = _safe_float(out.get("price"))
                            line = out.get("line") or out.get("handicap")
                            
                            try: line_f = float(line) if line is not None else None
                            except (ValueError, TypeError): line_f = None

                            if line_f == 2.5:
                                if "over" in name and merged_matches[key]["over_2_5"] is None:
                                    merged_matches[key]["over_2_5"] = price
                                elif "under" in name and merged_matches[key]["under_2_5"] is None:
                                    merged_matches[key]["under_2_5"] = price

        # 4. Construction de la liste finale
        events_data = []
        for key, data in merged_matches.items():
            # On ne garde que les matchs avec les 3 cotes 1X2 complètes et dans le futur
            if data["home_odds"] and data["draw_odds"] and data["away_odds"]:
                if not _is_in_future(data["commence_time"], buffer_min=10):
                    continue

                events_data.append({
                    "match_id": f"rapid_{key}",
                    "league": data["league"],
                    "home_team": data["home_team"],
                    "away_team": data["away_team"],
                    "home_odds": data["home_odds"],
                    "draw_odds": data["draw_odds"],
                    "away_odds": data["away_odds"],
                    "over_2_5": data["over_2_5"],
                    "under_2_5": data["under_2_5"],
                    "btts_yes": None,
                    "btts_no": None,
                    "commence_time": data["commence_time"],
                })

        print(f"✅ RapidOddsAPI : {len(events_data)} matchs avec cotes 1X2 complètes et futures")

        if matches is None:
            return events_data

        # 5. Matching avec les matchs Bzzoiro (si mode dict demandé)
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
