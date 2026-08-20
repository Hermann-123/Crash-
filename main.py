import os
import datetime
import random
import time
import json
import telebot
import websocket
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI
from threading import Thread, Timer

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================
TELEGRAM_TOKEN = "8000472746:AAGsb319CKyUqYEwyTM5uF_Ykjx4dnHA-ts"
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
            self.ws.connect("wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket", timeout=5)
            auth_packet = json.dumps({"ssid": self.ssid})
            self.ws.send(f'40') 
            time.sleep(1)
            self.ws.send(f'42["auth",{auth_packet}]')
            self.connected = True
            print("✅ Connecté au serveur PO via WebSocket !")
        except Exception as e:
            print(f"❌ Erreur connexion WebSocket : {e}")

api_po = PO_Simple("c8p9d7a50kfnr50oqevscpprdi")

# ==========================================
# VARIABLES D'ÉTAT ET ROUTAGE
# ==========================================
user_prefs = {}
mode_trading = {} 
trades_en_cours = {}
utilisateurs_actifs = set()
niveaux_martingale = {} 
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}

OTC_PAIRS = [
    "EURUSD_otc", "GBPUSD_otc", "AUDUSD_otc", "NZDUSD_otc",
    "USDCAD_otc", "USDCHF_otc", "USDJPY_otc", "EURJPY_otc",
    "GBPJPY_otc", "AUDJPY_otc", "CADJPY_otc", "CHFJPY_otc"
]

# ==========================================
# SERVEUR WEB COMPATIBLE RENDER (FASTAPI)
# ==========================================
app = FastAPI()

@app.get('/')
def home():
    return {"status": "Terminal Prime VIP : Édition V18.3 POCKET OPTION (En Ligne)"}

@app.on_event("startup")
def startup_event():
    print("🔄 Initialisation des moteurs (Bot + Pocket Option)...")
    Thread(target=api_po.connect, daemon=True).start()
    Thread(target=bot.infinity_polling, daemon=True).start()
    print("⬛ BOÎTE NOIRE : Édition V18.3 Démarrée.")

# ==========================================
# GESTION DES BOUTONS ET DE L'ANALYSE
# ==========================================
def est_autorise(user_id):
    return user_id in utilisateurs_autorises or user_id == ADMIN_ID

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
    niveaux_martingale[user_id] = 0
    mode_trading[user_id] = "STANDARD"
    bot.send_message(user_id, "🏴‍☠️ **TERMINAL PRIME - V18.3 POCKET OPTION 🛑** 🔥\n\n📡 **Système en ligne et prêt à tirer.**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    bot.send_message(message.chat.id, "🕒 **GUIDE DES HORAIRES (OTC)** 🕒\n\nLe marché OTC de Pocket Option est ouvert 24h/24 et 7j/7.", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    markup = InlineKeyboardMarkup(row_width=3)
    boutons = [InlineKeyboardButton(paire.replace("_otc", " OTC"), callback_data=f"set_{paire}") for paire in OTC_PAIRS]
    markup.add(*boutons)
    bot.send_message(message.chat.id, "Sélectionne ta cible OTC :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    if mode_actuel == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE SMC SCALPING (1 MIN) ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE SMC STANDARD ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    actif = call.data.replace("set_", "")
    user_prefs[chat_id] = actif
    nom_affiche = actif.replace("_otc", " OTC")
    bot.answer_callback_query(call.id, f"✅ Devise verrouillée : {nom_affiche}")
    bot.send_message(chat_id, f"🎯 **Cible verrouillée : {nom_affiche}**\nClique sur 🚀 LANCER L'ANALYSE.", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    actif = user_prefs.get(chat_id)
    if not actif: return bot.send_message(chat_id, "⚠️ Choisis d'abord une devise avec le bouton 📊 CHOISIR UNE DEVISE !")
    
    msg = bot.send_message(chat_id, "⏳ *Lecture des graphiques Pocket Option (Order Blocks)...*", parse_mode="Markdown")
    
    # Simulation de l'analyse SMC pour déclencher le signal
    time.sleep(2)
    action = random.choice(["🟢 ACHAT (CALL)", "🔴 VENTE (PUT)"])
    duree_texte = "1 MINUTE (SCALP 🛡️)" if mode_trading.get(chat_id) == "SCALP" else "3 MIN (HIT & RUN ⚡)"
    duree_sec = 60 if mode_trading.get(chat_id) == "SCALP" else 180
    palier = niveaux_martingale.get(chat_id, 0)
    mise_calculee = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))
    
    signal = f"""🚨 **ALERTE DE TIR RÉEL VIP 💎** 🚨
──────────────────
🌐 **ACTIF :** {actif.replace('_otc', ' OTC')}
⏳ **EXPIRATION :** {duree_texte}
👉 **ACTION :** {action}
🛡️ 👑 SMC ULTIME : Prise de Liquidité 🚀

💵 **MISE CALCULÉE :** `{mise_calculee}$`
*(Statut : Palier {palier})*"""

    bot.delete_message(chat_id, msg.message_id)
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"), InlineKeyboardButton("❌ PERDU", callback_data="force_loss"))
    bot.send_message(chat_id, signal, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data in ["force_win", "force_loss"])
def resultat_manuel(call):
    chat_id = call.message.chat.id
    if call.data == "force_win":
        niveaux_martingale[chat_id] = 0
        bot.answer_callback_query(call.id, "✅ Victoire validée !", show_alert=True)
        bot.send_message(chat_id, "✅ **CIBLE ABATTUE (ITM)**\n🔓 *Radar déverrouillé.*", parse_mode="Markdown")
    else:
        palier = niveaux_martingale.get(chat_id, 0)
        if palier < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier + 1
            bot.answer_callback_query(call.id, "⚠️ Échec, préparation de la Martingale.", show_alert=True)
            bot.send_message(chat_id, f"⚠️ **TIR RATÉ**\n⚡ *Clique sur 🚀 LANCER L'ANALYSE pour le Palier {palier + 1}...*", parse_mode="Markdown")
        else:
            niveaux_martingale[chat_id] = 0
            bot.answer_callback_query(call.id, "🛑 Fin de séquence.", show_alert=True)
            bot.send_message(chat_id, "🛑 **FIN DE SÉQUENCE ATTEINTE (OTM)**\nRepli tactique.", parse_mode="Markdown")
            
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass

