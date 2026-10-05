import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import app.core as core_module
from app.core import settings, logger
from app.data_providers import OddsProvider, FootballDataProvider
from app.services import SmartPoissonEngine, ConfidenceEngine, MarketBuilder, TicketFactory
from app.storage import save_json, load_json
from app.bot import bot, dp


odds_provider = OddsProvider()
football_provider = FootballDataProvider()

engine = SmartPoissonEngine()
confidence_engine = ConfidenceEngine()
market_builder = MarketBuilder()
ticket_factory = TicketFactory()


async def notify_startup():
    channel_id = settings.TELEGRAM_CHANNEL_ID

    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID est vide ou absent.")
        return

    try:
        me = await bot.get_me()
        logger.info(f"Bot connecté : @{me.username}")

        chat = await bot.get_chat(channel_id)
        logger.info(
            f"Canal trouvé : id={chat.id}, title={getattr(chat, 'title', None)}"
        )

        await bot.send_message(
            chat_id=channel_id,
            text="✅ Bot en ligne et opérationnel."
        )

        logger.info("Message de démarrage envoyé avec succès.")

    except Exception as e:
        logger.exception(f"Impossible d'envoyer le message de démarrage : {e}")


async def run_platform_pipeline():
    if core_module.PIPELINE_LOCK.locked():
        logger.warning("Pipeline déjà en cours, scan ignoré.")
        return

    async with core_module.PIPELINE_LOCK:
        logger.info("🔄 Démarrage scan premium...")
        matches = await odds_provider.fetch_upcoming_matches()

        all_picks = []

        for match in matches:
            try:
                home_form = await football_provider.get_team_form(match.home_team)
                away_form = await football_provider.get_team_form(match.away_team)

                sim = engine.simulate(match, home_form, away_form)
                audit = confidence_engine.score(match, sim, home_form, away_form)

                if not audit.is_approved:
                    continue

                picks = market_builder.build(match, sim, audit)
                all_picks.extend(picks)

                await asyncio.sleep(0.2)

            except Exception as e:
                logger.exception(f"Erreur pipeline {match.match_id}: {e}")

        portfolio = ticket_factory.build_portfolio(all_picks)
        core_module.CACHE_PORTFOLIO = portfolio

        total_tickets = sum(len(v) for v in portfolio.values())
        logger.info(f"Éléments générés : {total_tickets}")

        if settings.TELEGRAM_CHANNEL_ID and total_tickets > 0:
            try:
                await bot.send_message(
                    chat_id=settings.TELEGRAM_CHANNEL_ID,
                    text=f"✅ Nouveaux éléments disponibles : {total_tickets}."
                )
                logger.info("Notification envoyée avec succès.")
            except Exception as e:
                logger.exception(f"Erreur envoi notification : {e}")

        core_module.LAST_SCAN_SUMMARY = {
            "matches": len(matches),
            "picks": len(all_picks),
            "tickets": total_tickets,
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        save_json("last_scan_summary.json", core_module.LAST_SCAN_SUMMARY)
        save_json(
            "portfolio.json",
            {str(k): [t.model_dump() for t in v] for k, v in portfolio.items()}
        )

        logger.info(f"✅ Scan terminé: {core_module.LAST_SCAN_SUMMARY}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)

    await notify_startup()

    saved_summary = load_json("last_scan_summary.json", {})
    core_module.LAST_SCAN_SUMMARY = saved_summary

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_platform_pipeline,
        "interval",
        minutes=settings.SCAN_INTERVAL_MINUTES,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    startup_task = asyncio.create_task(run_platform_pipeline())
    polling_task = asyncio.create_task(dp.start_polling(bot))

    yield

    scheduler.shutdown()
    startup_task.cancel()
    polling_task.cancel()
    await bot.session.close()


app = FastAPI(title="WallStreet OS V3", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "status": "ONLINE",
        "version": "V3 Premium",
        "last_scan": core_module.LAST_SCAN_SUMMARY
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=False)
