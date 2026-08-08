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
        p_o35 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 3])) * 100

        est_corners = round(8.5 + (lambda_x + mu_y) * 1.5, 1)

        return SimulationResult(
            match_id=match.match_id, proba_home=p_home, proba_draw=p_draw, proba_away=p_away, 
            most_likely_score=f"{score_x}-{score_y}", proba_btts=p_btts, 
            proba_over_1_5=p_o15, proba_over_2_5=p_o25, proba_over_3_5=p_o35, estimated_corners=est_corners
        )

class AIRiskManager:
    async def evaluate_match(self, match: MatchData, sim: SimulationResult) -> AIAuditReport:
        base_confidence = max(sim.proba_home, sim.proba_draw, sim.proba_away)
        
        if base_confidence < 45.0:
            return AIAuditReport(confidence_score=base_confidence, justification='{"profil": "VETO", "analyse": "Match trop incertain."}', is_approved=False)

        if not settings.GROQ_API_KEY:
            return AIAuditReport(confidence_score=base_confidence, justification='{"profil": "INCERTAIN", "analyse": "Validation mathématique sans IA."}', is_approved=True)

        proba_u35 = 100 - sim.proba_over_3_5
        
        # 🧠 PROMPT "BÉTON ARMÉ" : Tolérance zéro pour le risque offensif
        prompt = f"""
        En tant que trader sportif ultra-conservateur, profile la physionomie de ce match : {match.home_team} vs {match.away_team}.
        
        DONNÉES ALGO : Victoire 1: {round(sim.proba_home,1)}%, Victoire 2: {round(sim.proba_away,1)}%, Moins de 3.5 buts: {round(proba_u35,1)}%.
        
        Mission : Renvoie UNIQUEMENT un objet JSON valide.
        Clés : "profil" ([DÉFENSIF, DÉSÉQUILIBRÉ, INCERTAIN, VETO]) et "analyse".
        - DÉFENSIF : Match fermé (Moins de 3.5 buts assuré).
        - DÉSÉQUILIBRÉ : Un favori écrasant.
        - INCERTAIN : Équipes de force similaire, on privilégiera la double chance.
        - VETO : Match amical, match à spectacle, ou forte volatilité. Ne prends AUCUN risque offensif.
        Dans "analyse", cite impérativement la statistique algo qui justifie ton choix (ex: "Avec 82% de chances de moins de 3.5 buts...").
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
        
        # Mode de survie strict
        survie = f'{{"profil": "INCERTAIN", "analyse": "Confirmation mathématique d\'urgence : {round(base_confidence,1)}% de fiabilité stat."}}'
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
                
            MAX_SAFE_ODDS = 1.65 # Limite stricte individuelle, on ne veut que des probas massives
            
            # 1. VICTOIRE ÉCRASANTE (> 65%)
            if ai_profile == "DÉSÉQUILIBRÉ":
                if p := (sim.proba_home if sim.proba_home >= 65.0 else sim.proba_away if sim.proba_away >= 65.0 else 0):
                    odds = max(1.15, round(100.0/p*0.92, 2))
                    team = match.home_team if sim.proba_home >= 65.0 else match.away_team
                    if odds <= MAX_SAFE_ODDS: 
                        pool.append({"match": match, "type": f"Victoire {team}", "odds": odds, "proba": p, "ai": ai_text})

            # 2. VERROU DÉFENSIF LARGE (Moins de 3.5 buts)
            elif ai_profile == "DÉFENSIF":
                proba_u35 = 100 - sim.proba_over_3_5
                if proba_u35 >= 75.0:
                    odds = max(1.15, round(100.0/proba_u35*0.92, 2))
                    if odds <= MAX_SAFE_ODDS: 
                        pool.append({"match": match, "type": "Moins de 3,5 buts", "odds": odds, "proba": proba_u35, "ai": ai_text})

            # 3. DOUBLE CHANCE BÉTON (1X ou X2)
            elif ai_profile == "INCERTAIN":
                if sim.proba_home + sim.proba_draw >= 82.0:
                    odds = max(1.10, round(100.0/(sim.proba_home+sim.proba_draw)*0.92, 2))
                    if odds <= MAX_SAFE_ODDS: 
                        pool.append({"match": match, "type": f"Double Chance (1X)", "odds": odds, "proba": sim.proba_home+sim.proba_draw, "ai": ai_text})
                elif sim.proba_away + sim.proba_draw >= 82.0:
                    odds = max(1.10, round(100.0/(sim.proba_away+sim.proba_draw)*0.92, 2))
                    if odds <= MAX_SAFE_ODDS: 
                        pool.append({"match": match, "type": f"Double Chance (X2)", "odds": odds, "proba": sim.proba_away+sim.proba_draw, "ai": ai_text})

        # 🚀 ASSEMBLAGE DES COMBINÉS SÉCURISÉS (Objectif cote >= 2.0)
        def get_best_combo(pool_list, min_odds, max_odds, min_items, max_items, min_proba_threshold):
            pool_list = sorted([p for p in pool_list if p['proba'] >= min_proba_threshold], key=lambda x: x['proba'], reverse=True)
            for r in range(min_items, max_items + 1):
                # Limite l'itération aux 20 meilleurs matchs pour la rapidité
                for combo in itertools.combinations(pool_list[:20], r):
                    total_odds = 1.0
                    for x in combo: total_odds *= x['odds']
                    if min_odds <= total_odds <= max_odds: return combo
            return None

        # 🌟 Combiné du Jour : Cote de 2.0 à 3.5 max, avec les matchs les plus fiables
        if combo_jour := get_best_combo(pool, 2.0, 3.5, 2, 4, 75.0):
            portfolio[TicketCategory.ULTRA_SAFE].append(self._format_combo(combo_jour, TicketCategory.ULTRA_SAFE, "🌟 COMBINÉ DU JOUR (BÉTON ARMÉ)"))
            
        if combo_vip := get_best_combo(pool, 3.0, 5.0, 3, 5, 70.0):
            portfolio[TicketCategory.VIP].append(self._format_combo(combo_vip, TicketCategory.VIP, "💎 COMBINÉ VIP (SÉCURITÉ ÉTENDUE)"))
            
        if combo_value := get_best_combo(pool, 5.0, 15.0, 4, 7, 65.0):
            cat_val = TicketCategory.VALUE_BET if hasattr(TicketCategory, 'VALUE_BET') else TicketCategory.VALUE
            portfolio[cat_val].append(self._format_combo(combo_value, cat_val, "🚀 VALUE BET (CONSERVATEUR)"))
            
        return dict(portfolio)

    def _format_combo(self, combo, cat, title):
        total_odds = round(np.prod([c['odds'] for c in combo]), 2)
        final_proba = round(np.prod([c['proba']/100 for c in combo]) * 100, 1)
        bet_text = "\n".join([f"*{i}️⃣ {c['match'].home_team} vs {c['match'].away_team}*\n👉 **{c['type']}**\n📊 Cote : {c['odds']} | 🎯 Confiance : {c['proba']:.1f}%\n" for i, c in enumerate(combo, 1)])
        ai_text = "\n".join([f"✔️ **{c['match'].home_team}** : {c['ai']}" for c in combo])
        return GeneratedTicket(category=cat, match_id="final", sport=SportType.SOCCER, match_title=title, bet_type=bet_text, odds=total_odds, ai_confidence=final_proba, ai_justification=ai_text)
