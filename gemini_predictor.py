"""
Prédicteur Gemini enrichi avec les vraies données API-Football.
Avec retry automatique sur 503/429.
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


def format_context(context: Optional[dict]) -> str:
    """Formate le contexte API-Football pour le prompt."""
    if not context or not context.get("available"):
        return ""

    hs = context.get("home_stats", {})
    aws = context.get("away_stats", {})
    h2h = context.get("h2h", [])
    h_inj = context.get("home_injuries", [])
    a_inj = context.get("away_injuries", [])

    h2h_str = "; ".join(
        f"{x['home']} {x['home_goals']}-{x['away_goals']} {x['away']}"
        for x in h2h[:5]
    ) or "aucun"

    h_inj_str = ", ".join(x['player'] for x in h_inj) or "aucun"
    a_inj_str = ", ".join(x['player'] for x in a_inj) or "aucun"

    return f"""

📊 DONNÉES RÉELLES (API-Football) — utilise-les en priorité :

Forme équipe A : {hs.get('form', '?')} (5 derniers)
Buts pour/m : {hs.get('goals_for_avg', 0):.2f}
Buts contre/m : {hs.get('goals_against_avg', 0):.2f}
Corners/m : {hs.get('corners_for_avg', 0):.1f}
Cartons/m : {hs.get('cards_yellow_avg', 0):.1f}

Forme équipe B : {aws.get('form', '?')} (5 derniers)
Buts pour/m : {aws.get('goals_for_avg', 0):.2f}
Buts contre/m : {aws.get('goals_against_avg', 0):.2f}
Corners/m : {aws.get('corners_for_avg', 0):.1f}
Cartons/m : {aws.get('cards_yellow_avg', 0):.1f}

H2H (5 derniers) : {h2h_str}
Absents équipe A : {h_inj_str}
Absents équipe B : {a_inj_str}
"""


async def predict_match_probas(client: httpx.AsyncClient, home: str, away: str,
                                league: str, kickoff: str,
                                context: Optional[dict] = None) -> Optional[dict]:
    if not GEMINI_API_KEY:
        return None

    ctx_block = format_context(context)

    prompt = f"""Tu es un analyste football expert. Analyse ce match et estime les probas.

Match : {home} vs {away}
Compétition : {league}
{ctx_block}

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
  "analysis": "2 phrases d'analyse basées sur les données réelles ci-dessus.",
  "advice": "1 phrase de conseil concret pour le parieur.",
  "advice_confidence": 0
}}

RÈGLES STRICTES :
- Toutes les probas entre 15 et 85
- victoire_home + match_nul + victoire_away = 100
- double_chance_1X = victoire_home + match_nul
- double_chance_X2 = match_nul + victoire_away
- double_chance_12 = victoire_home + victoire_away
- handicap_home_plus_05 = double_chance_1X
- handicap_away_plus_05 = double_chance_X2
- over_2.5 + under_2.5 = 100
- Si tu as des stats réelles : base tes probas dessus
- "analysis" : mentionne la forme, les buts moyens, le H2H si dispo
- "advice_confidence" : % de réussite estimé (40-90)
- Rien avant/après le JSON"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1200},
    }

    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"

    # Retry sur 503 / 429
    for attempt in range(4):
        try:
            r = await client.post(url, json=payload, timeout=35.0)

            if r.status_code == 503:
                wait = 2 ** attempt
                print(f"  ⏳ Gemini 503, retry dans {wait}s...")
                await asyncio.sleep(wait)
                continue

            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  ⏳ Gemini 429, retry dans {wait}s...")
                await asyncio.sleep(wait)
                continue

            if r.status_code >= 400:
                print(f"⚠️ Gemini {r.status_code}: {r.text[:150]}")
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
            print(f"⚠️ Gemini crash (attempt {attempt}): {e}")
            await asyncio.sleep(2 ** attempt)

    print(f"❌ Gemini échec définitif pour {home} vs {away}")
    return None
