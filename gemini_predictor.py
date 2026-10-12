"""
Prédicteur Gemini simple.
Fonction unique : predict_match_probas(client, home, away, league, kickoff)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"


async def predict_match_probas(client: httpx.AsyncClient, home: str, away: str,
                                league: str, kickoff: str) -> Optional[dict]:
    """Retourne les probas estimées par Gemini pour un match."""
    if not GEMINI_API_KEY:
        return None

    prompt = f"""Tu es un analyste football expert. Analyse ce match et estime les probas.

Match : {home} vs {away}
Compétition : {league}

Retourne UNIQUEMENT ce JSON :

{{
  "markets": {{
    "victoire_home": 0,
    "match_nul": 0,
    "victoire_away": 0,
    "double_chance_1X": 0,
    "double_chance_12": 0,
    "double_chance_X2": 0,
    "handicap_home_plus_05": 0,
    "handicap_away_plus_05": 0,
    "over_2.5": 0,
    "under_2.5": 0,
    "over_3.5": 0,
    "btts_yes": 0,
    "btts_no": 0,
    "over_6.5_corners": 0,
    "over_8.5_corners": 0,
    "over_10.5_corners": 0,
    "home_over_2.5_corners": 0,
    "away_over_2.5_corners": 0,
    "over_2.5_cartons": 0,
    "over_3.5_cartons": 0
  }},
  "analysis": "2 phrases d'analyse.",
  "advice": "1 phrase de conseil.",
  "advice_confidence": 0
}}

RÈGLES :
- Toutes les probas entre 15 et 85
- victoire_home + match_nul + victoire_away = 100
- double_chance_1X = victoire_home + match_nul
- double_chance_X2 = match_nul + victoire_away
- double_chance_12 = victoire_home + victoire_away
- handicap_home_plus_05 = double_chance_1X
- handicap_away_plus_05 = double_chance_X2
- over_2.5 + under_2.5 = 100
- "advice_confidence" : 40-90
- Rien avant/après le JSON"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1200},
    }

    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"

    # Retry automatique (Gemini renvoie souvent 503)
    for attempt in range(4):
        try:
            r = await client.post(url, json=payload, timeout=35.0)

            if r.status_code == 503:
                print(f"  ⏳ Gemini 503, retry {attempt + 1}/4...")
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                print(f"  ⏳ Gemini 429, retry {attempt + 1}/4...")
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                print(f"  ⚠️ Gemini {r.status_code}: {r.text[:150]}")
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
            print(f"  ⚠️ Gemini crash (attempt {attempt}): {e}")
            await asyncio.sleep(2 ** attempt)

    print(f"  ❌ Gemini échec définitif pour {home} vs {away}")
    return None
