import telebot
import requests
import logging
import os
import threading
from flask import Flask

# --- LOGGING (La boîte noire) ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# --- TES IDENTIFIANTS ---
TOKEN = "7641013539:AAEE4xxcGdzhOyHwoFwuHV7vnAbonsyMjyE"
bot = telebot.TeleBot(TOKEN)

# Ta nouvelle clé Football-Data.org
API_KEY_FOOT = "7d189cebfcc245dba669f86c41ebe1be"

# Ton ID Telegram VIP
MON_ID = 5968288964 

# --- MINI-SERVEUR POUR RENDER ---
app = Flask(__name__)
@app.route('/')
def index(): return "Bot Foot Connecté avec Football-Data !"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- FONCTION DE RÉCUPÉRATION DES MATCHS ---
def recuperer_matchs_v2(equipe_id):
    # On récupère les 5 derniers matchs TERMINÉS
    url = f"https://api.football-data.org/v4/teams/{equipe_id}/matches?status=FINISHED&limit=5"
    headers = {"X-Auth-Token": API_KEY_FOOT}

    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        return data
    except Exception as e:
        logging.error(f"Erreur API : {e}")
        return None

# --- COMMANDES TELEGRAM ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.chat.id != MON_ID: return
    texte = "⚽ **Système Opérationnel !** ⚽\n\nTa nouvelle clé est activée.\n👉 Tape /real pour tester."
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(commands=['real'])
def test_real(message):
    if message.chat.id != MON_ID: return
    
    bot.send_message(message.chat.id, "⏳ Analyse des derniers résultats du Real Madrid...")
    
    # Sur ce site, l'ID du Real Madrid est 86
    data = recuperer_matchs_v2(86)

    if not data or 'matches' not in data:
        bot.send_message(message.chat.id, "❌ Erreur de connexion à l'API.")
        return

    if len(data['matches']) == 0:
        bot.send_message(message.chat.id, "⚠️ Aucun match récent trouvé.")
        return

    reponse = "📊 **Derniers scores du Real Madrid :**\n\n"
    
    for match in data['matches']:
        equipe_dom = match['homeTeam']['shortName']
        equipe_ext = match['awayTeam']['shortName']
        score_dom = match['score']['fullTime']['home']
        score_ext = match['score']['fullTime']['away']
        
        reponse += f"🏟️ {equipe_dom}  **{score_dom} - {score_ext}** {equipe_ext}\n"

    bot.send_message(message.chat.id, reponse, parse_mode="Markdown")

# --- LANCEMENT ---
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    print("Le Bot est en ligne !")
    bot.infinity_polling()
        
