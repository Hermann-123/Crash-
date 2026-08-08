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
        
        if base_confidence < 45.0:
            return AIAuditReport(confidence_score=base_confidence, justification='{"profil": "VETO", "analyse": "Match trop incertain."}', is_approved=False)

        if not settings.GROQ_API_KEY:
            return AIAuditReport(confidence_score=base_confidence, justification='{"profil": "INCERTAIN", "analyse": "Validation mathématique sans IA."}', is_approved=True)

        # 🧠 CERVEAU 2 : Le Profiler Indépendant en format JSON
        prompt = f"""
        En tant que trader sportif expert, profile la physionomie tactique de ce match : {match.home_team} vs {match.away_team}.
        
        Mission : Renvoie UNIQUEMENT un objet JSON valide. Interdiction d'écrire du texte en dehors du JSON.
        Le JSON doit contenir exactement deux clés :
        1. "profil" : Choisis UN SEUL mot parmi : [OFFENSIF, DÉFENSIF, DÉSÉQUILIBRÉ, INCERTAIN, VETO].
           - OFFENSIF : Défenses faibles, match à spectacle.
           - DÉFENSIF : Match fermé, tactique, peu de buts prévus.
           - DÉSÉQUILIBRÉ : Un grand favori qui va dominer.
           - INCERTAIN : Équipes de même niveau, indécis.
           - VETO : Piège de bookmaker, match amical, coupe sans enjeu.
        2. "analyse" : UNE SEULE phrase percutante (max 30 mots) justifiant ce profil.
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
                    # Nettoyage au cas où l'IA mettrait des balises markdown
                    if ans.startswith("```json"): ans = ans[7:]
                    if ans.startswith("```"): ans = ans[3:]
                    if ans.endswith("```"): ans = ans[:-3]
                    ans = ans.strip()
                    
                    # Vérification de la validité du JSON
                    json.loads(ans)
                    
                    is_approved = "VETO" not in ans.upper()
                    return AIAuditReport(confidence_score=round(base_confidence, 1), justification=ans, is_approved=is_approved)
        except Exception as e: 
            logger.error(f"Erreur IA : {e}")
            pass
        
        return AIAuditReport(confidence_score=base_confidence, justification='{"profil": "VETO", "analyse": "Échec de lecture IA."}', is_approved=False)

class TicketFactory:
    def build_portfolio(self, evaluated_matches: List[Tuple[MatchData, SimulationResult, AIAuditReport]]):
        portfolio = defaultdict(list)
        pool = []
        
        for match, sim, ai in evaluated_matches:
            if not ai.is_approved: continue
            
            p_home, p_draw, p_away = sim.proba_home, sim.proba_draw, sim.proba_away
            base_confidence = max(p_home, p_draw, p_away) 
            
            # Extraction du JSON de l'IA
            try:
                ai_data = json.loads(ai.justification)
                ai_profile = ai_data.get("profil", "INCERTAIN").upper()
                ai_text = ai_data.get("analyse", "Aucune analyse disponible.")
            except:
                continue # Si le JSON est cassé, on rejette le match
                
            # 🛡️ FILTRE BOOKMAKER : Cote max autorisée pour les paris "Sécurité"
            MAX_SAFE_ODDS = 1.75
            
            # ⚖️ LE JUGE SUPRÊME : Croisement des Mathématiques et du Profil IA

            # 1. MATCHS DÉSÉQUILIBRÉS (Domination attendue par l'IA)
            if ai_profile == "DÉSÉQUILIBRÉ":
                if p_home >= 55.0:
                    odds = max(1.35, round(100.0/p_home*0.92, 2))
                    if odds <= MAX_SAFE_ODDS:
                        pool.append({"match": match, "type": f"Victoire {match.home_team} (1)", "odds": odds, "proba": p_home, "ai": ai_text})
                
                if p_away >= 55.0:
                    odds = max(1.35, round(100.0/p_away*0.92, 2))
                    if odds <= MAX_SAFE_ODDS:
                        pool.append({"match": match, "type": f"Victoire {match.away_team} (2)", "odds": odds, "proba": p_away, "ai": ai_text})

            # 2. MATCHS DÉFENSIFS (Fermé, peu de buts attendus)
            elif ai_profile == "DÉFENSIF":
                if sim.proba_over_2_5 < 35.0:
                    odds = max(1.40, round(100.0/(100-sim.proba_over_2_5)*0.92, 2))
                    if odds <= MAX_SAFE_ODDS:
                        pool.append({"match": match, "type": "Moins de 2,5 buts dans le match", "odds": odds, "proba": 100 - sim.proba_over_2_5, "ai": ai_text})
                
                if sim.proba_btts < 40.0:
                    odds = max(1.40, round(100.0/(100-sim.proba_btts)*0.92, 2))
                    if odds <= MAX_SAFE_ODDS:
                        pool.append({"match": match, "type": "Les 2 équipes marquent (BTTS : Non)", "odds": odds, "proba": 100 - sim.proba_btts, "ai": ai_text})

            # 3. MATCHS OFFENSIFS (Spectacle, buts des deux côtés)
            elif ai_profile == "OFFENSIF":
                if sim.proba_over_1_5 >= 78.0:
                    pool.append({"match": match, "type": "Plus de 1,5 buts dans le match", "odds": max(1.20, round(100.0/sim.proba_over_1_5*0.92, 2)), "proba": sim.proba_over_1_5, "ai": ai_text})
                
                if sim.proba_over_2_5 >= 60.0:
                    pool.append({"match": match, "type": "Plus de 2,5 buts dans le match", "odds": max(1.55, round(100.0/sim.proba_over_2_5*0.92, 2)), "proba": sim.proba_over_2_5, "ai": ai_text})
                
                if sim.proba_btts >= 62.0:
                    pool.append({"match": match, "type": "Les 2 équipes marquent (BTTS : Oui)", "odds": max(1.60, round(100.0/sim.proba_btts*0.92, 2)), "proba": sim.proba_btts, "ai": ai_text})

            # 4. MATCHS INCERTAINS / PIÈGES (Sécurité maximale via Double Chance)
            elif ai_profile == "INCERTAIN":
                if p_home + p_draw >= 82.0:
                    pool.append({"match": match, "type": f"Double Chance (1X) : {match.home_team} ou Nul", "odds": max(1.15, round(100.0/(p_home+p_draw)*0.92, 2)), "proba": p_home+p_draw, "ai": f"Match piège, mais {match.home_team} devrait éviter la défaite à domicile."})
                if p_away + p_draw >= 82.0:
                    pool.append({"match": match, "type": f"Double Chance (X2) : {match.away_team} ou Nul", "odds": max(1.15, round(100.0/(p_away+p_draw)*0.92, 2)), "proba": p_away+p_draw, "ai": f"Match indécis, couverture sur {match.away_team}."})


        # 🚀 L'ALGORITHME D'ASSEMBLAGE DES TICKETS
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

        # 🌟 Combiné du Jour (Sécurité Maximale : Exige 75% de réussite minimum)
        combo_jour = get_best_combo(pool, 1.8, 3.5, 2, 4, min_proba_threshold=75.0)
        if combo_jour:
            portfolio[TicketCategory.ULTRA_SAFE].append(self._format_combo(combo_jour, TicketCategory.ULTRA_SAFE, "🌟 COMBINÉ DU JOUR (SÉCURITÉ MAX)"))

        # 💎 Combiné VIP (Très haute rentabilité : Exige 62% de réussite minimum)
        combo_vip = get_best_combo(pool, 3.0, 5.5, 3, 5, min_proba_threshold=62.0)
        if combo_vip:
            portfolio[TicketCategory.VIP].append(self._format_combo(combo_vip, TicketCategory.VIP, "💎 COMBINÉ VIP (RENTABILITÉ)"))

        # 🚀 Value Bet (Toutes les opportunités)
        combo_value = get_best_combo(pool, 6.0, 45.0, 4, 7, min_proba_threshold=0.0)
        if combo_value:
            cat_val = TicketCategory.VALUE_BET if hasattr(TicketCategory, 'VALUE_BET') else TicketCategory.VALUE
            portfolio[cat_val].append(self._format_combo(combo_value, cat_val, "🚀 VALUE BET (GROSSE COTE)"))

        return dict(portfolio)

    def _format_combo(self, combo, cat, title):
        total_odds = 1.0
        combo_proba_math = 1.0
        
        bet_text = ""
        ai_text = "🧠 **Rapport IA par Consensus :**\n"
        
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
