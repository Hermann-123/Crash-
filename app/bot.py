from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import app.core as core_module
from app.core import settings
from app.models import TicketCategory
from app.data_providers import FootballDataProvider
from app.services import SmartPoissonEngine, ConfidenceEngine

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

football_provider = FootballDataProvider()
engine = SmartPoissonEngine()
confidence_engine = ConfidenceEngine()


class Form(StatesGroup):
    waiting_for_manual_match = State()


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🛡 Ticket Safe Premium"),
                KeyboardButton(text="🚀 Ticket Value Premium")
            ],
            [
                KeyboardButton(text="📊 Statut"),
                KeyboardButton(text="✍️ Analyse Manuelle")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def _build_manual_text(home_team, away_team, sim, audit, home_form, away_form):
    favored = home_team if sim.proba_home >= sim.proba_away else away_team
    return (
        f"📌 Analyse manuelle\n\n"
        f"Match: {home_team} vs {away_team}\n"
        f"Score probable: {sim.most_likely_score}\n"
        f"Proba domicile: {sim.proba_home}%\n"
        f"Proba nul: {sim.proba_draw}%\n"
        f"Proba extérieur: {sim.proba_away}%\n"
        f"Over 2.5: {sim.proba_over_2_5}%\n"
        f"BTTS Oui: {sim.proba_btts_yes}%\n"
        f"Confiance globale: {audit.confidence_score}/100\n\n"
        f"Forme {home_team}: {home_form.last5_points} pts / 5 matchs, "
        f"{home_form.last5_goals_for} marqués, {home_form.last5_goals_against} encaissés\n"
        f"Forme {away_team}: {away_form.last5_points} pts / 5 matchs, "
        f"{away_form.last5_goals_for} marqués, {away_form.last5_goals_against} encaissés\n\n"
        f"Favori modèle: {favored}\n"
        f"Justification: {audit.justification}"
    )


@router.message(CommandStart())
async def start_cmd(message: Message):
    await message.answer(
        "🤖 *WallStreet OS V3*\n\n"
        "Bot premium football.\n"
        "Choisis une option ci-dessous.",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )


@router.message(Command("status"))
@router.message(F.text == "📊 Statut")
async def status_cmd(message: Message):
    s = core_module.LAST_SCAN_SUMMARY or {}
    if not s:
        await message.answer("Aucun scan disponible.")
        return

    await message.answer(
        f"📡 Dernier scan\n"
        f"- Matchs: {s.get('matches', 0)}\n"
        f"- Picks: {s.get('picks', 0)}\n"
        f"- Tickets: {s.get('tickets', 0)}\n"
        f"- Heure: {s.get('time', 'N/A')}"
    )


@router.message(F.text.in_(["🛡 Ticket Safe Premium", "🚀 Ticket Value Premium"]))
async def get_ticket(message: Message):
    mapping = {
        "🛡 Ticket Safe Premium": TicketCategory.SAFE,
        "🚀 Ticket Value Premium": TicketCategory.VALUE,
    }

    cat = mapping[message.text]
    tickets = core_module.CACHE_PORTFOLIO.get(cat, [])

    if not tickets:
        await message.answer("📭 Aucun ticket disponible.")
        return

    t = tickets[-1]
    text = (
        f"{t.title}\n\n"
        f"📈 Cote totale: {t.total_odds}\n"
        f"🎯 Confiance combinée: {t.confidence}%\n"
        f"🕒 Généré: {t.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"{t.summary}"
    )

    if len(text) > 4000:
        text = text[:4000] + "\n\n[Message tronqué]"

    await message.answer(text)


@router.message(F.text == "✍️ Analyse Manuelle")
async def ask_manual(message: Message, state: FSMContext):
    await message.answer("Envoie le match au format : Equipe A vs Equipe B")
    await state.set_state(Form.waiting_for_manual_match)


@router.message(Form.waiting_for_manual_match)
async def process_manual(message: Message, state: FSMContext):
    raw = (message.text or "").strip()

    if " vs " not in raw.lower():
        await message.answer("Format invalide. Exemple: Real Madrid vs Milan")
        return

    parts = raw.split(" vs ")
    if len(parts) != 2:
        await message.answer("Format invalide. Exemple: Real Madrid vs Milan")
        return

    home_team = parts[0].strip()
    away_team = parts[1].strip()

    await message.answer("⏳ Analyse en cours...")

    try:
        home_form = await football_provider.get_team_form(home_team)
        away_form = await football_provider.get_team_form(away_team)

        from datetime import datetime, timezone
        from app.models import MatchData, SportType

        fake_match = MatchData(
            match_id=f"manual_{home_team}_{away_team}",
            sport=SportType.SOCCER,
            league="Manual Analysis",
            kickoff_at=datetime.now(timezone.utc),
            home_team=home_team,
            away_team=away_team,
            home_odds=2.10,
            draw_odds=3.20,
            away_odds=3.40,
            bookmaker="Manual Synthetic Odds",
        )

        sim = engine.simulate(fake_match, home_form, away_form)
        audit = confidence_engine.score(fake_match, sim, home_form, away_form)

        text = _build_manual_text(home_team, away_team, sim, audit, home_form, away_form)
        core_module.MANUAL_ANALYSIS_CACHE[f"{home_team} vs {away_team}"] = text
        await message.answer(text)

    except Exception as e:
        await message.answer(f"Erreur durant l'analyse manuelle: {e}")

    await state.clear()


dp.include_router(router)
