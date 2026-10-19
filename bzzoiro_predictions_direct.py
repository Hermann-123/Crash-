"""
Bzzoiro Direct — Version CORRIGÉE (structure event.markets).
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
            print(f"⚠️ Bzzoiro {path} -> {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro {path} crash: {e}")
        return {}


def _safe_float(v, default=0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        if 0 <= f <= 1:
            return f * 100
        if 0 < f <= 100:
            return f
        return default
    except (ValueError, TypeError):
        return default


def extract_bzzoiro_native_markets(pred: dict) -> dict:
    """Parse markets selon la structure réelle : pred['markets'] contient sous-dicts."""
    if not pred:
        return {}

    result = {}
    markets = pred.get("markets", {}) or {}

    if not isinstance(markets, dict):
        return result

    # 1X2
    mr = markets.get("match_result", {}) or {}
    if isinstance(mr, dict):
        h = _safe_float(mr.get("prob_home"))
        d = _safe_float(mr.get("prob_draw"))
        a = _safe_float(mr.get("prob_away"))
        if 0 < h < 100: result["victoire_home"] = round(h, 1)
        if 0 < d < 100: result["match_nul"] = round(d, 1)
        if 0 < a < 100: result["victoire_away"] = round(a, 1)

    # Over/Under
    ou = markets.get("over_under", {}) or {}
    if isinstance(ou, dict):
        o25 = _safe_float(ou.get("prob_over_25") or ou.get("over_2_5") or ou.get("over_25"))
        if 0 < o25 < 100:
            result["over_2.5"] = round(o25, 1)
            result["under_2.5"] = round(100.0 - o25, 1)
        o35 = _safe_float(ou.get("prob_over_35") or ou.get("over_3_5") or ou.get("over_35"))
        if 0 < o35 < 100:
            result["over_3.5"] = round(o35, 1)

    # BTTS
    btts = markets.get("btts", {}) or {}
    if isinstance(btts, dict):
        yes = _safe_float(btts.get("prob_yes") or btts.get("yes") or btts.get("gg"))
        if 0 < yes < 100:
            result["btts_yes"] = round(yes, 1)
            result["btts_no"] = round(100.0 - yes, 1)

    # Corners
    corners = markets.get("corners", {}) or {}
    if isinstance(corners, dict):
        c85 = _safe_float(corners.get("prob_over_85") or corners.get("over_85"))
        c95 = _safe_float(corners.get("prob_over_95") or corners.get("over_95"))
        c105 = _safe_float(corners.get("prob_over_105") or corners.get("over_105"))
        if 0 < c85 < 100: result["over_8.5_corners"] = round(c85, 1)
        if 0 < c95 < 100: result["over_9.5_corners"] = round(c95, 1)
        if 0 < c105 < 100: result["over_10.5_corners"] = round(c105, 1)

    # Double Chance
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = round(result["victoire_home"] + result["match_nul"], 1)
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = round(result["victoire_home"] + result["victoire_away"], 1)
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = round(result["match_nul"] + result["victoire_away"], 1)

    # BTTS+O2.5
    if "btts_yes" in result and "over_2.5" in result:
        result["btts_over_25"] = round(min(result["btts_yes"], result["over_2.5"]) * 0.85, 1)

    return result


def extract_native_confidence(pred: dict) -> float:
    """Cherche la confiance dans model, recommendations, ou racine."""
    # Dans model
    model = pred.get("model", {}) or {}
    if isinstance(model, dict):
        for k in ["confidence", "confidence_score", "score", "accuracy"]:
            if model.get(k) is not None:
                v = _safe_float(model[k])
                if 0 < v <= 100:
                    return v

    # Dans recommendations
    recs = pred.get("recommendations", []) or []
    if isinstance(recs, list):
        for r in recs:
            if isinstance(r, dict):
                for k in ["confidence", "probability", "prob"]:
                    if r.get(k) is not None:
                        v = _safe_float(r[k])
                        if 0 < v <= 100:
                            return v

    # À la racine
    for k in ["confidence", "confidence_score", "model_confidence"]:
        if pred.get(k) is not None:
            v = _safe_float(pred[k])
            if 0 < v <= 100:
                return v

    # Fallback : meilleure proba 1X2
    markets = pred.get("markets", {}) or {}
    mr = markets.get("match_result", {}) or {}
    if isinstance(mr, dict):
        h = _safe_float(mr.get("prob_home"))
        d = _safe_float(mr.get("prob_draw"))
        a = _safe_float(mr.get("prob_away"))
        if max(h, d, a) > 0:
            return max(h, d, a)

    return 65.0


def extract_bzzoiro_score_exact(pred: dict) -> dict:
    """Parse markets.score."""
    if not pred:
        return {}
    markets = pred.get("markets", {}) or {}
    score = markets.get("score", {}) or {}
    if isinstance(score, dict):
        # Format {"2-1": 12.5, "1-0": 9.3}
        best_h, best_a, best_p = -1, -1, 0.0
        for k, v in score.items():
            if isinstance(k, str):
                m = re.match(r"^(\d+)\s*[-:]\s*(\d+)$", k.strip())
                if m:
                    try:
                        p = float(v)
                        if p > 1.0: p = p / 100.0
                        if 0 < p <= 0.6 and p > best_p:
                            best_p = p
                            best_h, best_a = int(m.group(1)), int(m.group(2))
                    except (ValueError, TypeError):
                        pass
        if best_h >= 0:
            return {"home": best_h, "away": best_a, "prob": round(best_p, 4)}
        # Format {"home": 2, "away": 1, "prob": 12.5}
        if "home" in score and "away" in score:
            try:
                return {
                    "home": int(score["home"]),
                    "away": int(score["away"]),
                    "prob": round(_safe_float(score.get("prob")) / 100.0, 4)
                }
            except (ValueError, TypeError):
                pass
    return {}


async def fetch_bzzoiro_predictions_direct(min_confidence: float = 65.0) -> list[dict]:
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante")
        return []

    print(f"🔮 Bzzoiro DIRECT : récupération (confiance ≥ {min_confidence})...")

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=25.0) as client:
        data = await _get(client, "/predictions/", {
            "date_from": today,
            "date_to": tomorrow,
            "limit": "500",
        })
        predictions = data.get("results", []) or data.get("data", []) or []

    print(f"🔮 Bzzoiro DIRECT : {len(predictions)} brutes")
    if not predictions:
        return []

    enriched = []
    for pred in predictions:
        try:
            # === LE FIX : les équipes sont dans pred['event'] ===
            event = pred.get("event", {}) or {}
            home = event.get("home_team") or event.get("home") or pred.get("home_team") or pred.get("home", "")
            away = event.get("away_team") or event.get("away") or pred.get("away_team") or pred.get("away", "")
            if not home or not away:
                continue

            kickoff = (event.get("commence_time") or event.get("kickoff") or event.get("date")
                       or pred.get("commence_time") or pred.get("kickoff", ""))
            if not kickoff:
                continue

            league = (event.get("league_name") or event.get("league") or event.get("competition")
                      or pred.get("league_name") or pred.get("league", ""))

            markets = extract_bzzoiro_native_markets(pred)
            confidence = extract_native_confidence(pred)

            enriched.append({
                "event_id": f"bzz_{pred.get('id', home + away)}",
                "home": home,
                "away": away,
                "league": league,
                "kickoff": kickoff,
                "bzz_markets": markets,
                "bzz_confidence": round(confidence, 1),
                "bzz_raw": pred,
                "bzz_score_exact": extract_bzzoiro_score_exact(pred),
                "has_odds": False,
                "real_odds": {},
                "odds_sport_key": "",
            })
        except Exception as e:
            print(f"  ⚠️ Erreur parsing: {e}")
            continue

    enriched.sort(key=lambda x: x["bzz_confidence"], reverse=True)

    with_markets = sum(1 for e in enriched if e["bzz_markets"])
    print(f"🔮 Bzzoiro DIRECT : {len(enriched)} retenues ({with_markets} avec marchés parsés)")

    for p in enriched[:10]:
        m_count = len(p["bzz_markets"]) if p["bzz_markets"] else 0
        has_25 = "✅" if p["bzz_markets"].get("btts_over_25") else "❌"
        print(f"   [{p['bzz_confidence']:.0f}%] {p['home']} vs {p['away']} • {m_count} marchés • BTTS+O2.5:{has_25}")

    return enriched
