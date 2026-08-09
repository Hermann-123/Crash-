import asyncio
import httpx
import json
import numpy as np
from scipy.stats import poisson
from typing import List, Tuple, Dict
from datetime import datetime
from collections import defaultdict
import itertools

from app.models import MatchData, SimulationResult, MarketCandidate, AIValidationResult, GeneratedTicket, TicketCategory, SportType
from app.core import settings, logger

# ⚙️ 1. LE MOTEUR MATHÉMATIQUE (DIXON-COLES)
class DixonColesEngine:
    def __init__(self, rho: float = -0.15, home_advantage: float = 1.15):
        self.rho = rho
        self.home_advantage = home_advantage
        self.max_goals = 6

    def simulate(self, match: MatchData) -> SimulationResult:
        lambda_x = (1.0 / match.home_odds) * 1.8 * self.home_advantage
        mu_y = (1.0 / match.away_odds) * 1.8
        matrix = np.zeros((self.max_goals, self.max_goals))

        for i in range(self.max_goals):
            for j in range(self.max_goals):
                matrix[i, j] = poisson.pmf(i, lambda_x) * poisson.pmf(j, mu_y)
        
        matrix /= np.sum(matrix)
        p_home = float(np.sum(np.tril(matrix, -1))) * 100
        p_draw = float(np.sum(np.diag(matrix))) * 100
        p_away = float(np.sum(np.triu(matrix, 1))) * 100

        best_idx = np.argmax(matrix)
        score_x, score_y = np.unravel_index(best_idx, matrix.shape)

        p_btts = float(np.sum(matrix[1:, 1:])) * 100
        p_o15 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 1])) * 100
        p_o25 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 2])) * 100
        p_o35 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 3])) * 100

        est_corners = round(8.5 + (lambda_x + mu_y) * 1.5, 1)

        return SimulationResult(
            match_id=match.match_id, proba_home=p_home, proba_draw=p_draw, proba_away=p_away, 
            most_likely_score=f"{score_x}-{score_y}", proba_btts=p_btts, 
            proba_over_1_5=p_o15, proba_over_2_5=p_o25, proba_over_3_5=p_o35, estimated_corners=est_corners
        )

# 📊 2. LE GÉNÉRATEUR ET FILTRE DE MARCHÉS
class MarketEngine:
    def generate_and_filter(self, match: MatchData, sim: SimulationResult, bookmaker_odds: Dict[str, float]) -> List[MarketCandidate]:
        candidates = []
        
        if "1" in bookmaker_odds:
            candidates.append(self._build_candidate("1X2", f"Victoire {match.home_team}", sim.proba_home, bookmaker_odds["1"]))
        if "2" in bookmaker_odds:
            candidates.append(self._build_candidate("1X2", f"Victoire {match.away_team}", sim.proba_away, bookmaker_odds["2"]))
            
        if "O1.5" in bookmaker_odds:
            candidates.append(self._build_candidate("OVER_UNDER", "Plus de 1,5 buts", sim.proba_over_1_5, bookmaker_odds["O1.5"]))
        if "O2.5" in bookmaker_odds:
            candidates.append(self._build_candidate("OVER_UNDER", "Plus de 2,5 buts", sim.proba_over_2_5, bookmaker_odds["O2.5"]))
        if "U2.5" in bookmaker_odds:
            candidates.append(self._build_candidate("OVER_UNDER", "Moins de 2,5 buts", 100 - sim.proba_over_2_5, bookmaker_odds["U2.5"]))
        if "U3.5" in bookmaker_odds:
            candidates.append(self._build_candidate("OVER_UNDER", "Moins de 3,5 buts", 100 - sim.proba_over_3_5, bookmaker_odds["U3.5"]))

        # 🛡️ LE FILTRE ANTI-PIÈGES (MODIFIÉ)
        # On interdit les cotes > 1.90. Si le bookmaker donne 2.15, c'est qu'il y a un risque énorme.
        valid_markets = [c for c in candidates if c.probability >= 52.0 and 1.20 <= c.real_odds <= 1.90]
        
        valid_markets.sort(key=lambda x: x.probability, reverse=True)
        return valid_markets[:4]

    def _build_candidate(self, m_type: str, selection: str, proba: float, real_odds: float) -> MarketCandidate:
        implied_proba = (1.0 / real_odds) * 100 if real_odds > 0 else 0
        edge = proba - implied_proba
        return MarketCandidate(market_type=m_type, selection=selection, probability=proba, real_odds=real_odds, implied_probability=implied_proba, edge=edge)

# 🧠 3. L'ANALYSTE IA (VALIDATION FINALE)
class AIValidator:
    def __init__(self):
        # 🚨 CORRECTION API GROQ : 1 requête à la fois pour éviter le bannissement "Too Many Requests"
        self.semaphore = asyncio.Semaphore(1) 

    async def evaluate_markets(self, match: MatchData, top_markets: List[MarketCandidate]) -> AIValidationResult:
        if not top_markets:
            return AIValidationResult(decision="VETO", reason="Aucun marché sécurisé disponible.")

        market_text = "\n".join([f"- '{m.selection}' | Proba math: {round(m.probability, 1)}% | Cote: {m.real_odds}" for m in top_markets])

        prompt = f"""
        Tu es le module d'analyse finale d'un Quant Fund sportif.
        Analyse tactiquement ce match : {match.home_team} vs {match.away_team}.
        
        Le moteur mathématique a présélectionné ces marchés viables avec leurs vraies cotes bookmaker :
        {market_text}
        
        Mission :
        1. Compare les marchés proposés.
        2. Identifie LE SEUL pari qui présente la sécurité maximale par rapport à sa cote.
        3. Si le match te semble trop piège, tu dois rejeter (VETO).
        
        Renvoie UNIQUEMENT un JSON avec 3 clés :
        {{
            "decision": "APPROVED" ou "VETO",
            "primary_market_selection": "Recopie EXACTEMENT le texte du pari que tu as choisi",
            "reason": "Analyse tactique et statistique (max 30 mots) justifiant ce choix."
        }}
        """
        
        async with self.semaphore:
            # Pause de 1.5s pour respecter la limite de l'API Groq gratuite
            await asyncio.sleep(1.5)
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                        json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}]}, timeout=15.0
                    )
                    if response.status_code == 200:
                        ans = response.json()['choices'][0]['message']['content'].strip()
                        if "```json" in ans: ans = ans.split("```json")[1].split("```")[0]
                        elif "```" in ans: ans = ans.split("```")[1].split("```")[0]
                        data = json.loads(ans)
                        
                        if data.get("decision") == "APPROVED":
                            chosen_sel = data.get("primary_market_selection", "").strip()
                            chosen_market = next((m for m in top_markets if m.selection == chosen_sel), None)
                            if chosen_market:
                                return AIValidationResult(decision="APPROVED", primary_market=chosen_market, reason=data.get("reason", ""))
                        
                        return AIValidationResult(decision="VETO", reason=data.get("reason", "Jugé trop risqué par l'IA."))
                    else:
                        logger.warning(f"Erreur API Groq {response.status_code}")
            except Exception as e:
                logger.error(f"Erreur connexion IA sur {match.home_team}: {e}")
                
        # Survie si l'IA crash vraiment
        if top_markets:
            return AIValidationResult(decision="APPROVED", primary_market=top_markets[0], reason="Validation math (IA injoignable).")
        return AIValidationResult(decision="VETO", reason="Échec IA.")

# 🚀 4. L'USINE À TICKETS
class TicketFactory:
    def build_portfolio(self, evaluated_matches: List[Tuple[MatchData, AIValidationResult]]):
        portfolio = defaultdict(list)
        pool = []
        
        for match, ai_res in evaluated_matches:
            if ai_res.decision == "APPROVED" and ai_res.primary_market:
                pool.append({
                    "match": match, 
                    "type": ai_res.primary_market.selection, 
                    "odds": ai_res.primary_market.real_odds, 
                    "proba": ai_res.primary_market.probability, 
                    "ai": ai_res.reason
                })

        def get_best_combo(pool_list, min_odds, max_odds, min_items, max_items):
            pool_list = sorted(pool_list, key=lambda x: x['proba'], reverse=True)
            for r in range(min_items, max_items + 1):
                for combo in itertools.combinations(pool_list[:20], r):
                    match_ids = [x['match'].match_id for x in combo]
                    if len(set(match_ids)) != len(match_ids): continue 
                    
                    total_odds = 1.0
                    for x in combo: total_odds *= x['odds']
                    
                    if min_odds <= round(total_odds, 2) <= max_odds: 
                        return combo
            return None

        # Exactement 2 Matchs / Cote entre 2.2 et 3.5
        if combo_jour := get_best_combo(pool, 2.2, 3.5, 2, 2):
            portfolio[TicketCategory.ULTRA_SAFE].append(self._format_combo(combo_jour, TicketCategory.ULTRA_SAFE, "🌟 COMBINÉ DU JOUR (2 MATCHS BÉTON)"))
            
        return dict(portfolio)

    def _format_combo(self, combo, cat, title):
        total_odds = round(np.prod([c['odds'] for c in combo]), 2)
        final_proba = round(np.prod([c['proba']/100 for c in combo]) * 100, 1)
        
        bet_text = "\n".join([f"*{i}️⃣ {c['match'].home_team} vs {c['match'].away_team}*\n👉 **{c['type']}**\n📊 Cote Bookmaker : {c['odds']} | 🎯 Proba Algo : {c['proba']:.1f}%\n" for i, c in enumerate(combo, 1)])
        ai_text = "\n".join([f"✔️ **{c['match'].home_team}** :\n{c['ai']}\n" for c in combo])
        
        return GeneratedTicket(
            category=cat, match_id="final", sport=SportType.SOCCER, match_title=title, 
            bet_type=bet_text.strip(), odds=total_odds, ai_confidence=final_proba, ai_justification=ai_text.strip()
        )
