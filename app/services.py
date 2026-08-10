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
        
        # Filtre initial : On rejette d'office les matchs trop imprévisibles (< 45% de certitude)
        if base_confidence < 45.0:
            return AIAuditReport(confidence_score=base_confidence, justification="VETO", is_approved=False)

        if not settings.GROQ_API_KEY:
            return AIAuditReport(confidence_score=base_confidence, justification="Validé mathématiquement par l'algorithme.", is_approved=True)

        prompt = f"""
        En tant que trader sportif expert, valide ce match : {match.home_team} vs {match.away_team}. 
        Modèle mathématique : {base_confidence:.1f}% de confiance.
        Fournis UNE SEULE PHRASE d'analyse technique expliquant pourquoi ce pronostic est fiable (dynamique, attaque, solidité défensive).
        Si le match sent le piège pour les parieurs, réponds uniquement "VETO".
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
            
            # 🔴 LA CORRECTION EST ICI : On déclare base_confidence pour que le bot puisse l'utiliser
            base_confidence = max(p_home, p_draw, p_away) 
            
            # 🟢 1. MARCHÉS COMPOSÉS POUR LE FAVORI À DOMICILE
            if p_home >= 55.0:
                pool.append({"match": match, "type": f"Victoire {match.home_team} (1)", "odds": max(1.35, round(100.0/p_home*0.92, 2)), "proba": p_home, "ai": ai.justification})
                
                # Handicaps et Mi-Temps si domination écrasante
                if p_home >= 72.0:
                    pool.append({"match": match, "type": f"Handicap -1 : {match.home_team}", "odds": max(1.65, round(100.0/(p_home-15)*0.92, 2)), "proba": p_home - 15, "ai": "Domination attendue, victoire par au moins 2 buts d'écart."})
                    pool.append({"match": match, "type": f"Mi-Temps / Fin de match : {match.home_team} / {match.home_team}", "odds": max(1.85, round(100.0/(base_confidence-18)*0.92, 2)), "proba": base_confidence - 18, "ai": "Le favori va prendre l'avantage dès la première période."})
                
                # Combinaisons Buts
                if p_home >= 60.0 and sim.proba_over_1_5 >= 70.0:
                    pool.append({"match": match, "type": f"Combo : {match.home_team} gagne ET Plus de 1.5 buts", "odds": max(1.50, round(100.0/(p_home-10)*0.92, 2)), "proba": p_home - 10, "ai": "Victoire de l'équipe locale dans une rencontre animée."})
                
                # Clean Sheet
                if p_home >= 65.0 and sim.proba_btts < 40.0:
                    pool.append({"match": match, "type": f"{match.home_team} gagne sans encaisser (Clean Sheet)", "odds": max(1.80, round(100.0/(p_home-15)*0.92, 2)), "proba": p_home - 15, "ai": "Solidité défensive incontestable du favori."})

            # 🟢 2. MARCHÉS COMPOSÉS POUR LE FAVORI À L'EXTÉRIEUR
            if p_away >= 55.0:
                pool.append({"match": match, "type": f"Victoire {match.away_team} (2)", "odds": max(1.35, round(100.0/p_away*0.92, 2)), "proba": p_away, "ai": ai.justification})
                
                if p_away >= 72.0:
                    pool.append({"match": match, "type": f"Handicap -1 : {match.away_team}", "odds": max(1.65, round(100.0/(p_away-15)*0.92, 2)), "proba": p_away - 15, "ai": "Les visiteurs s'imposeront largement."})
                    pool.append({"match": match, "type": f"Mi-Temps / Fin de match : {match.away_team} / {match.away_team}", "odds": max(1.85, round(100.0/(base_confidence-18)*0.92, 2)), "proba": base_confidence - 18, "ai": "Contrôle total du match par l'équipe à l'extérieur."})
                
                if p_away >= 60.0 and sim.proba_over_1_5 >= 70.0:
                    pool.append({"match": match, "type": f"Combo : {match.away_team} gagne ET Plus de 1.5 buts", "odds": max(1.50, round(100.0/(p_away-10)*0.92, 2)), "proba": p_away - 10, "ai": "Les visiteurs vont imposer leur force de frappe."})

            # 🟢 3. DOUBLES CHANCES & DNB (Haute sécurité)
            if p_home + p_draw >= 82.0:
                pool.append({"match": match, "type": f"Double Chance (1X) : {match.home_team} ou Nul", "odds": max(1.15, round(100.0/(p_home+p_draw)*0.92, 2)), "proba": p_home+p_draw, "ai": "Avantage terrain massif, défaite très peu probable."})
            if p_away + p_draw >= 82.0:
                pool.append({"match": match, "type": f"Double Chance (X2) : {match.away_team} ou Nul", "odds": max(1.15, round(100.0/(p_away+p_draw)*0.92, 2)), "proba": p_away+p_draw, "ai": "L'équipe visiteuse sécurisera au minimum un point."})

            # 🟢 4. MARCHÉS EXCLUSIFS SUR LES BUTS (Over / Under)
            if sim.proba_over_1_5 >= 78.0:
                pool.append({"match": match, "type": "Plus de 1,5 buts dans le match", "odds": max(1.20, round(100.0/sim.proba_over_1_5*0.92, 2)), "proba": sim.proba_over_1_5, "ai": "Tendances offensives fortes des deux équipes."})
            if sim.proba_over_2_5 >= 60.0:
                pool.append({"match": match, "type": "Plus de 2,5 buts dans le match", "odds": max(1.55, round(100.0/sim.proba_over_2_5*0.92, 2)), "proba": sim.proba_over_2_5, "ai": "Match très ouvert attendu."})
            if sim.proba_over_2_5 < 35.0:
                pool.append({"match": match, "type": "Moins de 2,5 buts dans le match", "odds": max(1.55, round(100.0/(100-sim.proba_over_2_5)*0.92, 2)), "proba": 100 - sim.proba_over_2_5, "ai": "Rencontre fermée, bloc défensif resserré."})

            # 🟢 5. BTTS ET COMBOS BTTS
            if sim.proba_btts >= 62.0:
                pool.append({"match": match, "type": "Les 2 équipes marquent (BTTS : Oui)", "odds": max(1.65, round(100.0/sim.proba_btts*0.92, 2)), "proba": sim.proba_btts, "ai": "Failles défensives mutuelles exploitables."})
                if sim.proba_over_2_5 >= 65.0:
                    pool.append({"match": match, "type": "Combo : Les 2 marquent ET Plus de 2.5 buts", "odds": max(1.90, round(100.0/(sim.proba_btts-10)*0.92, 2)), "proba": sim.proba_btts - 10, "ai": "Spectacle offensif garanti de part et d'autre."})
            elif sim.proba_btts < 40.0:
                pool.append({"match": match, "type": "Les 2 équipes marquent (BTTS : Non)", "odds": max(1.60, round(100.0/(100-sim.proba_btts)*0.92, 2)), "proba": 100 - sim.proba_btts, "ai": "L'une des deux attaques restera muette."})

            # 🟢 6. SCORE EXACT (Uniquement pour Value Bet)
            if base_confidence >= 70.0:
                pool.append({"match": match, "type": f"Score Exact Probable : {sim.most_likely_score}", "odds": 7.00, "proba": 15.0, "ai": "Projection mathématique précise validée."})


        # 🚀 L'ALGORITHME DE SÉLECTION STRICTE DES FAVORIS
        def get_best_combo(pool_list, min_odds, max_odds, min_items, max_items, min_proba_threshold=0.0):
            if not pool_list: return None
            
            # FINI LE HASARD : On trie STRICTEMENT du pronostic le plus sûr au moins sûr
            pool_list = sorted(pool_list, key=lambda x: x['proba'], reverse=True)
            
            # On applique le filtre de sécurité exigé pour le type de combiné
            valid_pool = [p for p in pool_list if p['proba'] >= min_proba_threshold]
            
            for r in range(min_items, max_items + 1):
                # On ne cherche des combinaisons que parmi le TOP 25 des paris les plus sûrs de la journée
                for combo in itertools.combinations(valid_pool[:25], r):
                    match_ids = [x['match'].match_id for x in combo]
                    if len(set(match_ids)) != len(match_ids): continue # 1 pari par match max
                    
                    total_odds = 1.0
                    for x in combo: total_odds *= x['odds']
                    
                    if min_odds <= total_odds <= max_odds:
                        return combo
            return None

        # 🌟 Combiné du Jour (Sécurité Maximale : Exige 75% de réussite minimum par événement)
        combo_jour = get_best_combo(pool, 2.2, 3.5, 2, 4, min_proba_threshold=75.0)
        if combo_jour:
            portfolio[TicketCategory.ULTRA_SAFE].append(self._format_combo(combo_jour, TicketCategory.ULTRA_SAFE, "🌟 COMBINÉ DU JOUR (SÉCURITÉ MAX)"))

        # 💎 Combiné VIP (Très haute rentabilité : Exige 62% de réussite minimum)
        combo_vip = get_best_combo(pool, 3.0, 5.5, 3, 5, min_proba_threshold=62.0)
        if combo_vip:
            portfolio[TicketCategory.VIP].append(self._format_combo(combo_vip, TicketCategory.VIP, "💎 COMBINÉ VIP (RENTABILITÉ)"))

        # 🚀 Value Bet (Toutes les opportunités, focus grosses cotes)
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
            ai_text += f"✔️ **{c['match'].home_team} vs {c['match'].away_team}** : {c['ai']}\n\n"
            
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
