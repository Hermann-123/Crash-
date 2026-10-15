"""
Récupération des prédictions Bzzoiro via le bon endpoint.
Endpoint correct : /events/{id}/prediction/ (PAS /predictions/)
AVEC DEBUG pour voir les events Bzzoiro bruts.
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
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=15.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro {path} -> {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro {path} crash: {e}")
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

    # Récupérer aussi les marchés dans un sous-dict "markets"
    markets_sub = p.get("markets", {}) or {}
    for bzz_key, gemini_key in key_map.items():
        v = markets_sub.get(bzz_key)
        if v is None:
            continue
        if gemini_key in result:
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
    """Calcule un score de confiance Bzzoiro (0-100)."""
    confidence = 0.0

    raw_conf = pred.get("confidence", 0)
    try:
        raw_conf = float(raw_conf)
        if 0 <= raw_conf <= 1:
            raw_conf = raw_conf * 100
        confidence += min(raw_conf, 100) * 0.4
    except (ValueError, TypeError):
        pass

    if "victoire_home" in markets and "victoire_away" in markets:
        h = markets.get("victoire_home", 0)
        a = markets.get("victoire_away", 0)
        d = markets.get("match_nul", 0)
        max_prob = max(h, a, d)
        confidence += (max_prob - 33.3) * 0.6

    confidence += min(len(markets) * 3, 20)

    return max(0.0, min(100.0, confidence))


async def fetch_bzzoiro_events_for_date(client: httpx.AsyncClient, date_iso: str) -> list[dict]:
    """Récupère tous les événements Bzzoiro pour une date."""
    data = await _get(client, "/events/", {
        "date_from": date_iso,
        "date_to": date_iso,
        "limit": "300",
    })
    events = data.get("results", []) or data.get("data", []) or []
    print(f"🔮 Bzzoiro events {date_iso} : {len(events)} bruts")

    # === DEBUG : Afficher les 15 premiers events ===
    for i, ev in enumerate(events[:15]):
        h = ev.get("home_team", "") or ev.get("home", "") or ev.get("team_home", "")
        a = ev.get("away_team", "") or ev.get("away", "") or ev.get("team_away", "")
        league = ev.get("league", "") or ev.get("competition", "") or ""
        if isinstance(league, dict):
            league = league.get("name", "")
        print(f"   📋 BZZ #{i+1}: '{h}' vs '{a}' | {league}")
    # ================================================

    return events


async def get_bzzoiro_best_matches(matches: list[dict], min_confidence: float = 60.0) -> list[dict]:
    """
    Récupère les prédictions Bzzoiro match par match via /events/{id}/prediction/.
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

    enriched_matches = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for date_iso in dates_needed:
            events = await fetch_bzzoiro_events_for_date(client, date_iso)

            if not events:
                print(f"⚠️ Aucun événement Bzzoiro pour {date_iso}")
                continue

            # Pour chaque match, chercher l'event Bzzoiro correspondant
            for m in matches:
                mh = _norm(m.get("home", ""))
                ma = _norm(m.get("away", ""))
                if not mh or not ma:
                    continue

                matched_event = None
                for ev in events:
                    h = ev.get("home_team", "") or ev.get("home", "") or ev.get("team_home", "")
                    a = ev.get("away_team", "") or ev.get("away", "") or ev.get("team_away", "")
                    if not h or not a:
                        continue
                    hn = _norm(h)
                    an = _norm(a)
                    if (mh in hn or hn in mh) and (ma in an or an in ma):
                        matched_event = ev
                        break

                if not matched_event:
                    print(f"   ❌ Pas de match BZZ pour: '{m.get('home','')}' vs '{m.get('away','')}'")
                    continue

                event_id = matched_event.get("id")
                if not event_id:
                    continue

                # Récupérer la prédiction
                pred = await _get(client, f"/events/{event_id}/prediction/")
                if not pred:
                    continue

                markets = extract_bzzoiro_markets(pred)
                if not markets:
                    continue

                confidence = compute_bzzoiro_confidence(pred, markets)

                if confidence >= min_confidence:
                    m_copy = dict(m)
                    m_copy["bzz_markets"] = markets
                    m_copy["bzz_confidence"] = round(confidence, 1)
                    m_copy["bzz_raw"] = pred
                    enriched_matches.append(m_copy)

                await asyncio.sleep(0.3)

    # Trier par confiance décroissante
    enriched_matches.sort(key=lambda x: x["bzz_confidence"], reverse=True)
    print(f"🔮 Bzzoiro : {len(enriched_matches)} matchs retenus (confiance ≥ {min_confidence})")
    for m in enriched_matches[:10]:
        print(f"   🎯 [{m['bzz_confidence']:.0f}] {m['home']} vs {m['away']}")

    return enriched_matches
