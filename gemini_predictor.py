"""
Prédicteur Gemini avec Search grounding.
Génère les probas de tous les marchés d'un match.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_SEARCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"

async def predict_match_probas(client: httpx.AsyncClient, home: str, away: str,
                                league: str, kickoff: str) -> Optional[dict]:
    """Demande à Gemini (avec Search) les probas de tous les marchés d'un match."""
    if not GEMINI_API_KEY:
        return None

    prompt = f"""Tu es un analyste football professionnel. Utilise GOOGLE SEARCH pour trouver les données réelles.

Match : {home} vs {away}
Compétition : {league}
Date : {kickoff}

ÉTAPE 1 — CHERCHE les infos :
- Forme des 2 équipes (5 derniers matchs)
- H2H (5 dernières confrontations)
- Moyenne de buts marqués/encaissés
- Moyenne de corners par match
- Moyenne de cartons par match
- Blessés et suspendus
- Enjeu du match

ÉTAPE 2 — CALCULE les probas (%) pour ces marchés :

Retourne UNIQUEMENT ce JSON (rien d'autre, pas de texte avant/après) :

{{
  "context": {{
    "home_form": "...",
    "away_form": "...",
    "h2h": "...",
    "injuries": "...",
    "corners_avg": 0.0,
    "cards_avg": 0.0
  }},
  "markets": {{
    "double_chance_1X": 0,
    "double_chance_12": 0,
    "double_chance_X2": 0,
    "victoire_home": 0,
    "match_nul": 0,
    "victoire_away": 0,
    "over_0.5": 0,
    "over_1.5": 0,
    "over_2.5": 0,
    "over_3.5": 0,
    "btts_yes": 0,
    "btts_no": 0,
    "over_8.5_corners": 0,
    "over_9.5_corners": 0,
    "over_2.5_cartons": 0,
    "over_3.5_cartons": 0
  }}
}}

RÈGLES :
- Les probas sont des entiers entre 0 et 100
- Cohérence : double_chance_1X = victoire_home + match_nul (approx)
- Ne dépasse jamais 92% ni en dessous de 8%
- Si tu ne trouves pas une info, mets une proba raisonnable basée sur le contexte
- Sois PRÉCIS, pas générique"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2000,
        },
    }

    try:
        r = await client.post(
            f"{GEMINI_SEARCH_URL}?key={GEMINI_API_KEY}",
            json=payload, timeout=45.0,
        )
        if r.status_code >= 400:
            print(f"⚠️ Gemini predict {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()

        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return None

        # Extraire le JSON
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            # Essaie de nettoyer
            raw = m.group().replace("\n", " ").replace("  ", " ")
            try:
                return json.loads(raw)
            except Exception:
                return None
    except Exception as e:
        print(f"⚠️ predict_match_probas crash: {e}")
        return None
