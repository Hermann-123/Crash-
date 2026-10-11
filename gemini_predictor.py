"""
Prédicteur Gemini SANS Search grounding.
Modèle : gemini-3.1-flash-lite (quota 1500/jour)
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

    prompt = f"""Tu es un analyste football. Estime les probas pour ce match :

Match : {home} vs {away}
Compétition : {league}

Retourne UNIQUEMENT ce JSON :

{{
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
    "btts_no": 45
  }}
}}

RÈGLES :
- Probas entre 5 et 92
- victoire_home + match_nul + victoire_away ≈ 100
- Rien avant/après le JSON"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1000},
    }

    try:
        r = await client.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=30.0)
        if r.status_code >= 400:
            print(f"⚠️ Gemini {r.status_code}: {r.text[:200]}")
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
