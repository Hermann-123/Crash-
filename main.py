import asyncio
import httpx
import os
from datetime import datetime
from fastapi import FastAPI
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager

from app.core import settings, logger
import app.core as core_module
from app.models import MatchData, SportType

# 🔄 NOUVEAUX IMPORTS DE L'ARCHITECTURE QUANT FUND
from app.services import DixonColesEngine, MarketEngine, AIValidator, TicketFactory
from app.bot import bot, dp

soccer_engine = DixonColesEngine()
market_engine = MarketEngine()
ai_validator = AIValidator()
ticket_factory = TicketFactory()

# TA CLÉ THE ODDS API
API_KEY_ODDS = "55a670c7b44c3dcc3c9750e9f5c51da1"

async def fetch_real_odds_matches() -> list:
    # 🚨 MODIFICATION CRUCIALE : Ajout de 'totals' et 'btts' dans l'URL
    url = f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={API_KEY_ODDS}&regions=eu&markets=h2h,totals,btts"
    matches_and_odds = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=20.0)
            if response.status_code == 200:
                data = response.json()
                for m in data:
                    commence_time = m.get('commence_time', '')
                    if not commence_time.startswith(today_str):
                        continue
                        
                    if 'bookmakers' in m and len(m['bookmakers']) > 0:
                        home, away = m['home_team'], m['away_team']
                        bookmaker_odds = {}
                        
                        # 🧠 EXTRACTION DES COTES RÉELLES POUR TOUS LES MARCHÉS
                        for market in m['bookmakers'][0]['markets']:
                            m_key = market['key']
                            for outcome in market['outcomes']:
                                name = outcome['name']
                                price = outcome['price']
                                
                                if m_key == 'h2h':
                                    if name == home: bookmaker_odds["1"] = price
                                    elif name == away: bookmaker_odds["2"] = price
                                    elif name == 'Draw': bookmaker_odds["X"] = price
                                elif m_key == 'totals':
                                    pt = outcome.get('point')
                                    if name == 'Over' and pt == 1.5: bookmaker_odds["O1.5"] = price
                                    elif name == 'Over' and pt == 2.5: bookmaker_odds["O2.5"] = price
                                    elif name == 'Under' and pt == 2.5: bookmaker_odds["U2.5"] = price
                                    elif name == 'Under' and pt == 3.5: bookmaker_odds["U3.5"] = price
                                elif m_key == 'btts':
                                    if name == 'Yes': bookmaker_odds["BTTS_Y"] = price
                                    elif name == 'No': bookmaker_odds["BTTS_N"] = price

                        # 📐 RECONSTITUTION MATHÉMATIQUE DES DOUBLES CHANCES
                        if "1" in bookmaker_odds and "X" in bookmaker_odds and "2" in bookmaker_odds:
                            # Formule bookmaker : (Cote 1 * Cote Nul) / (Cote 1 + Cote Nul)
                            bookmaker_odds["1X"] = round((bookmaker_odds["1"] * bookmaker_odds["X"]) / (bookmaker_odds["1"] + bookmaker_odds["X"]), 2)
                            bookmaker_odds["X2"] = round((bookmaker_odds["2"] * bookmaker_odds["X"]) / (bookmaker_odds["2"] + bookmaker_odds["X"]), 2)

                        if "1" in bookmaker_odds and "2" in bookmaker_odds and "X" in bookmaker_odds:
                            match_data = MatchData(
                                match_id=m['id'],
                                sport=SportType.SOCCER,
                                league=m['sport_title'],
                                match_date=datetime.now(),
                                home_team=home,
                                away_team=away,
                                home_odds=bookmaker_odds["1"],
                                draw_odds=bookmaker_odds["X"],
                                away_odds=bookmaker_odds["2"]
                            )
                            # On retourne le match ET le dictionnaire de ses cotes réelles
                            matches_and_odds.append((match_data, bookmaker_odds))
                            
                            if len(matches_and_odds) >= 100:
                                break
    except Exception as e:
        logger.error(f"Erreur API : {e}")
        
    return matches_and_odds

# ⚡ NOUVELLE FONCTION : Traitement d'un seul match pour le parallélisme
async def process_single_match(match, real_odds):
    sim = soccer_engine.simulate(match)
    top_markets = market_engine.generate_and_filter(match, sim, real_odds)
    ai_report = await ai_validator.evaluate_markets(match, top_markets)
    return (match, ai_report)

async def run_platform_pipeline():
    logger.info("🔄 [SCAN] Recherche de nouveaux combinés avec Edge Quantitatif...")
    matches_and_odds = await fetch_real_odds_matches()
    
    if not matches_and_odds: return

    # 🚀 EXÉCUTION CONCURRENTE : On traite les 100 matchs en parallèle via Groq !
    tasks = [process_single_match(m, odds) for m, odds in matches_and_odds]
    evaluated = await asyncio.gather(*tasks)

    new_portfolio = ticket_factory.build_portfolio(evaluated)
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    tickets_generes = 0
    
    for category, tickets in new_portfolio.items():
        if category not in core_module.CACHE_PORTFOLIO:
            core_module.CACHE_PORTFOLIO[category] = []
            
        for new_ticket in tickets:
            daily_alert_key = f"alert_{category.name}_{today_str}"
            
            if daily_alert_key not in core_module.SENT_ALERTS:
                core_module.CACHE_PORTFOLIO[category] = [new_ticket] 
                core_module.SENT_ALERTS.add(daily_alert_key)
                tickets_generes += 1
                
                if settings.ARCHIVE_CHANNEL_ID and settings.ARCHIVE_CHANNEL_ID != "-100VOTRE_ID_ICI":
                    titre_canal = "🌟 COMBINÉ SAFE" if category.name == "ULTRA_SAFE" else "💎 COMBINÉ VIP" if category.name == "VIP" else "🚀 VALUE BET"
                    alert_msg = f"🚨 **NOUVEAU {titre_canal} DÉTECTÉ ET ENREGISTRÉ !**\n\n📈 **Cote atteinte : {new_ticket.odds}**\n\n👉 *Ouvre le bot principal pour consulter ce ticket verrouillé pour aujourd'hui !*"
                    try:
                        await bot.send_message(chat_id=settings.ARCHIVE_CHANNEL_ID, text=alert_msg)
                        await asyncio.sleep(1)
                    except: pass

    if tickets_generes > 0 and settings.ARCHIVE_CHANNEL_ID and settings.ARCHIVE_CHANNEL_ID != "-100VOTRE_ID_ICI":
        try:
            await bot.send_message(chat_id=settings.ARCHIVE_CHANNEL_ID, text=f"✅ {tickets_generes} nouveaux TICKETS ont été verrouillés. Fini le scan pour ces catégories aujourd'hui, bon gain !")
        except: pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)
    if settings.ARCHIVE_CHANNEL_ID and settings.ARCHIVE_CHANNEL_ID != "-100VOTRE_ID_ICI":
        try:
            await bot.send_message(chat_id=settings.ARCHIVE_CHANNEL_ID, text="🟢 **MOTEUR QUANTITATIF EN LIGNE !**\nVraies cotes bookmakers + Filtre Edge + IA Validator activés.")
        except: pass

    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_platform_pipeline, 'interval', minutes=45) 
    scheduler.start()
    
    asyncio.create_task(run_platform_pipeline())
    bot_task = asyncio.create_task(dp.start_polling(bot))
    yield
    scheduler.shutdown()
    bot_task.cancel()
    await bot.session.close()

app = FastAPI(title="WallStreet OS", lifespan=lifespan)
@app.get("/")
async def health(): return {"status": "ONLINE - MOTEUR QUANTITATIF ACTIF"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), reload=False)
