"""
Bzzoiro Direct — Version avec DIAGNOSTIC COMPLET.
Logge la structure réelle + accepte plusieurs formats.
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
            print(f"⚠️ Bzzoiro {path} -> {r.status_code} | {r.text[:200]}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro {path} crash: {e}")
        return {}


def _safe_float(v, default=0.0) -> float:
    """Convertit n'importe quoi en float entre 0 et 100."""
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


def _extract_from_market_dict(mkt: dict) -> dict:
    """Extrait les probas d'un dict de marché, quel que soit le nom des clés."""
    result = {}
    if not isinstance(mkt, dict):
        return result

    # Chercher les clés home/draw/away avec tous les noms possibles
    home_keys = ["prob_home", "home", "home_win", "prob_1", "1", "team_a", "home_probability"]
    draw_keys = ["prob_draw", "draw", "prob_x", "x", "tie", "draw_probability"]
    away_keys = ["prob_away", "away", "away_win", "prob_2", "2", "team_b", "away_probability"]

    for k in home_keys:
        if k in mkt:
            result["victoire_home"] = _safe_float(mkt[k])
            break
    for k in draw_keys:
        if k in mkt:
            result["match_nul"] = _safe_float(mkt[k])
            break
    for k in away_keys:
        if k in mkt:
            result["victoire_away"] = _safe_float(mkt[k])
            break

    # Over/Under
    for k in ["prob_over_25", "over_2_5", "over_25", "o25", "over25"]:
        if k in mkt:
            result["over_2.5"] = _safe_float(mkt[k])
            break
    for k in ["prob_under_25", "under_2_5", "under_25", "u25", "under25"]:
        if k in mkt:
            result["under_2.5"] = _safe_float(mkt[k])
            break

    # BTTS
    for k in ["prob_yes", "btts_yes", "yes", "gg", "both_score"]:
        if k in mkt:
            result["btts_yes"] = _safe_float(mkt[k])
            break
    for k in ["prob_no", "btts_no", "no", "ng", "both_not_score"]:
        if k in mkt:
            result["btts_no"] = _safe_float(mkt[k])
            break

    return result


def extract_bzzoiro_native_markets(pred: dict) -> dict:
    """
    Version ROBUSTE qui accepte plusieurs formats de l'API Bzzoiro.
    Logge ce qu'elle voit pour diagnostic.
    """
    if not pred:
        return {}

    result = {}
    markets = pred.get("markets", {}) or pred.get("predictions", {}) or pred.get("tips", {})

    # Cas 1 : markets est une LISTE
    if isinstance(markets, list):
        for mkt in markets:
            if isinstance(mkt, dict):
                # Nom du marché
                mkt_name = str(mkt.get("name", "") or mkt.get("market", "") or mkt.get("type", "")).lower()
                probs = _extract_from_market_dict(mkt)

                if "result" in mkt_name or "1x2" in mkt_name or "match" in mkt_name or "winner" in mkt_name:
                    if "victoire_home" in probs: result["victoire_home"] = probs["victoire_home"]
                    if "match_nul" in probs: result["match_nul"] = probs["match_nul"]
                    if "victoire_away" in probs: result["victoire_away"] = probs["victoire_away"]
                if "over" in mkt_name or "under" in mkt_name or "total" in mkt_name or "ou" in mkt_name:
                    if "over_2.5" in probs: result["over_2.5"] = probs["over_2.5"]
                    if "under_2.5" in probs: result["under_2.5"] = probs["under_2.5"]
                if "btts" in mkt_name or "both" in mkt_name or "gg" in mkt_name:
                    if "btts_yes" in probs: result["btts_yes"] = probs["btts_yes"]
                    if "btts_no" in probs: result["btts_no"] = probs["btts_no"]

    # Cas 2 : markets est un dict avec sous-dicts (format original)
    elif isinstance(markets, dict):
        # On cherche d'abord les sous-dicts connus
        for key in ["match_result", "1x2", "1X2", "result", "winner", "home_draw_away"]:
            sub = markets.get(key)
            if isinstance(sub, dict):
                probs = _extract_from_market_dict(sub)
                if "victoire_home" in probs: result["victoire_home"] = probs["victoire_home"]
                if "match_nul" in probs: result["match_nul"] = probs["match_nul"]
                if "victoire_away" in probs: result["victoire_away"] = probs["victoire_away"]
                break

        for key in ["over_under", "ou", "total_goals", "goals", "totals"]:
            sub = markets.get(key)
            if isinstance(sub, dict):
                probs = _extract_from_market_dict(sub)
                if "over_2.5" in probs: result["over_2.5"] = probs["over_2.5"]
                if "under_2.5" in probs: result["under_2.5"] = probs["under_2.5"]
                break

        for key in ["btts", "btts_yes_no", "both_teams_to_score", "gg_ng"]:
            sub = markets.get(key)
            if isinstance(sub, dict):
                probs = _extract_from_market_dict(sub)
                if "btts_yes" in probs: result["btts_yes"] = probs["btts_yes"]
                if "btts_no" in probs: result["btts_no"] = probs["btts_no"]
                break

        # Si rien trouvé, on cherche les probas direct dans markets
        if not result:
            direct = _extract_from_market_dict(markets)
            result.update(direct)

    # Dériver Double Chance
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = round(result["victoire_home"] + result["match_nul"], 1)
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = round(result["victoire_home"] + result["victoire_away"], 1)
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = round(result["match_nul"] + result["victoire_away"], 1)

    # Dériver BTTS+O2.5
    if "btts_yes" in result and "over_2.5" in result:
        result["btts_over_25"] = round(min(result["btts_yes"], result["over_2.5"]) * 0.85, 1)

    return result


def extract_native_confidence(pred: dict) -> float:
    """Confiance native — cherche dans tous les champs possibles."""
    for key in ["confidence", "confidence_score", "model_confidence", "score", "accuracy", "trust"]:
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

    # Fallback : meilleure proba du marché
    markets = pred.get("markets", {}) or {}
    if isinstance(markets, dict):
        mr = markets.get("match_result", {}) or markets.get("1x2", {}) or {}
        if isinstance(mr, dict):
            h = _safe_float(mr.get("prob_home") or mr.get("home"))
            d = _safe_float(mr.get("prob_draw") or mr.get("draw"))
            a = _safe_float(mr.get("prob_away") or mr.get("away"))
            if max(h, d, a) > 0:
                return max(h, d, a)

    return 65.0  # Défaut


def extract_bzzoiro_score_exact(pred: dict) -> dict:
    if not pred:
        return {}
    # Chercher dans markets
    markets = pred.get("markets", {}) or {}
    if isinstance(markets, dict):
        for key in ("exact_score", "score_exact", "correct_score", "score"):
            cand = markets.get(key)
            if isinstance(cand, dict):
                try:
                    h = int(cand.get("home", -1))
                    a = int(cand.get("away", -1))
                    p = float(cand.get("prob", cand.get("probability", 0)))
                    if 0 <= h <= 8 and 0 <= a <= 8:
                        if p > 1.0: p = p / 100.0
                        if 0 < p <= 0.6:
                            return {"home": h, "away": a, "prob": round(p, 4)}
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

    # === DIAGNOSTIC : on logge la structure du 1er match ===
    first = predictions[0]
    print(f"🔍 DIAGNOSTIC Bzzoiro — clés du 1er match : {list(first.keys())}")
    mk = first.get("markets") or first.get("predictions") or first.get("tips")
    print(f"🔍 Type de 'markets' : {type(mk).__name__}")
    if isinstance(mk, dict):
        print(f"🔍 Sous-clés markets : {list(mk.keys())[:10]}")
        if mk:
            first_sub = list(mk.values())[0] if mk else None
            if isinstance(first_sub, dict):
                print(f"🔍 Exemple sous-dict : {dict(list(first_sub.items())[:5])}")
    elif isinstance(mk, list):
        print(f"🔍 Longueur liste markets : {len(mk)}")
        if mk and isinstance(mk[0], dict):
            print(f"🔍 Clés du 1er élément : {list(mk[0].keys())[:10]}")

    enriched = []
    for pred in predictions:
        try:
            home = pred.get("home_team", "") or pred.get("home", "") or pred.get("team_a", "")
            away = pred.get("away_team", "") or pred.get("away", "") or pred.get("team_b", "")
            if not home or not away:
                continue

            kickoff = pred.get("commence_time", "") or pred.get("kickoff", "") or pred.get("date", "") or pred.get("start_time", "")
            if not kickoff:
                continue

            markets = extract_bzzoiro_native_markets(pred)
            confidence = extract_native_confidence(pred)

            # On garde TOUS les matchs valides, même sans marchés (pour contexte)
            enriched.append({
                "event_id": f"bzz_{pred.get('id', home + away)}",
                "home": home,
                "away": away,
                "league": pred.get("league_name", "") or pred.get("league", "") or pred.get("competition", "") or pred.get("tournament", ""),
                "kickoff": kickoff,
                "bzz_markets": markets,  # peut être {} si parsing échoue
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

    # Tri par confiance
    enriched.sort(key=lambda x: x["bzz_confidence"], reverse=True)

    # Diagnostic final : combien ont des marchés parsés ?
    with_markets = sum(1 for e in enriched if e["bzz_markets"])
    print(f"🔮 Bzzoiro DIRECT : {len(enriched)} retenues ({with_markets} avec marchés parsés)")

    for p in enriched[:10]:
        has_m = "✅" if p["bzz_markets"] else "⚠️"
        m_count = len(p["bzz_markets"]) if p["bzz_markets"] else 0
        print(f"   {has_m} [{p['bzz_confidence']:.0f}%] {p['home']} vs {p['away']} • {m_count} marchés")

    return enriched
