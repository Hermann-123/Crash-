"""
weekly_analyzer.py — Analyseur Multi-Jours & Commande /semaine pour VOLATILITY INDEX
Synchronisé avec quant_engine.py (ESPN + Bzzoiro + Dixon-Coles calibré + Gemini AI).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from quant_engine import (
    MARKET_LABELS,
    evaluate_selection_tiered,
    extract_odds_for_match,
    fetch_bzzoiro_matches,
    fetch_espn_prematch_context,
    fetch_market_odds,
    generate_gemini_analysis,
    resolve_league_config,
)

logger = logging.getLogger("volatility_index.weekly")


def analyze_upcoming_period(days: int = 7, max_matches: int = 5) -> list[dict[str, Any]]:
    """
    Analyse les meilleures opportunités sur les `days` prochains jours
    en appliquant les mêmes verrous anti-risque que le ticket VIP quotidien.
    """
    days = max(1, min(days, 14))
    max_matches = max(1, min(max_matches, 15))

    now_utc = datetime.now(timezone.utc)
    odds_cache: dict[str, list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []

    for offset in range(days):
        target_dt = now_utc + timedelta(days=offset)
        date_iso = target_dt.strftime("%Y-%m-%d")
        date_espn = target_dt.strftime("%Y%m%d")

        matches = fetch_bzzoiro_matches(date_iso)
        for match in matches:
            cfg = resolve_league_config(match.get("league", ""))
            espn_slug = cfg["espn"]
            odds_sport = cfg["odds"]

            if odds_sport not in odds_cache:
                odds_cache[odds_sport] = fetch_market_odds(odds_sport)

            match_odds = extract_odds_for_match(
                match["home_team"], match["away_team"], odds_cache[odds_sport]
            )
            if not match_odds:
                continue

            espn_ctx = fetch_espn_prematch_context(
                slug=espn_slug,
                date_yyyymmdd=date_espn,
                home_team=match["home_team"],
                away_team=match["away_team"],
            )

            best_for_match: dict[str, Any] | None = None
            for mkt_key, (target_odd, all_odds) in match_odds.items():
                if mkt_key == "X":
                    continue
                evaluated = evaluate_selection_tiered(
                    match=match,
                    espn_ctx=espn_ctx,
                    market_key=mkt_key,
                    target_odd=target_odd,
                    all_market_odds=all_odds,
                )
                if evaluated and (
                    best_for_match is None
                    or evaluated["ev"] > best_for_match["ev"]
                ):
                    best_for_match = evaluated

            if best_for_match:
                candidates.append(best_for_match)

    ranked = sorted(
        candidates,
        key=lambda x: (x["tier"] == "VIP_VALUE", x["ev"], x["calibrated_prob"]),
        reverse=True,
    )
    return ranked[:max_matches]


def format_weekly_report(picks: list[dict[str, Any]], days: int) -> str:
    """Formate le rapport Telegram pour la commande /semaine ou le bouton Top Semaine."""
    if not picks:
        return (
            f"📅 TOP SEMAINE ({days} jours)\n\n"
            "Aucune opportunité calibrée ne respecte nos seuils de sécurité sur cette période."
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  📅 TOP OPPORTUNITÉS ({days}J)  ",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 {len(picks)} sélections validées (ESPN + Bzzoiro + Dixon-Coles)",
        "",
    ]

    for idx, p in enumerate(picks, start=1):
        label = MARKET_LABELS.get(p["market"], p["market"])
        commentary = generate_gemini_analysis(p)
        lines.extend(
            [
                f"#{idx} • {p['match_name']} ({p['league']})",
                f"🎯 {label} @ {p['odd']} | Conf: {p['calibrated_prob']}% | EV: {p['ev']}",
                f"🏟️ {p['venue']} | Classement: {p['ranks']}",
                f"🧠 {commentary}",
                "───────────────────────",
            ]
        )

    return "\n".join(lines)
