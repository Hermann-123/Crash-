"""
Récupération des prédictions Bzzoiro (fusion Dixon-Coles + Negative-Binomial + Marché).
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
    }

    for bzz_key, gemini_key in key_map.items():
        v = p.get(bzz_key)
        if v is None:
            continue
        try:
            fv = float(v)
            if 0 <= fv <= 1:
                fv = fv * 100
            result[gemini_key] = round(fv, 1)
        except (ValueError, TypeError):
            pass

    return result


async def get_bzzoiro_predictions_batch(matches: list[dict]) -> dict:
    """
    Récupère les prédictions Bzzoiro pour une liste de matchs.
    Retourne {event_id: {"markets": {...}, "confidence": float, "raw": {...}}}
    """
    if not BZZOIRO_API_KEY:
        return {}

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
        return {}

    results = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for date_iso in dates_needed:
            data = await _get(client, "/predictions/", {
                "date_from": date_iso,
                "date_to": date_iso,
                "limit": "500",
            })
            predictions = data.get("results", []) or data.get("data", []) or []
            print(f"🔮 Bzzoiro predictions {date_iso} : {len(predictions)} brutes")

            for pred in predictions:
                h = pred.get("home_team", "") or pred.get("home", "")
                a = pred.get("away_team", "") or pred.get("away", "")
                if not h or not a:
                    continue
                hn = _norm(h)
                an = _norm(a)

                for m in matches:
                    mh = _norm(m.get("home", ""))
                    ma = _norm(m.get("away", ""))
                    if (mh in hn or hn in mh) and (ma in an or an in ma):
                        event_id = m.get("event_id", "")
                        if event_id and event_id not in results:
                            markets = extract_bzzoiro_markets(pred)
                            if markets:
                                results[event_id] = {
                                    "markets": markets,
                                    "confidence": pred.get("confidence", 0),
                                    "raw": pred,
                                }
                        break

    print(f"🔮 Bzzoiro predictions mappées : {len(results)}/{len(matches)}")
    return results
