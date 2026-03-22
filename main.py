import telebot
import requests
import logging
import os
import threading
from flask import Flask

# ==========================================
# 1. LA BOÎTE NOIRE (LOGGING)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("boite_noire.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logging.info("La boîte noire est activée. Démarrage du système.")

# ==========================================
# 2. TES IDENTIFIANTS SECRETS
# ==========================================
# Ton Token Telegram 
TOKEN = "7641013539:AAEE4xxcGdzhOyHwoFwuHV7vnAbonsyMjyE"
bot = telebot.TeleBot(TOKEN)

# Ta NOUVELLE Clé API-Sports (Connexion Directe)
API_KEY_FOOT = "a401855b9c55c032d2d63fac4c019306"

# Ton ID Telegram VIP
MON_ID = 5968288964 

# ==========================================
# 3. MINI-SERVEUR WEB POUR RENDER
# ==========================================
app = Flask(__name__)

@app.route('/')
def index():
    return "Le Bot Football est en ligne avec la connexion API directe !"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# 4. FONCTIONS DE SÉCURITÉ ET D'API DIRECTE
# ==========================================
def is_admin(chat_id):
    return chat_id == MON_ID

def recuperer_matchs(equipe_id):
    """Interroge l'API de foot EN DIRECT (Sans RapidAPI)."""
    url = "https://v3.football.api-sports.io/fixtures"
    querystring = {"team": str(equipe_id), "last": "5"}
    
    # Header simplifié pour la connexion directe
    headers = {
        "x-apisports-key": API_KEY_FOOT
    }

    try:
        logging.info(f"Tentative de connexion DIRECTE pour l'équipe ID: {equipe_id}...")
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status() 
        
        data = response.json()
        logging.info("✅ Données récupérées avec succès depuis l'API directe.")
        return data

    except Exception as e:
        logging.error(f"CRASH API LORS DE LA RECHERCHE DE L'ÉQUIPE {equipe_id} : {e}")
        return None

# ==========================================
# 5. L'INTERFACE TELEGRAM DU BOT
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        bot.send_message(chat_id, "⛔ Accès refusé.")
        return
        
    texte = (
        "⚽ **Bienvenue dans ton Centre d'Analyse Football !** ⚽\n\n"
        "La nouvelle API est connectée avec succès.\n\n"
        "👉 Tape la commande /real pour lancer le test final sur le Real Madrid."
    )
    bot.send_message(chat_id, texte, parse_mode="Markdown")

@bot.message_handler(commands=['real'])
def test_real_madrid(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return

    bot.send_message(chat_id, "⏳ Connexion à la base de données API-Sports en cours...")
    
    # ID du Real Madrid = 541
    data = recuperer_matchs(541)

    if data is None:
        bot.send_message(chat_id, "❌ Erreur critique de connexion. Vérifie la Boîte Noire.")
        return
        
    if data.get('errors'):
        erreur_api = data.get('errors')
        bot.send_message(chat_id, f"⚠️ L'API refuse l'accès. Voici sa raison :\n\n`{erreur_api}`", parse_mode="Markdown")
        return

    if not data.get('response'):
        bot.send_message(chat_id, "❌ L'API n'a renvoyé aucun match pour cette équipe.")
        return

    reponse_texte = "📊 **Les 5 derniers scores exacts du Real Madrid :**\n\n"
    
    for match in data['response']:
        equipe_dom = match['teams']['home']['name']
        equipe_ext = match['teams']['away']['name']
        buts_dom = match['goals']['home']
        buts_ext = match['goals']['away']
        
        if buts_dom is None or buts_ext is None:
            score = "Match reporté/non joué"
        else:
            score = f"{buts_dom} - {buts_ext}"
            
        reponse_texte += f"🏟️ {equipe_dom} {score} {equipe_ext}\n"

    bot.send_message(chat_id, reponse_texte, parse_mode="Markdown")

# ==========================================
# 6. LANCEMENT SIMULTANÉ
# ==========================================
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    print("Le Bot Football est en ligne avec la nouvelle clé API !")
    bot.infinity_polling()
        
