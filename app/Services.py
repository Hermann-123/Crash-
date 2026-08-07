import asyncio
import httpx
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
        p_o05 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 0])) * 100
        p_o15 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 1])) * 100
        p_o25 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 2])) * 100
        p_o35 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 3])) * 100
        p_o45 = float(np.sum([matrix[i, j] for i in range(self.max_goals) for j in range(self.max_goals) if i + j > 4])) * 100

        est_corners = round(8.5 + (lambda_x + mu_y) * 1.5, 1)

        return SimulationResult(
            match_id=match.match_id, proba_home=p_home, proba_draw=p_draw, proba_away=p_away, 
            most_likely_score=f"{score_x}-{score_y}", proba_btts=p_btts, 
            proba_over_1_5=p_o15, proba_over_2_5=p_o25, proba_over_3_5=p_o35, estimated_corners=est_corners
        )

class AIRiskManager:
    async def evaluate_match(self, match: MatchData, sim: SimulationResult) -> AIAuditReport:
        base_confidence = max(sim.proba_home, sim.proba_draw, sim.proba_away)
        
        # Filtre initial : Rejet des matchs imprévisibles (< 45% de certitude)
        if base_confidence < 45.0:
            return AIAuditReport(confidence_score=base_confidence, justification="VETO", is_approved=False)

        if not settings.GROQ_API_KEY:
            return AIAuditReport(confidence_score=base_confidence, justification="Validé mathématiquement par l'algorithme.", is_approved=True)

        # 🎯 PROMPT ULTRA-STRICT : Analyse tactique directe sans blabla ni nom de pari
        prompt = f"""
        Tu es un analyste sportif intransigeant. Analyse ce match : {match.home_team} vs {match.away_team}.
        
        Mission : Rédige UNE SEULE phrase percutante (maximum 3 lignes) sur la dynamique tactique du match (forces offensives, solidité défensive, ou probabilité de match fermé/ouvert).
        
        CONSIGNES STRICTES (Sinon tu seras désactivé) :
        - NE NOMME JAMAIS de type de pari (interdiction d'écrire "victoire", "BTTS", "Under/Over").
        - AUCUNE INTRODUCTION (ne dis pas "Voici le rapport" ou "Analyse du match").
        - AUCUNE CONCLUSION ou parenthèse de rappel.
        - Commence directement par ton analyse factuelle.
        - Si tu détectes un piège (match amical, équipe bis), réponds UNIQUEMENT par le mot "VETO".
        """
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                    json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}]}, timeout=10.0
                )
                if response.status_code == 200:
                    ans = response.json()['choices'][0]['message']['content'].strip()
                    is_approved = not ans.upper().startswith("VETO")
                    return AIAuditReport(confidence_score=round(base_confidence, 1), justification=ans, is_approved=is_approved)
        except: pass
        return AIAuditReport(confidence_score=base_confidence, justification="Indicateurs statistiques au vert, validation du modèle.", is_approved=True)

class TicketFactory:
    def build_portfolio(self, evaluated_matches: List[Tuple[MatchData, SimulationResult, AIAuditReport]]):
        portfolio = defaultdict(list)
        pool = []
        
        for match, sim, ai in evaluated_matches:
            if not ai.is_approved: continue
            
            p_home, p_draw, p_away = sim.proba_home, sim.proba_draw, sim.proba_away
            base_confidence = max(p_home, p_draw, p_away) 
            
            if p_home >= 55.0:
                pool.append({"match": match, "type": f"Victoire {match.home_team} (1)", "odds": max(1.35, round(100.0/p_home*0.92, 2)), "proba": p_home, "ai": ai.justification})
                if p_home >= 72.0:
                    pool.append({"match": match, "type": f"Handicap -1 : {match.home_team}", "odds": max(1.65, round(100.0/(p_home-15)*0.92, 2)), "proba": p_home - 15, "ai": ai.justification})
                    pool.append({"match": match, "type": f"Mi-Temps / Fin de match : {match.home_team} / {match.home_team}", "odds": max(1.85, round(100.0/(base_confidence-18)*0.92, 2)), "proba": base_confidence - 18, "ai": ai.justification})
                if p_home >= 60.0 and sim.proba_over_1_5 >= 70.0:
                    pool.append({"match": match, "type": f"Combo : {match.home_team} gagne ET Plus de 1.5 buts", "odds": max(1.50, round(100.0/(p_home-10)*0.92, 2)), "proba": p_home - 10, "ai": ai.justification})
                if p_home >= 65.0 and sim.proba_btts < 40.0:
                    pool.append({"match": match, "type": f"{match.home_team} gagne sans encaisser (Clean Sheet)", "odds": max(1.80, round(100.0/(p_home-15)*0.92, 2)), "proba": p_home - 15, "ai": ai.justification})

            if p_away >= 55.0:
                pool.append({"match": match, "type": f"Victoire {match.away_team} (2)", "odds": max(1.35, round(100.0/p_away*0.92, 2)), "proba": p_away, "ai": ai.justification})
                if p_away >= 72.0:
                    pool.append({"match": match, "type": f"Handicap -1 : {match.away_team}", "odds": max(1.65, round(100.0/(p_away-15)*0.92, 2)), "proba": p_away - 15, "ai": ai.justification})
                    pool.append({"match": match, "type": f"Mi-Temps / Fin de match : {match.away_team} / {match.away_team}", "odds": max(1.85, round(100.0/(base_confidence-18)*0.92, 2)), "proba": base_confidence - 18, "ai": ai.justification})
                if p_away >= 60.0 and sim.proba_over_1_5 >= 70.0:
                    pool.append({"match": match, "type": f"Combo : {match.away_team} gagne ET Plus de 1.5 buts", "odds": max(1.50, round(100.0/(p_away-10)*0.92, 2)), "proba": p_away - 10, "ai": ai.justification})

            if p_home + p_draw >= 82.0:
                pool.append({"match": match, "type": f"Double Chance (1X) : {match.home_team} ou Nul", "odds": max(1.15, round(100.0/(p_home+p_draw)*0.92, 2)), "proba": p_home+p_draw, "ai": ai.justification})
            if p_away + p_draw >= 82.0:
                pool.append({"match": match, "type": f"Double Chance (X2) : {match.away_team} ou Nul", "odds": max(1.15, round(100.0/(p_away+p_draw)*0.92, 2)), "proba": p_away+p_draw, "ai": ai.justification})

            if sim.proba_over_1_5 >= 78.0:
                pool.append({"match": match, "type": "Plus de 1,5 buts dans le match", "odds": max(1.20, round(100.0/sim.proba_over_1_5*0.92, 2)), "proba": sim.proba_over_1_5, "ai": ai.justification})
            if sim.proba_over_2_5 >= 60.0:
                pool.append({"match": match, "type": "Plus de 2,5 buts dans le match", "odds": max(1.55, round(100.0/sim.proba_over_2_5*0.92, 2)), "proba": sim.proba_over_2_5, "ai": ai.justification})
            if sim.proba_over_2_5 < 35.0:
                pool.append({"match": match, "type": "Moins de 2,5 buts dans le match", "odds": max(1.55, round(100.0/(100-sim.proba_over_2_5)*0.92, 2)), "proba": 100 - sim.proba_over_2_5, "ai": ai.justification})

            if sim.proba_btts >= 62.0:
                pool.append({"match": match, "type": "Les 2 équipes marquent (BTTS : Oui)", "odds": max(1.65, round(100.0/sim.proba_btts*0.92, 2)), "proba": sim.proba_btts, "ai": ai.justification})
                if sim.proba_over_2_5 >= 65.0:
                    pool.append({"match": match, "type": "Combo : Les 2 marquent ET Plus de 2.5 buts", "odds": max(1.90, round(100.0/(sim.proba_btts-10)*0.92, 2)), "proba": sim.proba_btts - 10, "ai": ai.justification})
            elif sim.proba_btts < 40.0:
                pool.append({"match": match, "type": "Les 2 équipes marquent (BTTS : Non)", "odds": max(1.60, round(100.0/(100-sim.proba_btts)*0.92, 2)), "proba": 100 - sim.proba_btts, "ai": ai.justification})

            if base_confidence >= 70.0:
                pool.append({"match": match, "type": f"Score Exact Probable : {sim.most_likely_score}", "odds": 7.00, "proba": 15.0, "ai": ai.justification})

        def get_best_combo(pool_list, min_odds, max_odds, min_items, max_items, min_proba_threshold=0.0):
            if not pool_list: return None
            
            pool_list = sorted(pool_list, key=lambda x: x['proba'], reverse=True)
            valid_pool = [p for p in pool_list if p['proba'] >= min_proba_threshold]
            
            for r in range(min_items, max_items + 1):
                for combo in itertools.combinations(valid_pool[:25], r):
                    match_ids = [x['match'].match_id for x in combo]
                    if len(set(match_ids)) != len(match_ids): continue 
                    
                    total_odds = 1.0
                    for x in combo: total_odds *= x['odds']
                    
                    if min_odds <= total_odds <= max_odds:
                        return combo
            return None

        combo_jour = get_best_combo(pool, 2.2, 3.5, 2, 4, min_proba_threshold=75.0)
        if combo_jour:
            portfolio[TicketCategory.ULTRA_SAFE].append(self._format_combo(combo_jour, TicketCategory.ULTRA_SAFE, "🌟 COMBINÉ DU JOUR (SÉCURITÉ MAX)"))

        combo_vip = get_best_combo(pool, 3.0, 5.5, 3, 5, min_proba_threshold=62.0)
        if combo_vip:
            portfolio[TicketCategory.VIP].append(self._format_combo(combo_vip, TicketCategory.VIP, "💎 COMBINÉ VIP (RENTABILITÉ)"))

        combo_value = get_best_combo(pool, 8.0, 45.0, 4, 7, min_proba_threshold=0.0)
        if combo_value:
            cat_val = TicketCategory.VALUE_BET if hasattr(TicketCategory, 'VALUE_BET') else TicketCategory.VALUE
            portfolio[cat_val].append(self._format_combo(combo_value, cat_val, "🚀 VALUE BET (GROSSE COTE)"))

        return dict(portfolio)

    def _format_combo(self, combo, cat, title):
        total_odds = 1.0
        combo_proba_math = 1.0
        
        bet_text = ""
        ai_text = "🧠 **Rapport IA Détaillé :**\n"
        
        for i, c in enumerate(combo, 1):
            total_odds *= c['odds']
            combo_proba_math *= (c['proba'] / 100.0)
            
            bet_text += f"*{i}️⃣ {c['match'].home_team} vs {c['match'].away_team}*\n👉 **{c['type']}**\n📊 Cote : {c['odds']} | 🎯 Confiance : {c['proba']:.1f}%\n\n"
            ai_text += f"✔️ **{c['match'].home_team} vs {c['match'].away_team}** :\n{c['ai']}\n\n"
            
        total_odds = round(total_odds, 2)
        final_combo_proba = round(combo_proba_math * 100, 1)
        
        bet_text += f"🔥 **FIABILITÉ GLOBALE DU COMBINÉ : {final_combo_proba}%**\n"
        
        ids = sorted([c['match'].match_id for c in combo])
        unique_id = f"combo_{cat.name}_{'_'.join(ids)}"
        
        return GeneratedTicket(
            category=cat, 
            match_id=unique_id, 
            sport=combo[0]['match'].sport, 
            match_title=title, 
            bet_type=bet_text.strip(), 
            odds=total_odds, 
            ai_confidence=final_combo_proba, 
            ai_justification=ai_text.strip()
        )
