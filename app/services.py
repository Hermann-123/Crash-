

import asyncio
import httpx
import json
import numpy as np
from scipy.stats import poisson
from typing import List, Tuple
from datetime import datetime
from collections import defaultdict
import itertools

from app.models import MatchData, SimulationResult, AIAuditReport, GeneratedTicket, TicketCategory, SportType
from app.core import settings, logger

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

        est_corners = round(8.5 + (lambda_x + mu_y) * 1.5, 1)

        return SimulationResult(
            match_id=match.match_id, proba_home=p_home, proba_draw=p_draw, proba_away=p_away, 
            most_likely_score=f"{score_x}-{score_y}", proba_btts=p_btts, 
            proba_over_1_5=p_o15, proba_over_2_5=p_o25, estimated_corners=est_corners
        )

class AIRiskManager:
    async def evaluate_match(self, match: MatchData, sim: SimulationResult) -> AIAuditReport:
        base_confidence = max(sim.proba_home, sim.proba_draw, sim.proba_away)
        
        if base_confidence < 45.0:
            return AIAuditReport(confidence_score=base_confidence, justification='{"profil": "VETO", "analyse": "Match trop incertain."}', is_approved=False)

        if not settings.GROQ_API_KEY:
            return AIAuditReport(confidence_score=base_confidence, justification='{"profil": "INCERTAIN", "analyse": "Validation mathématique sans IA."}', is_approved=True)

        # 🧠 PROMPT OPTIMISÉ : L'IA reçoit maintenant les stats pour être factuelle
        prompt = f"""
        En tant que trader sportif expert, profile la physionomie tactique de ce match : {match.home_team} vs {match.away_team}.
        
        DONNÉES ALGO : Victoire 1: {round(sim.proba_home,1)}%, Victoire 2: {round(sim.proba_away,1)}%, Over 2.5: {round(sim.proba_over_2_5,1)}%, BTTS: {round(sim.proba_btts,1)}%.
        
        Mission : Renvoie UNIQUEMENT un objet JSON.
        Clés : "profil" ([OFFENSIF, DÉFENSIF, DÉSÉQUILIBRÉ, INCERTAIN, VETO]) et "analyse".
        Dans "analyse", cite impérativement une stat parmi celles fournies pour justifier ton profil.
        """
        
        await asyncio.sleep(5.0)
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                    json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}]}, timeout=10.0
                )
                if response.status_code == 200:
                    ans = response.json()['choices'][0]['message']['content'].strip()
                    if "```json" in ans: ans = ans.split("```json")[1].split("```")[0]
                    elif "```" in ans: ans = ans.split("```")[1].split("```")[0]
                    json.loads(ans)
                    return AIAuditReport(confidence_score=round(base_confidence, 1), justification=ans, is_approved="VETO" not in ans.upper())
        except: pass
        
        # Mode de survie
        survie = f'{{"profil": "INCERTAIN", "analyse": "Confirmation statistique : {round(base_confidence,1)}% de probabilité mathématique."}}'
        return AIAuditReport(confidence_score=base_confidence, justification=survie, is_approved=True)

class TicketFactory:
    def build_portfolio(self, evaluated_matches: List[Tuple[MatchData, SimulationResult, AIAuditReport]]):
        portfolio = defaultdict(list)
        pool = []
        
        for match, sim, ai in evaluated_matches:
            if not ai.is_approved: continue
            try:
                ai_data = json.loads(ai.justification)
                ai_profile, ai_text = ai_data.get("profil", "INCERTAIN").upper(), ai_data.get("analyse", "")
            except: continue
                
            MAX_SAFE_ODDS = 1.85 # Limite de sécurité individuelle
            
            if ai_profile == "DÉSÉQUILIBRÉ":
                if p := (sim.proba_home if sim.proba_home >= 55.0 else sim.proba_away if sim.proba_away >= 55.0 else 0):
                    odds = max(1.35, round(100.0/p*0.92, 2))
                    if odds <= MAX_SAFE_ODDS: pool.append({"match": match, "type": f"Victoire", "odds": odds, "proba": p, "ai": ai_text})

            elif ai_profile == "DÉFENSIF":
                if sim.proba_over_2_5 < 35.0 and (odds := max(1.40, round(100.0/(100-sim.proba_over_2_5)*0.92, 2))) <= MAX_SAFE_ODDS:
                    pool.append({"match": match, "type": "Moins de 2,5 buts", "odds": odds, "proba": 100 - sim.proba_over_2_5, "ai": ai_text})
                elif sim.proba_btts < 40.0 and (odds := max(1.40, round(100.0/(100-sim.proba_btts)*0.92, 2))) <= MAX_SAFE_ODDS:
                    pool.append({"match": match, "type": "BTTS : Non", "odds": odds, "proba": 100 - sim.proba_btts, "ai": ai_text})

            elif ai_profile == "OFFENSIF":
                if sim.proba_over_2_5 >= 60.0:
                    pool.append({"match": match, "type": "Plus de 2,5 buts", "odds": max(1.55, round(100.0/sim.proba_over_2_5*0.92, 2)), "proba": sim.proba_over_2_5, "ai": ai_text})
                elif sim.proba_btts >= 62.0:
                    pool.append({"match": match, "type": "BTTS : Oui", "odds": max(1.60, round(100.0/sim.proba_btts*0.92, 2)), "proba": sim.proba_btts, "ai": ai_text})

        # 🚀 ASSEMBLAGE AVEC FILTRE DE RENTABILITÉ (Cote totale >= 2.0)
        def get_best_combo(pool_list, min_odds, max_odds, min_items, max_items, min_proba_threshold):
            pool_list = sorted([p for p in pool_list if p['proba'] >= min_proba_threshold], key=lambda x: x['proba'], reverse=True)
            for r in range(min_items, max_items + 1):
                for combo in itertools.combinations(pool_list[:25], r):
                    total_odds = 1.0
                    for x in combo: total_odds *= x['odds']
                    if min_odds <= total_odds <= max_odds: return combo
            return None

        if combo := get_best_combo(pool, 2.0, 4.0, 2, 4, 72.0):
            portfolio[TicketCategory.ULTRA_SAFE].append(self._format_combo(combo, TicketCategory.ULTRA_SAFE, "🌟 COMBINÉ DU JOUR (BÉNÉFICE MAX)"))
            
        return dict(portfolio)

    def _format_combo(self, combo, cat, title):
        total_odds = round(np.prod([c['odds'] for c in combo]), 2)
        final_proba = round(np.prod([c['proba']/100 for c in combo]) * 100, 1)
        bet_text = "\n".join([f"*{i}️⃣ {c['match'].home_team} vs {c['match'].away_team}*\n👉 **{c['type']}** (Cote: {c['odds']})" for i, c in enumerate(combo, 1)])
        ai_text = "\n".join([f"✔️ {c['match'].home_team} : {c['ai']}" for c in combo])
        return GeneratedTicket(category=cat, match_id="final", sport=SportType.SOCCER, match_title=title, bet_type=bet_text, odds=total_odds, ai_confidence=final_proba, ai_justification=ai_text)
