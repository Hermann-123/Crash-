"""
Prédicteur Gemini enrichi avec recherche contextuelle (Web).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
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
    return f"""Tu es un pronostiqueur sportif professionnel et un expert en analyse de données football.
Analyse ce match en profondeur : {home} vs {away} ({league}).
{ctx_block}

INSTRUCTIONS CRUCIALES :
1. Utilise tes connaissances récentes (blessures, forme, enjeux, météo, fatigue, motivation).
2. Sois impitoyable : si le match est déséquilibré ou sans intérêt, dis-le.
3. Cherche la "Value" (l'erreur du bookmaker).

Retourne UNIQUEMENT ce JSON valide (aucun texte avant ou après) :

{{
  "markets": {{
    "victoire_home": 0,
    "match_nul": 0,
    "victoire_away": 0,
    "double_chance_1X": 0,
    "double_chance_X2": 0,
    "double_chance_12": 0,
    "over_2.5": 0,
    "under_2.5": 0,
    "over_1.5": 0,
    "under_1.5": 0,
    "over_3.5": 0,
    "under_3.5": 0,
    "btts_yes": 0,
    "btts_no": 0
  }},
  "analysis": "Une analyse d'expert en 3 phrases. Mentionne le contexte (blessures, enjeu, forme). Sois précis et tranchant.",
  "advices": [
    {{"text": "Conseil de pro 1 (ex: 'Le bookmaker sous-estime l'attaque de X')", "confidence": 0}}
  ]
}}

RÈGLES :
- Probas entre 1 et 99
- victoire_home + match_nul + victoire_away = 100
- over_2.5 + under_2.5 = 100
- Rien avant/après le JSON"""

async def call_gemini(client, model, payload):
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"
    for attempt in range(3):
        try:
            # Timeout augmenté à 60s car l'analyse est plus profonde
            r = await client.post(url, json=payload, timeout=60.0) 
            if r.status_code in (503, 429):
                wait = min(2 ** attempt, 10)
                print(f"  ⏳ Gemini {model} {r.status_code}, retry {attempt+1}/3...")
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 400:
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
                return None
                
            return parsed
        except Exception as e:
            print(f"  ⚠️ Gemini {model} crash: {repr(e)}") # Affiche la vraie erreur
            await asyncio.sleep(2)
    return None

async def predict_match_probas(client, home, away, league, kickoff, context=None):
    if not GEMINI_API_KEY:
        return None
    ctx_block = format_context(context)
    prompt = build_prompt(home, away, league, ctx_block)
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1500},
    }
    
    for i, model in enumerate(GEMINI_MODELS):
        result = await call_gemini(client, model, payload)
        if result is not None:
            return result
        if i < len(GEMINI_MODELS) - 1:
            print(f"  🔄 Bascule vers {GEMINI_MODELS[i+1]}")
    return None
