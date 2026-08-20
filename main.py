import os
import sys
import datetime
import random
import time
import string
import json
import pandas as pd
import ta
import requests
import telebot
import websocket
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = "8658287331:AAHSVaQRoPcE1ake0a-lkxdpjtVCjHQzj_Q"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 

COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3  

# ==========================================
# CONNEXION DIRECTE POCKET OPTION (WEBSOCKET)
# ==========================================
class PO_Simple:
    def __init__(self, ssid):
        self.ssid = ssid
        self.ws = websocket.WebSocket()
        self.connected = False
        
    def connect(self):
        try:
            self.ws.connect("wss://ws.pocketoption.com/socket.io/?EIO=3&transport=websocket", timeout=5)
            # Authentification de session
            auth_packet = json.dumps({"ssid": self.ssid})
            self.ws.send(f'42["auth",{auth_packet}]')
            self.connected = True
            print("✅ Connecté au serveur PO via WebSocket !")
        except Exception as e:
            print(f"❌ Erreur connexion WebSocket : {e}")

    def get_realtime_price(self, asset):
        # Simulation de récupération du prix via WebSocket pour l'exemple
        return None

print("🔄 Initialisation du connecteur Pocket Option OTC...")
api_po = PO_Simple("c8p9d7a50kfnr50oqevscpprdi")
# On lance la connexion dans un thread pour ne pas bloquer le bot
Thread(target=api_po.connect, daemon=True).start()

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================
user_prefs = {}
mode_trading = {} 
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}
cooldown_actifs = {} 
niveaux_martingale = {} 

utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}

OTC_PAIRS = [
    "EURUSD_otc", "GBPUSD_otc", "AUDUSD_otc", "NZDUSD_otc",
    "USDCAD_otc", "USDCHF_otc", "USDJPY_otc", "EURJPY_otc",
    "GBPJPY_otc", "AUDJPY_otc", "CADJPY_otc", "CHFJPY_otc"
]

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V18.1 POCKET OPTION (Direct WS)"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# SYSTÈME DE GESTION DES ACCÈS VIP
# ==========================================
def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration: return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ**", parse_mode="Markdown")
            except: pass
            return False
    return False

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle in cles_generees:
            jours = cles_generees[cle]
            if jours == "LIFETIME":
                utilisateurs_autorises[chat_id] = "LIFETIME"
            else:
                utilisateurs_autorises[chat_id] = datetime.datetime.now() + datetime.timedelta(days=jours)
            del cles_generees[cle] 
            bot.send_message(chat_id, "🎉 **ACCÈS DÉVERROUILLÉ !**\nTapez /start", parse_mode="Markdown")
    except: pass

# ==========================================
# INTERFACE UTILISATEUR & COMMANDES
# ==========================================
def obtenir_clavier(user_id):
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    btn_mode = "🛡️ MODE: SMC STANDARD" if mode_actuel == "STANDARD" else "🔥 MODE: SMC SCALP"
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton(btn_mode), KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    texte = "🏴‍☠️ **TERMINAL PRIME - V18.1 POCKET OPTION 🛑** 🔥\n\n📡 **Connexion directe WS établie**."
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    bot.send_message(message.chat.id, "🕒 **GUIDE DES HORAIRES (OTC)** 🕒\n\nLe marché OTC de Pocket Option est ouvert 24h/24 et 7j/7.", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    boutons = [InlineKeyboardButton(paire.replace("_otc", " OTC"), callback_data=f"set_{paire}") for paire in OTC_PAIRS]
    markup.add(*boutons)
    bot.send_message(message.chat.id, "Sélectionne ta cible OTC :", reply_markup=markup)

if __name__ == "__main__":
    keep_alive()
    print("⬛ BOÎTE NOIRE : Édition V18.1 Démarrée.", flush=True)
    bot.infinity_polling()
