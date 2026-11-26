import os
import re
import json
import logging
import requests
from typing import Any, Dict, List, Optional

logger = logging.getLogger("VOLATILITY_GEMINI")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Tentaive d'import du SDK officiel google-genai (avec fallback REST automatique)
try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False


def _extract_json_block(text: str) -> Dict[str, Any]:
    """Extrait proprement un objet JSON depuis une réponse texte ou Markdown."""
    if not text:
        return {}
    cleaned = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Impossible de parser le JSON Gemini : %s", cleaned[:200])
        return {}


def _call_gemini(
    prompt: str,
    use_search: bool = True,
    temperature: float = 0.35,
    max_tokens: int = 900,
) -> str:
    """Appelle Gemini avec Google Search Grounding (SDK puis fallback REST)."""
    if not GEMINI_API_KEY:
        logger.error("Clé GEMINI_API_KEY manquante dans les variables d'environnement.")
        return ""

    # 1. Via le SDK google-genai
    if HAS_GENAI_SDK:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            config_kwargs: Dict[str, Any] = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if use_search:
                config_kwargs["tools"] = [{"google_search": {}}]

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logger.warning("Fallback REST activé suite à erreur SDK Gemini : %s", e)

    # 2. Fallback direct via API REST v1beta
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    try:
        resp = requests.post(url, json=payload, timeout=25)
        if resp.status_code != 200 and use_search:
            # Réessai sans outil de recherche si le modèle/compte restreint le grounding
            payload.pop("tools", None)
            resp = requests.post(url, json=payload, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts).strip()
    except Exception as e:
        logger.error("Erreur REST Gemini : %s", e)

    return ""


def bayesian_smooth_probas(
    model_probas: Dict[str, float],
    odds: Dict[str, float],
    market_weight: float = 0.35,
) -> Dict[str, float]:
    """
    Recalibre les probabilités IA / Dixon-Coles avec les probabilités implicites
    du marché (dé-vigées) pour éviter les surestimations d'EV.
    """
    smoothed = dict(model_probas)

    # Normalisation 1X2 si les 3 cotes sont présentes
    o1, ox, o2 = odds.get("1", 0), odds.get("X", 0), odds.get("2", 0)
    if o1 > 1.0 and ox > 1.0 and o2 > 1.0:
        inv_sum = (1.0 / o1) + (1.0 / ox) + (1.0 / o2)
        imp_1 = (1.0 / o1) / inv_sum
        imp_x = (1.0 / ox) / inv_sum
        imp_2 = (1.0 / o2) / inv_sum

        w_m = max(0.0, min(0.7, market_weight))
        w_s = 1.0 - w_m

        p1 = w_s * model_probas.get("home_win", imp_1) + w_m * imp_1
        px = w_s * model_probas.get("draw", imp_x) + w_m * imp_x
        p2 = w_s * model_probas.get("away_win", imp_2) + w_m * imp_2
        total_1x2 = p1 + px + p2
        if total_1x2 > 0:
            smoothed["home_win"] = round(p1 / total_1x2, 4)
            smoothed["draw"] = round(px / total_1x2, 4)
            smoothed["away_win"] = round(p2 / total_1x2, 4)

    # Normalisation Over / Under 2.5
    ou_over, ou_under = odds.get("over_25", 0), odds.get("under_25", 0)
    if ou_over > 1.0 and ou_under > 1.0:
        inv_ou = (1.0 / ou_over) + (1.0 / ou_under)
        imp_over = (1.0 / ou_over) / inv_ou
        imp_under = (1.0 / ou_under) / inv_ou

        w_m = max(0.0, min(0.7, market_weight))
        w_s = 1.0 - w_m

        po = w_s * model_probas.get("over_25", imp_over) + w_m * imp_over
        pu = w_s * model_probas.get("under_25", imp_under) + w_m * imp_under
        tot_ou = po + pu
        if tot_ou > 0:
            smoothed["over_25"] = round(po / tot_ou, 4)
            smoothed["under_25"] = round(pu / tot_ou, 4)

    return smoothed


def predict_match_probas(
    match_data: Optional[Dict[str, Any]] = None,
    *,
    home_team: str = "",
    away_team: str = "",
    league: str = "",
    match_date: str = "",
    odds: Optional[Dict[str, float]] = None,
    dc_probas: Optional[Dict[str, float]] = None,
    stats_context: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Évalue un match via Gemini + Google Search et retourne les probabilités calibrées,
    le statut anti-piège (is_trap) et les facteurs clés.
    Compatible avec un appel par dictionnaire (match_data) ou par arguments nommés.
    """
    data = dict(match_data or {})
    home = home_team or data.get("home_team") or data.get("home", "")
    away = away_team or data.get("away_team") or data.get("away", "")
    comp = league or data.get("league") or data.get("competition", "Football")
    date_str = match_date or data.get("match_date") or data.get("commence_time", "")
    match_odds = odds or data.get("odds", {})
    base_dc = dc_probas or data.get("dc_probas") or data.get("probas", {})
    extra_stats = stats_context or data.get("stats_context") or data.get("stats", {})

    # Probabilités par défaut issues de Dixon-Coles si disponibles
    default_probas = {
        "home_win": float(base_dc.get("home_win", 0.40)),
        "draw": float(base_dc.get("draw", 0.28)),
        "away_win": float(base_dc.get("away_win", 0.32)),
        "over_25": float(base_dc.get("over_25", 0.50)),
        "under_25": float(base_dc.get("under_25", 0.50)),
        "btts_yes": float(base_dc.get("btts_yes", 0.50)),
        "btts_no": float(base_dc.get("btts_no", 0.50)),
    }

    prompt = f"""Tu es un analyste quantitatif football pour le moteur VOLATILITY INDEX.
Effectue une recherche web rapide sur l'actualité récente de ce match :
- Match : {home} vs {away}
- Compétition : {comp}
- Date : {date_str}
- Cotes bookmakers : {json.dumps(match_odds, ensure_ascii=False)}
- Modèle statistique initial (Dixon-Coles / xG) : {json.dumps(default_probas, ensure_ascii=False)}
- Contexte statistique : {json.dumps(extra_stats, ensure_ascii=False)}

Vérifie impérativement :
1. Blessures majeures, suspensions ou rotation d'effectif annoncée.
2. Enjeu réel du match (qualification déjà acquise, derby, match couperet, fatigue européenne).
3. Détection de "match piège" (is_trap = true si rotation massive, équipe déjà qualifiée/éliminée sans motivation, météo extrême ou incohérence majeure entre stats et news).

Réponds UNIQUEMENT avec un objet JSON valide suivant exactement ce schéma :
{{
  "home_win": 0.00,
  "draw": 0.00,
  "away_win": 0.00,
  "over_25": 0.00,
  "under_25": 0.00,
  "btts_yes": 0.00,
  "btts_no": 0.00,
  "is_trap": false,
  "trap_reason": "",
  "confidence": 75.0,
  "key_facts": ["fait 1", "fait 2"]
}}"""

    raw_text = _call_gemini(prompt, use_search=True, temperature=0.2, max_tokens=700)
    parsed = _extract_json_block(raw_text)

    if not parsed:
        smoothed = bayesian_smooth_probas(default_probas, match_odds)
        return {
            **smoothed,
            "is_trap": False,
            "trap_reason": "",
            "confidence": round(max(smoothed.get("home_win", 0.5), smoothed.get("away_win", 0.5), smoothed.get("under_25", 0.5), smoothed.get("over_25", 0.5)) * 100, 1),
            "key_facts": [],
        }

    raw_probas = {
        "home_win": float(parsed.get("home_win", default_probas["home_win"])),
        "draw": float(parsed.get("draw", default_probas["draw"])),
        "away_win": float(parsed.get("away_win", default_probas["away_win"])),
        "over_25": float(parsed.get("over_25", default_probas["over_25"])),
        "under_25": float(parsed.get("under_25", default_probas["under_25"])),
        "btts_yes": float(parsed.get("btts_yes", default_probas["btts_yes"])),
        "btts_no": float(parsed.get("btts_no", default_probas["btts_no"])),
    }

    # Lissage bayésien avec les cotes du marché
    calibrated = bayesian_smooth_probas(raw_probas, match_odds, market_weight=0.35)

    return {
        **calibrated,
        "is_trap": bool(parsed.get("is_trap", False)),
        "trap_reason": str(parsed.get("trap_reason", "")).strip(),
        "confidence": float(parsed.get("confidence", 70.0)),
        "key_facts": parsed.get("key_facts", []),
    }


def explain_selected_pick_with_gemini(
    match_data: Optional[Dict[str, Any]] = None,
    *,
    home_team: str = "",
    away_team: str = "",
    league: str = "",
    selection: str = "",
    market: str = "",
    odd: float = 0.0,
    probability: float = 0.0,
    stats_context: Optional[Dict[str, Any]] = None,
    pick_index: int = 1,
    **kwargs: Any,
) -> str:
    """
    Génère une analyse fluide en 4 temps (style Café Foot) strictement verrouillée
    sur le marché sélectionné (1X2, Under/Over, BTTS, Double Chance) et sans intro répétitive.
    """
    data = dict(match_data or {})
    home = home_team or data.get("home_team") or data.get("home", "Équipe Domicile")
    away = away_team or data.get("away_team") or data.get("away", "Équipe Extérieur")
    comp = league or data.get("league") or data.get("competition", "")
    chosen_pick = selection or market or data.get("selection") or data.get("market", "")
    chosen_odd = odd or float(data.get("odd", 0.0))
    chosen_prob = probability or float(data.get("probability", data.get("confidence", 0.0)))
    if chosen_prob <= 1.0 and chosen_prob > 0:
        chosen_prob = round(chosen_prob * 100, 1)
    context = stats_context or data.get("stats_context") or data.get("stats", {})

    opening_styles = {
        1: "Commence directement par la dynamique du match ou l'enjeu sportif du soir.",
        2: "Commence directement par un chiffre clé (xG, série défensive/offensive ou écart statistique).",
        3: "Commence directement par le rapport de force tactique entre les deux équipes.",
    }
    style_directive = opening_styles.get(pick_index, opening_styles[2])

    prompt = f"""Rédige une analyse courte et percutante en français (3 à 4 phrases maximum, 65 à 95 mots) pour un ticket VIP Telegram.

DONNÉES EXACTES DU PARI RETENU :
- Rencontre : {home} vs {away} ({comp})
- PARI SÉLECTIONNÉ : {chosen_pick}
- Cote : {chosen_odd} | Probabilité modèle calibrée : {chosen_prob}%
- Données statistiques (xG, Dixon-Coles, H2H, forme) : {json.dumps(context, ensure_ascii=False)}

RÈGLES IMPÉRATIVES :
1. VERROUILLAGE MARCHÉ : Ton argumentaire doit justifier EXCLUSIVEMENT le pari "{chosen_pick}".
   - Si le pari est "Moins de 2.5 buts" (Under), parle de maîtrise défensive, faible volume d'occasions (xG), gestion du tempo ou bloc bas — JAMAIS d'une pluie de buts ni d'une défense qui va exploser.
   - Si le pari est "Plus de 2.5 buts" (Over), parle de projection offensive, déséquilibres et xG élevés.
   - Si le pari est une victoire (1 ou 2) ou Double Chance, justifie la supériorité ou la solidité de l'équipe visée.
2. INTERDICTION ABSOLUE d'utiliser "Salut les pronos", "Bonjour", "Voici l'analyse" ou toute formule d'introduction générique.
3. {style_directive}
4. Structure fluide en un seul paragraphe : constat fort -> appui statistique (xG / Dixon-Coles) -> contexte/enjeu -> brève nuance de vigilance.
5. Pas de puces, pas de titres, pas d'emojis dans le paragraphe."""

    explanation = _call_gemini(prompt, use_search=True, temperature=0.4, max_tokens=300)

    if explanation:
        # Nettoyage de sécurité si le modèle glisse malgré tout une salutation
        explanation = re.sub(
            r"^(Salut les pronos\s*[!,.:-]*\s*|Bonjour\s*[!,.:-]*\s*)",
            "",
            explanation,
            flags=re.IGNORECASE,
        ).strip()
        return explanation

    # Fallback déterministe cohérent avec le marché si l'API est indisponible
    pick_lower = chosen_pick.lower()
    if "moins" in pick_lower or "under" in pick_lower:
        return (
            f"Le profil statistique de ce {home} - {away} pointe vers une rencontre fermée et tactique, "
            f"avec une probabilité estimée à {chosen_prob}% pour le {chosen_pick}. "
            f"Les projections xG et le modèle Dixon-Coles confirment un rythme maîtrisé où les deux blocs limitent les espaces dans l'axe. "
            f"Seul un but très précoce sur coup de pied arrêté pourrait forcer l'équipe menée à se découvrir."
        )
    if "plus" in pick_lower or "over" in pick_lower:
        return (
            f"Les indicateurs offensifs convergent vers un duel ouvert entre {home} et {away}, "
            f"validant le scénario {chosen_pick} à {chosen_prob}% de réussite estimée. "
            f"La production d'xG des deux formations laisse entrevoir des transitions rapides et plusieurs situations franches. "
            f"La vigilance portera uniquement sur l'efficacité dans le dernier geste en première période."
        )
    return (
        f"Dans ce duel de {comp}, les métriques avancées placent {chosen_pick} en position de force "
        f"avec une probabilité calibrée de {chosen_prob}%. "
        f"L'écart de niveau mesuré par Dixon-Coles et la dynamique récente justifient la valeur prise à {chosen_odd}, "
        f"sous réserve d'une gestion sérieuse des temps faibles."
    )
