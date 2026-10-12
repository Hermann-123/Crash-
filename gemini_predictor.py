"""
Prédicteur Gemini enrichi avec H2H + prédictions ML + corners/cartons.
Accepte les données Bzzoiro ET API-Football.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# ⚠️ Vérifie que ce nom de modèle est bien valide sur ton compte API.
# Les modèles standards actuels sont : gemini-1.5-flash, gemini-1.5-pro, gemini-2.0-flash.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"


def format_context(context: Optional[dict]) -> str:
    """Formate tout le contexte (Bzzoiro ou API-Football) pour Gemini."""
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

    # Formes
    if home_form and home_form != "?":
        parts.append(f"Forme équipe A : {home_form}")
    if away_form and away_form != "?":
        parts.append(f"Forme équipe B : {away_form}")

    # H2H
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

    # Prédictions ML (Bzzoiro uniquement)
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

    # Blessés (API-Football)
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

    # Corners/Cartons (API-Football)
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

RÈGLES :
- Toutes les probas entre 15 et 85
- victoire_home + match_nul + victoire_away = 100
- double_chance_1X = victoire_home + match_nul
- double_chance_X2 = match_nul + victoire_away
- double_chance_12 = victoire_home + victoire_away
- over_2.5 + under_2.5 = 100
- Si tu as des corners/cartons réels, BASE tes probas dessus
- Si tu as un H2H réel, mentionne-le dans analysis
- "analysis" : 2 phrases max
- "advice_confidence" : 40-90
- Rien avant/après le JSON"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1200},
    }

    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"

    for attempt in range(4):
        try:
            r = await client.post(url, json=payload, timeout=35.0)

            if r.status_code == 503:
                print(f"  ⏳ Gemini 503, retry {attempt+1}/4...")
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                print(f"  ⏳ Gemini 429, retry {attempt+1}/4...")
                await asyncio.sleep(5 * (attempt + 1))
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
