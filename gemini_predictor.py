"""
gemini_predictor.py — Module d'analyse IA sans hallucination pour VOLATILITY INDEX
Fournit :
- predict_match_probas(client, home, away, league, kickoff, context=None)
- explain_selected_pick_with_gemini(client, home, away, league, kickoff, pick_label, proba_pct, odd, context=None)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

FORBIDDEN_OPENINGS = (
    r"^salut[^.!?\n]*[.!?\n]+\s*",
    r"^bonjour[^.!?\n]*[.!?\n]+\s*",
    r"^bonsoir[^.!?\n]*[.!?\n]+\s*",
    r"^coucou[^.!?\n]*[.!?\n]+\s*",
    r"^hello[^.!?\n]*[.!?\n]+\s*",
    r"^chers?\s+parieurs?[^.!?\n]*[.!?\n]+\s*",
)


def _extract_json_block(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _sanitize_analysis_text(
    text: str,
    expected_proba_pct: float | None = None,
    pick_label: str = "",
    neutral_venue: bool = False,
) -> str:
    """Nettoie les tics de langage et vérifie la cohérence stricte du texte généré."""
    if not text:
        return ""
    out = re.sub(r"\s+", " ", text).strip()
    for pat in FORBIDDEN_OPENINGS:
        out = re.sub(pat, "", out, flags=re.IGNORECASE).strip()

    out = out.strip('"\' ')

    if expected_proba_pct is not None and expected_proba_pct > 0:
        target_str = f"{expected_proba_pct:.1f}%"
        def _replace_pct(match: re.Match) -> str:
            val = float(match.group(1).replace(",", "."))
            if abs(val - expected_proba_pct) <= 15.0:
                return target_str
            return match.group(0)

        out = re.sub(r"\b(\d{2}(?:[.,]\d)?)\s*%", _replace_pct, out)

    if neutral_venue:
        out = re.sub(r"\bà domicile\b", "sur terrain neutre", out, flags=re.IGNORECASE)
        out = re.sub(r"\bdevant son public\b", "sans avantage du public", out, flags=re.IGNORECASE)

    sentences = re.split(r"(?<=[.!?])\s+", out)
    out = " ".join(sentences[:3]).strip()
    return out


def _validate_market_consistency(text: str, pick_label: str) -> bool:
    """Vérifie que l'analyse ne contredit pas le type de pari sélectionné."""
    low_pick = (pick_label or "").lower()
    low_txt = (text or "").lower()
    if not low_txt:
        return False

    if "moins de 2.5" in low_pick or "under 2.5" in low_pick:
        goal_terms = ("but", "défens", "fermé", "ferme", "rythme", "xg", "compact", "serré", "moins de 2.5", "under")
        if not any(t in low_txt for t in goal_terms):
            return False
        if "festival offensif" in low_txt or "pluie de buts" in low_txt:
            return False

    if "plus de 2.5" in low_pick or "over 2.5" in low_pick:
        if "match fermé" in low_txt or "peu de buts" in low_txt:
            return False

    return True


def _get_market_rule(pick_label: str) -> str:
    low = (pick_label or "").lower()
    if "moins de 2.5" in low or "under 2.5" in low:
        return (
            "Le pari est MOINS DE 2.5 BUTS. Justifie EXCLUSIVEMENT pourquoi le match sera fermé avec peu de buts "
            "(xG cumulé faible, solidité défensive, prudence tactique). Interdiction totale d'argumenter sur la victoire d'une équipe."
        )
    if "plus de 2.5" in low or "over 2.5" in low or "plus de 1.5" in low:
        return (
            "Le pari est un OVER BUTS. Justifie EXCLUSIVEMENT le potentiel offensif, les xG cumulés élevés et la perméabilité défensive des deux équipes."
        )
    if "btts" in low or "2 équipes marquent" in low:
        return (
            "Le pari est LES 2 ÉQUIPES MARQUENT (BTTS Oui). Explique pourquoi chacune des deux équipes possède les armes pour marquer et encaisser."
        )
    if "double chance" in low or "(1x)" in low or "(x2)" in low:
        return (
            "Le pari est une DOUBLE CHANCE. Explique pourquoi l'équipe couverte dispose d'une assise suffisante pour éviter la défaite."
        )
    return (
        "Le pari est une VICTOIRE SÈCHE (1 ou 2). Justifie la supériorité statistique, les xG et la dynamique de l'équipe sélectionnée."
    )


async def _call_gemini_json(client: httpx.AsyncClient, prompt: str, temperature: float = 0.15) -> Optional[dict[str, Any]]:
    api_key = os.getenv("GEMINI_API_KEY", "") or GEMINI_API_KEY
    if not api_key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    resp = await client.post(url, params={"key": api_key}, json=payload)
    if resp.status_code != 200:
        return None
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    raw_text = "".join(p.get("text", "") for p in parts)
    return _extract_json_block(raw_text)


async def predict_match_probas(
    client: httpx.AsyncClient,
    home: str,
    away: str,
    league: str,
    kickoff: str,
    context: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    ctx = context or {}
    dixon = ctx.get("dixon_coles") or {}
    odds = ctx.get("bookmaker_odds") or {}
    bzz = ctx.get("bzzoiro_probas") or {}
    espn = ctx.get("espn_context") or {}

    neutral = bool(espn.get("neutral_venue", False))
    venue_name = espn.get("venue_name") or "Stade officiel"
    venue_city = espn.get("venue_city") or "Domicile"
    home_rank = espn.get("home_rank", "N/A")
    away_rank = espn.get("away_rank", "N/A")
    injuries = espn.get("injuries") or []

    prompt = f"""Tu es un analyste quantitatif football professionnel. Analyse ce match UNIQUEMENT à partir des données vérifiées ci-dessous.

MATCH : {home} vs {away}
COMPÉTITION : {league}
DATE/HEURE : {kickoff}
STADE VÉRIFIÉ : {venue_name} ({venue_city})
TERRAIN NEUTRE : {"OUI (délocalisé, aucun avantage du public)" if neutral else "NON"}
CLASSEMENT ESPN : {home} = {home_rank} | {away} = {away_rank}
BLESSURES/ABSENTS ESPN : {', '.join(injuries[:8]) if injuries else 'Aucun absent majeur signalé'}
DIXON-COLES / XG : {json.dumps(dixon, ensure_ascii=False)}
COTES BOOKMAKER RÉELLES : {json.dumps(odds, ensure_ascii=False)}
PROBABILITÉS BZZOIRO : {json.dumps(bzz, ensure_ascii=False)}

RÈGLES STRICTES :
1. Tes probabilités (en %, entre 5 et 88) doivent rester cohérentes avec les cotes bookmaker réelles (ne dépasse jamais la probabilité implicite du marché de plus de 8 points).
2. N'invente JAMAIS un classement (ex: "top 8"), une ville ou un nom de stade non écrit ci-dessus.
3. Si STADE VÉRIFIÉ = "Stade officiel (Domicile)", ne cite aucun nom de stade ni de ville.
4. Si TERRAIN NEUTRE = OUI, mentionne explicitement la rencontre sur terrain neutre.
5. Interdiction absolue d'utiliser une salutation ("Salut", "Bonjour", etc.). Rédige directement 2 à 3 phrases analytiques factuelles.

Réponds UNIQUEMENT au format JSON valide suivant :
{{
  "markets": {{
    "victoire_home": 0.0,
    "match_nul": 0.0,
    "victoire_away": 0.0,
    "over_1.5": 0.0,
    "over_2.5": 0.0,
    "under_2.5": 0.0,
    "btts_yes": 0.0,
    "btts_no": 0.0,
    "double_chance_1X": 0.0,
    "double_chance_X2": 0.0
  }},
  "analysis": "Analyse factuelle directe en 2-3 phrases sans salutation."
}}"""

    res = await _call_gemini_json(client, prompt, temperature=0.1)
    if not res or not isinstance(res, dict):
        return None
    markets = res.get("markets") if isinstance(res.get("markets"), dict) else {}
    analysis = _sanitize_analysis_text(
        str(res.get("analysis") or ""),
        expected_proba_pct=None,
        neutral_venue=neutral,
    )
    return {"markets": markets, "analysis": analysis}


async def explain_selected_pick_with_gemini(
    client: httpx.AsyncClient,
    home: str,
    away: str,
    league: str,
    kickoff: str,
    pick_label: str,
    proba_pct: float,
    odd: float,
    context: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    ctx = context or {}
    dixon = ctx.get("dixon_coles") or {}
    odds = ctx.get("bookmaker_odds") or {}
    bzz = ctx.get("bzzoiro_probas") or {}
    espn = ctx.get("espn_context") or {}

    neutral = bool(espn.get("neutral_venue", False))
    venue_name = espn.get("venue_name") or "Stade officiel"
    venue_city = espn.get("venue_city") or "Domicile"
    venue_full = f"{venue_name} ({venue_city})"
    home_rank = espn.get("home_rank", "N/A")
    away_rank = espn.get("away_rank", "N/A")
    injuries = espn.get("injuries") or []

    market_rule = _get_market_rule(pick_label)

    prompt = f"""Tu es l'éditeur quantitatif senior de VOLATILITY INDEX. Rédige la justification finale d'un pari déjà sélectionné.

AFFICHE : {home} vs {away} ({league})
PARI RETENU : {pick_label}
PROBABILITÉ CALIBRÉE : {proba_pct:.1f}%
COTE BOOKMAKER : {odd:.2f}
STADE : {venue_full}
TERRAIN NEUTRE : {"True" if neutral else "False"}
CLASSEMENTS VÉRIFIÉS : {home} ({home_rank}) vs {away} ({away_rank})
ABSENTS/BLESSÉS : {', '.join(injuries[:8]) if injuries else 'Aucun absent majeur signalé'}
DONNÉES STATISTIQUES (xG / Dixon-Coles) : {json.dumps(dixon, ensure_ascii=False)}
COTES DU MARCHÉ : {json.dumps(odds, ensure_ascii=False)}
MODÈLE BZZOIRO : {json.dumps(bzz, ensure_ascii=False)}

RÈGLES ABSOLUES :
1. Utilise UNIQUEMENT les faits ci-dessus. N'invente jamais un classement (ex: "top 8"), une ville ou un nom de stade.
2. Si Stade = "Stade officiel (Domicile)", ne cite aucun nom de ville ni de stade dans ton analyse.
3. Si tu mentionnes la probabilité, cite uniquement {proba_pct:.1f}% (interdiction d'écrire un autre pourcentage).
4. Interdiction d'utiliser des salutations ("Salut", "Bonjour", etc.). Rédige directement l'analyse en 3 phrases maximum, ton analytique et professionnel.
5. {market_rule}
6. Si Terrain neutre = True, précise explicitement que la rencontre est délocalisée à {venue_full} sans avantage du public.
7. Mets "is_safe": false UNIQUEMENT si les données montrent une contradiction majeure (ex: Under 2.5 avec xG cumulé > 2.60 ou équipe décimée par les blessures).

Réponds UNIQUEMENT au format JSON valide :
{{
  "is_safe": true,
  "analysis": "Texte analytique direct en 3 phrases maximum."
}}"""

    res = await _call_gemini_json(client, prompt, temperature=0.1)
    if not res or not isinstance(res, dict):
        return None

    is_safe = bool(res.get("is_safe", True))
    cleaned_txt = _sanitize_analysis_text(
        str(res.get("analysis") or ""),
        expected_proba_pct=proba_pct,
        pick_label=pick_label,
        neutral_venue=neutral,
    )
    if not _validate_market_consistency(cleaned_txt, pick_label):
        return {"is_safe": False, "analysis": ""}

    return {"is_safe": is_safe, "analysis": cleaned_txt}
