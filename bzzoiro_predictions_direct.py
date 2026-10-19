"""
Récupération DIRECTE des prédictions Bzzoiro.
Correction erreur 400 : on utilise date_from/date_to au lieu de upcoming=true.
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

import httpx

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"
_CACHE: dict = {}


def _headers() -> dict:
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    try:
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=20.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro direct {path} -> {r.status_code} | {r.text[:100]}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro direct {path} crash: {e}")
        return {}


def extract_bzzoiro_native_markets(pred: dict) -> dict:
    if not pred:
        return {}
    result = {}
    markets = pred.get("markets", {}) or {}
    if not isinstance(markets, dict):
        return {}

    mr = markets.get("match_result", {}) or {}
    if isinstance(mr, dict):
        try:
            h = float(mr.get("prob_home", 0))
            d = float(mr.get("prob_draw", 0))
            a = float(mr.get("prob_away", 0))
            if 0 < h < 100: result["victoire_home"] = round(h, 1)
            if 0 < d < 100: result["match_nul"] = round(d, 1)
            if 0 < a < 100: result["victoire_away"] = round(a, 1)
        except (ValueError, TypeError): pass

    ou = markets.get("over_under", {}) or {}
    if isinstance(ou, dict):
        try:
            o25 = float(ou.get("prob_over_25", 0))
            if 0 < o25 < 100:
                result["over_2.5"] = round(o25, 1)
                result["under_2.5"] = round(100.0 - o25, 1)
            o35 = float(ou.get("prob_over_35", 0))
            if 0 < o35 < 100: result["over_3.5"] = round(o35, 1)
        except (ValueError, TypeError): pass

    btts = markets.get("btts", {}) or {}
    if isinstance(btts, dict):
        try:
            yes = float(btts.get("prob_yes", 0))
            if 0 < yes < 100:
                result["btts_yes"] = round(yes, 1)
                result["btts_no"] = round(100.0 - yes, 1)
        except (ValueError, TypeError): pass

    corners = markets.get("corners")
    if isinstance(corners, dict):
        try:
            c85 = float(corners.get("prob_over_85", 0))
            c95 = float(corners.get("prob_over_95", 0))
            c105 = float(corners.get("prob_over_105", 0))
            if 0 < c85 < 100: result["over_8.5_corners"] = round(c85, 1)
            if 0 < c95 < 100: result["over_9.5_corners"] = round(c95, 1)
            if 0 < c105 < 100: result["over_10.5_corners"] = round(c105, 1)
        except (ValueError, TypeError): pass

    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = result["victoire_home"] + result["match_nul"]
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = result["victoire_home"] + result["victoire_away"]
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = result["match_nul"] + result["victoire_away"]

    if "btts_yes" in result and "over_2.5" in result:
        combined = min(result["btts_yes"], result["over_2.5"]) * 0.85
        result["btts_over_25"] = round(combined, 1)

    return result


def extract_native_confidence(pred: dict) -> float:
    for key in ["confidence", "confidence_score", "model_confidence"]:
        v = pred.get(key)
        if v is not None:
            try:
                fv = float(v)
                if 0 <= fv <= 1: return fv * 100
                if 0 < fv <= 100: return fv
            except (ValueError, TypeError): pass
    markets = pred.get("markets", {}) or {}
    best_prob = 0.0
    mr = markets.get("match_result", {}) or {}
    if isinstance(mr, dict):
        try:
            h = float(mr.get("prob_home", 0))
            d = float(mr.get("prob_draw", 0))
            a = float(mr.get("prob_away", 0))
            best_prob = max(h, d, a)
        except (ValueError, TypeError): pass
    if best_prob > 0: return best_prob
    return 0.0


def extract_bzzoiro_score_exact(pred: dict) -> dict:
    if not pred: return {}
    markets = pred.get("markets", {}) or {}
    if isinstance(markets, dict):
        for key in ("exact_score", "score_exact", "correct_score"):
            cand = markets.get(key)
            if isinstance(cand, dict):
                try:
                    h, a = int(cand.get("home", -1)), int(cand.get("away", -1))
                    p = float(cand.get("prob", cand.get("probability", 0)))
                    if 0 <= h <= 8 and 0 <= a <= 8:
                        if p > 1.0: p = p / 100.0
                        if 0 < p <= 0.6: return {"home": h, "away": a, "prob": round(p, 4)}
                except (ValueError, TypeError): pass
    return {}


async def fetch_bzzoiro_predictions_direct(min_confidence: float = 75.0) -> list[dict]:
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante")
        return []

    print(f"🔮 Bzzoiro DIRECT : récupération (confiance ≥ {min_confidence})...")

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=25.0) as client:
        # Correction erreur 400 : date_from / date_to
        data = await _get(client, "/predictions/", {
            "date_from": today,
            "date_to": tomorrow,
            "limit": "500",
        })
        predictions = data.get("results", []) or data.get("data", []) or []

    print(f"🔮 Bzzoiro DIRECT : {len(predictions)} prédictions brutes")
    if not predictions:
        return []

    enriched = []
    for pred in predictions:
        try:
            home = pred.get("home_team", "") or pred.get("home", "")
            away = pred.get("away_team", "") or pred.get("away", "")
            if not home or not away: continue

            kickoff = pred.get("commence_time", "") or pred.get("kickoff", "") or pred.get("date", "")
            if not kickoff: continue

            try:
                dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
                if dt.date() < datetime.now().date(): continue
            except Exception: pass

            markets = extract_bzzoiro_native_markets(pred)
            if not markets: continue

            confidence = extract_native_confidence(pred)
            if confidence < min_confidence: continue

            score_exact = extract_bzzoiro_score_exact(pred)

            enriched.append({
                "event_id": f"bzz_{pred.get('id', home + away)}",
                "home": home, "away": away,
                "league": pred.get("league_name", "") or pred.get("league", "") or pred.get("competition", ""),
                "kickoff": kickoff,
                "bzz_markets": markets,
                "bzz_confidence": round(confidence, 1),
                "bzz_raw": pred,
                "bzz_score_exact": score_exact,
                "has_odds": False, "real_odds": {}, "odds_sport_key": "",
            })
        except Exception as e:
            continue

    enriched.sort(key=lambda x: x["bzz_confidence"], reverse=True)
    print(f"🔮 Bzzoiro DIRECT : {len(enriched)} prédictions retenues")
    for p in enriched[:10]:
        se = p.get("bzz_score_exact") or {}
        se_txt = f" ⚽{se['home']}-{se['away']}" if se.get("home") is not None else ""
        print(f"   🎯 [{p['bzz_confidence']:.0f}%]{se_txt} {p['home']} vs {p['away']}")

    return enriched
