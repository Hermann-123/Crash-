"""
Récupération DIRECTE des prédictions Bzzoiro avec leur confiance native.
Endpoint : /api/predictions/?upcoming=true
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from datetime import datetime
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
            print(f"⚠️ Bzzoiro direct {path} -> {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro direct {path} crash: {e}")
        return {}


def extract_bzzoiro_native_markets(pred: dict) -> dict:
    """Extrait les marchés avec la VRAIE structure Bzzoiro."""
    if not pred:
        return {}

    result = {}
    markets = pred.get("markets", {}) or {}
    if not isinstance(markets, dict):
        return {}

    # 1X2
    mr = markets.get("match_result", {}) or {}
    if isinstance(mr, dict):
        try:
            h = float(mr.get("prob_home", 0))
            d = float(mr.get("prob_draw", 0))
            a = float(mr.get("prob_away", 0))
            if 0 < h < 100:
                result["victoire_home"] = round(h, 1)
            if 0 < d < 100:
                result["match_nul"] = round(d, 1)
            if 0 < a < 100:
                result["victoire_away"] = round(a, 1)
        except (ValueError, TypeError):
            pass

    # Over/Under
    ou = markets.get("over_under", {}) or {}
    if isinstance(ou, dict):
        try:
            o25 = float(ou.get("prob_over_25", 0))
            if 0 < o25 < 100:
                result["over_2.5"] = round(o25, 1)
                result["under_2.5"] = round(100.0 - o25, 1)
            o35 = float(ou.get("prob_over_35", 0))
            if 0 < o35 < 100:
                result["over_3.5"] = round(o35, 1)
        except (ValueError, TypeError):
            pass

    # BTTS
    btts = markets.get("btts", {}) or {}
    if isinstance(btts, dict):
        try:
            yes = float(btts.get("prob_yes", 0))
            if 0 < yes < 100:
                result["btts_yes"] = round(yes, 1)
                result["btts_no"] = round(100.0 - yes, 1)
        except (ValueError, TypeError):
            pass

    # Corners
    corners = markets.get("corners")
    if isinstance(corners, dict):
        try:
            c85 = float(corners.get("prob_over_85", 0))
            c95 = float(corners.get("prob_over_95", 0))
            c105 = float(corners.get("prob_over_105", 0))
            if 0 < c85 < 100:
                result["over_8.5_corners"] = round(c85, 1)
            if 0 < c95 < 100:
                result["over_9.5_corners"] = round(c95, 1)
            if 0 < c105 < 100:
                result["over_10.5_corners"] = round(c105, 1)
        except (ValueError, TypeError):
            pass

    # Double chance dérivée
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = result["victoire_home"] + result["match_nul"]
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = result["victoire_home"] + result["victoire_away"]
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = result["match_nul"] + result["victoire_away"]

    # BTTS + Over 2.5 combiné
    if "btts_yes" in result and "over_2.5" in result:
        combined = min(result["btts_yes"], result["over_2.5"]) * 0.85
        result["btts_over_25"] = round(combined, 1)

    return result


def extract_native_confidence(pred: dict) -> float:
    """
    Récupère la VRAIE confiance Bzzoiro (0-100).
    Cherche dans plusieurs champs possibles.
    """
    for key in ["confidence", "confidence_score", "model_confidence"]:
        v = pred.get(key)
        if v is not None:
            try:
                fv = float(v)
                if 0 <= fv <= 1:
                    return fv * 100
                if 0 < fv <= 100:
                    return fv
            except (ValueError, TypeError):
                pass

    markets = pred.get("markets", {}) or {}
    best_prob = 0.0
    mr = markets.get("match_result", {}) or {}
    if isinstance(mr, dict):
        try:
            h = float(mr.get("prob_home", 0))
            d = float(mr.get("prob_draw", 0))
            a = float(mr.get("prob_away", 0))
            best_prob = max(h, d, a)
        except (ValueError, TypeError):
            pass

    if best_prob > 0:
        return best_prob

    return 0.0


def extract_bzzoiro_score_exact(pred: dict) -> dict:
    """
    Extrait le score exact le plus probable depuis la prédiction Bzzoiro.
    """
    if not pred:
        return {}

    markets = pred.get("markets", {}) or {}
    if isinstance(markets, dict):
        for key in ("exact_score", "score_exact", "correct_score"):
            cand = markets.get(key)
            if isinstance(cand, dict):
                try:
                    h = int(cand.get("home", -1))
                    a = int(cand.get("away", -1))
                    p = float(cand.get("prob", cand.get("probability", 0)))
                    if 0 <= h <= 8 and 0 <= a <= 8:
                        if p > 1.0:
                            p = p / 100.0
                        if 0 < p <= 0.6:
                            return {"home": h, "away": a, "prob": round(p, 4)}
                except (ValueError, TypeError):
                    pass
                best_score, best_prob = None, 0.0
                for k, v in cand.items():
                    m = re.match(r"^(\d+)\s*[-:]\s*(\d+)$", str(k).strip())
                    if not m:
                        continue
                    try:
                        p = float(v)
                    except (ValueError, TypeError):
                        continue
                    if p > 1.0:
                        p = p / 100.0
                    if 0 < p <= 0.6 and p > best_prob:
                        best_prob = p
                        best_score = (int(m.group(1)), int(m.group(2)))
                if best_score:
                    return {"home": best_score[0], "away": best_score[1], "prob": round(best_prob, 4)}

    for key in ("most_likely_score", "exact_score", "predicted_score"):
        cand = pred.get(key)
        if isinstance(cand, dict):
            try:
                h = int(cand.get("home", -1))
                a = int(cand.get("away", -1))
                p = float(cand.get("prob", cand.get("probability", 0)))
                if 0 <= h <= 8 and 0 <= a <= 8:
                    if p > 1.0:
                        p = p / 100.0
                    if 0 < p <= 0.6:
                        return {"home": h, "away": a, "prob": round(p, 4)}
            except (ValueError, TypeError):
                pass
        elif isinstance(cand, str):
            m = re.match(r"^(\d+)\s*[-:]\s*(\d+)$", cand.strip())
            if m:
                return {"home": int(m.group(1)), "away": int(m.group(2)), "prob": 0.0}

    return {}


async def fetch_bzzoiro_predictions_direct(min_confidence: float = 75.0) -> list[dict]:
    """
    Récupère TOUTES les prédictions Bzzoiro à venir avec confiance >= min_confidence.
    """
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante")
        return []

    print(f"🔮 Bzzoiro DIRECT : récupération prédictions (confiance ≥ {min_confidence})...")

    async with httpx.AsyncClient(timeout=25.0) as client:
        data = await _get(client, "/predictions/", {"upcoming": "true"})
        predictions = data.get("results", []) or data.get("data", []) or []

        if not predictions:
            today = datetime.now().strftime("%Y-%m-%d")
            data = await _get(client, "/predictions/", {
                "date_from": today,
                "date_to": today,
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
            if not home or not away:
                continue

            kickoff = pred.get("commence_time", "") or pred.get("kickoff", "") or pred.get("date", "")
            if not kickoff:
                continue

            try:
                dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
                if dt.date() != datetime.now().date():
                    continue
            except Exception:
                pass

            markets = extract_bzzoiro_native_markets(pred)
            if not markets:
                continue

            confidence = extract_native_confidence(pred)
            if confidence < min_confidence:
                continue

            score_exact = extract_bzzoiro_score_exact(pred)

            enriched.append({
                "event_id": f"bzz_{pred.get('id', home + away)}",
                "home": home,
                "away": away,
                "league": pred.get("league_name", "") or pred.get("league", "") or pred.get("competition", ""),
                "kickoff": kickoff,
                "bzz_markets": markets,
                "bzz_confidence": round(confidence, 1),
                "bzz_raw": pred,
                "bzz_score_exact": score_exact,
                "has_odds": False,
                "real_odds": {},
                "odds_sport_key": "",
            })
        except Exception as e:
            print(f"  ⚠️ Erreur parsing pred: {e}")
            continue

    enriched.sort(key=lambda x: x["bzz_confidence"], reverse=True)

    print(f"🔮 Bzzoiro DIRECT : {len(enriched)} prédictions retenues (confiance ≥ {min_confidence})")
    n_score = sum(1 for e in enriched if e.get("bzz_score_exact"))
    print(f"⚽ Score exact Bzzoiro disponible : {n_score}/{len(enriched)}")
    for p in enriched[:15]:
        se = p.get("bzz_score_exact") or {}
        se_txt = ""
        if se.get("home") is not None:
            se_txt = f" ⚽{se['home']}-{se['away']}"
        print(f"   🎯 [{p['bzz_confidence']:.0f}%]{se_txt} {p['home']} vs {p['away']} • {p['league'][:30]}")

    return enriched
