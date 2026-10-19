"""
Bzzoiro Direct — Version ULTRA-DÉFENSIVE avec log par prédiction.
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta

import httpx

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"


def _headers():
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


def _safe_float(v):
    if v is None:
        return 0.0
    try:
        f = float(v)
        if 0 <= f <= 1:
            return f * 100
        if 0 < f <= 100:
            return f
        return 0.0
    except (ValueError, TypeError):
        return 0.0


async def _get(client, path, params=None):
    try:
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=20.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro {path} -> {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro {path} crash: {e}")
        return {}


def _parse_markets(mk):
    """Extrait les probas depuis le dict markets. Log les erreurs."""
    if not isinstance(mk, dict):
        return {}

    result = {}

    # 1X2
    mr = mk.get("match_result")
    if isinstance(mr, dict):
        h = _safe_float(mr.get("prob_home"))
        d = _safe_float(mr.get("prob_draw"))
        a = _safe_float(mr.get("prob_away"))
        if h > 0: result["victoire_home"] = round(h, 1)
        if d > 0: result["match_nul"] = round(d, 1)
        if a > 0: result["victoire_away"] = round(a, 1)

    # Over/Under
    ou = mk.get("over_under")
    if isinstance(ou, dict):
        o25 = _safe_float(ou.get("prob_over_25"))
        if o25 > 0:
            result["over_2.5"] = round(o25, 1)
            result["under_2.5"] = round(100 - o25, 1)
        o35 = _safe_float(ou.get("prob_over_35"))
        if o35 > 0:
            result["over_3.5"] = round(o35, 1)

    # BTTS
    btts = mk.get("btts")
    if isinstance(btts, dict):
        y = _safe_float(btts.get("prob_yes"))
        if y > 0:
            result["btts_yes"] = round(y, 1)
            result["btts_no"] = round(100 - y, 1)

    # Dérivés
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = round(result["victoire_home"] + result["match_nul"], 1)
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = round(result["victoire_home"] + result["victoire_away"], 1)
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = round(result["match_nul"] + result["victoire_away"], 1)
    if "btts_yes" in result and "over_2.5" in result:
        result["btts_over_25"] = round(min(result["btts_yes"], result["over_2.5"]) * 0.85, 1)

    return result


async def fetch_bzzoiro_predictions_direct(min_confidence=65.0):
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante")
        return []

    print(f"🔮 Bzzoiro : fetching...")

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            data = await _get(client, "/predictions/", {
                "date_from": datetime.now().strftime("%Y-%m-%d"),
                "date_to": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
                "limit": "500",
            })
            preds = data.get("results") or data.get("data") or []
    except Exception as e:
        print(f"❌ Fetch crash: {e}")
        return []

    print(f"🔮 Bzzoiro : {len(preds)} brutes")
    if not preds:
        return []

    # LOG CHAQUE PRÉDICTION (5 premières)
    enriched = []
    for idx, pred in enumerate(preds):
        if not isinstance(pred, dict):
            if idx < 5:
                print(f"🔍#{idx}: NOT DICT ({type(pred).__name__})")
            continue

        # Parse markets
        mk = pred.get("markets")
        parsed = _parse_markets(mk)

        # LOG pour les 5 premières
        if idx < 5:
            mk_type = type(mk).__name__
            mk_keys = list(mk.keys())[:5] if isinstance(mk, dict) else "N/A"
            print(f"🔍#{idx}: markets={mk_type}, keys={mk_keys}, parsed={list(parsed.keys()) if parsed else 'EMPTY'}")

        # Équipes
        ev = pred.get("event") or {}
        home = ""
        away = ""
        kickoff = datetime.now().isoformat()
        league = ""

        if isinstance(ev, dict):
            for k in ["home_team", "home", "team_a", "homeTeam"]:
                v = ev.get(k)
                if v:
                    home = v if isinstance(v, str) else (v.get("name", "") if isinstance(v, dict) else str(v))
                    break
            for k in ["away_team", "away", "team_b", "awayTeam"]:
                v = ev.get(k)
                if v:
                    away = v if isinstance(v, str) else (v.get("name", "") if isinstance(v, dict) else str(v))
                    break
            for k in ["commence_time", "kickoff", "date", "start_time"]:
                if ev.get(k):
                    kickoff = str(ev[k])
                    break
            league = ev.get("league_name") or ev.get("league") or ""

        # Fallback names
        home = home or f"Équipe_{pred.get('id', idx)}_A"
        away = away or f"Équipe_{pred.get('id', idx)}_B"

        enriched.append({
            "event_id": f"bzz_{pred.get('id', idx)}",
            "home": str(home),
            "away": str(away),
            "league": str(league),
            "kickoff": kickoff,
            "bzz_markets": parsed,
            "bzz_confidence": 70.0,
            "bzz_raw": pred,
            "bzz_score_exact": {},
            "has_odds": False,
            "real_odds": {},
            "odds_sport_key": "",
        })

    with_m = sum(1 for e in enriched if e["bzz_markets"])
    print(f"🔮 Bzzoiro : {len(enriched)} retenues ({with_m} avec marchés)")

    return enriched
