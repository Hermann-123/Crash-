import os
import logging
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any, Dict, List, Optional
import requests

from gemini_predictor import predict_match_probas, explain_selected_pick_with_gemini

logger = logging.getLogger("VOLATILITY_WEEKLY")

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# Plage de cote cible pour les tickets combinés / simples VIP
MIN_TARGET_ODD = 2.00
MAX_TARGET_ODD = 3.50
MAX_LEGS = 3
MIN_SINGLE_ODD = 1.35
MAX_SINGLE_ODD = 2.40
MIN_EV_THRESHOLD = 1.05
MIN_PROB_THRESHOLD = 0.56


def _fetch_upcoming_matches_fallback(days_ahead: int = 7) -> List[Dict[str, Any]]:
    """
    Récupère les matchs à venir sur `days_ahead` jours si `main.py` ne passe pas
    directement sa liste de matchs pré-chargés.
    """
    if not ODDS_API_KEY:
        return []

    now = datetime.now(timezone.utc)
    end_date = now + timedelta(days=days_ahead)
    leagues = [
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_italy_serie_a",
        "soccer_germany_bundesliga",
        "soccer_france_ligue_one",
        "soccer_uefa_champs_league",
        "soccer_uefa_europa_league",
    ]
    collected: List[Dict[str, Any]] = []

    for sport_key in leagues:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
            "commenceTimeFrom": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commenceTimeTo": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                continue
            for ev in resp.json():
                home = ev.get("home_team", "")
                away = ev.get("away_team", "")
                odds_map: Dict[str, float] = {}
                for bm in ev.get("bookmakers", [])[:1]:
                    for mkt in bm.get("markets", []):
                        if mkt.get("key") == "h2h":
                            for out in mkt.get("outcomes", []):
                                if out["name"] == home:
                                    odds_map["1"] = float(out["price"])
                                elif out["name"] == away:
                                    odds_map["2"] = float(out["price"])
                                elif out["name"].lower() == "draw":
                                    odds_map["X"] = float(out["price"])
                        elif mkt.get("key") == "totals":
                            for out in mkt.get("outcomes", []):
                                if out.get("point") == 2.5:
                                    if out["name"].lower() == "over":
                                        odds_map["over_25"] = float(out["price"])
                                    elif out["name"].lower() == "under":
                                        odds_map["under_25"] = float(out["price"])
                if odds_map:
                    collected.append(
                        {
                            "match_id": ev.get("id", f"{home}_{away}"),
                            "home_team": home,
                            "away_team": away,
                            "league": ev.get("sport_title", sport_key),
                            "match_date": ev.get("commence_time", ""),
                            "odds": odds_map,
                        }
                    )
        except Exception as e:
            logger.warning("Erreur récupération ligue %s : %s", sport_key, e)

    return collected


def _extract_best_market_for_match(
    match: Dict[str, Any],
    calibrated_probas: Dict[str, Any],
    min_ev: float = MIN_EV_THRESHOLD,
    min_prob: float = MIN_PROB_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """
    Sélectionne le meilleur marché à EV positive sur un match donné
    (1, 2, Over 2.5, Under 2.5) en vérifiant le filtre anti-piège.
    """
    if calibrated_probas.get("is_trap", False):
        logger.info(
            "Match écarté (anti-piège) : %s vs %s (%s)",
            match.get("home_team"),
            match.get("away_team"),
            calibrated_probas.get("trap_reason", "risque détecté"),
        )
        return None

    odds = match.get("odds", {})
    home = match.get("home_team", "Domicile")
    away = match.get("away_team", "Extérieur")

    candidates = [
        ("1", f"Victoire {home} (1)", calibrated_probas.get("home_win", 0.0), odds.get("1", 0.0)),
        ("2", f"Victoire {away} (2)", calibrated_probas.get("away_win", 0.0), odds.get("2", 0.0)),
        ("over_25", "Plus de 2.5 buts", calibrated_probas.get("over_25", 0.0), odds.get("over_25", 0.0)),
        ("under_25", "Moins de 2.5 buts", calibrated_probas.get("under_25", 0.0), odds.get("under_25", 0.0)),
    ]

    best_pick: Optional[Dict[str, Any]] = None
    best_score = -1.0

    for m_key, label, prob, odd in candidates:
        if not (MIN_SINGLE_ODD <= odd <= MAX_SINGLE_ODD):
            continue
        if prob < min_prob:
            continue
        ev = round(prob * odd, 3)
        if ev < min_ev:
            continue

        # Score pondéré EV × probabilité pour favoriser la régularité
        score = ev * prob
        if score > best_score:
            best_score = score
            best_pick = {
                "match_id": match.get("match_id", f"{home}_{away}"),
                "home_team": home,
                "away_team": away,
                "league": match.get("league", ""),
                "match_date": match.get("match_date", ""),
                "market_key": m_key,
                "selection": label,
                "odd": round(odd, 2),
                "probability": round(prob, 4),
                "confidence": round(prob * 100, 1),
                "ev": ev,
                "stats_context": {
                    "dc_probas": match.get("dc_probas", {}),
                    "stats": match.get("stats", {}),
                    "key_facts": calibrated_probas.get("key_facts", []),
                },
            }

    return best_pick


def build_dynamic_tickets(
    value_picks: List[Dict[str, Any]],
    min_odd: float = MIN_TARGET_ODD,
    max_odd: float = MAX_TARGET_ODD,
    max_legs: int = MAX_LEGS,
    max_tickets: int = 3,
) -> List[Dict[str, Any]]:
    """
    Assemble dynamiquement des tickets de 1, 2 ou 3 sélections maximum
    dans la fenêtre [min_odd, max_odd], sans jamais forcer 3 matchs si 1 ou 2 suffisent.
    """
    if not value_picks:
        return []

    # Tri par EV × probabilité décroissante
    sorted_picks = sorted(
        value_picks, key=lambda x: x["ev"] * x["probability"], reverse=True
    )[:12]

    all_combos: List[Dict[str, Any]] = []

    for size in range(1, max_legs + 1):
        for combo in combinations(sorted_picks, size):
            # Éviter deux paris sur le même match
            match_ids = {p["match_id"] for p in combo}
            if len(match_ids) != size:
                continue

            total_odd = 1.0
            total_prob = 1.0
            for leg in combo:
                total_odd *= leg["odd"]
                total_prob *= leg["probability"]

            total_odd = round(total_odd, 2)
            if not (min_odd <= total_odd <= max_odd):
                continue

            combined_ev = round(total_prob * total_odd, 2)
            # Bonus de compacité : à EV égale, on préfère un simple ou un duo à un trio
            compactness_bonus = {1: 1.08, 2: 1.04, 3: 1.00}.get(size, 1.0)
            ranking_score = combined_ev * total_prob * compactness_bonus

            all_combos.append(
                {
                    "legs": list(combo),
                    "leg_count": size,
                    "total_odd": total_odd,
                    "estimated_win_rate": round(total_prob * 100, 1),
                    "ev": combined_ev,
                    "ranking_score": ranking_score,
                }
            )

    all_combos.sort(key=lambda c: c["ranking_score"], reverse=True)

    selected_tickets: List[Dict[str, Any]] = []
    used_match_ids = set()

    for combo in all_combos:
        combo_ids = {leg["match_id"] for leg in combo["legs"]}
        # Diversifie les matchs d'un ticket hebdomadaire à l'autre
        if combo_ids & used_match_ids:
            continue
        selected_tickets.append(combo)
        used_match_ids.update(combo_ids)
        if len(selected_tickets) >= max_tickets:
            break

    return selected_tickets


def format_weekly_telegram_message(tickets: List[Dict[str, Any]]) -> str:
    """Formate les tickets hebdomadaires pour Telegram avec analyses IA verrouillées."""
    if not tickets:
        return (
            "⚡ *VOLATILITY INDEX • RADAR HEBDO*\n"
            "Aucune opportunité à EV positive sécurisée détectée sur la fenêtre actuelle."
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "     ⚡ *VOLATILITY INDEX* ⚡",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "📅 *Analyse Hebdomadaire VIP • Multi-Jours*",
        "",
    ]

    for t_idx, ticket in enumerate(tickets, start=1):
        leg_count = ticket["leg_count"]
        label_type = {1: "Simple Haute Valeur", 2: "Duo Haute Confiance", 3: "Trio Calibré"}.get(
            leg_count, f"Combiné {leg_count} sélections"
        )
        lines.extend(
            [
                f"🛡️ *TICKET #{t_idx} • {label_type} ({leg_count} sélection{'s' if leg_count > 1 else ''})*",
                f"🎫 Cote totale : *{ticket['total_odd']:.2f}* | 📈 Réussite est. : *{ticket['estimated_win_rate']}%* | 💎 EV : *{ticket['ev']}*",
                "",
            ]
        )

        for idx, leg in enumerate(ticket["legs"], start=1):
            analysis = explain_selected_pick_with_gemini(
                match_data=leg,
                pick_index=idx,
            )
            lines.extend(
                [
                    f"┌─ *#{idx} {leg['home_team']} vs {leg['away_team']}*",
                    f"│ 🏆 {leg['league']} • 🕒 {leg['match_date']}",
                    f"│ 🎯 *{leg['selection']}*",
                    f"│ 📊 Confiance : *{leg['confidence']}%* | 💰 Cote : *{leg['odd']:.2f}*",
                    f"│ 🧠 {analysis}",
                    "└─────────────",
                    "",
                ]
            )

    lines.append("⚠️ _Analyse statistique bayésienne. Gestion de bankroll recommandée (1-3% par ticket)._")
    return "\n".join(lines)


def analyze_weekly_top_matches(
    matches: Optional[List[Dict[str, Any]]] = None,
    *,
    days_ahead: int = 7,
    min_odd: float = MIN_TARGET_ODD,
    max_odd: float = MAX_TARGET_ODD,
    max_legs: int = MAX_LEGS,
    max_tickets: int = 2,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Point d'entrée principal appelé par `main.py` pour l'analyse multi-jours / hebdomadaire.
    Retourne les meilleurs picks filtrés, les tickets assemblés et le texte Telegram prêt à l'envoi.
    """
    pool = matches if matches is not None else _fetch_upcoming_matches_fallback(days_ahead=days_ahead)
    value_picks: List[Dict[str, Any]] = []

    for match in pool[:25]:
        calibrated = predict_match_probas(match)
        best_pick = _extract_best_market_for_match(match, calibrated)
        if best_pick:
            value_picks.append(best_pick)

    tickets = build_dynamic_tickets(
        value_picks=value_picks,
        min_odd=min_odd,
        max_odd=max_odd,
        max_legs=max_legs,
        max_tickets=max_tickets,
    )

    formatted_text = format_weekly_telegram_message(tickets)

    return {
        "analyzed_matches_count": len(pool),
        "value_picks": value_picks,
        "tickets": tickets,
        "formatted_message": formatted_text,
    }
