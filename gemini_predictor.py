"""
Prédicteur Gemini SANS Search grounding.
Utilise les connaissances internes de Gemini pour estimer les probas.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


async def predict_match_probas(client: httpx.AsyncClient, home: str, away: str,
                                league: str, kickoff: str) -> Optional[dict]:
    """Demande à Gemini les probas (sans Search, juste ses connaissances)."""
    if not GEMINI_API_KEY:
        return None

    prompt = f"""Tu es un analyste football. Estime les probas des marchés pour ce match :

Match : {home} vs {away}
Compétition : {league}
Date : {kickoff}

Utilise tes connaissances sur ces équipes pour estimer les probabilités.

Retourne UNIQUEMENT ce JSON (rien d'autre) :

{{
  "context": {{
    "home_form": "V,N,D,V,N",
    "away_form": "N,V,N,D,V",
    "h2h": "2-3, 4-0, 2-2, 1-1, 0-2",
    "injuries": "2 blessés côté {home}",
    "corners_avg": 10.0,
    "cards_avg": 4.0
  }},
  "markets": {{
    "double_chance_1X": 60,
    "double_chance_12": 75,
    "double_chance_X2": 55,
    "victoire_home": 35,
    "match_nul": 30,
    "victoire_away": 35,
    "over_0.5": 90,
    "over_1.5": 72,
    "over_2.5": 50,
    "over_3.5": 30,
    "btts_yes": 55,
    "btts_no": 45,
    "over_8.5_corners": 60,
    "over_9.5_corners": 50,
    "over_2.5_cartons": 65,
    "over_3.5_cartons": 50
  }}
}}

RÈGLES :
- Probas entre 5 et 92
- Cohérence : victoire_home + match_nul + victoire_away ≈ 100
- Cohérence : over_0.5 > over_1.5 > over_2.5 > over_3.5
- Basé sur ton estimation raisonnable, pas inventé
- Rien avant/après le JSON"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1500,
        },
    }

    try:
        r = await client.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload, timeout=45.0,
        )
        if r.status_code >= 400:
            print(f"⚠️ Gemini {r.status_code}: {r.text[:200]}")
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
            raw = m.group().replace("\n", " ").replace("  ", " ")
            try:
                return json.loads(raw)
            except Exception:
                return None
    except Exception as e:
        print(f"⚠️ predict_match_probas crash: {e}")
        return None
