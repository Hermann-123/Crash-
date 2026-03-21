import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
from flask import Flask
import threading

# 1. RÉCUPÉRATION DES IDENTIFIANTS
# Le token est caché sur Render
TOKEN = os.environ.get("BOT_TOKEN") 
bot = telebot.TeleBot(TOKEN)

# Ton ID personnel (seul ce compte aura accès au bot)
MON_ID = 5968288964 

# Portefeuille virtuel
user_data = {}

# 2. MINI-SERVEUR WEB POUR RENDER (L'astuce anti-coupure)
app = Flask(__name__)

@app.route('/')
def index():
    return "Le serveur du Bot Sniper est en ligne !"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# 3. VÉRIFICATION DE SÉCURITÉ
def is_admin(chat_id):
    """Vérifie si la personne qui parle au bot est bien toi."""
    return chat_id == MON_ID

# 4. LE CŒUR DU BOT
@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    
    # Sécurité : On bloque les inconnus
    if not is_admin(chat_id):
        bot.send_message(chat_id, "⛔ Accès refusé. Ce bot est privé.")
        return
        
    if chat_id not in user_data:
        user_data[chat_id] = {"bankroll": 10000}
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📊 Lancer une Analyse", callback_data="start_analysis"))
    
    texte = (
        f"Salut Sniper ! 🎯\n\n"
        f"🔒 Accès autorisé pour l'Administrateur.\n"
        f"💰 **Capital actuel : {user_data[chat_id]['bankroll']} FCFA**\n\n"
        f"Clique ci-dessous pour initialiser le calcul du prochain tir."
    )
    bot.send_message(chat_id, texte, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "start_analysis")
def ask_for_odds(call):
    chat_id = call.message.chat.id
    if not is_admin(chat_id):
        return
        
    bot.answer_callback_query(call.id)
    msg = bot.send_message(chat_id, "✈️ À quelle cote l'avion vient-il de se crasher ? (ex: 1.45)")
    bot.register_next_step_handler(msg, process_odds)

def process_odds(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return
        
    try:
        cote_precedente = float(message.text.replace(',', '.'))
    except ValueError:
        bot.send_message(chat_id, "❌ Erreur : Entre un nombre valide (ex: 1.45).")
        return

    bankroll = user_data[chat_id]["bankroll"]

    # Stratégie Dynamique
    if cote_precedente < 1.20:
        cible_sniper = 1.45
        pourcentage_mise = 0.05 
        strategie = "Contre-attaque après un crash rapide."
    elif 1.20 <= cote_precedente < 2.00:
        cible_sniper = 1.30
        pourcentage_mise = 0.02 
        strategie = "Tir classique dans la zone de confort."
    else:
        cible_sniper = 1.15
        pourcentage_mise = 0.01 
        strategie = "Prudence maximale après un gros vol."

    mise_calculee = int(bankroll * pourcentage_mise)
    if mise_calculee < 100:
        mise_calculee = 100

    user_data[chat_id]["mise_en_cours"] = mise_calculee
    user_data[chat_id]["cible_en_cours"] = cible_sniper

    texte_sniper = (
        f"🎯 **ANALYSE TERMINÉE** 🎯\n\n"
        f"🧠 Stratégie : {strategie}\n\n"
        f"🔫 **ORDRE DE TIR :**\n"
        f"👉 Retrait automatique à : **x{cible_sniper}**\n"
        f"💵 Mise exacte à placer : **{mise_calculee} FCFA**\n\n"
        f"✈️ *Envoie-moi la cote finale pour le rapport de tir :*"
    )
    
    msg = bot.send_message(chat_id, texte_sniper, parse_mode="Markdown")
    bot.register_next_step_handler(msg, verify_shot)

def verify_shot(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        return
        
    try:
        resultat_reel = float(message.text.replace(',', '.'))
    except ValueError:
        msg = bot.send_message(chat_id, "❌ Erreur : J'ai besoin du chiffre exact (ex: 2.10). Réessaie :")
        bot.register_next_step_handler(msg, verify_shot)
        return

    cible_sniper = user_data[chat_id]["cible_en_cours"]
    mise = user_data[chat_id]["mise_en_cours"]

    if resultat_reel >= cible_sniper:
        gain_net = int((mise * cible_sniper) - mise)
        user_data[chat_id]["bankroll"] += gain_net
        reponse = f"✅ **CIBLE TOUCHÉE !**\nBénéfice net : +{gain_net} FCFA. Bien joué. 😎"
    else:
        user_data[chat_id]["bankroll"] -= mise
        reponse = f"❌ **TIR MANQUÉ.**\nPerte : -{mise} FCFA."

    reponse += f"\n\n💰 **Nouveau Capital : {user_data[chat_id]['bankroll']} FCFA**"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📊 Préparer le prochain tir", callback_data="start_analysis"))
    
    bot.send_message(chat_id, reponse, reply_markup=markup, parse_mode="Markdown")

# 5. LANCEMENT SIMULTANÉ
if __name__ == "__main__":
    # On lance le serveur web dans un processus séparé (thread)
    threading.Thread(target=run_flask).start()
    
    # On lance le bot Telegram
    print("Le bot Sniper est en ligne et sécurisé !")
    bot.infinity_polling()
