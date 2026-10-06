"""
Premium Football Bot - Système complet de pronostics football
Moteur d'aide à la décision avec analyse probabiliste, filtrage intelligent et publication Telegram
"""

# ==========================================
# IMPORTS GLOBAUX
# ==========================================
import asyncio
import json
import math
import random
import uuid
import logging
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==========================================
# 1. CONFIGURATION ET CORE
# ==========================================
class Settings(BaseSettings):
    PORT: int = 8000
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHANNEL_ID: str = ""
    SCAN_INTERVAL_MINUTES: int = 60
    ODDS_API_KEY: str = ""
    FOOTBALL_DATA_API_KEY: str = ""
    AI_API_KEY: str = ""
    
    class Config:
        env_file = ".env"

settings = Settings()

# Configuration des logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("PremiumBot")

# Verrou pour éviter les scans concurrents
scan_lock = asyncio.Lock()

# Cache en mémoire minimal et état global
global_state = {
    "last_scan_summary": None,
    "current_portfolio": None,
    "is_scanning": False
}

# ==========================================
# 2. MODÈLES DE DONNÉES
# ==========================================
class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class Match(BaseModel):
    match_id: str
    home_team: str
    away_team: str
    league: str
    start_time: datetime
    odds: Optional[Dict[str, float]] = None

class TeamForm(BaseModel):
    team_name: str
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_scored: int = 0
    goals_conceded: int = 0
    recent_results: List[str] = []
    is_home: bool = True

class SimulationResult(BaseModel):
    match_id: str
    expected_home_goals: float
    expected_away_goals: float
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over15_prob: float
    under45_prob: float
    btts_yes_prob: float
    btts_no_prob: float
    scenario_label: str

class ConfidenceAudit(BaseModel):
    match_id: str
    confidence_score: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    is_approved: bool
    reasons: List[str]

class Pick(BaseModel):
    match_id: str
    market: str
    selection: str
    confidence: int
    rationale: str

class Ticket(BaseModel):
    ticket_id: str
    category: str
    selections: List[Pick]
    cumulative_confidence: float
    rationale: str

class TicketPortfolio(BaseModel):
    scan_date: datetime
    total_matches_analyzed: int
    matches_approved: int
    tickets: List[Ticket]

class AIAnalysis(BaseModel):
    match_id: str
    confidence_adjustment: int
    approved: bool
    reasoning: str

# ==========================================
# 3. DATA PROVIDERS
# ==========================================
async def fetch_upcoming_matches() -> List[Match]:
    """Récupère les matchs à venir (simulation pour le test)"""
    logger.info("Récupération des matchs à venir...")
    try:
        # ICI: Appel API réel en production
        # async with httpx.AsyncClient() as client:
        #     response = await client.get("https://api.football-data.org/v4/matches", ...)
        
        matches = []
        teams = [
            ("PSG", "Marseille"), ("Real Madrid", "Barcelona"), 
            ("Man City", "Liverpool"), ("Bayern", "Dortmund"),
            ("Juventus", "Inter"), ("Arsenal", "Chelsea")
        ]
        leagues = ["Ligue 1", "La Liga", "Premier League", "Bundesliga", "Serie A"]
        
        for i, (home, away) in enumerate(teams):
            matches.append(Match(
                match_id=f"match_{i+1}",
                home_team=home,
                away_team=away,
                league=leagues[i % len(leagues)],
                start_time=datetime.now() + timedelta(hours=i+2)
            ))
        logger.info(f"{len(matches)} matchs récupérés avec succès.")
        return matches
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des matchs: {e}")
        return []

async def get_team_form(team_name: str, is_home: bool) -> TeamForm:
    """Récupère la forme récente d'une équipe (simulation pour le test)"""
    logger.debug(f"Récupération de la forme pour {team_name} (Domicile: {is_home})")
    try:
        # ICI: Appel API réel en production
        results_pool = ["W", "W", "D", "L", "W", "D", "W", "L", "L", "D"]
        recent = random.sample(results_pool, 5)
        
        wins = recent.count("W")
        draws = recent.count("D")
        losses = recent.count("L")
        
        goals_scored = (wins * random.randint(1, 3)) + (draws * random.randint(0, 1))
        goals_conceded = (losses * random.randint(1, 2)) + (draws * random.randint(0, 1)) + random.randint(0, 2)
        
        return TeamForm(
            team_name=team_name,
            wins=wins, draws=draws, losses=losses,
            goals_scored=goals_scored, goals_conceded=goals_conceded,
            recent_results=recent,
            is_home=is_home
        )
    except Exception as e:
        logger.error(f"Erreur récupération forme {team_name}: {e}")
        return TeamForm(team_name=team_name, wins=1, draws=2, losses=2, 
                        goals_scored=4, goals_conceded=5, recent_results=["D","D","L","W","L"], is_home=is_home)

# ==========================================
# 4. MOTEUR D'ANALYSE FOOTBALL
# ==========================================
def poisson_probability(lambda_val: float, k: int) -> float:
    """Calcule la probabilité de k événements avec une distribution de Poisson."""
    return (math.pow(lambda_val, k) * math.exp(-lambda_val)) / math.factorial(k)

def analyze_match(match: Match, home_form: TeamForm, away_form: TeamForm) -> SimulationResult:
    """Analyse statistique complète d'un match"""
    logger.info(f"Analyse statistique : {match.home_team} vs {match.away_team}")
    
    league_avg_home = 1.45
    league_avg_away = 1.15
    
    home_attack = (home_form.goals_scored / 5) / (league_avg_home / 1.5)
    away_defense = (away_form.goals_conceded / 5) / (league_avg_away / 1.5)
    
    away_attack = (away_form.goals_scored / 5) / (league_avg_away / 1.5)
    home_defense = (home_form.goals_conceded / 5) / (league_avg_home / 1.5)
    
    home_advantage = 1.15 
    
    expected_home = (home_attack * away_defense * league_avg_home) * home_advantage
    expected_away = (away_attack * home_defense * league_avg_away)
    
    home_stability = 1.0 - (home_form.losses / 5) * 0.1
    away_stability = 1.0 - (away_form.losses / 5) * 0.1
    
    expected_home *= home_stability
    expected_away *= away_stability
    
    expected_home = max(0.3, min(3.5, expected_home))
    expected_away = max(0.2, min(3.0, expected_away))
    
    home_win, draw, away_win = 0.0, 0.0, 0.0
    over15, under45, btts_yes = 0.0, 0.0, 0.0
    
    for i in range(6):
        for j in range(6):
            p = poisson_probability(expected_home, i) * poisson_probability(expected_away, j)
            if i > j: home_win += p
            elif i == j: draw += p
            else: away_win += p
            
            if i + j > 1: over15 += p
            if i + j < 4: under45 += p
            if i > 0 and j > 0: btts_yes += p
            
    btts_no = 1.0 - btts_yes
    
    max_prob = max(home_win, draw, away_win)
    if max_prob < 0.42:
        scenario = "Match très équilibré / indécis"
    elif home_win > 0.55:
        scenario = "Fort avantage domicile"
    elif away_win > 0.45:
        scenario = "Avantage extérieur clair"
    elif over15 > 0.75 and max_prob < 0.50:
        scenario = "Match ouvert, scénario over"
    elif under45 > 0.70 and btts_no > 0.60:
        scenario = "Match fermé, scénario under"
    else:
        scenario = "Avantage domicile prudent"

    return SimulationResult(
        match_id=match.match_id,
        expected_home_goals=round(expected_home, 2),
        expected_away_goals=round(expected_away, 2),
        home_win_prob=round(home_win, 3),
        draw_prob=round(draw, 3),
        away_win_prob=round(away_win, 3),
        over15_prob=round(over15, 3),
        under45_prob=round(under45, 3),
        btts_yes_prob=round(btts_yes, 3),
        btts_no_prob=round(btts_no, 3),
        scenario_label=scenario
    )

# ==========================================
# 5. MOTEUR DE CONFIANCE
# ==========================================
def evaluate_confidence(match: Match, sim: SimulationResult, home_form: TeamForm, away_form: TeamForm) -> ConfidenceAudit:
    """Évalue la confiance et décide si un match est exploitable"""
    logger.info(f"Évaluation de la confiance pour {match.match_id}")
    score = 50
    reasons = []
    
    max_1x2 = max(sim.home_win_prob, sim.draw_prob, sim.away_win_prob)
    if max_1x2 >= 0.55:
        score += 20
        reasons.append("Scénario 1X2 clair et déséquilibré.")
    elif max_1x2 >= 0.45:
        score += 10
        reasons.append("Léger avantage identifié.")
    else:
        score -= 20
        reasons.append("Match trop équilibré, risque de match nul élevé.")
        
    home_consistency = home_form.wins + home_form.draws
    away_consistency = away_form.wins + away_form.draws
    if home_consistency >= 3 and away_consistency >= 3:
        score += 10
        reasons.append("Les deux équipes sont régulières.")
    elif home_form.losses >= 3 or away_form.losses >= 3:
        score -= 15
        reasons.append("Forte instabilité détectée (beaucoup de défaites).")
        
    if sim.over15_prob > 0.75:
        score += 10
        reasons.append("Potentiel Over 1.5 très élevé.")
    if sim.under45_prob > 0.75:
        score += 10
        reasons.append("Potentiel Under 4.5 très élevé.")
        
    if max_1x2 < 0.40 and sim.over15_prob < 0.65 and sim.under45_prob < 0.65:
        score -= 30
        reasons.append("Aucun edge statistique clair sur aucun marché.")

    score = max(0, min(100, score))
    is_approved = score >= 65
    
    if score >= 80: risk = RiskLevel.LOW
    elif score >= 65: risk = RiskLevel.MEDIUM
    else: risk = RiskLevel.HIGH
    
    if not is_approved:
        reasons.insert(0, "REJETÉ : Score de confiance insuffisant.")
        
    logger.info(f"Confiance {match.match_id}: {score}/100 | Approuvé: {is_approved} | Raisons: {reasons}")
    
    return ConfidenceAudit(
        match_id=match.match_id,
        confidence_score=score,
        risk_level=risk,
        is_approved=is_approved,
        reasons=reasons
    )

# ==========================================
# 6. MARKET BUILDER
# ==========================================
def build_picks(match: Match, sim: SimulationResult, audit: ConfidenceAudit) -> List[Pick]:
    """Génère des picks exploitables à partir de l'analyse"""
    if not audit.is_approved:
        logger.info(f"Aucun pick généré pour {match.match_id} (match rejeté).")
        return []
        
    picks = []
    logger.info(f"Construction des picks pour {match.match_id}")
    
    max_1x2 = max(sim.home_win_prob, sim.draw_prob, sim.away_win_prob)
    
    if sim.home_win_prob > 0.60:
        picks.append(Pick(match_id=match.match_id, market="1X2", selection="1", 
                          confidence=audit.confidence_score, rationale=f"Domination domicile attendue ({sim.home_win_prob:.0%})"))
    elif sim.away_win_prob > 0.55:
        picks.append(Pick(match_id=match.match_id, market="1X2", selection="2", 
                          confidence=audit.confidence_score, rationale=f"Avantage extérieur solide ({sim.away_win_prob:.0%})"))
                          
    elif sim.home_win_prob + sim.draw_prob > 0.75:
        picks.append(Pick(match_id=match.match_id, market="Double Chance", selection="1X", 
                          confidence=audit.confidence_score, rationale="Domicile ou nul très probable pour sécuriser"))
    elif sim.away_win_prob + sim.draw_prob > 0.70:
        picks.append(Pick(match_id=match.match_id, market="Double Chance", selection="X2", 
                          confidence=audit.confidence_score, rationale="Extérieur ou nul couvre le risque de match nul"))
                          
    if sim.over15_prob > 0.78:
        picks.append(Pick(match_id=match.match_id, market="Over/Under", selection="Over 1.5", 
                          confidence=audit.confidence_score, rationale=f"Match ouvert attendu ({sim.over15_prob:.0%})"))
                          
    if sim.under45_prob > 0.80:
        picks.append(Pick(match_id=match.match_id, market="Over/Under", selection="Under 4.5", 
                          confidence=audit.confidence_score, rationale=f"Match fermé, peu de buts attendus ({sim.under45_prob:.0%})"))
                          
    if sim.btts_no_prob > 0.65 and sim.expected_away_goals < 0.9:
        picks.append(Pick(match_id=match.match_id, market="BTTS", selection="No", 
                          confidence=audit.confidence_score, rationale="Attaque extérieure fragile, clean sheet probable"))

    if not picks:
        logger.warning(f"Match approuvé mais aucun marché n'atteint les seuils de sécurité pour {match.match_id}.")
        
    return picks

# ==========================================
# 7. AI ANALYZER (OPTIONNEL)
# ==========================================
async def analyze_with_ai(match: Match, sim: SimulationResult, audit: ConfidenceAudit) -> AIAnalysis:
    """Seconde lecture IA - ne remplace pas les stats"""
    if not settings.AI_API_KEY:
        logger.info("Pas de clé IA configurée. Fallback local appliqué.")
        return AIAnalysis(
            match_id=match.match_id,
            confidence_adjustment=0,
            approved=audit.is_approved,
            reasoning="Analyse IA désactivée (clé manquante). Fallback sur stats pures."
        )

    logger.info(f"Appel IA pour seconde lecture sur {match.match_id}")
    try:
        # ICI: Intégration réelle OpenAI/Anthropic en production
        adjustment = -5 if "Derby" in match.league else 0
        reasoning = "Contexte de derby identifié. Légère baisse de confiance appliquée par sécurité." if adjustment < 0 else "Contexte standard, validation du scénario statistique."
        
        return AIAnalysis(
            match_id=match.match_id,
            confidence_adjustment=adjustment,
            approved=audit.is_approved and (audit.confidence_score + adjustment) >= 60,
            reasoning=reasoning
        )
    except Exception as e:
        logger.error(f"Erreur IA: {e}. Fallback appliqué.")
        return AIAnalysis(match_id=match.match_id, confidence_adjustment=0, approved=audit.is_approved, reasoning="Fallback local suite à une erreur IA.")

# ==========================================
# 8. TICKET FACTORY
# ==========================================
def build_tickets(all_picks: List[Pick]) -> List[Ticket]:
    """Assemble les picks validés en tickets cohérents"""
    if not all_picks:
        logger.warning("Aucun pick disponible. Aucun ticket ne sera généré.")
        return []

    logger.info(f"Assemblage des tickets à partir de {len(all_picks)} picks validés...")
    tickets = []

    elite_picks = [p for p in all_picks if p.confidence >= 80]
    if len(elite_picks) >= 1:
        elite_selections = sorted(elite_picks, key=lambda x: x.confidence, reverse=True)[:2]
        cum_conf = sum(p.confidence for p in elite_selections) / len(elite_selections)
        tickets.append(Ticket(
            ticket_id=str(uuid.uuid4())[:8],
            category="high_confidence",
            selections=elite_selections,
            cumulative_confidence=round(cum_conf, 1),
            rationale="Sélection d'élite. Scénarios statistiques très clairs et robustes."
        ))
        logger.info(f"Ticket 'High Confidence' créé avec {len(elite_selections)} sélections.")

    safe_picks = [p for p in all_picks if p.confidence >= 70 and p.market in ["Double Chance", "Over/Under"]]
    if len(safe_picks) >= 2:
        safe_selections = sorted(safe_picks, key=lambda x: x.confidence, reverse=True)[:3]
        cum_conf = sum(p.confidence for p in safe_selections) / len(safe_selections)
        tickets.append(Ticket(
            ticket_id=str(uuid.uuid4())[:8],
            category="low_risk",
            selections=safe_selections,
            cumulative_confidence=round(cum_conf, 1),
            rationale="Ticket de sécurité. Marchés couverts et probabilités élevées."
        ))
        logger.info(f"Ticket 'Low Risk' créé avec {len(safe_selections)} sélections.")

    if len(all_picks) >= 2:
        balanced_selections = sorted(all_picks, key=lambda x: x.confidence, reverse=True)[:2]
        cum_conf = sum(p.confidence for p in balanced_selections) / len(balanced_selections)
        tickets.append(Ticket(
            ticket_id=str(uuid.uuid4())[:8],
            category="balanced",
            selections=balanced_selections,
            cumulative_confidence=round(cum_conf, 1),
            rationale="Ticket équilibré. Meilleur ratio risque/rendement du jour."
        ))
        logger.info(f"Ticket 'Balanced' créé avec {len(balanced_selections)} sélections.")

    logger.info(f"Assemblage terminé. {len(tickets)} tickets finaux générés.")
    return tickets

# ==========================================
# 9. STORAGE
# ==========================================
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
SUMMARY_FILE = DATA_DIR / "scan_summary.json"

def save_portfolio(portfolio: TicketPortfolio):
    try:
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(portfolio.model_dump(mode="json"), f, indent=4, ensure_ascii=False)
        logger.info("Portefeuille de tickets sauvegardé sur le disque.")
    except Exception as e:
        logger.error(f"Erreur sauvegarde portefeuille: {e}")

def load_portfolio() -> Optional[Dict[str, Any]]:
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erreur lecture portefeuille: {e}")
    return None

def save_scan_summary(summary: Dict[str, Any]):
    try:
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=4, ensure_ascii=False)
        logger.info("Résumé du scan sauvegardé.")
    except Exception as e:
        logger.error(f"Erreur sauvegarde résumé: {e}")

def load_scan_summary() -> Optional[Dict[str, Any]]:
    if SUMMARY_FILE.exists():
        try:
            with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erreur lecture résumé: {e}")
    return None

# ==========================================
# 10. BOT TELEGRAM
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏆 <b>Premium Football Bot</b>\n\n"
        "Bienvenue. Ce moteur analyse les matchs et ne publie que des scénarios robustes.\n\n"
        "Commandes :\n"
        "/status - État du système\n"
        "/tickets - Voir les tickets du jour\n"
        "/scan_now - Forcer un scan immédiat",
        parse_mode="HTML"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = global_state.get("last_scan_summary") or load_scan_summary()
    if not summary:
        await update.message.reply_text("⚪ Aucun scan effectué pour le moment.")
        return

    msg = (
        f"📊 <b>État du système</b>\n\n"
        f"Dernier scan : {summary.get('scan_date', 'N/A')}\n"
        f"Matchs analysés : {summary.get('total_matches_analyzed', 0)}\n"
        f"Matchs approuvés : {summary.get('matches_approved', 0)}\n"
        f"Tickets générés : {summary.get('tickets_count', 0)}\n"
        f"Statut scan : {'🟢 En cours' if global_state.get('is_scanning') else '⚪ Inactif'}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def tickets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    portfolio_data = global_state.get("current_portfolio") or load_portfolio()
    
    if not portfolio_data or not portfolio_data.get("tickets"):
        await update.message.reply_text(
            "⛔ <b>Aucun ticket fiable disponible.</b>\n\n"
            "Le moteur n'a détecté aucun scénario robuste aujourd'hui. "
            "Mieux vaut ne pas jouer que jouer à l'aveugle.",
            parse_mode="HTML"
        )
        return

    msg = f"🎟 <b>Tickets Disponibles</b> ({portfolio_data.get('scan_date', '')})\n\n"
    
    for ticket in portfolio_data["tickets"]:
        category_emoji = {"high_confidence": "🔥", "low_risk": "🛡", "balanced": "⚖️"}.get(ticket["category"], "🎟")
        msg += f"{category_emoji} <b>{ticket['category'].upper()}</b> (Confiance: {ticket['cumulative_confidence']}%)\n"
        msg += f"<i>{ticket['rationale']}</i>\n"
        
        for pick in ticket["selections"]:
            msg += f"  • {pick['selection']} ({pick['market']}) - {pick['rationale']}\n"
        msg += "\n"

    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n... (liste tronquée)"

    await update.message.reply_text(msg, parse_mode="HTML")

async def scan_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if global_state.get("is_scanning"):
        await update.message.reply_text("⏳ Un scan est déjà en cours, veuillez patienter.")
        return
    
    await update.message.reply_text("🔄 Lancement d'un scan manuel...")
    context.application.create_task(run_analysis_pipeline())

def create_telegram_bot() -> Optional[Application]:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN manquant. Le bot Telegram ne démarrera pas.")
        return None

    builder = Application.builder().token(settings.TELEGRAM_BOT_TOKEN)
    app = builder.build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("tickets", tickets_command))
    app.add_handler(CommandHandler("scan_now", scan_now_command))

    return app

async def send_telegram_notification(portfolio_dict: dict):
    """Envoie une notification automatique au channel"""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHANNEL_ID:
        return

    try:
        app = create_telegram_bot()
        if not app: return
        
        async with app:
            await app.initialize()
            
            tickets = portfolio_dict.get("tickets", [])
            if not tickets:
                msg = "⚪ <b>Scan terminé.</b> Aucun scénario robuste détecté. Repos pour le moteur."
            else:
                msg = f"🚨 <b>NOUVEAUX PRONOSTICS</b>\n{len(tickets)} tickets validés par le moteur.\nUtilisez /tickets dans le bot privé pour les consulter."
                
            await app.bot.send_message(chat_id=settings.TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
            logger.info("Notification Telegram envoyée au channel.")
    except Exception as e:
        logger.error(f"Erreur envoi notification Telegram: {e}")

# ==========================================
# 11. PIPELINE PRINCIPAL
# ==========================================
async def run_analysis_pipeline():
    """Cœur du réacteur : récupère, analyse, filtre, et publie."""
    if global_state.get("is_scanning"):
        logger.warning("Scan déjà en cours. Ignoré.")
        return

    async with scan_lock:
        global_state["is_scanning"] = True
        logger.info("="*50)
        logger.info("DÉMARRAGE DU PIPELINE D'ANALYSE")
        logger.info("="*50)

        try:
            matches = await fetch_upcoming_matches()
            if not matches:
                logger.error("Aucun match récupéré. Pipeline interrompu.")
                return

            all_picks = []
            approved_count = 0

            for match in matches:
                logger.info(f"--- Traitement : {match.home_team} vs {match.away_team} ---")
                
                home_form = await get_team_form(match.home_team, is_home=True)
                away_form = await get_team_form(match.away_team, is_home=False)
                
                if not home_form or not away_form:
                    logger.warning(f"Données de forme manquantes pour {match.match_id}. Match ignoré.")
                    continue

                sim_result = analyze_match(match, home_form, away_form)
                logger.info(f"Simulation OK | xG: {sim_result.expected_home_goals}-{sim_result.expected_away_goals} | Scénario: {sim_result.scenario_label}")

                audit = evaluate_confidence(match, sim_result, home_form, away_form)
                
                if not audit.is_approved:
                    logger.info(f"❌ MATCH REJETÉ : {match.home_team} vs {match.away_team} | Raisons: {'; '.join(audit.reasons)}")
                    continue

                approved_count += 1
                logger.info(f"✅ MATCH APPROUVÉ : Score {audit.confidence_score}/100")

                ai_analysis = await analyze_with_ai(match, sim_result, audit)
                if ai_analysis.confidence_adjustment != 0:
                    logger.info(f"🧠 Ajustement IA : {ai_analysis.confidence_adjustment} | {ai_analysis.reasoning}")
                    final_confidence = max(0, min(100, audit.confidence_score + ai_analysis.confidence_adjustment))
                    audit.confidence_score = final_confidence
                    audit.is_approved = ai_analysis.approved

                picks = build_picks(match, sim_result, audit)
                if picks:
                    all_picks.extend(picks)
                    logger.info(f"🎯 {len(picks)} pick(s) généré(s) pour ce match.")
                else:
                    logger.info(f"⚠️ Match approuvé mais aucun marché ne valide les seuils de sécurité.")

            tickets = build_tickets(all_picks)
            
            portfolio = TicketPortfolio(
                scan_date=datetime.now(),
                total_matches_analyzed=len(matches),
                matches_approved=approved_count,
                tickets=tickets
            )

            portfolio_dict = portfolio.model_dump(mode="json")
            save_portfolio(portfolio)
            
            summary = {
                "scan_date": portfolio.scan_date.strftime("%Y-%m-%d %H:%M"),
                "total_matches_analyzed": portfolio.total_matches_analyzed,
                "matches_approved": portfolio.matches_approved,
                "tickets_count": len(portfolio.tickets)
            }
            save_scan_summary(summary)
            
            global_state["current_portfolio"] = portfolio_dict
            global_state["last_scan_summary"] = summary

            logger.info("="*50)
            logger.info(f"PIPELINE TERMINÉ. {len(tickets)} tickets générés.")
            logger.info("="*50)

            await send_telegram_notification(portfolio_dict)

        except Exception as e:
            logger.exception(f"Erreur critique dans le pipeline : {e}")
        finally:
            global_state["is_scanning"] = False

# ==========================================
# 12. FASTAPI APPLICATION
# ==========================================
scheduler = AsyncIOScheduler()
telegram_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    
    logger.info("Démarrage de l'application Premium Football Bot...")
    
    global_state["current_portfolio"] = load_portfolio()
    
    telegram_app = create_telegram_bot()
    if telegram_app:
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot Telegram démarré et en écoute (polling).")
    
    scheduler.add_job(
        run_analysis_pipeline, 
        'interval', 
        minutes=settings.SCAN_INTERVAL_MINUTES, 
        id='scan_job', 
        name='Analyse Périodique'
    )
    scheduler.start()
    logger.info(f"Scheduler démarré. Scan toutes les {settings.SCAN_INTERVAL_MINUTES} minutes.")
    
    asyncio.create_task(run_analysis_pipeline())

    yield

    logger.info("Arrêt de l'application...")
    scheduler.shutdown(wait=False)
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
    logger.info("Arrêt terminé.")

app = FastAPI(title="Premium Football Bot API", lifespan=lifespan)

@app.get("/")
async def health_check():
    return {
        "status": "online",
        "service": "Premium Football Bot",
        "is_scanning": global_state.get("is_scanning", False)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("premium_bot:app", host="0.0.0.0", port=settings.PORT, reload=False)
