from enum import Enum
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class SportType(Enum):
    SOCCER = "soccer"
    BASKETBALL = "basketball"

class TicketCategory(Enum):
    ULTRA_SAFE = "Ultra Safe (Sécurité Max)"
    VIP = "VIP (Victoires & DNB)"
    VALUE = "Value Bets (Buts, Scores & Handicaps)"
    MARKETS = "Marchés Spéciaux (Corners & Mi-temps)"

class MatchData(BaseModel):
    match_id: str
    sport: SportType
    league: str
    match_date: datetime
    home_team: str
    away_team: str
    home_odds: float = 1.90
    draw_odds: float = 3.40
    away_odds: float = 3.80

class SimulationResult(BaseModel):
    match_id: str
    proba_home: float
    proba_draw: float
    proba_away: float
    most_likely_score: str
    proba_btts: float
    proba_over_1_5: float
    proba_over_2_5: float
    proba_over_3_5: float
    estimated_corners: float

class AIAuditReport(BaseModel):
    confidence_score: float
    justification: str
    is_approved: bool

class GeneratedTicket(BaseModel):
    category: TicketCategory
    match_id: str
    sport: SportType
    match_title: str
    bet_type: str
    odds: float
    ai_confidence: float
    ai_justification: str
