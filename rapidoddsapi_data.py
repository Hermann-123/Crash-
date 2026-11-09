"""
RapidOddsAPI Data — Cotes réelles (SDK officiel)
pip install rapidoddsapi
"""
from __future__ import annotations

import os
import unicodedata
import re
from typing import Optional
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# SDK officiel
try:
    from rapidoddsapi import RapidOddsAPI
except ImportError:
    RapidOddsAPI = None
    print("❌ rapidoddsapi non installé. Exécute : pip install rapidoddsapi")

RAPIDODDS_API_KEY = os.getenv("RAPIDODDSAPI_API_KEY", "")

# Ligues supportées (identifiants exacts de l’API)
SOCCER_LEAGUES = [
    "EPL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "MLS"
]

# Feed bookmaker (nom exact, sensible à la casse)
BOOKMAKER_FEED = "Pinnacle"  # ou "Bet365", "DraftKings", etc.


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


def fetch_all_odds(matches: list[dict] = None):
    if not RAPIDODDS_API_KEY:
        print("⚠️ RapidOddsAPI : RAPIDODDSAPI_API_KEY manquante")
        return [] if matches is None else {}
    if not RapidOddsAPI:
        print("⚠️ SDK rapidoddsapi non disponible")
        return [] if matches is None else {}

    client = RapidOddsAPI(api_key=RAPIDODDS_API_KEY)

    all_events = []

    for league in SOCCER_LEAGUES:
        # --- 1X2 (head_to_head) ---
        try:
            print(f"🔎 RapidOddsAPI : {league} (head_to_head)...")
            odds_resp = client.get_odds(league, ["head_to_head"], [BOOKMAKER_FEED])
            games = odds_resp.get("games", [])
            if games:
                print(f"✅ {league} : {len(games)} matchs 1X2 récupérés")
                for entry in games:
                    game = entry.get("game", {})
                    home = game.get("home_team", "")
                    away = game.get("away_team", "")
                    commence_time = game.get("start_time") or game.get("commence_time", "")
                    if not home or not away or not _is_in_future(commence_time):
                        continue

                    home_odds = draw_odds = away_odds = None
                    for book in entry.get("bookmakers", []):
                        for market in book.get("markets", []):
                            for out in market.get("outcomes", []):
                                name = (out.get("name") or "").lower()
                                price = _safe_float(out.get("price"))
                                if not price: continue
                                if name in ("home", "1"): home_odds = price
                                elif name in ("away", "2"): away_odds = price
                    # head_to_head ne renvoie pas le nul → on laisse None
                    if home_odds and away_odds:
                        all_events.append({
                            "match_id": f"rapid_{_norm(home)}__{_norm(away)}",
                            "league": league,
                            "home_team": home,
                            "away_team": away,
                            "home_odds": home_odds,
                            "draw_odds": None,   # calculé par Dixon
                            "away_odds": away_odds,
                            "over_2_5": None,
                            "under_2_5": None,
                            "btts_yes": None,
                            "btts_no": None,
                            "commence_time": commence_time,
                        })
            else:
                print(f"⚠️ {league} : aucun match 1X2 pour {BOOKMAKER_FEED}")
        except Exception as e:
            print(f"⚠️ {league} (1X2) erreur : {e}")

        # --- Totals (Over/Under 2.5) ---
        try:
            print(f"🔎 RapidOddsAPI : {league} (totals)...")
            totals_resp = client.get_odds(league, ["totals"], [BOOKMAKER_FEED])
            games_totals = totals_resp.get("games", [])
            if games_totals:
                print(f"✅ {league} : {len(games_totals)} matchs Totals récupérés")
                for entry in games_totals:
                    game = entry.get("game", {})
                    home = game.get("home_team", "")
                    away = game.get("away_team", "")
                    commence_time = game.get("start_time") or game.get("commence_time", "")
                    if not home or not away or not _is_in_future(commence_time):
                        continue

                    over_25 = under_25 = None
                    for book in entry.get("bookmakers", []):
                        for market in book.get("markets", []):
                            for out in market.get("outcomes", []):
                                name = (out.get("name") or "").lower()
                                price = _safe_float(out.get("price"))
                                line = out.get("line") or out.get("handicap")
                                try: line_f = float(line) if line is not None else None
                                except: line_f = None
                                if line_f == 2.5:
                                    if "over" in name: over_25 = price
                                    elif "under" in name: under_25 = price

                    if over_25 and under_25:
                        # Fusionner avec l'événement existant
                        key = f"{_norm(home)}__{_norm(away)}"
                        for ev in all_events:
                            if ev["match_id"] == f"rapid_{key}":
                                ev["over_2_5"] = over_25
                                ev["under_2_5"] = under_25
                                break
            else:
                print(f"⚠️ {league} : aucun Totals pour {BOOKMAKER_FEED}")
        except Exception as e:
            print(f"⚠️ {league} (Totals) erreur : {e}")

    print(f"✅ RapidOddsAPI : {len(all_events)} matchs au total avec cotes réelles")

    if matches is None:
        return all_events

    # Matching avec les matchs Bzzoiro
    results = {}
    for m in matches:
        target_home = _norm(m.get("home", ""))
        target_away = _norm(m.get("away", ""))
        if not target_home or not target_away:
            continue

        best, best_score = None, 0
        for ev in all_events:
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
