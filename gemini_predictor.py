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
_BASE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
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

    if (trap_warning or risk_level == "HIGH") and "match piège" not in analysis.lower():
        analysis = f"{analysis} (Match piège / risque élevé détecté).".strip()

    parsed["markets"] = clean_m
    parsed["analysis"] = analysis
    parsed["risk_level"] = risk_level
    parsed["trap_warning"] = trap_warning
    return parsed


def _extract_raw_json(data: dict) -> Optional[dict]:
    try:
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts") or []
        text_chunks = [
            str(p.get("text") or "")
            for p in parts
            if isinstance(p, dict) and not p.get("thought", False)
        ]
        if not text_chunks:
            text_chunks = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
        text = "".join(text_chunks).strip()
    except (KeyError, IndexError, TypeError):
        return None

    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    raw_json = text[start : end + 1] if end != -1 else text[start:]
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return None


def _extract_json_from_response(data: dict) -> Optional[dict]:
    parsed = _extract_raw_json(data)
    if parsed is None:
        return None
    return _sanitize_prediction(parsed)


async def call_gemini(client: httpx.AsyncClient, model: str, prompt: str, raw_mode: bool = False) -> Optional[dict]:
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"

    payloads = [
        (
            "search",
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"googleSearch": {}}],
                "generationConfig": {
                    "temperature": 0.25,
                    "maxOutputTokens": 4096,
                },
            },
        ),
        (
            "json",
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.20,
                    "maxOutputTokens": 4096,
                    "responseMimeType": "application/json",
                },
            },
        ),
    ]

    for mode_name, payload in payloads:
        for attempt in range(2):
            try:
                r = await client.post(url, json=payload, timeout=50.0)
                if r.status_code in (503, 429):
                    wait = 3 * (attempt + 1)
                    print(f"  ⏳ Gemini {model} ({mode_name}) HTTP {r.status_code}, retry {attempt+1}/2...")
                    await asyncio.sleep(wait)
                    continue
                if r.status_code >= 400:
                    err_snippet = re.sub(r"\s+", " ", r.text)[:180]
                    print(f"  ⚠️ Gemini {model} ({mode_name}) HTTP {r.status_code} : {err_snippet}")
                    break

                parsed = _extract_raw_json(r.json()) if raw_mode else _extract_json_from_response(r.json())
                if parsed is not None:
                    return parsed
                print(f"  ⚠️ Gemini {model} ({mode_name}) : réponse non-JSON")
                break
            except Exception as e:
                print(f"  ⚠️ Gemini {model} ({mode_name}) crash: {repr(e)}")
                await asyncio.sleep(1.5)
    return None


async def predict_match_probas(client, home, away, league, kickoff, context=None):
    if not GEMINI_API_KEY:
        print("  ❌ GEMINI_API_KEY absente")
        return None
    ctx_block = format_context(context, kickoff=str(kickoff or ""))
    prompt = build_prompt(home, away, league, ctx_block)

    for i, model in enumerate(GEMINI_MODELS):
        result = await call_gemini(client, model, prompt, raw_mode=False)
        if result is not None:
            return result
        if i < len(GEMINI_MODELS) - 1:
            print(f"  🔄 Bascule vers {GEMINI_MODELS[i+1]}")
    return None


async def explain_selected_pick_with_gemini(
    client: httpx.AsyncClient,
    home: str,
    away: str,
    league: str,
    kickoff: str,
    pick_label: str,
    proba_pct: float,
    odd: float,
    context: Optional[dict] = None,
) -> Optional[dict]:
    """
    Enrichissement éditorial & vérification finale ciblée sur le pari exact retenu dans le ticket VIP.
    Retourne {"analysis": str, "is_safe": bool}.
    """
    if not GEMINI_API_KEY:
        return None

    today_date = datetime.now(TZ).strftime("%d/%m/%Y")
    ctx_block = format_context(context, kickoff=str(kickoff or ""))
    prompt = f"""Tu es un analyste sportif professionnel d'élite (style Café Foot Pro : précis, chiffré, fluide et sans blabla).
Date : {today_date}. Vérifie via tes connaissances et la recherche web l'actualité exacte du match (classement, dynamique récente domicile/extérieur, absences confirmées, enjeu).

MATCH : {home} (Domicile) vs {away} (Extérieur) — Compétition : {league}
PARI RETENU POUR LE TICKET : "{pick_label}" (Confiance modèle : {proba_pct:.1f}%, Cote : {odd:.2f})
{ctx_block}

MISSION :
1. Vérifie si le pari "{pick_label}" est cohérent, solide et sans piège majeur (rotation massive, match sans enjeu, blessures clés contraires au pari).
2. Rédige une analyse riche et convaincante de 4 à 5 phrases (420 à 560 caractères) justifiant directement pourquoi "{pick_label}" est la meilleure option sur ce match :
   - Phrase 1 : Enjeu et dynamique actuelle au classement / dans la compétition.
   - Phrase 2 : Chiffres clés (rendement domicile/extérieur, xG attendus, série de buts ou solidité défensive, H2H).
   - Phrase 3 : Facteur terrain / joueur décisif ou point de vigilance tactique.
   - Phrase 4 : Conclusion claire et tranchée validant "{pick_label}" à {odd:.2f}.

Retourne UNIQUEMENT ce JSON valide :
{{
  "is_safe": true,
  "analysis": "Texte complet de 4 à 5 phrases aligné à 100% avec {pick_label}."
}}"""

    for model in GEMINI_MODELS[:2]:
        res = await call_gemini(client, model, prompt, raw_mode=True)
        if isinstance(res, dict) and isinstance(res.get("analysis"), str) and len(res["analysis"].strip()) >= 80:
            return {
                "is_safe": bool(res.get("is_safe", True)),
                "analysis": res["analysis"].strip(),
            }
    return None
