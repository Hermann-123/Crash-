"""
Marchés dérivés : Corners et Cartons.
Utilise les moyennes d'API-Football pour calculer des probas Poisson.
"""
from __future__ import annotations

import math


def _poisson_cdf(lam: float, k: int) -> float:
    """P(X >= k) pour une Poisson(lam)."""
    if k <= 0:
        return 1.0
    # P(X >= k) = 1 - P(X <= k-1)
    cum = 0.0
    for i in range(k):
        cum += (lam ** i) * math.exp(-lam) / math.factorial(i)
    return max(0.0, min(1.0, 1.0 - cum))


def compute_corners_probs(home_corners_avg: float, away_corners_avg: float,
                           league_corners_avg: float = 10.5) -> dict:
    """
    Retourne les probas corners :
    {"over_8.5": 0.62, "under_8.5": 0.38, "over_9.5": ..., "over_10.5": ..., ...}
    """
    if home_corners_avg <= 0 or away_corners_avg <= 0:
        # Fallback sur la moyenne du championnat
        home_corners_avg = league_corners_avg * 0.55
        away_corners_avg = league_corners_avg * 0.45

    total_lambda = home_corners_avg + away_corners_avg

    result = {
        "total_lambda": round(total_lambda, 2),
    }
    for line in (8.5, 9.5, 10.5, 11.5):
        threshold = int(math.ceil(line))
        p_over = _poisson_cdf(total_lambda, threshold)
        result[f"over_{line}"] = round(p_over, 4)
        result[f"under_{line}"] = round(1.0 - p_over, 4)

    # Corners par équipe
    for line in (4.5, 5.5, 6.5):
        threshold = int(math.ceil(line))
        result[f"home_over_{line}"] = round(_poisson_cdf(home_corners_avg, threshold), 4)
        result[f"away_over_{line}"] = round(_poisson_cdf(away_corners_avg, threshold), 4)

    return result


def compute_cards_probs(home_cards_avg: float, away_cards_avg: float,
                         league_cards_avg: float = 3.8) -> dict:
    """
    Retourne les probas cartons (jaunes uniquement) :
    {"over_2.5": 0.68, "over_3.5": 0.52, ...}
    """
    if home_cards_avg <= 0 or away_cards_avg <= 0:
        home_cards_avg = league_cards_avg * 0.5
        away_cards_avg = league_cards_avg * 0.5

    total_lambda = home_cards_avg + away_cards_avg

    result = {
        "total_lambda": round(total_lambda, 2),
    }
    for line in (2.5, 3.5, 4.5, 5.5):
        threshold = int(math.ceil(line))
        p_over = _poisson_cdf(total_lambda, threshold)
        result[f"over_{line}"] = round(p_over, 4)
        result[f"under_{line}"] = round(1.0 - p_over, 4)

    return result
