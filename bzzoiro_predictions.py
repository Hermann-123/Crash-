"""
Récupération des prédictions Bzzoiro avec matching ultra-robuste.
AVEC DEBUG complet pour diagnostiquer la structure des données.
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


def extract_bzzoiro_markets(pred: dict) -> dict:
    """
    Extraction flexible des marchés Bzzoiro.
    Les données sont dans pred['markets'] avec des clés variables.
    """
    if not pred:
        return {}

    result = {}

    # Récupérer le dict markets (structure Bzzoiro)
    markets = pred.get("markets", {}) or {}
    if not isinstance(markets, dict) or not markets:
        markets = pred.get("predictions", pred) or {}

    # === DEBUG : afficher la structure complète ===
    print(f"      MARKETS KEYS: {list(markets.keys())}")
    print(f"      MARKETS CONTENU: {str(markets)[:800]}")
    # =============================================

    # Mapping flexible
    patterns = {
        "victoire_home": ["1", "home", "home_win", "p1", "1X2_1", "victory_home", "home_win_probability"],
        "match_nul": ["X", "x", "draw", "nul", "1X2_X", "draw_probability"],
        "victoire_away": ["2", "away", "away_win", "p2", "1X2_2", "victory_away", "away_win_probability"],
        "over_2.5": ["over_2.5", "over_25", "over25", "o2.5", "plus_2.5", "over", "over_2_5"],
        "under_2.5": ["under_2.5", "under_25", "under25", "u2.5", "moins_2.5", "under", "under_2_5"],
        "btts_yes": ["btts_yes", "btts", "btts_y", "yes", "BTTS", "btts_probability"],
        "btts_no": ["btts_no", "btts_n", "no", "BTTS_NO"],
    }

    def extract_numeric(obj, depth=0):
        if depth > 3:
            return None
        if isinstance(obj, (int, float)):
            try:
                fv = float(obj)
                if 0 < fv < 100:
                    return fv
                elif 0 < fv <= 1:
                    return fv * 100
            except (ValueError, TypeError):
                return None
        if isinstance(obj, dict):
            for k, v in obj.items():
                r = extract_numeric(v, depth + 1)
                if r is not None:
                    return r
        if isinstance(obj, list):
            for item in obj:
                r = extract_numeric(item, depth + 1)
                if r is not None:
                    return r
        return None

    def find_value(mkts, candidates):
        for key in candidates:
            if key in mkts:
                v = mkts[key]
                r = extract_numeric(v)
                if r is not None:
                    return r
        # Match partiel (insensible à la casse)
        for mkts_key, mkts_val in mkts.items():
            for cand in candidates:
                if cand.lower() in str(mkts_key).lower() or str(mkts_key).lower() in cand.lower():
                    r = extract_numeric(mkts_val)
                    if r is not None:
                        return r
        return None

    for gemini_key, candidates in patterns.items():
        v = find_value(markets, candidates)
        if v is not None:
            result[gemini_key] = round(v, 1)

    return result


def compute_bzzoiro_confidence(pred: dict, markets: dict) -> float:
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
        max_prob = max(
            markets.get("victoire_home", 0),
            markets.get("victoire_away", 0),
            markets.get("match_nul", 0),
        )
        confidence += (max_prob - 33.3) * 0.6

    confidence += min(len(markets) * 3, 20)
    return max(0.0, min(100.0, confidence))


async def fetch_bzzoiro_events_for_date(client: httpx.AsyncClient, date_iso: str) -> list[dict]:
    data = await _get(client, "/events/", {
        "date_from": date_iso,
        "date_to": date_iso,
        "limit": "300",
    })
    events = data.get("results", []) or data.get("data", []) or []
    print(f"🔮 Bzzoiro events {date_iso} : {len(events)} bruts")
    return events


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
            debug_count = 0
            for m in matches:
                matched_event = find_best_event_match(m, events)
                if not matched_event:
                    continue

                matched_count += 1
                event_id = matched_event.get("id")
                if not event_id:
                    continue

                pred = await _get(client, f"/events/{event_id}/prediction/")

                # Debug pour les 3 premiers
                if debug_count < 3:
                    print(f"   🔍 DEBUG pred pour {m.get('home','')} vs {m.get('away','')}:")
                    print(f"      Type: {type(pred).__name__}")
                    if isinstance(pred, dict):
                        print(f"      Clés top: {list(pred.keys())[:15]}")
                    debug_count += 1

                if not pred:
                    continue

                markets = extract_bzzoiro_markets(pred)
                if not markets:
                    continue

                confidence = compute_bzzoiro_confidence(pred, markets)

                if debug_count <= 3:
                    print(f"      Marchés extraits: {markets}")
                    print(f"      Confiance: {confidence:.1f}")

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
