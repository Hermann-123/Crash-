from enum import Enum
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class SportType(str, Enum):
    SOCCER = "soccer"


class TicketCategory(str, Enum):
    SAFE = "SAFE"
    VALUE = "VALUE"


class MatchData(BaseModel):
    match_id: str
    sport: SportType
    league: str
    kickoff_at: datetime
    home_team: str
    away_team: str
    home_odds: float
    draw_odds: float
    away_odds: float
    bookmaker: Optional[str] = None
    country: Optional[str] = None


class TeamFormSnapshot(BaseModel):
    team_name: str
    league: Optional[str] = None
    last5_points: int = 0
    last5_goals_for: int = 0
    last5_goals_against: int = 0
    avg_goals_for: float = 0.0
    avg_goals_against: float = 0.0
    home_avg_goals_for: float = 0.0
    home_avg_goals_against: float = 0.0
    away_avg_goals_for: float = 0.0
    away_avg_goals_against: float = 0.0
    matches_sampled: int = 0
    rank: Optional[int] = None


class SimulationResult(BaseModel):
    match_id: str
    home_lambda: float
    away_lambda: float
    total_lambda: float
    proba_home: float
    proba_draw: float
    proba_away: float
    proba_over_2_5: float
    proba_under_2_5: float
    proba_btts_yes: float
    proba_btts_no: float
    most_likely_score: str
    draw_risk: float


class AIAuditReport(BaseModel):
    confidence_score: float
    justification: str
    is_approved: bool
    risk_flags: List[str] = []


class PickCandidate(BaseModel):
    match_id: str
    match_title: str
    market: str
    selection: str
    bookmaker_odds: float
    model_probability: float
    implied_probability: float
    fair_odds: float
    edge: float
    expected_value: float
    confidence: float
    justification: str
    category: TicketCategory
    kickoff_at: datetime
    bookmaker: Optional[str] = None
    league: Optional[str] = None
    score_hint: Optional[str] = None


class GeneratedTicket(BaseModel):
    category: TicketCategory
    ticket_id: str
    title: str
    total_odds: float
    confidence: float
    created_at: datetime
    picks: List[PickCandidate]
    summary: str
