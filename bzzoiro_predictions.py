"""
Récupération des prédictions Bzzoiro avec matching ultra-robuste.
Gère les variations de noms : Stade Rennais vs Rennes, Olympique de Marseille vs Marseille, etc.
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
# MOTS À IGNORER (préfixes/suffixes communs)
# =========================================================
IGNORE_WORDS = {
    # Types de clubs
    "fc", "cf", "sc", "ac", "afc", "as", "ss", "ssc", "rc", "cd", "fk",
    "sk", "jk", "sd", "ud", "ca", "cs", "us", "sv", "bv", "bsc", "fsv",
    "vfb", "vfl", "tsg", "spvgg", "kfc", "krc", "kv", "kaa", "vv", "cfc",
    "if", "bk", "ff", "ik", "fk", "fc",
    # Mots descriptifs à ignorer
    "stade", "olympique", "sporting", "sport", "club", "de", "of",
    "the", "and", "of", "and", "&",
    "atletico", "atlético", "athletic", "atletico",
    "real", "royal", "racing",
    "united", "utd", "city", "town", "albion", "rovers", "wanderers",
}

# =========================================================
# DICTIONNAIRE D'ALIAS API → BZZOIRO
# =========================================================
TEAM_ALIASES = {
    # France
    "rennes": "stade rennais",
    "stade rennais": "stade rennais",
    "marseille": "olympique de marseille",
    "olympique de marseille": "olympique de marseille",
    "olympique marseille": "olympique de marseille",
    "lyon": "olympique lyonnais",
    "lille": "losc lille",
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    
    # Italie
    "inter": "inter milan",
    "milan": "ac milan",
    "roma": "as roma",
    "lazio": "ss lazio",
    "napoli": "ssc napoli",
    "juventus": "juventus",
    "atalanta": "atalanta",
    "fiorentina": "fiorentina",
    
    # Angleterre
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
    
    # Allemagne
    "dortmund": "borussia dortmund",
    "bayern": "bayern munich",
    "bayern munchen": "bayern munich",
    "leverkusen": "bayer leverkusen",
    "frankfurt": "eintracht frankfurt",
    "hoffenheim": "tsg hoffenheim",
    "freiburg": "sc freiburg",
    
    # Espagne
    "atletico": "atletico madrid",
    "athletic": "athletic bilbao",
    "betis": "real betis",
    "sociedad": "real sociedad",
    "barca": "fc barcelona",
    
    # Portugal
    "porto": "fc porto",
    "benfica": "sl benfica",
    "braga": "sporting braga",
    "sporting": "sporting cp",
    
    # Danemark
    "copenhagen": "fc kobenhavn",
    "fc copenhagen": "fc kobenhavn",
    "fc kobenhavn": "fc kobenhavn",
    "kobenhavn": "fc kobenhavn",
    "nordsjaelland": "fc nordsjaelland",
    "nordsjælland": "fc nordsjaelland",
    "fc nordsjaelland": "fc nordsjaelland",
    "fc nordsjælland": "fc nordsjaelland",
    
    # Écosse
    "hearts": "heart of midlothian",
    "heart of midlothian": "heart of midlothian",
    
    # Turquie
    "besiktas": "besiktas jk",
    "beşiktaş": "besiktas jk",
    "besiktas jk": "besiktas jk",
    "fenerbahce": "fenerbahce sk",
    "galatasaray": "galatasaray sk",
    
    # Amérique du Sud
    "huracan": "huracan",
    "atletico huracan": "huracan",
    "atlético huracán": "huracan",
    "gimnasia": "gimnasia y esgrima",
    "gimnasia la plata": "gimnasia y esgrima",
    "gimnasia y esgrima": "gimnasia y esgrima",
    
    # Belgique
    "sint truiden": "sint-truidense vv",
    "sint-truidense vv": "sint-truidense vv",
    "stvv": "sint-truidense vv",
    
    # Grèce
    "olympiakos": "olympiacos fc",
    "olympiacos": "olympiacos fc",
    "olympiacos fc": "olympiacos fc",
    "olympiakos piraeus": "olympiacos fc",
}


# =========================================================
# NORMALISATION ULTRA-ROBUSTE
# =========================================================
def _norm(s: str) -> str:
    """Normalisation agressive des noms d'équipes."""
    if not s:
        return ""
    s = str(s).lower().strip()
    # Accents + caractères spéciaux
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    # Remplacer & par and
    s = s.replace("&", " and ")
    # Ponctuation
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    # Découper et filtrer
    words = s.split()
    filtered = []
    for w in words:
        if w in IGNORE_WORDS:
            continue
        if len(w) <= 1:
            continue
        filtered.append(w)
    if not filtered:
        return " ".join(words).strip()
    return " ".join(filtered).strip()


def _similarity_score(name_a: str, name_b: str) -> float:
    """Score de similarité 0.0 à 1.0."""
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


# =========================================================
# MATCHING ULTRA-ROBUSTE
# =========================================================
def find_best_event_match(match: dict, events: list[dict]) -> Optional[dict]:
    """Trouve le meilleur event Bzzoiro pour un match via scoring."""
    mh_raw = match.get("home", "")
    ma_raw = match.get("away", "")
    if not mh_raw or not ma_raw:
        return None

    # Alias
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

        # Alias sur Bzzoiro aussi
        h = TEAM_ALIASES.get(h.lower().strip(), h)
        a = TEAM_ALIASES.get(a.lower().strip(), a)

        score_h = _similarity_score(mh, h)
        score_a = _similarity_score(ma, a)

        # Score minimum des deux (pour éviter les faux positifs)
        combined = min(score_h, score_a)
        # Bonus si les deux sont forts
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


def _headers() -> dict:
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


def extract_bzzoiro_markets(pred: dict) -> dict:
    """Extrait les probas Bzzoiro au format Gemini (%)."""
    if not pred:
        return {}

    result = {}
    p = pred.get("predictions", pred) or {}

    key_map = {
        "home_win": "victoire_home", "draw": "match_nul", "away_win": "victoire_away",
        "over_2.5": "over_2.5", "over_2_5": "over_2.5",
        "under_2.5": "under_2.5", "under_2_5": "under_2.5",
        "btts_yes": "btts_yes", "btts_no": "btts_no",
        "home_win_probability": "victoire_home", "draw_probability": "match_nul",
        "away_win_probability": "victoire_away",
        "over_2_5_probability": "over_2.5", "under_2_5_probability": "under_2.5",
        "btts_probability": "btts_yes",
        "prob_home": "victoire_home", "prob_draw": "match_nul", "prob_away": "victoire_away",
        "prob_over_25": "over_2.5", "prob_under_25": "under_2.5", "prob_btts": "btts_yes",
        "p_home": "victoire_home", "p_draw": "match_nul", "p_away": "victoire_away",
    }

    sources = [p, p.get("markets", {}) or {}]
    for source in sources:
        for bzz_key, gemini_key in key_map.items():
            if gemini_key in result:
                continue
            v = source.get(bzz_key)
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
        max_prob = max(
            markets.get("victoire_home", 0),
            markets.get("victoire_away", 0),
            markets.get("match_nul", 0),
        )
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
    return events


async def get_bzzoiro_best_matches(matches: list[dict], min_confidence: float = 60.0) -> list[dict]:
    """Filtre les matchs via Bzzoiro avec matching ultra-robuste."""
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
