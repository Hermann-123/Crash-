import asyncio
import os
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message

# 🔑 TES CLÉS (À remplacer pour le test)
TELEGRAM_TOKEN = "8000472746:AAGt50VAqUof8tPIGgQ96jF2MzK7gxpkMbE"
GROQ_API_KEY = "TA_CLE_GROQ"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# 🧠 LE CERVEAU DU BOT (Le "System Prompt")
# C'est ici que tu définis l'identité de l'entreprise pour ton client.
SYSTEM_PROMPT = """
Tu es l'assistant virtuel officiel du restaurant "Le Palais Gourmand".
Ton ton est professionnel, chaleureux et accueillant.

VOICI LES INFORMATIONS DE L'ENTREPRISE :
- Menu : Pizzas (5000 FCFA), Burgers (4000 FCFA), Plats locaux (Attiéké Poisson à 3500 FCFA).
- Horaires : Ouvert tous les jours de 11h00 à 23h00.
- Livraison : Gratuite à partir de 10 000 FCFA de commande.
- Réservation : Possible par ce chat.

TES RÈGLES STRICTES :
1. Tu ne dois répondre QU'AUX questions concernant le restaurant, la nourriture, ou les réservations.
2. Si l'utilisateur te parle de sport, de politique, de code ou d'autre chose, refuse poliment de répondre et ramène la conversation sur le restaurant.
3. Sois concis. Ne fais pas de longues phrases.
4. Incite toujours le client à passer commande ou à réserver.
"""

async def get_ai_response(user_message: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.3 # Température basse pour que le bot reste sérieux et précis
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content'].strip()
            return "Désolé, notre système est en maintenance. Veuillez patienter."
        except Exception as e:
            print(f"Erreur Groq: {e}")
            return "Erreur de connexion au serveur."

@dp.message(CommandStart())
async def send_welcome(message: Message):
    welcome_text = (
        "👋 Bienvenue au *Palais Gourmand* !\n\n"
        "Je suis l'assistant virtuel du restaurant. Je suis là pour vous aider avec :\n"
        "🍕 Notre Menu\n"
        "🛵 Les livraisons\n"
        "📅 Prendre une réservation\n\n"
        "Que puis-je faire pour vous aujourd'hui ?"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message()
async def handle_customer_query(message: Message):
    # Indicateur "en train d'écrire..." pour faire pro
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Appel à l'IA
    reponse_ia = await get_ai_response(message.text)
    
    # Envoi de la réponse au client
    await message.answer(reponse_ia)

async def main():
    print("🟢 Bot Vitrine (Service Client) en ligne !")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
