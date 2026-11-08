"""
Odds-API.io Data — Cotes réelles de bookmakers
Docs : https://docs.odds-api.io
Auth : apiKey en query parameter
Free tier : 100 req/h, 500 req/jour
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

ODDSAPI_BASE_URL = "https://api.odds-api.io/v3"
ODDSAPI_KEY = os.getenv("ODDSAPI_KEY", "")

CACHE: dict = {}
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
    params["apiKey"] = ODDSAPI_KEY

    try:
        r = await client.get(f"{ODDSAPI_BASE_URL}{path}", params=params, timeout=25.0)
        if r.status_code == 429:
            print("⚠️ Odds-API.io RATE LIMIT (429)")
            return {}
        if r.status_code >= 400:
            print(f"⚠️ Odds-API.io {path} -> {r.status_code} | {r.text[:150]}")
            return {}
        return r.json() if r.text else {}
    except Exception as e:
        print(f"⚠️ Odds-API.io {path} crash: {e}")
        return {}


def _parse_odds_item(item: dict) -> dict:
    """Parse une ligne de cote Odds-API.io (format : {home, draw, away} ou {over, under})"""
    return {
        "home": _safe_float(item.get("home")),
        "draw": _safe_float(item.get("draw")),
        "away": _safe_float(item.get("away")),
        "over": _safe_float(item.get("over")),
        "under": _safe_float(item.get("under")),
        "yes": _safe_float(item.get("yes")),
        "no": _safe_float(item.get("no")),
    }


async def fetch_all_odds(matches: list[dict] = None) -> object:
    """
    API publique :
      - sans matches  -> list[dict] (tous les matchs avec cotes)
      - avec matches  -> {event_id: odds_dict} matché par noms
    """
    if not ODDSAPI_KEY:
        print("⚠️ Odds-API.io : ODDSAPI_KEY manquante")
        return [] if matches is None else {}

    async with httpx.AsyncClient(timeout=25.0) as client:
        # 1. Récupérer les événements de football à venir
        events_data = await _get(client, "/events", {
            "sport": "football",
            "status": "pending",
        })

        if not isinstance(events_data, list):
            print("⚠️ Odds-API.io : réponse /events invalide")
            return [] if matches is None else {}

        print(f"🔎 Odds-API.io : {len(events_data)} événements football trouvés")

        # 2. Filtrer les événements futurs
        upcoming = [
            ev for ev in events_data
            if _is_in_future(ev.get("date", ""), buffer_min=10)
        ]
        print(f"🔎 Odds-API.io : {len(upcoming)} événements futurs (après filtre)")

        if not upcoming:
            return [] if matches is None else {}

        # 3. Récupérer les cotes par lots de 10 via /odds/multi (1 requête = 10 events)
        events_data_list = []
        batch_size = 10
        for i in range(0, len(upcoming), batch_size):
            batch = upcoming[i:i + batch_size]
            ids = [str(ev.get("id")) for ev in batch if ev.get("id")]
            if not ids:
                continue

            odds_resp = await _get(client, "/odds/multi", {
                "eventIds": ",".join(ids),
            })

            if not isinstance(odds_resp, list):
                continue

            for ev_odds in odds_resp:
                ev_id = ev_odds.get("id")
                if not ev_id:
                    continue

                home_team = ev_odds.get("home", "")
                away_team = ev_odds.get("away", "")
                league = ev_odds.get("league", {}).get("name", "")
                commence_time = ev_odds.get("date", "")

                home_odds = draw_odds = away_odds = None
                over_25 = under_25 = None
                btts_yes = btts_no = None

                bookmakers = ev_odds.get("bookmakers", {})
                if isinstance(bookmakers, dict):
                    for bk_name, bk_data in bookmakers.items():
                        if not isinstance(bk_data, dict):
                            continue
                        markets = bk_data.get("markets", {})
                        if not isinstance(markets, dict):
                            continue

                        # ML (Moneyline / 1X2)
                        ml = markets.get("ML") or markets.get("ml")
                        if isinstance(ml, list) and ml:
                            for line in ml:
                                parsed = _parse_odds_item(line)
                                if home_odds is None and parsed["home"]: home_odds = parsed["home"]
                                if draw_odds is None and parsed["draw"]: draw_odds = parsed["draw"]
                                if away_odds is None and parsed["away"]: away_odds = parsed["away"]

                        # Totals (Over/Under 2.5)
                        totals = markets.get("Totals") or markets.get("totals")
                        if isinstance(totals, list) and totals:
                            for line in totals:
                                hdp = line.get("hdp")
                                try:
                                    hdp_f = float(hdp) if hdp is not None else None
                                except (ValueError, TypeError):
                                    hdp_f = None
                                if hdp_f == 2.5:
                                    parsed = _parse_odds_item(line)
                                    if over_25 is None and parsed["over"]: over_25 = parsed["over"]
                                    if under_25 is None and parsed["under"]: under_25 = parsed["under"]

                        # BTTS
                        btts = markets.get("BTTS") or markets.get("btts") or markets.get("Both Teams to Score")
                        if isinstance(btts, list) and btts:
                            for line in btts:
                                parsed = _parse_odds_item(line)
                                if btts_yes is None and parsed["yes"]: btts_yes = parsed["yes"]
                                if btts_no is None and parsed["no"]: btts_no = parsed["no"]

                        if home_odds and draw_odds and away_odds:
                            break

                if home_odds and draw_odds and away_odds:
                    events_data_list.append({
                        "match_id": f"oa_{ev_id}",
                        "league": league,
                        "home_team": home_team,
                        "away_team": away_team,
                        "home_odds": home_odds,
                        "draw_odds": draw_odds,
                        "away_odds": away_odds,
                        "over_2_5": over_25,
                        "under_2_5": under_25,
                        "btts_yes": btts_yes,
                        "btts_no": btts_no,
                        "commence_time": commence_time,
                    })

            await asyncio.sleep(1.0)  # respect rate limit

        print(f"✅ Odds-API.io : {len(events_data_list)} matchs avec cotes 1X2 complètes")

        # Debug 1 seule fois
        global _DEBUG_DONE
        if not _DEBUG_DONE and events_data_list:
            sample = events_data_list[0]
            print(f"🔎 Exemple : {sample['home_team']} vs {sample['away_team']} "
                  f"({sample['home_odds']}/{sample['draw_odds']}/{sample['away_odds']})")
            _DEBUG_DONE = True

        if matches is None:
            return events_data_list

        # Matching par noms
        results = {}
        for m in matches:
            target_home = _norm(m.get("home", ""))
            target_away = _norm(m.get("away", ""))
            if not target_home or not target_away:
                continue

            best, best_score = None, 0
            for ev in events_data_list:
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

        print(f"📊 Odds-API.io matching : {len(results)}/{len(matches)} matchs avec cotes")
        return results
