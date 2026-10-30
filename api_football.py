"""
API-Football v3 — Récupération massive des cotes par date (ultra-optimisé).
Utilise 5 requêtes pour récupérer des dizaines de matchs avec cotes.
"""
from __future__ import annotations
import asyncio
import os
from typing import Optional
import httpx

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# On exclut le Top 5 (Anti-Vague)
TOP_LEAGUES_TO_EXCLUDE = ["Premier League", "Ligue 1", "La Liga", "Serie A", "Bundesliga"]

def _headers() -> dict:
    return {"x-apisports-key": API_FOOTBALL_KEY}

def _safe_float(v) -> Optional[float]:
    if v is None: return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except (ValueError, TypeError): return None

async def fetch_odds_for_date(date_str: str) -> dict:
    """
    Récupère toutes les cotes de la journée en 5 requêtes max.
    Retourne un dictionnaire prêt pour le bot.
    """
    if not API_FOOTBALL_KEY:
        print("⚠️ API-Football : pas de clé")
        return {}

    results = {}
    print(f"📊 API-Football : Chargement des cotes du {date_str}...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # On demande les 5 premières pages d'odds (environ 50 à 100 matchs couverts)
        for page in range(1, 6):
            try:
                r = await client.get(
                    f"{API_FOOTBALL_BASE}/odds", 
                    headers=_headers(), 
                    params={"date": date_str, "page": page, "timezone": "UTC"}
                )
                
                if r.status_code == 429:
                    print("⏳ Limite API atteinte. Pause de 10s...")
                    await asyncio.sleep(10)
                    continue # On réessaie
                
                if r.status_code >= 400:
                    break
                    
                data = r.json()
                response = data.get("response", [])
                if not response:
                    break # Plus de matchs sur cette page

                for item in response:
                    league_name = item.get("league", {}).get("name", "")
                    
                    # FILTRE ANTI-VAGUE : On jette les grands championnats
                    if any(top in league_name for top in TOP_LEAGUES_TO_EXCLUDE):
                        continue

                    fixture = item.get("fixture", {})
                    fixture_id = fixture.get("id")
                    
                    if not fixture_id:
                        continue

                    # Parsing des bookmakers
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

                        # Si on a le 1X2 complet, on s'arrête pour ce match
                        if odds_data["home"] and odds_data["draw"] and odds_data["away"]:
                            break
                    
                    # On garde le match SEULEMENT si le favori n'est pas trop évident (< 1.30)
                    if odds_data["home"] or odds_data["away"]:
                        min_odd = min([x for x in [odds_data["home"], odds_data["away"]] if x] or [10])
                        if min_odd >= 1.30:
                            event_id = f"af_{fixture_id}"
                            results[event_id] = {
                                "event_id": event_id,
                                "fixture_id": fixture_id,
                                "home": item.get("teams", {}).get("home", {}).get("name", ""),
                                "away": item.get("teams", {}).get("away", {}).get("name", ""),
                                "league": league_name,
                                "kickoff": fixture.get("date", ""),
                                "odds": odds_data
                            }
                
                await asyncio.sleep(1) # Petite pause entre les pages
                
            except Exception as e:
                print(f"⚠️ API-Football crash page {page}: {e}")
                break

    print(f"📊 API-Football : {len(results)} matchs avec cotes trouvés (Hors Top 5).")
    return results
