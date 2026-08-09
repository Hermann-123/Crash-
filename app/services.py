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

# ⚙️ 1. LE MOTEUR MATHÉMATIQUE
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

# 📊 2. LE GÉNÉRATEUR ET FILTRE (DYNAMIQUE)
class MarketEngine:
    def generate_and_filter(self, match: MatchData, sim: SimulationResult, bookmaker_odds: Dict[str, float]) -> List[MarketCandidate]:
        candidates = []
        
        if "1" in bookmaker_odds: candidates.append(self._build_candidate("1X2", f"Victoire {match.home_team}", sim.proba_home, bookmaker_odds["1"]))
        if "2" in bookmaker_odds: candidates.append(self._build_candidate("1X2", f"Victoire {match.away_team}", sim.proba_away, bookmaker_odds["2"]))
        if "O1.5" in bookmaker_odds: candidates.append(self._build_candidate("OVER_UNDER", "Plus de 1,5 buts", sim.proba_over_1_5, bookmaker_odds["O1.5"]))
        if "O2.5" in bookmaker_odds: candidates.append(self._build_candidate("OVER_UNDER", "Plus de 2,5 buts", sim.proba_over_2_5, bookmaker_odds["O2.5"]))
        if "U2.5" in bookmaker_odds: candidates.append(self._build_candidate("OVER_UNDER", "Moins de 2,5 buts", 100 - sim.proba_over_2_5, bookmaker_odds["U2.5"]))
        
        # 🚨 SUPPRESSION DU "U3.5" : On l'a retiré du code pour éviter le spam !
        
        if "BTTS_Y" in bookmaker_odds: candidates.append(self._build_candidate("BTTS", "BTTS : Oui", sim.proba_btts, bookmaker_odds["BTTS_Y"]))
        if "BTTS_N" in bookmaker_odds: candidates.append(self._build_candidate("BTTS", "BTTS : Non", 100 - sim.proba_btts, bookmaker_odds["BTTS_N"]))

        # 🛡️ FILTRE 81%
        valid_markets = [c for c in candidates if c.probability >= 81.0 and 1.15 <= c.real_odds <= 1.90]
        valid_markets.sort(key=lambda x: x.probability, reverse=True)
                
        return valid_markets[:4]

    def _build_candidate(self, m_type: str, selection: str, proba: float, real_odds: float) -> MarketCandidate:
        return MarketCandidate(market_type=m_type, selection=selection, probability=proba, real_odds=real_odds, 
                               implied_probability=(1.0 / real_odds) * 100 if real_odds > 0 else 0, 
                               edge=proba - ((1.0 / real_odds) * 100 if real_odds > 0 else 0))

# 🧠🧠 3. L'ANALYSTE À DOUBLE CERVEAU
class AIValidator:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(1) 
        self.api_url = "https://api.groq.com/openai/v1/chat/completions"

    def _extract_json(self, text: str) -> dict:
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1: return json.loads(text[start:end+1])
            return {}
        except: return {}

    async def evaluate_markets(self, match: MatchData, top_markets: List[MarketCandidate]) -> AIValidationResult:
        if not top_markets: return AIValidationResult(decision="VETO", reason="Aucun marché n'atteint les 81%.")

        market_text = "\n".join([f"- '{m.selection}' (Proba: {round(m.probability, 1)}%, Cote: {m.real_odds})" for m in top_markets])

        # 🧠 CERVEAU 1 (Obligation de privilégier les vrais paris)
        prompt_c1 = f"""
        Tu es le CERVEAU 1. Match: {match.home_team} vs {match.away_team}.
        Voici les marchés à plus de 81% de fiabilité :
        {market_text}
        
        RÈGLE D'OR : Privilégie TOUJOURS une "Victoire", un pari "Plus de buts" ou "BTTS : Oui" s'ils sont dans la liste. Ne choisis un pari "Moins de buts" que si c'est l'unique choix possible.
        
        Choisis LE MEILLEUR pari. Renvoie UNIQUEMENT un JSON strict : {{"decision": "APPROVED", "pari": "Recopie le texte exact", "raison": "..."}}
        """
        
        async with self.semaphore:
            await asyncio.sleep(1.2)
            try:
                async with httpx.AsyncClient() as client:
                    res1 = await client.post(self.api_url, headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"}, json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt_c1}]}, timeout=10.0)
                    if res1.status_code == 200:
                        ans1 = res1.json()['choices'][0]['message']['content']
                        data1 = self._extract_json(ans1)
                        chosen_sel = data1.get("pari", "")
                        
                        # 🧠 CERVEAU 2 (Recadré pour ne plus demander les actualités)
                        if data1.get("decision") == "APPROVED" and chosen_sel:
                            prompt_c2 = f"""
                            Tu es le CERVEAU 2 (Juge des Risques). Le Cerveau 1 propose '{chosen_sel}' pour {match.home_team} vs {match.away_team}.
                            
                            RÈGLE ABSOLUE : Tu n'as pas besoin de connaître l'actualité ou les blessures de ces équipes. Juge UNIQUEMENT sur la pure logique tactique de cette confrontation.
                            Si la logique globale te semble solide, réponds APPROVED. Si tu détectes une aberration, réponds VETO.
                            
                            Renvoie UNIQUEMENT un JSON strict : {{"decision": "APPROVED" ou "VETO", "raison": "Ton avis tactique (max 15 mots)"}}
                            """
                            await asyncio.sleep(1.2)
                            res2 = await client.post(self.api_url, headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"}, json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt_c2}]}, timeout=10.0)
                            
                            if res2.status_code == 200:
                                ans2 = res2.json()['choices'][0]['message']['content']
                                data2 = self._extract_json(ans2)
                                
                                if data2.get("decision") == "APPROVED":
                                    chosen_market = next((m for m in top_markets if m.selection == chosen_sel), None)
                                    if chosen_market:
                                        return AIValidationResult(decision="APPROVED", primary_market=chosen_market, reason=f"{data2.get('raison')}")
                                
                                return AIValidationResult(decision="VETO", reason=f"VETO C2: {data2.get('raison', 'Risque détecté.')}")
            except Exception as e: logger.error(f"Erreur IA : {e}")
                
        return AIValidationResult(decision="VETO", reason="Erreur de vérification IA.")

# 🚀 4. L'USINE À TICKETS
class TicketFactory:
    def build_portfolio(self, evaluated_matches: List[Tuple[MatchData, AIValidationResult]]):
        portfolio = defaultdict(list)
        pool = []
        
        for match, ai_res in evaluated_matches:
            if ai_res.decision == "APPROVED" and ai_res.primary_market:
                pool.append({"match": match, "type": ai_res.primary_market.selection, "odds": ai_res.primary_market.real_odds, "proba": ai_res.primary_market.probability, "ai": ai_res.reason})

        def get_best_combo(pool_list, min_odds, max_odds, min_items, max_items):
            pool_list = sorted(pool_list, key=lambda x: x['proba'], reverse=True)
            for r in range(min_items, max_items + 1):
                for combo in itertools.combinations(pool_list[:20], r):
                    match_ids = [x['match'].match_id for x in combo]
                    if len(set(match_ids)) != len(match_ids): continue 
                    
                    total_odds = 1.0
                    for x in combo: total_odds *= x['odds']
                    
                    if min_odds <= round(total_odds, 2) <= max_odds: return combo
            return None

        if combo_jour := get_best_combo(pool, 2.2, 3.5, 2, 2):
            portfolio[TicketCategory.ULTRA_SAFE].append(self._format_combo(combo_jour, TicketCategory.ULTRA_SAFE, "🌟 COMBINÉ DU JOUR (DOUBLE IA - 81%+)"))
            
        return dict(portfolio)

    def _format_combo(self, combo, cat, title):
        total_odds = round(np.prod([c['odds'] for c in combo]), 2)
        final_proba = round(np.prod([c['proba']/100 for c in combo]) * 100, 1)
        
        bet_text = "\n".join([f"*{i}️⃣ {c['match'].home_team} vs {c['match'].away_team}*\n👉 **{c['type']}**\n📊 Cote : {c['odds']} | 🎯 Proba Algo : {c['proba']:.1f}%\n" for i, c in enumerate(combo, 1)])
        ai_text = "\n".join([f"✔️ **{c['match'].home_team}** :\n{c['ai']}\n" for c in combo])
        
        return GeneratedTicket(
            category=cat, match_id="final", sport=SportType.SOCCER, match_title=title, 
            bet_type=bet_text.strip(), odds=total_odds, ai_confidence=final_proba, ai_justification=ai_text.strip()
        )
