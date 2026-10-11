"""
Prédicteur Gemini avec avis personnel.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"


async def predict_match_probas(client: httpx.AsyncClient, home: str, away: str,
                                league: str, kickoff: str) -> Optional[dict]:
    if not GEMINI_API_KEY:
        return None

    prompt = f"""Tu es un analyste football expert. Analyse ce match et estime les probas.

Match : {home} vs {away}
Compétition : {league}

Retourne UNIQUEMENT ce JSON :

{{
  "markets": {{
    "double_chance_1X": 0,
    "double_chance_X2": 0,
    "victoire_home": 0,
    "match_nul": 0,
    "victoire_away": 0,
    "over_2.5": 0,
    "under_2.5": 0,
    "over_3.5": 0,
    "btts_yes": 0,
    "btts_no": 0
  }},
  "analysis": "2 phrases d'analyse sur le match : niveau des équipes, forme, enjeu.",
  "advice": "1 phrase de conseil pour le parieur."
}}

RÈGLES :
- Toutes les probas entre 15 et 85
- victoire_home + match_nul + victoire_away = 100
- over_2.5 + under_2.5 = 100
- "analysis" : 2 phrases max, factuelles
- "advice" : 1 phrase, directive
- Rien avant/après le JSON"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 900},
    }

    try:
        r = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=30.0)
        if r.status_code >= 400:
            print(f"⚠️ Gemini {r.status_code}")
            return None
        data = r.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return None

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
        print(f"⚠️ Gemini crash: {e}")
        return None
