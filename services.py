import numpy as np
from scipy.stats import poisson
from datetime import datetime
from collections import defaultdict
from typing import List, Dict

from app.models import (
    MatchData,
    TeamFormSnapshot,
    SimulationResult,
    AIAuditReport,
    PickCandidate,
    GeneratedTicket,
    TicketCategory,
)
from app.core import settings


class SmartPoissonEngine:
    def __init__(self, max_goals: int = 7):
        self.max_goals = max_goals

    def _remove_margin_1x2(self, h: float, d: float, a: float):
        ph = 1 / h
        pd = 1 / d
        pa = 1 / a
        total = ph + pd + pa
        return ph / total, pd / total, pa / total

    def _market_anchor(self, match: MatchData):
        fh, fd, fa = self._remove_margin_1x2(match.home_odds, match.draw_odds, match.away_odds)

        strength = fh - fa
        home_lambda = 1.25 + (strength * 1.20)
        away_lambda = 1.00 - (strength * 0.95)

        if fd >= 0.29:
            home_lambda -= 0.08
            away_lambda -= 0.08

        return max(0.25, home_lambda), max(0.25, away_lambda)

    def _form_adjustment(self, home_form: TeamFormSnapshot, away_form: TeamFormSnapshot, base_home: float, base_away: float):
        home_attack = 1 + ((home_form.avg_goals_for - 1.2) * 0.18)
        home_def_opp = 1 + ((away_form.avg_goals_against - 1.2) * 0.16)

        away_attack = 1 + ((away_form.avg_goals_for - 1.0) * 0.18)
        away_def_opp = 1 + ((home_form.avg_goals_against - 1.0) * 0.16)

        home_points_boost = 1 + ((home_form.last5_points - 7) * 0.015)
        away_points_boost = 1 + ((away_form.last5_points - 7) * 0.015)

        home_split = 1 + ((home_form.home_avg_goals_for - 1.3) * 0.10)
        away_split = 1 + ((away_form.away_avg_goals_for - 1.0) * 0.10)

        home_lambda = base_home * home_attack * home_def_opp * home_points_boost * home_split
        away_lambda = base_away * away_attack * away_def_opp * away_points_boost * away_split

        home_lambda = max(0.20, min(home_lambda, 3.20))
        away_lambda = max(0.20, min(away_lambda, 3.20))

        return round(home_lambda, 3), round(away_lambda, 3)

    def simulate(self, match: MatchData, home_form: TeamFormSnapshot, away_form: TeamFormSnapshot) -> SimulationResult:
        base_home, base_away = self._market_anchor(match)
        home_lambda, away_lambda = self._form_adjustment(home_form, away_form, base_home, base_away)

        matrix = np.zeros((self.max_goals + 1, self.max_goals + 1))

        for i in range(self.max_goals + 1):
            for j in range(self.max_goals + 1):
                matrix[i, j] = poisson.pmf(i, home_lambda) * poisson.pmf(j, away_lambda)

        matrix /= matrix.sum()

        p_home = float(np.tril(matrix, -1).sum()) * 100
        p_draw = float(np.diag(matrix).sum()) * 100
        p_away = float(np.triu(matrix, 1).sum()) * 100

        p_over_2_5 = float(sum(
            matrix[i, j]
            for i in range(self.max_goals + 1)
            for j in range(self.max_goals + 1)
            if i + j >= 3
        )) * 100

        p_btts = float(matrix[1:, 1:].sum()) * 100

        best_idx = np.argmax(matrix)
        sx, sy = np.unravel_index(best_idx, matrix.shape)

        return SimulationResult(
            match_id=match.match_id,
            home_lambda=round(home_lambda, 3),
            away_lambda=round(away_lambda, 3),
            total_lambda=round(home_lambda + away_lambda, 3),
            proba_home=round(p_home, 2),
            proba_draw=round(p_draw, 2),
            proba_away=round(p_away, 2),
            proba_over_2_5=round(p_over_2_5, 2),
            proba_under_2_5=round(100 - p_over_2_5, 2),
            proba_btts_yes=round(p_btts, 2),
            proba_btts_no=round(100 - p_btts, 2),
            most_likely_score=f"{sx}-{sy}",
            draw_risk=round(p_draw, 2),
        )


class ConfidenceEngine:
    def score(self, match: MatchData, sim: SimulationResult, home_form: TeamFormSnapshot, away_form: TeamFormSnapshot) -> AIAuditReport:
        base_outcome = max(sim.proba_home, sim.proba_away)
        edge_form = abs(home_form.last5_points - away_form.last5_points)
        goal_gap = abs(home_form.avg_goals_for - away_form.avg_goals_for)

        risk_flags = []
        score = 0
        score += min(40, base_outcome * 0.55)
        score += min(15, edge_form * 1.6)
        score += min(10, goal_gap * 6.0)
        score += min(10, max(0, 30 - sim.draw_risk) * 0.33)

        if sim.draw_risk >= 29:
            risk_flags.append("DRAW_RISK_HIGH")
            score -= 8

        if abs(sim.proba_home - sim.proba_away) < 7:
            risk_flags.append("BALANCED")
            score -= 6

        if home_form.matches_sampled < 3 or away_form.matches_sampled < 3:
            risk_flags.append("LOW_DATA")
            score -= 10

        score = max(0, min(100, round(score, 1)))
        approved = score >= settings.MIN_CONFIDENCE_PERCENT

        favored_side = "domicile" if sim.proba_home >= sim.proba_away else "extérieur"
        justification = (
            f"Score probable {sim.most_likely_score}, intensité {sim.home_lambda:.2f}-{sim.away_lambda:.2f}, "
            f"avantage {favored_side}, risque de nul {sim.draw_risk:.1f}%."
        )

        return AIAuditReport(
            confidence_score=score,
            justification=justification,
            is_approved=approved,
            risk_flags=risk_flags,
        )


class MarketBuilder:
    def build(self, match: MatchData, sim: SimulationResult, ai: AIAuditReport) -> List[PickCandidate]:
        picks = []

        def add_pick(market, selection, odds, model_p):
            implied = round((1 / odds) * 100, 2)
            fair_odds = round(100 / model_p, 2) if model_p > 0 else 999.0
            edge = round(model_p - implied, 2)
            ev = round((model_p / 100.0) * odds - 1, 4)

            if model_p < settings.MIN_VALUE_PROBABILITY:
                return
            if edge < settings.MIN_EDGE_PERCENT:
                return
            if ai.confidence_score < settings.MIN_CONFIDENCE_PERCENT:
                return

            cat = TicketCategory.SAFE if model_p >= settings.MIN_SAFE_PROBABILITY and ai.confidence_score >= 65 else TicketCategory.VALUE

            picks.append(
                PickCandidate(
                    match_id=match.match_id,
                    match_title=f"{match.home_team} vs {match.away_team}",
                    market=market,
                    selection=selection,
                    bookmaker_odds=round(odds, 2),
                    model_probability=round(model_p, 2),
                    implied_probability=implied,
                    fair_odds=fair_odds,
                    edge=edge,
                    expected_value=ev,
                    confidence=ai.confidence_score,
                    justification=ai.justification,
                    category=cat,
                    kickoff_at=match.kickoff_at,
                    bookmaker=match.bookmaker,
                    league=match.league,
                    score_hint=sim.most_likely_score,
                )
            )

        if sim.proba_home > sim.proba_away:
            add_pick("1X2", f"{match.home_team} gagne", match.home_odds, sim.proba_home)
        else:
            add_pick("1X2", f"{match.away_team} gagne", match.away_odds, sim.proba_away)

        return picks


class TicketFactory:
    def build_portfolio(self, picks: List[PickCandidate]) -> Dict[TicketCategory, List[GeneratedTicket]]:
        grouped = defaultdict(list)

        safe = sorted(
            [p for p in picks if p.category == TicketCategory.SAFE],
            key=lambda x: (x.confidence, x.edge, x.expected_value),
            reverse=True,
        )

        value = sorted(
            [p for p in picks if p.category == TicketCategory.VALUE],
            key=lambda x: (x.edge, x.expected_value, x.confidence),
            reverse=True,
        )

        if safe:
            grouped[TicketCategory.SAFE].append(self._make_ticket("🛡 Ticket Safe Premium", TicketCategory.SAFE, safe[:2]))

        if value:
            grouped[TicketCategory.VALUE].append(self._make_ticket("🚀 Ticket Value Premium", TicketCategory.VALUE, value[:2]))

        return dict(grouped)

    def _make_ticket(self, title: str, category: TicketCategory, picks: List[PickCandidate]) -> GeneratedTicket:
        total_odds = 1.0
        proba_combo = 1.0
        lines = []

        for i, p in enumerate(picks, 1):
            total_odds *= p.bookmaker_odds
            proba_combo *= (p.model_probability / 100.0)
            lines.append(
                f"{i}. {p.match_title}\n"
                f"   - Sélection: {p.selection}\n"
                f"   - Cote book: {p.bookmaker_odds}\n"
                f"   - Proba modèle: {p.model_probability}%\n"
                f"   - Cote juste: {p.fair_odds}\n"
                f"   - Edge: {p.edge}%\n"
                f"   - EV: {p.expected_value}\n"
                f"   - Hint score: {p.score_hint}"
            )

        summary = "\n\n".join(lines)
        confidence = round(proba_combo * 100, 2)

        return GeneratedTicket(
            category=category,
            ticket_id=f"{category}_{'_'.join([p.match_id for p in picks])}",
            title=title,
            total_odds=round(total_odds, 2),
            confidence=confidence,
            created_at=datetime.utcnow(),
            picks=picks,
            summary=summary,
        )
