from __future__ import annotations
import asyncio
import os
from typing import Optional
import httpx

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

def _headers() -> dict:
    return {"x-apisports-key": API_FOOTBALL_KEY}

def _safe_float(v) -> Optional[float]:
    if v is None: return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except (ValueError, TypeError): return None

async def fetch_odds_for_date(date_str: str) -> dict:
    if not API_FOOTBALL_KEY: return {}

    results = {}
    print(f"📊 API-Football : Chargement des cotes du {date_str} (Toutes ligues)...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # On va chercher toutes les pages disponibles
        for page in range(1, 31):
            try:
                r = await client.get(
                    f"{API_FOOTBALL_BASE}/odds", 
                    headers=_headers(), 
                    params={"date": date_str, "page": page, "timezone": "UTC"}
                )
                if r.status_code == 429:
                    await asyncio.sleep(5)
                    continue
                if r.status_code >= 400: break
                    
                data = r.json()
                response = data.get("response", [])
                if not response: break

                for item in response:
                    league_name = item.get("league", {}).get("name", "")
                    
                    fixture = item.get("fixture", {})
                    fixture_id = fixture.get("id")
                    
                    teams = item.get("teams", {})
                    home_name = teams.get("home", {}).get("name")
                    away_name = teams.get("away", {}).get("name")
                    
                    if not fixture_id or not home_name or not away_name: continue

                    # Filtre strict : on ne garde que les matchs NON COMMENCÉS
                    status = fixture.get("status", {}).get("short", "")
                    if status not in ["NS", "TBD"]: continue

                    odds_data = {"home": None, "draw": None, "away": None, "over_2.5": None, "under_2.5": None, "btts_yes": None, "btts_no": None}
                    
                    for bk in item.get("bookmakers", []):
                        for bet in bk.get("bets", []):
                            bet_name = (bet.get("name") or "").lower()
                            values = bet.get("values", [])
                            
                            if "match winner" in bet_name:
                                for v in values:
                                    val = (v.get("value") or "").lower()
                                    odd = _safe_float(v.get("odd"))
                                    if val == "home" and not odds_data["home"]: odds_data["home"] = odd
                                    elif val == "draw" and not odds_data["draw"]: odds_data["draw"] = odd
                                    elif val == "away" and not odds_data["away"]: odds_data["away"] = odd
                            
                            elif ("over/under" in bet_name or "goals over/under" in bet_name) and "2.5" in bet_name:
                                for v in values:
                                    val = (v.get("value") or "").lower()
                                    odd = _safe_float(v.get("odd"))
                                    if "over" in val and not odds_data["over_2.5"]: odds_data["over_2.5"] = odd
                                    elif "under" in val and not odds_data["under_2.5"]: odds_data["under_2.5"] = odd
                            
                            elif "both teams" in bet_name:
                                for v in values:
                                    val = (v.get("value") or "").lower()
                                    odd = _safe_float(v.get("odd"))
                                    if val == "yes" and not odds_data["btts_yes"]: odds_data["btts_yes"] = odd
                                    elif val == "no" and not odds_data["btts_no"]: odds_data["btts_no"] = odd

                        if odds_data["home"] and odds_data["draw"] and odds_data["away"]:
                            break
                    
                    # ON GARDE TOUS LES MATCHS AVEC COTES (Même les favoris, c'est main.py qui filtrera)
                    if odds_data["home"] or odds_data["away"]:
                        event_id = f"af_{fixture_id}"
                        results[event_id] = {
                            "event_id": event_id,
                            "fixture_id": fixture_id,
                            "home": home_name,
                            "away": away_name,
                            "league": league_name,
                            "kickoff": fixture.get("date", ""),
                            "odds": odds_data
                        }
                await asyncio.sleep(0.5)
            except Exception:
                break
    print(f"📊 API-Football : {len(results)} matchs valides avec cotes (Toutes ligues).")
    return results
