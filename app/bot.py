import os
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.core import settings
import app.core as core_module
from app.models import TicketCategory

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

class Form(StatesGroup):
    waiting_for_manual_match = State()

# 🎛️ CLAVIER EN GRILLE 2x2 (2 boutons par ligne)
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🌟 Combiné du Jour"),
                KeyboardButton(text="💎 Combiné VIP")
            ],
            [
                KeyboardButton(text="🚀 Value Bet"),
                KeyboardButton(text="📊 Analyse Manuelle")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

@router.message(CommandStart())
async def command_start(message: Message):
    text = (
        "🏛 **WALLSTREET OS - TRADING SPORTIF**\n\n"
        "⚡️ Flux The Odds API : Connecté\n"
        "⚙️ Moteur Combinatoire : Actif\n"
        "🤖 IA Groq : Connectée\n\n"
        "👨‍💻 Créateur : @hermann1123\n"
        "Utilisez le clavier en bas pour sélectionner votre combiné :"
    )
    await message.answer(text, reply_markup=main_keyboard(), parse_mode="Markdown")

# 📲 GESTION DES CLICS SUR LES BOUTONS DU CLAVIER
@router.message(F.text.in_([
    "🌟 Combiné du Jour",
    "💎 Combiné VIP",
    "🚀 Value Bet"
]))
async def fetch_tickets_by_text(message: Message):
    text_map = {
        "🌟 Combiné du Jour": TicketCategory.ULTRA_SAFE,
        "💎 Combiné VIP": TicketCategory.VIP,
        "🚀 Value Bet": TicketCategory.VALUE_BET if hasattr(TicketCategory, 'VALUE_BET') else TicketCategory.VALUE
    }
    
    category = text_map.get(message.text)
    tickets = core_module.CACHE_PORTFOLIO.get(category, [])

    if not tickets:
        await message.answer("📭 Aucun ticket validé pour le moment. L'IA analyse les marchés...")
        return

    t = tickets[-1] 
    
    response = f"🏛 **{t.match_title}**\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    response += f"{t.bet_type}\n\n"
    response += f"📈 **COTE TOTALE : {t.odds}**\n\n"
    response += f"{t.ai_justification}\n"
        
    await message.answer(response, parse_mode="Markdown")

@router.message(F.text == "📊 Analyse Manuelle")
async def ask_manual(message: Message, state: FSMContext):
    await message.answer("📝 Entrez le nom du match à analyser (ex: Real Madrid vs Milan) :")
    await state.set_state(Form.waiting_for_manual_match)

@router.message(Form.waiting_for_manual_match)
async def process_manual(message: Message, state: FSMContext):
    await message.answer("⚙️ Analyse manuelle désactivée temporairement.")
    await state.clear()

dp.include_router(router)
