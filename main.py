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
TOKEN = "7641013539:AAHidh_Hlpuv8jcSx8X-L5-_OVTebuUvyXw"
bot = telebot.TeleBot(TOKEN)

API_KEY_FOOT = "Ab5a054667msh0a1ea9c796930c5p169b7fjsn0f250f6b6c19"
MON_ID = 5968288964 

# ==========================================
# 3. MINI-SERVEUR WEB POUR RENDER
# ==========================================
app = Flask(__name__)

@app.route('/')
def index():
    return "Le Bot Football est en ligne, API connectée et Boîte Noire active !"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# 4. FONCTIONS DE SÉCURITÉ ET D'API
# ==========================================
def is_admin(chat_id):
    return chat_id == MON_ID

def recuperer_matchs(equipe_id):
    """Interroge l'API de foot."""
    url = "https://v3.football.api-sports.io/fixtures"
    querystring = {"team": str(equipe_id), "last": "5"}
    headers = {
        "x-rapidapi-key": API_KEY_FOOT,
        "x-rapidapi-host": "v3.football.api-sports.io"
    }

    try:
        logging.info(f"Tentative de connexion à l'API pour l'équipe ID: {equipe_id}...")
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status() 
        
        data = response.json()
        logging.info("✅ Données récupérées avec succès depuis l'API.")
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
        "L'API est connectée. Le système est prêt à décortiquer les statistiques.\n\n"
        "👉 Tape la commande /real pour tester la connexion."
    )
    bot.send_message(chat_id, texte, parse_mode="Markdown")

@bot.message_handler(commands=['real'])
def test_real_madrid(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return

    bot.send_message(chat_id, "⏳ Connexion à la base de données API-Sports en cours...")
    
    # On lance la recherche
    data = recuperer_matchs(541)

    # 1er filtre : Le crash technique total
    if data is None:
        bot.send_message(chat_id, "❌ Erreur critique de connexion. Vérifie la Boîte Noire.")
        return
        
    # 2ème filtre : L'erreur d'abonnement RapidAPI (Le problème qu'on cherche)
    if data.get('errors'):
        erreur_api = data.get('errors')
        bot.send_message(chat_id, f"⚠️ L'API refuse l'accès. Voici sa raison :\n\n`{erreur_api}`\n\n*(Copie-moi ce message !)*", parse_mode="Markdown")
        return

    # 3ème filtre : L'API marche mais ne trouve rien
    if not data.get('response'):
        bot.send_message(chat_id, "❌ L'API n'a renvoyé aucun match pour cette équipe.")
        return

    # Si tout va bien, on affiche les scores !
    reponse_texte = "📊 **Les 5 derniers scores exacts du Real Madrid :**\n\n"
    
    for match in data['response']:
        equipe_dom = match['teams']['home']['name']
        equipe_ext = match['teams']['away']['name']
        buts_dom = match['goals']['home']
        buts_ext = match['goals']['away']
        
        if buts_dom is None or buts_ext is None:
            score = "Match reporté"
        else:
            score = f"{buts_dom} - {buts_ext}"
            
        reponse_texte += f"🏟️ {equipe_dom} {score} {equipe_ext}\n"

    bot.send_message(chat_id, reponse_texte, parse_mode="Markdown")

# ==========================================
# 6. LANCEMENT SIMULTANÉ
# ==========================================
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    print("Le Bot Football est en ligne et écoute sur Telegram !")
    bot.infinity_polling()
    
