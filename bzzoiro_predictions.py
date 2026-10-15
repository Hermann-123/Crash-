"""
Récupération et filtrage des prédictions Bzzoiro.
Bzzoiro = filtre principal qui sélectionne les meilleurs matchs.
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from typing import Optional

import httpx

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"

_PRED_CACHE: dict = {}


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
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=15.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro pred {path} -> {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro pred {path} crash: {e}")
        return {}


def extract_bzzoiro_markets(pred: dict) -> dict:
    """Extrait les probas Bzzoiro au format Gemini (%)."""
    if not pred:
        return {}

    result = {}
    p = pred.get("predictions", pred) or {}

    key_map = {
        "home_win": "victoire_home",
        "draw": "match_nul",
        "away_win": "victoire_away",
        "over_2.5": "over_2.5",
        "over_2_5": "over_2.5",
        "under_2.5": "under_2.5",
        "under_2_5": "under_2.5",
        "btts_yes": "btts_yes",
        "btts_no": "btts_no",
        "home_win_probability": "victoire_home",
        "draw_probability": "match_nul",
        "away_win_probability": "victoire_away",
        "over_2_5_probability": "over_2.5",
        "under_2_5_probability": "under_2.5",
        "btts_probability": "btts_yes",
        "prob_home": "victoire_home",
        "prob_draw": "match_nul",
        "prob_away": "victoire_away",
        "prob_over_25": "over_2.5",
        "prob_under_25": "under_2.5",
        "prob_btts": "btts_yes",
        "p_home": "victoire_home",
        "p_draw": "match_nul",
        "p_away": "victoire_away",
    }

    for bzz_key, gemini_key in key_map.items():
        v = p.get(bzz_key)
        if v is None:
            continue
        try:
            fv = float(v)
            if 0 <= fv <= 1:
                fv = fv * 100
            if 0 < fv < 100:
                result[gemini_key] = round(fv, 1)
        except (ValueError, TypeError):
            pass

    return result


def compute_bzzoiro_confidence(pred: dict, markets: dict) -> float:
    """
    Calcule un score de confiance Bzzoiro (0-100).
    Basé sur :
    - Confidence explicite du modèle
    - Écart max entre les probas (favori clair = confiance)
    - Nombre de marchés disponibles
    """
    confidence = 0.0

    # 1. Confidence explicite
    raw_conf = pred.get("confidence", 0)
    try:
        raw_conf = float(raw_conf)
        if 0 <= raw_conf <= 1:
            raw_conf = raw_conf * 100
        confidence += min(raw_conf, 100) * 0.4
    except (ValueError, TypeError):
        pass

    # 2. Écart entre les probas (favori clair = confiance élevée)
    if "victoire_home" in markets and "victoire_away" in markets:
        h = markets.get("victoire_home", 0)
        a = markets.get("victoire_away", 0)
        d = markets.get("match_nul", 0)
        max_prob = max(h, a, d)
        # Plus le favori est marqué, plus la confiance est haute
        confidence += (max_prob - 33.3) * 0.6  # 33.3% = neutre

    # 3. Bonus nombre de marchés
    confidence += min(len(markets) * 3, 20)

    return max(0.0, min(100.0, confidence))


async def get_bzzoiro_best_matches(matches: list[dict], min_confidence: float = 60.0) -> list[dict]:
    """
    Récupère les prédictions Bzzoiro ET filtre les matchs où Bzzoiro a confiance.

    Retourne une liste de matchs enrichis avec :
    - bzz_markets : dict des probas Bzzoiro
    - bzz_confidence : score de confiance
    - bzz_raw : données brutes
    """
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante — Bzzoiro désactivé")
        return []

    # Grouper les matchs par date
    dates_needed = set()
    for m in matches:
        k = m.get("kickoff", "")
        try:
            d = datetime.fromisoformat(k.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            dates_needed.add(d)
        except Exception:
            continue

    if not dates_needed:
        return []

    print(f"🔮 Bzzoiro : scan sur {len(dates_needed)} date(s)...")
    all_predictions = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for date_iso in dates_needed:
            data = await _get(client, "/predictions/", {
                "date_from": date_iso,
                "date_to": date_iso,
                "limit": "500",
            })
            predictions = data.get("results", []) or data.get("data", []) or []
            print(f"🔮 Bzzoiro {date_iso} : {len(predictions)} prédictions brutes")
            all_predictions.extend(predictions)

    if not all_predictions:
        print("⚠️ Aucune prédiction Bzzoiro trouvée")
        return []

    # Mapper chaque match avec sa prédiction Bzzoiro
    enriched_matches = []
    for m in matches:
        mh = _norm(m.get("home", ""))
        ma = _norm(m.get("away", ""))
        if not mh or not ma:
            continue

        best_pred = None
        for pred in all_predictions:
            h = pred.get("home_team", "") or pred.get("home", "")
            a = pred.get("away_team", "") or pred.get("away", "")
            if not h or not a:
                continue
            hn = _norm(h)
            an = _norm(a)
            if (mh in hn or hn in mh) and (ma in an or an in ma):
                best_pred = pred
                break

        if not best_pred:
            continue

        markets = extract_bzzoiro_markets(best_pred)
        if not markets:
            continue

        confidence = compute_bzzoiro_confidence(best_pred, markets)

        if confidence >= min_confidence:
            m_copy = dict(m)
            m_copy["bzz_markets"] = markets
            m_copy["bzz_confidence"] = round(confidence, 1)
            m_copy["bzz_raw"] = best_pred
            enriched_matches.append(m_copy)

    # Trier par confiance décroissante
    enriched_matches.sort(key=lambda x: x["bzz_confidence"], reverse=True)
    print(f"🔮 Bzzoiro : {len(enriched_matches)} matchs retenus (confiance ≥ {min_confidence})")
    for m in enriched_matches[:10]:
        print(f"   🎯 [{m['bzz_confidence']:.0f}] {m['home']} vs {m['away']} • {m.get('league','')[:30]}")

    return enriched_matches
