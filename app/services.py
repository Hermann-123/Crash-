# app/services.py
#
# WALLSTREET OS - PROFESSIONAL ANALYSIS ENGINE (QUANT FUND V4)
# ------------------------------------------------------------
import asyncio
import json
import itertools
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

import httpx
import numpy as np
from scipy.stats import poisson

from app.models import MatchData, SimulationResult, AIAuditReport, GeneratedTicket, TicketCategory, SportType
from app.core import settings, logger

# ============================================================
# CONFIGURATION DU MOTEUR
# ============================================================
MIN_MARKET_SCORE = 52.0  # Plus bas pour laisser l'Edge s'exprimer
MAX_MATCHES_PER_CATEGORY = 2 # Objectif Hermann : Exactement 2 matchs
AI_TIMEOUT = 12.0

@dataclass
class MarketCandidate:
    match: MatchData
    market: str
    selection: str
    probability: float
    odds: Optional[float]
    mathematical_score: float
    explanation: str
    edge: float
    priority: int = 0

def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))

def safe_probability(value: float) -> float:
    return round(clamp(value), 2)

def normalize_market_name(name: str) -> str:
    return name.lower().replace(" ", "_").replace(".", "_").replace(",", "_")

# ============================================================
# 1. MOTEUR DIXON-COLES
# ============================================================
class DixonColesEngine:
    def __init__(self, rho: float = -0.15, home_advantage: float = 1.15, max_goals: int = 8):
        self.rho = rho
        self.home_advantage = home_advantage
        self.max_goals = max_goals

    def _tau(self, x: int, y: int, lambda_x: float, mu_y: float) -> float:
        if x == 0 and y == 0: return 1.0 - lambda_x * mu_y * self.rho
        if x == 0 and y == 1: return 1.0 + lambda_x * self.rho
        if x == 1 and y == 0: return 1.0 + mu_y * self.rho
        if x == 1 and y == 1: return 1.0 - self.rho
        return 1.0

    def _expected_goals(self, match: MatchData) -> Tuple[float, float]:
        home_odds = max(match.home_odds, 1.01)
        away_odds = max(match.away_odds, 1.01)
        lambda_home = ((1.0 / home_odds) * 1.8 * self.home_advantage)
        lambda_away = ((1.0 / away_odds) * 1.8)
        return (max(lambda_home, 0.05), max(lambda_away, 0.05))

    def simulate(self, match: MatchData) -> SimulationResult:
        lambda_home, lambda_away = self._expected_goals(match)
        matrix = np.zeros((self.max_goals, self.max_goals), dtype=float)

        for home_goals in range(self.max_goals):
            for away_goals in range(self.max_goals):
                base_probability = poisson.pmf(home_goals, lambda_home) * poisson.pmf(away_goals, lambda_away)
                correction = self._tau(home_goals, away_goals, lambda_home, lambda_away)
                matrix[home_goals, away_goals] = max(base_probability * correction, 0.0)

        total = matrix.sum()
        if total <= 0: matrix[:] = 1.0 / matrix.size
        else: matrix /= total

        p_home = np.tril(matrix, -1).sum() * 100
        p_draw = np.diag(matrix).sum() * 100
        p_away = np.triu(matrix, 1).sum() * 100

        p_btts_yes = matrix[1:, 1:].sum() * 100
        
        p_over_1_5 = 0.0
        p_over_2_5 = 0.0
        p_over_3_5 = 0.0

        for i in range(self.max_goals):
            for j in range(self.max_goals):
                goals = i + j
                prob = matrix[i, j]
                if goals > 1: p_over_1_5 += prob * 100
                if goals > 2: p_over_2_5 += prob * 100
                if goals > 3: p_over_3_5 += prob * 100

        best_index = np.argmax(matrix)
        score_home, score_away = np.unravel_index(best_index, matrix.shape)
        
        est_corners = round(8.5 + (lambda_home + lambda_away) * 1.5, 1)

        return SimulationResult(
            match_id=match.match_id, proba_home=safe_probability(p_home), proba_draw=safe_probability(p_draw),
            proba_away=safe_probability(p_away), most_likely_score=f"{score_home}-{score_away}",
            proba_btts=safe_probability(p_btts_yes), proba_over_1_5=safe_probability(p_over_1_5),
            proba_over_2_5=safe_probability(p_over_2_5), proba_over_3_5=safe_probability(p_over_3_5), estimated_corners=est_corners
        )

# ============================================================
# 2. GENERATEUR DE MARCHES (VRAIES COTES ET EDGE)
# ============================================================
class MarketAnalyzer:
    def generate_candidates(self, match: MatchData, sim: SimulationResult, bookmaker_odds: Dict[str, float]) -> List[MarketCandidate]:
        candidates = []
        
        # Helper pour injecter la vraie cote et calculer l'Edge
        def add_candidate(m_type, selection, proba, priority, odds_key):
            real_odds = bookmaker_odds.get(odds_key)
            if real_odds:
                implied = (1.0 / real_odds) * 100
                edge = proba - implied
                # On ne garde que si l'Edge est positif et la cote réaliste
                if edge > 0 and 1.25 <= real_odds <= 2.20:
                    candidates.append(MarketCandidate(
                        match=match, market=m_type, selection=selection, probability=proba,
                        odds=real_odds, mathematical_score=proba, 
                        explanation=f"Proba:{proba:.1f}% | Cote:{real_odds} | Edge:{edge:.1f}%", edge=edge, priority=priority
                    ))

        add_candidate("1X2", f"Victoire {match.home_team}", sim.proba_home, 10, "1")
        add_candidate("1X2", f"Victoire {match.away_team}", sim.proba_away, 10, "2")
        add_candidate("OVER_UNDER", "Plus de 1,5 buts", sim.proba_over_1_5, 7, "O1.5")
        add_candidate("OVER_UNDER", "Plus de 2,5 buts", sim.proba_over_2_5, 8, "O2.5")
        #add_candidate("OVER_UNDER", "Moins de 2,5 buts", 100 - sim.proba_over_2_5, 7, "U2.5")
        #add_candidate("OVER_UNDER", "Moins de 3,5 buts", 100 - sim.proba_over_3_5, 6, "U3.5")
        add_candidate("BTTS", "BTTS Oui", sim.proba_btts, 8, "BTTS_Y")
        add_candidate("BTTS", "BTTS Non", 100 - sim.proba_btts, 6, "BTTS_N")

        return candidates

# ============================================================
# 3. IA - SECOND AVIS (Juge de l'Edge)
# ============================================================
class AIRiskManager:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(2)

    def _extract_json(self, text: str) -> dict:
        try:
            s = text.find('{')
            e = text.rfind('}')
            return json.loads(text[s:e+1]) if s != -1 and e != -1 else {}
        except: return {}

    async def evaluate_match(self, match: MatchData, sim: SimulationResult, candidates: List[MarketCandidate]) -> AIAuditReport:
        valid = [c for c in candidates if c.mathematical_score >= MIN_MARKET_SCORE]
        if not valid: return AIAuditReport(confidence_score=0.0, justification="VETO: Pas de Value Bet.", is_approved=False)

        valid.sort(key=lambda x: (x.edge, x.priority), reverse=True)
        top_candidates = valid[:5]
        
        market_context = [{"selection": c.selection, "probability": c.probability, "odds": c.odds, "edge": round(c.edge,1)} for c in top_candidates]

        prompt = f"""
        Tu es le gestionnaire des risques d'un Quant Fund sportif. Match : {match.home_team} vs {match.away_team}.
        Voici les SEULES opportunités "Value Bet" (avec un edge positif par rapport au bookmaker) :
        {json.dumps(market_context, ensure_ascii=False, indent=2)}
        
        Mission :
        1. Choisis le marché qui offre le meilleur équilibre entre Sécurité (probabilité) et Rentabilité (Edge).
        2. Recopie EXACTEMENT le texte de la sélection.
        
        Réponds UNIQUEMENT via ce JSON : {{"decision": "ACCEPT", "selection": "texte", "reason": "Justification (max 15 mots)"}}
        """

        async with self.semaphore:
            await asyncio.sleep(1.2)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"}, json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}]}, timeout=AI_TIMEOUT)
                    if resp.status_code == 200:
                        data = self._extract_json(resp.json()['choices'][0]['message']['content'])
                        
                        if data.get("decision") == "ACCEPT":
                            sel = data.get("selection", "")
                            chosen = next((c for c in top_candidates if c.selection == sel), None)
                            if chosen:
                                return AIAuditReport(confidence_score=chosen.probability, justification=json.dumps({"market": chosen.market, "selection": chosen.selection, "reason": data.get("reason"), "odds": chosen.odds}), is_approved=True)
                        return AIAuditReport(confidence_score=0, justification="VETO IA", is_approved=False)
            except Exception as exc:
                logger.error(f"Erreur AI : {exc}")
                
        # Fallback ultra-sécurisé
        best = top_candidates[0]
        return AIAuditReport(confidence_score=best.probability, justification=json.dumps({"market": best.market, "selection": best.selection, "reason": "Sélection par Edge max (Erreur IA).", "odds": best.odds}), is_approved=True)

# ============================================================
# 4. FABRIQUE DE TICKETS (L'Assembleur 100 000 FCFA)
# ============================================================
class TicketFactory:
    def build_portfolio(self, evaluated_matches: List[Tuple[MatchData, SimulationResult, AIAuditReport]]):
        portfolio = defaultdict(list)
        selected = []

        for match, sim, ai in evaluated_matches:
            if not ai.is_approved: continue
            try:
                data = json.loads(ai.justification)
                selected.append({
                    "match": match, "selection": data["selection"], 
                    "odds": float(data.get("odds", 0.0)), "confidence": ai.confidence_score, "ai_reason": data["reason"]
                })
            except: continue

        selected.sort(key=lambda x: x["confidence"], reverse=True)

        # 🚀 ASSEMBLEUR : Exactement 2 matchs, Cote 2.2 à 3.5
        for combo in itertools.combinations(selected[:15], 2):
            total_odds = combo[0]["odds"] * combo[1]["odds"]
            if 2.2 <= round(total_odds, 2) <= 3.5:
                # Création du ticket parfait
                bet_text = ""
                ai_text = ""
                for i, item in enumerate(combo, 1):
                    bet_text += f"*{i}️⃣ {item['match'].home_team} vs {item['match'].away_team}*\n👉 **{item['selection']}**\n📊 Cote : {item['odds']} | 🎯 Proba : {item['confidence']:.1f}%\n\n"
                    ai_text += f"✔️ **{item['match'].home_team}** : {item['ai_reason']}\n\n"
                
                final_proba = round((combo[0]["confidence"]/100) * (combo[1]["confidence"]/100) * 100, 1)
                
                ticket = GeneratedTicket(
                    category=TicketCategory.ULTRA_SAFE, match_id="final_quant_combo", sport=SportType.SOCCER,
                    match_title="🌟 COMBINÉ VALUE BET (APPROCHE PRO)", bet_type=bet_text.strip(), odds=round(total_odds, 2),
                    ai_confidence=final_proba, ai_justification=ai_text.strip()
                )
                portfolio[TicketCategory.ULTRA_SAFE].append(ticket)
                break # On s'arrête au premier combo parfait généré

        return dict(portfolio)

# ============================================================
# 5. PIPELINE GLOBAL (Utilisé par main.py)
# ============================================================
class AnalysisPipeline:
    def __init__(self):
        self.math_engine = DixonColesEngine()
        self.market_analyzer = MarketAnalyzer()
        self.ai_manager = AIRiskManager()
        self.ticket_factory = TicketFactory()

    async def analyze_match(self, match: MatchData, bookmaker_odds: Dict[str, float]):
        simulation = self.math_engine.simulate(match)
        candidates = self.market_analyzer.generate_candidates(match, simulation, bookmaker_odds)
        ai_report = await self.ai_manager.evaluate_match(match, simulation, candidates)
        return (match, simulation, ai_report)

    async def build_daily_portfolio(self, matches_and_odds: List[Tuple[MatchData, Dict[str, float]]]):
        # On utilise asyncio.gather pour traiter plusieurs matchs très rapidement !
        tasks = [self.analyze_match(m, odds) for m, odds in matches_and_odds]
        evaluated = await asyncio.gather(*tasks)
        return self.ticket_factory.build_portfolio(evaluated)

pipeline = AnalysisPipeline()
