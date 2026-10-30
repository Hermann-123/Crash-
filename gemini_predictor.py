"""
Prédicteur Gemini enrichi.
RETRY ROBUSTE + FALLBACK MULTI-MODÈLES + SCORE EXACT.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def format_context(context):
    if not context or not context.get("available"):
        return ""
    h2h = context.get("h2h", {})
    parts = []
    if h2h:
        total = h2h.get("total_matches", 0)
        hw = h2h.get("home_wins", 0)
        d = h2h.get("draws", 0)
        aw = h2h.get("away_wins", 0)
        if total:
            parts.append(f"\nH2H ({total} matchs) : {hw}V dom / {d}N / {aw}V ext")
    return "\n".join(parts)


def build_prompt(home, away, league, ctx_block):
    return f"""Tu es un analyste football expert. Analyse ce match.

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
    "double_chance_X2": 0,
    "over_2.5": 0,
    "under_2.5": 0,
    "btts_yes": 0,
    "btts_no": 0
  }},
  "score_exact": {{ "home": 0, "away": 0, "prob": 0 }},
  "analysis": "2 phrases max.",
  "advices": [
    {{"text": "1er conseil", "confidence": 0}},
    {{"text": "2ème conseil (optionnel)", "confidence": 0}}
  ]
}}

RÈGLES :
- Probas entre 15 et 85
- victoire_home + match_nul + victoire_away = 100
- over_2.5 + under_2.5 = 100
- Rien avant/après le JSON"""


async def call_gemini(client, model, payload):
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"
    for attempt in range(3):
        try:
            r = await client.post(url, json=payload, timeout=30.0)
            if r.status_code in (503, 429):
                wait = min(2 ** attempt, 10)
                print(f"  ⏳ Gemini {model} {r.status_code}, retry {attempt+1}/3 (wait {wait}s)")
                await asyncio.sleep(wait)
                continue
            if r.status_code == 404:
                print(f"  ⚠️ Gemini {model} 404")
                return None
            if r.status_code >= 400:
                print(f"  ⚠️ Gemini {model} {r.status_code}: {r.text[:100]}")
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
                parsed = json.loads(m.group())
            except json.JSONDecodeError:
                raw = m.group().replace("\n", " ").replace("  ", " ")
                try:
                    parsed = json.loads(raw)
                except:
                    return None
            if "advices" not in parsed and "advice" in parsed:
                parsed["advices"] = [{"text": parsed.get("advice", ""), "confidence": parsed.get("advice_confidence", 0)}]
            return parsed
        except Exception as e:
            print(f"  ⚠️ Gemini {model} crash: {e}")
            await asyncio.sleep(2)
    print(f"  ❌ Gemini {model} échec après 3 tentatives")
    return None


def _normalize_score_exact(raw):
    if not isinstance(raw, dict): return {}
    try:
        h = int(raw.get("home", -1))
        a = int(raw.get("away", -1))
        p = float(raw.get("prob", 0))
    except: return {}
    if not (0 <= h <= 8 and 0 <= a <= 8): return {}
    if p < 0: return {}
    if p > 1.0: p = p / 100.0
    if p > 0.6: p = 0.0
    return {"home": h, "away": a, "prob": round(p, 4)}


async def predict_match_probas(client, home, away, league, kickoff, context=None):
    if not GEMINI_API_KEY:
        return None
    ctx_block = format_context(context)
    prompt = build_prompt(home, away, league, ctx_block)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1600},
    }
    for i, model in enumerate(GEMINI_MODELS):
        print(f"  🧠 Analyse de {home} vs {away} avec {model}")
        result = await call_gemini(client, model, payload)
        if result is not None:
            if i > 0:
                print(f"  ✅ Fallback réussi via {model}")
            result["score_exact"] = _normalize_score_exact(result.get("score_exact"))
            return result
        if i < len(GEMINI_MODELS) - 1:
            print(f"  🔄 Bascule vers {GEMINI_MODELS[i+1]}")
    print(f"❌ Tous les modèles Gemini ont échoué pour {home} vs {away}")
    return None
