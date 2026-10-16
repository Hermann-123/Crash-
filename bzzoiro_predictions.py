"""
Récupération des prédictions Bzzoiro avec extraction CORRIGÉE.
Inclut maintenant : 1X2, O/U, BTTS, Corners.
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


# =========================================================
# MOTS À IGNORER
# =========================================================
IGNORE_WORDS = {
    "fc", "cf", "sc", "ac", "afc", "as", "ss", "ssc", "rc", "cd", "fk",
    "sk", "jk", "sd", "ud", "ca", "cs", "us", "sv", "bv", "bsc", "fsv",
    "vfb", "vfl", "tsg", "spvgg", "kfc", "krc", "kv", "kaa", "vv", "cfc",
    "if", "bk", "ff", "ik",
    "stade", "olympique", "sporting", "sport", "club", "de", "of",
    "the", "and", "of",
    "united", "utd", "city", "town", "albion", "rovers", "wanderers",
}


# =========================================================
# DICTIONNAIRE D'ALIAS API → BZZOIRO
# =========================================================
TEAM_ALIASES = {
    "rennes": "stade rennais",
    "stade rennais": "stade rennais",
    "marseille": "olympique de marseille",
    "olympique de marseille": "olympique de marseille",
    "olympique marseille": "olympique de marseille",
    "lyon": "olympique lyonnais",
    "lille": "losc lille",
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    "inter": "inter milan",
    "milan": "ac milan",
    "roma": "as roma",
    "lazio": "ss lazio",
    "napoli": "ssc napoli",
    "juventus": "juventus",
    "atalanta": "atalanta",
    "fiorentina": "fiorentina",
    "brighton": "brighton and hove albion",
    "brighton and hove albion": "brighton and hove albion",
    "brighton & hove albion": "brighton and hove albion",
    "man city": "manchester city",
    "man united": "manchester united",
    "man utd": "manchester united",
    "spurs": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "west ham": "west ham united",
    "newcastle": "newcastle united",
    "dortmund": "borussia dortmund",
    "bayern": "bayern munich",
    "bayern munchen": "bayern munich",
    "leverkusen": "bayer leverkusen",
    "frankfurt": "eintracht frankfurt",
    "hoffenheim": "tsg hoffenheim",
    "freiburg": "sc freiburg",
    "atletico": "atletico madrid",
    "athletic": "athletic bilbao",
    "betis": "real betis",
    "sociedad": "real sociedad",
    "barca": "fc barcelona",
    "porto": "fc porto",
    "benfica": "sl benfica",
    "braga": "sporting braga",
    "sporting": "sporting cp",
    "copenhagen": "fc kobenhavn",
    "fc copenhagen": "fc kobenhavn",
    "fc kobenhavn": "fc kobenhavn",
    "kobenhavn": "fc kobenhavn",
    "nordsjaelland": "fc nordsjaelland",
    "nordsjælland": "fc nordsjaelland",
    "fc nordsjaelland": "fc nordsjaelland",
    "fc nordsjælland": "fc nordsjaelland",
    "hearts": "heart of midlothian",
    "heart of midlothian": "heart of midlothian",
    "besiktas": "besiktas jk",
    "beşiktaş": "besiktas jk",
    "besiktas jk": "besiktas jk",
    "fenerbahce": "fenerbahce sk",
    "galatasaray": "galatasaray sk",
    "huracan": "huracan",
    "atletico huracan": "huracan",
    "atlético huracán": "huracan",
    "gimnasia": "gimnasia y esgrima",
    "gimnasia la plata": "gimnasia y esgrima",
    "gimnasia y esgrima": "gimnasia y esgrima",
    "sint truiden": "sint-truidense vv",
    "sint-truidense vv": "sint-truidense vv",
    "stvv": "sint-truidense vv",
    "olympiakos": "olympiacos fc",
    "olympiacos": "olympiacos fc",
    "olympiacos fc": "olympiacos fc",
    "olympiakos piraeus": "olympiacos fc",
    "salzburg": "red bull salzburg",
    "rb salzburg": "red bull salzburg",
    "red bull salzburg": "red bull salzburg",
}


# =========================================================
# NORMALISATION
# =========================================================
def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    words = s.split()
    filtered = [w for w in words if w not in IGNORE_WORDS and len(w) > 1]
    if not filtered:
        return " ".join(words).strip()
    return " ".join(filtered).strip()


def _similarity_score(name_a: str, name_b: str) -> float:
    a = _norm(name_a)
    b = _norm(name_b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        ratio = shorter / longer if longer > 0 else 0
        return 0.70 + 0.25 * ratio
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(inter) / len(union) if union else 0.0
    first_a = a.split()[0] if a.split() else ""
    first_b = b.split()[0] if b.split() else ""
    first_match = 0.20 if (first_a and first_a == first_b) else 0.0
    return min(1.0, jaccard * 0.75 + first_match)


def find_best_event_match(match: dict, events: list[dict]) -> Optional[dict]:
    mh_raw = match.get("home", "")
    ma_raw = match.get("away", "")
    if not mh_raw or not ma_raw:
        return None

    mh = _norm(TEAM_ALIASES.get(mh_raw.lower().strip(), mh_raw))
    ma = _norm(TEAM_ALIASES.get(ma_raw.lower().strip(), ma_raw))

    best_event = None
    best_score = 0.0
    THRESHOLD = 0.55

    for ev in events:
        h = ev.get("home_team", "") or ev.get("home", "") or ev.get("team_home", "")
        a = ev.get("away_team", "") or ev.get("away", "") or ev.get("team_away", "")
        if not h or not a:
            continue

        h = TEAM_ALIASES.get(h.lower().strip(), h)
        a = TEAM_ALIASES.get(a.lower().strip(), a)

        score_h = _similarity_score(mh, h)
        score_a = _similarity_score(ma, a)
        combined = min(score_h, score_a)
        if score_h > 0.75 and score_a > 0.75:
            combined += 0.15

        if combined > best_score:
            best_score = combined
            best_event = ev

    if best_event and best_score >= THRESHOLD:
        return best_event
    return None


# =========================================================
# API BZZOIRO
# =========================================================
def _headers() -> dict:
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


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


# =========================================================
# EXTRACTION (avec corners)
# =========================================================
def extract_bzzoiro_markets(pred: dict) -> dict:
    """Extraction avec corners inclus."""
    if not pred:
        return {}

    result = {}
    markets = pred.get("markets", {}) or {}
    if not isinstance(markets, dict) or not markets:
        return {}

    # === MATCH RESULT (1X2) ===
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

    # === OVER / UNDER ===
    ou = markets.get("over_under", {}) or {}
    if isinstance(ou, dict):
        try:
            over_25 = float(ou.get("prob_over_25", 0))
            if 0 < over_25 < 100:
                result["over_2.5"] = round(over_25, 1)
                result["under_2.5"] = round(100.0 - over_25, 1)
            over_35 = float(ou.get("prob_over_35", 0))
            if 0 < over_35 < 100:
                result["over_3.5"] = round(over_35, 1)
        except (ValueError, TypeError):
            pass

    # === BTTS ===
    btts = markets.get("btts", {}) or {}
    if isinstance(btts, dict):
        try:
            yes = float(btts.get("prob_yes", 0))
            if 0 < yes < 100:
                result["btts_yes"] = round(yes, 1)
                result["btts_no"] = round(100.0 - yes, 1)
        except (ValueError, TypeError):
            pass

    # === CORNERS ===
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

    return result


# =========================================================
# CONFIANCE
# =========================================================
def compute_bzzoiro_confidence(pred: dict, markets: dict) -> float:
    """Confiance = clarté du favori dans le 1X2 + richesse des données."""
    if not markets:
        return 0.0

    confidence = 0.0

    if "victoire_home" in markets and "victoire_away" in markets:
        h = markets.get("victoire_home", 0)
        d = markets.get("match_nul", 0)
        a = markets.get("victoire_away", 0)
        max_prob = max(h, d, a)

        if max_prob >= 75:
            confidence += 75
        elif max_prob >= 65:
            confidence += 55
        elif max_prob >= 55:
            confidence += 40
        elif max_prob >= 45:
            confidence += 25
        else:
            confidence += 10

    if "over_8.5_corners" in markets:
        confidence += 5

    confidence += min(len(markets) * 2, 15)

    return max(0.0, min(100.0, confidence))


# =========================================================
# FETCH EVENTS
# =========================================================
async def fetch_bzzoiro_events_for_date(client: httpx.AsyncClient, date_iso: str) -> list[dict]:
    data = await _get(client, "/events/", {
        "date_from": date_iso,
        "date_to": date_iso,
        "limit": "300",
    })
    events = data.get("results", []) or data.get("data", []) or []
    print(f"🔮 Bzzoiro events {date_iso} : {len(events)} bruts")
    return events


# =========================================================
# FONCTION PRINCIPALE
# =========================================================
async def get_bzzoiro_best_matches(matches: list[dict], min_confidence: float = 60.0) -> list[dict]:
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante — Bzzoiro désactivé")
        return []

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

            matched_count = 0
            for m in matches:
                matched_event = find_best_event_match(m, events)
                if not matched_event:
                    continue

                matched_count += 1
                event_id = matched_event.get("id")
                if not event_id:
                    continue

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

                await asyncio.sleep(0.2)

            print(f"🔮 Bzzoiro : {matched_count}/{len(matches)} matchs mappés")

    enriched_matches.sort(key=lambda x: x["bzz_confidence"], reverse=True)
    print(f"🔮 Bzzoiro : {len(enriched_matches)} matchs retenus (confiance ≥ {min_confidence})")
    for m in enriched_matches[:10]:
        print(f"   🎯 [{m['bzz_confidence']:.0f}] {m['home']} vs {m['away']}")

    return enriched_matches
