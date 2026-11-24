"""
Prédicteur Gemini Pro — Analyse quantitative + contextuelle (Méthode Pro & Café Foot en 4 temps).
Compatible avec main.py et weekly_analyzer.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_ENV_MODEL = os.getenv("GEMINI_MODEL", "").strip()
_BASE_MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]
GEMINI_MODELS = ([_ENV_MODEL] if _ENV_MODEL and _ENV_MODEL not in _BASE_MODELS else []) + _BASE_MODELS
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
TZ = ZoneInfo(os.getenv("NOTIFY_TZ", "Africa/Abidjan"))


def format_context(context: Optional[dict], kickoff: str = "") -> str:
    if not context or not isinstance(context, dict):
        return f"\nCoup d'envoi : {kickoff}" if kickoff else ""

    parts = []
    if kickoff:
        parts.append(f"Coup d'envoi : {kickoff}")

    # 1. Historique confrontations directes (Bzzoiro H2H)
    h2h = context.get("h2h")
    if isinstance(h2h, dict) and h2h.get("total_matches"):
        total = h2h.get("total_matches", 0)
        hw = h2h.get("home_wins", 0)
        d = h2h.get("draws", 0)
        aw = h2h.get("away_wins", 0)
        parts.append(f"Historique H2H ({total} matchs) : {hw}V dom / {d}N / {aw}V ext")

    # 2. Base statistique Dixon-Coles (Poisson)
    dixon = context.get("dixon_coles")
    if isinstance(dixon, dict) and dixon:
        ph = dixon.get("p_home")
        pd = dixon.get("p_draw")
        pa = dixon.get("p_away")
        o25 = dixon.get("over_2.5")
        btts = dixon.get("btts_yes")
        lh = dixon.get("lambda_home")
        la = dixon.get("lambda_away")
        stat_bits = []
        if ph is not None and pd is not None and pa is not None:
            stat_bits.append(f"1={float(ph)*100:.1f}% / X={float(pd)*100:.1f}% / 2={float(pa)*100:.1f}%")
        if lh is not None and la is not None:
            stat_bits.append(f"Buts attendus (xG) : {float(lh):.2f} - {float(la):.2f}")
        if o25 is not None:
            stat_bits.append(f"Over 2.5={float(o25)*100:.1f}%")
        if btts is not None:
            stat_bits.append(f"BTTS={float(btts)*100:.1f}%")
        if stat_bits:
            parts.append("Modèle statistique Dixon-Coles : " + " | ".join(stat_bits))

    # 3. Cotes bookmaker réelles
    odds = context.get("bookmaker_odds")
    if isinstance(odds, dict) and odds:
        odd_bits = []
        for k, lbl in (
            ("home", "1"),
            ("draw", "X"),
            ("away", "2"),
            ("over_2.5", "Over 2.5"),
            ("under_2.5", "Under 2.5"),
            ("btts_yes", "BTTS Oui"),
        ):
            if odds.get(k):
                odd_bits.append(f"{lbl}={odds[k]}")
        if odd_bits:
            parts.append("Cotes bookmaker réelles : " + " | ".join(odd_bits))

    # 4. Probabilités natives Bzzoiro
    bzz_m = context.get("bzzoiro_probas")
    if isinstance(bzz_m, dict) and bzz_m:
        bzz_bits = []
        for k, lbl in (("victoire_home", "1"), ("match_nul", "X"), ("victoire_away", "2"), ("over_2.5", "Over 2.5"), ("btts_yes", "BTTS")):
            if bzz_m.get(k) is not None:
                bzz_bits.append(f"{lbl}={bzz_m[k]}%")
        if bzz_bits:
            parts.append("Modèle Bzzoiro : " + " | ".join(bzz_bits))

    # 5. Forme / classement éventuels fournis par Bzzoiro
    for key, label in (("home_form", "Forme domicile"), ("away_form", "Forme extérieur"), ("summary", "Données terrain")):
        val = context.get(key)
        if val:
            parts.append(f"{label} : {val}")

    return ("\nCONTEXTE STATISTIQUE DISPONIBLE :\n- " + "\n- ".join(parts)) if parts else ""


def build_prompt(home: str, away: str, league: str, ctx_block: str) -> str:
    today_date = datetime.now(TZ).strftime("%d/%m/%Y")
    return f"""Tu es un pronostiqueur sportif professionnel d'élite (20+ ans d'expérience) dans le style "Café Foot / Briefing du jour" (direct, chiffré, vivant, lucide et nuancé).
Date actuelle : {today_date}. Utilise tes connaissances actuelles et la recherche web disponible pour vérifier le classement actuel, la forme récente et les absences du jour.
Analyse ce match avec une rigueur absolue : {home} (Domicile) vs {away} (Extérieur) — Compétition : {league}.
{ctx_block}

MÉTHODOLOGIE D'ANALYSE PRO EN 4 TEMPS (OBLIGATOIRE) :
1. ENJEU & DYNAMIQUE ACTUELLE : Situe les deux équipes au classement/compétition (points, course au titre/top 8/maintien) et leur état de forme récent.
2. SÉRIES DOMICILE / EXTÉRIEUR & SCÉNARIOS DE BUTS : Compare le rendement précis de {home} à domicile vs {away} à l'extérieur (victoires/défaites récentes, buts marqués/encaissés, tendance à ouvrir le score ou encaisser, matchs ouverts Over 2.5/BTTS ou fermés).
3. FACTEURS CLÉS & FILTRE ANTI-PIÈGE : Vérifie les joueurs décisifs/absences confirmées, changements de coach ou contexte (derby, revanche). Identifie tout risque majeur (rotation avant/après coupe d'Europe, équipe sans enjeu ou en crise) : si un piège existe, passe "trap_warning" à true et "risk_level" à "HIGH".
4. NUANCE & VERDICT TRANCHÉ : Pèse brièvement le contre-argument ("Mais attention..." / "Petit doute...") puis tranche clairement sur le pari le plus solide. Le marché ayant la plus forte probabilité dans le JSON DOIT correspondre exactement à la conclusion de ton texte "analysis".

Retourne UNIQUEMENT ce JSON valide (sans texte avant ni après) :
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
  "risk_level": "LOW",
  "trap_warning": false,
  "analysis": "Raisonnement style Café Foot en 3 à 5 phrases précises et chiffrées : enjeu & dynamique dom/ext, joueur clé ou absence, nuance (Mais attention...) et conclusion claire sur le pari le plus solide.",
  "advices": [
    {{"text": "Recommandation principale de pro", "confidence": 0}}
  ]
}}

RÈGLES MATHÉMATIQUES STRICTES :
- Probabilités entières entre 2 et 96 (ne gonfle jamais artificiellement un favori : même un grand favori dépasse rarement 75-82% en victoire sèche).
- victoire_home + match_nul + victoire_away = 100.
- over_1.5 + under_1.5 = 100 | over_2.5 + under_2.5 = 100 | over_3.5 + under_3.5 = 100.
- btts_yes + btts_no = 100.
- risk_level : "LOW" (match lisible et sûr), "MEDIUM" (incertitude modérée), ou "HIGH" (match piège / imprévisible)."""


def _sanitize_prediction(parsed: dict) -> Optional[dict]:
    """Vérifie et normalise mathématiquement les probabilités retournées par Gemini."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("markets"), dict):
        return None

    raw_m = parsed["markets"]
    clean_m = {}
    for k, v in raw_m.items():
        try:
            f = float(v)
            if 0 < f <= 1:
                f *= 100.0
            clean_m[k] = round(min(97.0, max(1.0, f)), 1)
        except (ValueError, TypeError):
            continue

    # Normalisation stricte 1X2 = 100%
    if all(k in clean_m for k in ("victoire_home", "match_nul", "victoire_away")):
        s = clean_m["victoire_home"] + clean_m["match_nul"] + clean_m["victoire_away"]
        if s > 0:
            vh = round((clean_m["victoire_home"] / s) * 100.0, 1)
            mn = round((clean_m["match_nul"] / s) * 100.0, 1)
            va = round(max(1.0, 100.0 - vh - mn), 1)
            clean_m["victoire_home"] = vh
            clean_m["match_nul"] = mn
            clean_m["victoire_away"] = va
            clean_m["double_chance_1X"] = round(min(97.0, vh + mn), 1)
            clean_m["double_chance_X2"] = round(min(97.0, mn + va), 1)
            clean_m["double_chance_12"] = round(min(97.0, vh + va), 1)

    # Normalisation des paires complémentaires (Over/Under, BTTS)
    for k1, k2 in (("over_1.5", "under_1.5"), ("over_2.5", "under_2.5"), ("over_3.5", "under_3.5"), ("btts_yes", "btts_no")):
        if k1 in clean_m and k2 in clean_m:
            s = clean_m[k1] + clean_m[k2]
            if s > 0:
                v1 = round((clean_m[k1] / s) * 100.0, 1)
                clean_m[k1] = v1
                clean_m[k2] = round(max(1.0, 100.0 - v1), 1)

    analysis = str(parsed.get("analysis") or "").strip()
    risk_level = str(parsed.get("risk_level") or "MEDIUM").upper().strip()
    trap_warning = bool(parsed.get("trap_warning", False))

    # Signal explicite dans l'analyse pour le filtre anti-piège de main.py et weekly_analyzer.py
    if (trap_warning or risk_level == "HIGH") and "match piège" not in analysis.lower():
        analysis = f"{analysis} (Match piège / risque élevé détecté).".strip()

    parsed["markets"] = clean_m
    parsed["analysis"] = analysis
    parsed["risk_level"] = risk_level
    parsed["trap_warning"] = trap_warning
    return parsed


def _extract_json_from_response(data: dict) -> Optional[dict]:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        return None

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return _sanitize_prediction(parsed)


async def call_gemini(client: httpx.AsyncClient, model: str, prompt: str) -> Optional[dict]:
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"

    payloads = [
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {
                "temperature": 0.25,
                "maxOutputTokens": 1400,
            },
        },
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.20,
                "maxOutputTokens": 1400,
                "responseMimeType": "application/json",
            },
        },
    ]

    for payload in payloads:
        for attempt in range(2):
            try:
                r = await client.post(url, json=payload, timeout=45.0)
                if r.status_code in (503, 429):
                    wait = 2 * (attempt + 1)
                    print(f"  ⏳ Gemini {model} {r.status_code}, retry {attempt+1}/2...")
                    await asyncio.sleep(wait)
                    continue
                if r.status_code >= 400:
                    break

                parsed = _extract_json_from_response(r.json())
                if parsed is not None:
                    return parsed
                break
            except Exception as e:
                print(f"  ⚠️ Gemini {model} crash: {repr(e)}")
                await asyncio.sleep(1.5)
    return None


async def predict_match_probas(client, home, away, league, kickoff, context=None):
    if not GEMINI_API_KEY:
        return None
    ctx_block = format_context(context, kickoff=str(kickoff or ""))
    prompt = build_prompt(home, away, league, ctx_block)

    for i, model in enumerate(GEMINI_MODELS):
        result = await call_gemini(client, model, prompt)
        if result is not None:
            return result
        if i < len(GEMINI_MODELS) - 1:
            print(f"  🔄 Bascule vers {GEMINI_MODELS[i+1]}")
    return None
