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
from app.services import DixonColesEngine, AIRiskManager, TicketFactory
from app.bot import bot, dp

soccer_engine = DixonColesEngine()
ai_manager = AIRiskManager()
ticket_factory = TicketFactory()

# TA CLÉ THE ODDS API
API_KEY_ODDS = "55a670c7b44c3dcc3c9750e9f5c51da1"

async def fetch_real_odds_matches() -> list:
    url = f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={API_KEY_ODDS}&regions=eu&markets=h2h"
    matches = []
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
                        cotes = {c['name']: c['price'] for c in m['bookmakers'][0]['markets'][0]['outcomes']}
                        home, away = m['home_team'], m['away_team']
                        
                        if home in cotes and away in cotes and 'Draw' in cotes:
                            matches.append(MatchData(
                                match_id=m['id'],
                                sport=SportType.SOCCER,
                                league=m['sport_title'],
                                match_date=datetime.now(),
                                home_team=home,
                                away_team=away,
                                home_odds=cotes[home],
                                draw_odds=cotes['Draw'],
                                away_odds=cotes[away]
                            ))
                            if len(matches) >= 100:
                                break
    except Exception as e:
        logger.error(f"Erreur API : {e}")
        
    return matches

async def run_platform_pipeline():
    logger.info("🔄 [SCAN] Recherche de nouveaux combinés...")
    matches = await fetch_real_odds_matches()
    
    if not matches: return

    evaluated = []
    for match in matches:
        sim = soccer_engine.simulate(match)
        ai_report = await ai_manager.evaluate_match(match, sim)
        evaluated.append((match, sim, ai_report))
        await asyncio.sleep(0.3)

    new_portfolio = ticket_factory.build_portfolio(evaluated)
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    tickets_generes = 0
    
    for category, tickets in new_portfolio.items():
        if category not in core_module.CACHE_PORTFOLIO:
            core_module.CACHE_PORTFOLIO[category] = []
            
        for new_ticket in tickets:
            # 🛑 ANTI-SPAM : On crée une clé unique pour aujourd'hui et pour cette catégorie
            daily_alert_key = f"alert_{category.name}_{today_str}"
            
            # Si on n'a pas encore envoyé de ticket pour cette catégorie aujourd'hui
            if daily_alert_key not in core_module.SENT_ALERTS:
                # On enregistre ce ticket en mémoire et on verrouille l'envoi pour aujourd'hui
                core_module.CACHE_PORTFOLIO[category] = [new_ticket] # On écrase l'ancien pour ne garder que le meilleur du jour
                core_module.SENT_ALERTS.add(daily_alert_key)
                tickets_generes += 1
                
                if settings.ARCHIVE_CHANNEL_ID and settings.ARCHIVE_CHANNEL_ID != "-100VOTRE_ID_ICI":
                    titre_canal = "🌟 COMBINÉ DU JOUR" if category.name == "ULTRA_SAFE" else "💎 COMBINÉ VIP" if category.name == "VIP" else "🚀 VALUE BET"
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
            await bot.send_message(chat_id=settings.ARCHIVE_CHANNEL_ID, text="🟢 **SERVEUR EN LIGNE !**\nSystème Anti-Spam activé. L'IA verrouillera un seul ticket par catégorie par jour.")
        except: pass

    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_platform_pipeline, 'interval', minutes=45) # Scan toutes les 45 mins
    scheduler.start()
    
    asyncio.create_task(run_platform_pipeline())
    bot_task = asyncio.create_task(dp.start_polling(bot))
    yield
    scheduler.shutdown()
    bot_task.cancel()
    await bot.session.close()

app = FastAPI(title="WallStreet OS", lifespan=lifespan)
@app.get("/")
async def health(): return {"status": "ONLINE - ANTI SPAM ACTIF"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), reload=False)
