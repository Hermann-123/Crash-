"""
Prédicteur Gemini enrichi avec H2H + corners/cartons.
AVEC RETRY ROBUSTE + FALLBACK MULTI-MODÈLES.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# === LISTE DE MODÈLES (fallback en cascade) ===
GEMINI_MODELS = [
    "gemini-3.5-flash-lite",  # Modèle principal (rapide, stable, moins de 503)
    "gemini-3.5-flash",       # Fallback 1
]

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def format_context(context: Optional[dict]) -> str:
    """Formate tout le contexte pour Gemini."""
    if not context or not context.get("available"):
        return ""

    h2h = context.get("h2h", {})
    pred = context.get("prediction", {})
    cc = context.get("corners_cards")
    home_form = context.get("home_form", "")
    away_form = context.get("away_form", "")
    home_inj = context.get("home_injuries", [])
    away_inj = context.get("away_injuries", [])
    source = context.get("source", "")

    parts = ["\n📊 DONNÉES RÉELLES — utilise-les en priorité :"]
    if source:
        parts.append(f"(source : {source})")

    if home_form and home_form != "?":
        parts.append(f"Forme équipe A : {home_form}")
    if away_form and away_form != "?":
        parts.append(f"Forme équipe B : {away_form}")

    if h2h:
        total = h2h.get("total_matches", 0)
        hw = h2h.get("home_wins", 0)
        d = h2h.get("draws", 0)
        aw = h2h.get("away_wins", 0)
        avg = h2h.get("avg_total_goals", 0)
        if total:
            parts.append(f"\nH2H ({total} matchs) : {hw}V dom / {d}N / {aw}V ext")
            if avg:
                parts.append(f"Moyenne buts H2H : {avg}")
        recent = h2h.get("recent_matches", [])
        if recent:
            parts.append("5 derniers H2H :")
            for m in recent[:5]:
                h = m.get("home_team", m.get("home", "?"))
                a = m.get("away_team", m.get("away", "?"))
                s = m.get("score", m.get("result", "?"))
                parts.append(f"  - {h} {s} {a}")

    if pred:
        parts.append("\nPrédictions ML CatBoost :")
        keys_to_try = [
            "home_win", "draw", "away_win", "over_2.5", "btts_yes",
            "home_win_probability", "draw_probability", "away_win_probability",
            "over_2_5_probability", "btts_probability",
        ]
        found = False
        for key in keys_to_try:
            v = pred.get(key)
            if v is not None:
                try:
                    parts.append(f"  - {key} : {float(v)*100:.1f}%")
                    found = True
                except (ValueError, TypeError):
                    pass
        if not found:
            for k, v in pred.items():
                if isinstance(v, (int, float)) and 0 <= v <= 1:
                    parts.append(f"  - {k} : {float(v)*100:.1f}%")

    if home_inj or away_inj:
        parts.append("\n🏥 Blessés/suspendus :")
        if home_inj:
            names = ", ".join(str(x.get("player", "?")) for x in home_inj[:5] if x.get("player"))
            if names:
                parts.append(f"  Équipe A : {names}")
        if away_inj:
            names = ", ".join(str(x.get("player", "?")) for x in away_inj[:5] if x.get("player"))
            if names:
                parts.append(f"  Équipe B : {names}")

    if cc and cc.get("available"):
        parts.append("\n📐 Corners/Cartons (moyennes réelles) :")
        hcf = cc.get("home_corners_for", 0)
        acf = cc.get("away_corners_for", 0)
        hca = cc.get("home_corners_against", 0)
        aca = cc.get("away_corners_against", 0)
        hcard = cc.get("home_cards_avg", 0)
        acard = cc.get("away_cards_avg", 0)
        parts.append(f"  Corners/match A : {hcf:.1f} pour / {hca:.1f} contre")
        parts.append(f"  Corners/match B : {acf:.1f} pour / {aca:.1f} contre")
        parts.append(f"  Total corners attendu : ~{hcf + acf:.1f}")
        parts.append(f"  Cartons jaunes : {hcard:.1f} / {acard:.1f}")
        parts.append(f"  Total cartons attendu : ~{hcard + acard:.1f}")
        parts.append("⚠️ UTILISE CES MOYENNES pour les marchés Corners/Cartons.")

    return "\n".join(parts)


def build_prompt(home: str, away: str, league: str, ctx_block: str) -> str:
    return f"""Tu es un analyste football expert. Analyse ce match et estime les probas.

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
    "over_2.5": 0,
    "under_2.5": 0,
    "over_3.5": 0,
    "btts_yes": 0,
    "btts_no": 0,
    "btts_over_25": 0,
    "over_8.5_corners": 0,
    "over_9.5_corners": 0,
    "over_10.5_corners": 0,
    "over_2.5_cartons": 0,
    "over_3.5_cartons": 0
  }},
  "score_exact": {{
    "home": 0,
    "away": 0,
    "prob": 0
  }},
  "analysis": "2 phrases d'analyse basées sur les données réelles ci-dessus.",
  "advices": [
    {{"text": "Nom du 1er pronostic conseillé", "confidence": 0}},
    {{"text": "Nom du 2ème pronostic conseillé (optionnel)", "confidence": 0}}
  ]
}}

RÈGLES :
- Toutes les probas entre 15 et 85
- victoire_home + match_nul + victoire_away = 100
- double_chance_1X = victoire_home + match_nul
- double_chance_X2 = match_nul + victoire_away
- double_chance_12 = victoire_home + victoire_away
- over_2.5 + under_2.5 = 100
- "btts_over_25" = probabilité que les DEUX équipes marquent ET qu'il y ait 3 buts ou plus (ex: 2-1, 3-1, 2-2...).
- Si tu as des corners/cartons réels, BASE tes probas dessus
- Pour les cartons : estime selon l'intensité du match, les enjeux, les équipes
- Pour les corners : utilise les moyennes réelles si disponibles
- Si tu as un H2H réel, mentionne-le dans analysis
- "score_exact" : score le plus probable selon ton analyse (home: buts équipe A, away: buts équipe B). "prob" est la probabilité 0-100 de ce score exact (typiquement entre 8 et 20).
- "analysis" : 2 phrases max
- "advices" : DONNE 1 OU 2 CONSEILS MAXIMUM. La "confidence" est en pourcentage (40-90).
- N'inclus PAS de conseils combinés
- Rien avant/après le JSON"""


async def call_gemini(client: httpx.AsyncClient, model: str, payload: dict) -> Optional[dict]:
    """Appelle un modèle Gemini avec 3 retries max et délais courts."""
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"

    for attempt in range(3):
        try:
            r = await client.post(url, json=payload, timeout=30.0)

            if r.status_code in (503, 429):
                wait = min(2 ** attempt, 10)  
                print(f"  ⏳ Gemini {model} {r.status_code}, retry {attempt+1}/3 (wait {wait}s)...")
                await asyncio.sleep(wait)
                continue

            if r.status_code == 404:
                print(f"  ⚠️ Gemini {model} 404 (modèle inexistant)")
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
                except Exception:
                    return None

            if "advices" not in parsed and "advice" in parsed:
                parsed["advices"] = [{
                    "text": parsed.get("advice", ""),
                    "confidence": parsed.get("advice_confidence", 0),
                }]

            return parsed

        except Exception as e:
            print(f"  ⚠️ Gemini {model} crash (attempt {attempt}): {e}")
            await asyncio.sleep(2)

    print(f"  ❌ Gemini {model} : échec après 3 tentatives")
    return None


def _normalize_score_exact(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    try:
        h = int(raw.get("home", -1))
        a = int(raw.get("away", -1))
        p = float(raw.get("prob", 0))
    except (ValueError, TypeError):
        return {}
    if not (0 <= h <= 8 and 0 <= a <= 8):
        return {}
    if p < 0:
        return {}
    if p > 1.0:
        p = p / 100.0
    if p > 0.6:
        p = 0.0
    return {"home": h, "away": a, "prob": round(p, 4)}


async def predict_match_probas(client: httpx.AsyncClient, home: str, away: str,
                                league: str, kickoff: str,
                                context: Optional[dict] = None) -> Optional[dict]:
    if not GEMINI_API_KEY:
        return None

    ctx_block = format_context(context)
    prompt = build_prompt(home, away, league, ctx_block)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1600},
    }

    for i, model in enumerate(GEMINI_MODELS):
        print(f"  🧠 Analyse de {home} vs {away} avec {model}...")
        result = await call_gemini(client, model, payload)
        if result is not None:
            if i > 0:
                print(f"  ✅ Fallback réussi via {model}")
            result["score_exact"] = _normalize_score_exact(result.get("score_exact"))
            return result
        else:
            if i < len(GEMINI_MODELS) - 1:
                print(f"  🔄 Bascule vers {GEMINI_MODELS[i+1]}...")
            continue

    print(f"❌ Tous les modèles Gemini ont échoué pour {home} vs {away}")
    return None
