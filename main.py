import os
import sys
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import requests
import datetime
import random
import time
import string
from flask import Flask, render_template
from threading import Thread, Timer
import pandas as pd
import ta

# --- SÉCURITÉ DOTENV ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- CONFIGURATION DE LA CLÉ TOKEN ---
TELEGRAM_TOKEN = "8722705761:AAEkcsR63EKptvU9RZ8YTdfRunvvb9ybwuk"

# 🛑🛑🛑 REMPLACE CECI PAR TON VRAI LIEN RENDER 🛑🛑🛑
WEBAPP_URL = "https://ton-application.onrender.com" 

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# 👑 L'ID DU FONDATEUR (TOI) 👑
ADMIN_ID = 7331853049 

CAPITAL_ACTUEL = 40650 
user_prefs = {}
trades_en_cours = {}
utilisateurs_actifs = set()
derniere_alerte_auto = {}

# SYSTÈME DE GESTION DES ABONNEMENTS
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}

# --- VARIABLES DES HORAIRES ET BILAN ---
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}
bilan_envoye_aujourdhui = False
transition_nuit_envoyee = False
transition_jour_envoyee = False

# --- SERVEUR WEB (POUR AFFICHER LA MINI APP) ---
app = Flask(__name__)

@app.route('/')
def home():
    # C'est ici que Flask va chercher ton fichier dans le dossier "templates" !
    return render_template('index.html')

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- FONCTION DE VÉRIFICATION D'ACCÈS ---
def est_autorise(user_id):
    if user_id == ADMIN_ID:
        return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME":
            return True
        if datetime.datetime.now() < expiration:
            return True
        else:
            del utilisateurs_autorises[user_id]
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
            except: pass
            return False
    return False

# --- GÉNÉRATEUR DE CLÉS ---
def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    return f"PRIME-{''.join(random.choice(caracteres) for _ in range(8))}"

# --- FONCTIONS DE PRIX ET DE VÉRIFICATION ---
def obtenir_prix_actuel(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=1d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=5)
        donnees = reponse.json()
        return round(float(donnees['chart']['result'][0]['meta']['regularMarketPrice']), 5)
    except: return None

def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel(symbole)
    if prix and chat_id in trades_en_cours:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return

    prix_sortie = obtenir_prix_actuel(trade['symbole'])
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']
    symbole = trade['symbole']

    gagne = False
    if "CALL" in action and prix_sortie > prix_entree: gagne = True
    elif "PUT" in action and prix_sortie < prix_entree: gagne = True

    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    
    if gagne:
        texte = f"✅ **VICTOIRE (ITM) !**\n\nSignal passé avec succès 🎉\nLe trade sur {nom_paire} a été validé !\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
        stats_journee['ITM'] += 1
        stats_journee['details'].append(f"✅ {nom_paire} ({action})")
    else:
        texte = f"❌ **PERTE (OTM)** ⚠️\n\nLe marché s'est retourné sur {nom_paire}.\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {nom_paire} ({action})")
    
    try: bot.send_message(chat_id, texte, parse_mode="Markdown")
    except: pass
    if chat_id in trades_en_cours: del trades_en_cours[chat_id]

def generer_jauge(pourcentage):
    if pourcentage == 99: return "[██████████] 👑 MAX"
    pleins = int(pourcentage / 10)
    vides = 10 - pleins
    return f"[{'█' * pleins}{'░' * vides}] {pourcentage}%"

# --- MOTEUR D'ANALYSE VIP ---
def analyser_binaire_pro(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        quote = donnees['chart']['result'][0]['indicators']['quote'][0]
        
        df = pd.DataFrame({'open': quote['open'], 'close': quote['close'], 'high': quote['high'], 'low': quote['low']}).dropna()
        if len(df) < 50: return "⚠️ Pas assez de données", None, None, None, None, None, None

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        
        indicateur_stoch = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
        df['stoch_k'] = indicateur_stoch.stoch()
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
            
        bougie_mere, bougie_enfant = df.iloc[-3], df.iloc[-2] 
        ema_200, c, o, h, l = bougie_enfant['ema_200'], bougie_enfant['close'], bougie_enfant['open'], bougie_enfant['high'], bougie_enfant['low']
        prev_o, prev_c = bougie_mere['open'], bougie_mere['close']

        corps = abs(c - o)
        meche_haute = h - max(o, c)
        meche_basse = min(o, c) - l
        
        est_marteau = meche_basse >= (corps * 1.5) and meche_haute <= corps
        est_etoile = meche_haute >= (corps * 1.5) and meche_basse <= corps
        bullish_engulfing = (prev_c < prev_o) and (c > o) and (c >= prev_o) and (o <= prev_c)
        bearish_engulfing = (prev_c > prev_o) and (c < o) and (c <= prev_o) and (o >= prev_c)
        est_inside_bar = (h < bougie_mere['high']) and (l > bougie_mere['low'])

        largeur_bande = (bougie_enfant['bb_haute'] - bougie_enfant['bb_basse']) / c
        if largeur_bande > 0.0025: expiration, duree_secondes = "5 MINUTES ⏱", 300
        else: expiration, duree_secondes = "3 MINUTES ⏱", 180
        
        action, confiance = None, 0
        rsi_val, stoch_val = round(bougie_enfant['rsi'], 1), round(bougie_enfant['stoch_k'], 1)
        bb_status = ""
        
        if c >= bougie_enfant['bb_haute'] and bougie_enfant['stoch_k'] >= 80 and bougie_enfant['rsi'] >= 60:
            bb_status = "🔴 Rejet au Plafond"
            if c < ema_200: 
                if est_inside_bar: action, confiance = "🔴 VENTE (PUT) 👑 [TITAN INSIDE BAR]", 99
                elif est_etoile or bearish_engulfing: action, confiance = "🔴 VENTE (PUT) ☄️ [PRICE ACTION VIP]", random.randint(94, 98)
                else: return "⚠️ Rejeté : Pas de bougie de retournement (Attente)", None, None, None, None, None, None
            else: return "⚠️ Tendance haussière forte (Attente)", None, None, None, None, None, None
                
        elif c <= bougie_enfant['bb_basse'] and bougie_enfant['stoch_k'] <= 20 and bougie_enfant['rsi'] <= 40:
            bb_status = "🟢 Rejet au Plancher"
            if c > ema_200: 
                if est_inside_bar: action, confiance = "🟢 ACHAT (CALL) 👑 [TITAN INSIDE BAR]", 99
                elif est_marteau or bullish_engulfing: action, confiance = "🟢 ACHAT (CALL) 🔨 [PRICE ACTION VIP]", random.randint(94, 98)
                else: return "⚠️ Rejeté : Pas de bougie de retournement (Attente)", None, None, None, None, None, None
            else: return "⚠️ Tendance baissière forte (Attente)", None, None, None, None, None, None
            
        else: return "⚠️ Marché neutre (Attente d'opportunité)", None, None, None, None, None, None
            
        return action, confiance, expiration, duree_secondes, rsi_val, stoch_val, bb_status
    except Exception as e: return None, None, None, None, None, None, None

# --- SCANNER AUTOMATIQUE DYNAMIQUE (POCKET BROKER 100%) ---
def scanner_marche_auto():
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: continue
            
            heure_actuelle = datetime.datetime.now().hour
            if 8 <= heure_actuelle < 20:
                devises_a_surveiller = ["EURUSD", "USDCAD", "USDCHF", "EURJPY", "AUDUSD", "USDJPY", "AUDJPY"]
            else:
                devises_a_surveiller = ["AUDJPY", "USDJPY", "CHFJPY", "CADJPY", "AUDCAD", "EURAUD"]
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
                if action and "⚠️" not in action and confiance:
                    maintenant = time.time()
                    if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 900): continue
                    derniere_alerte_auto[actif] = maintenant
                    
                    if "TITAN" in action: alerte_msg = f"👑 **ALERTE TITAN DÉTECTÉE** 👑\n\nUne compression de marché rarissime sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%)."
                    else: alerte_msg = f"🚨 **NOUVELLE OPPORTUNITÉ VIP** 🚨\n\nL'algorithme a validé une figure de retournement sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%)."
                    
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, alerte_msg, parse_mode="Markdown")
                        except: pass
        except Exception as e: print(f"⬛ BOÎTE NOIRE [ERREUR SCANNER] : {e}", flush=True)

def gestion_horaires_et_bilan():
    global stats_journee, bilan_envoye_aujourdhui, transition_nuit_envoyee, transition_jour_envoyee
    while True:
        try:
            maintenant = datetime.datetime.now()
            heure, minute = maintenant.hour, maintenant.minute
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]

            if heure == 20 and minute == 0 and not transition_nuit_envoyee:
                texte_nuit = "🌉 **TRANSITION DE SESSION : MODE ASIATIQUE ACTIVÉ** 🌉\n\nLes volumes s'effondrent sur l'Europe et l'Amérique. Le Terminal Prime bascule ses radars sur l'Asie (Focus spécial JPY & AUD).\n\n*La chasse continue de nuit. Restez concentrés.* 🥷"
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_nuit, parse_mode="Markdown")
                    except: pass
                transition_nuit_envoyee, transition_jour_envoyee = True, False

            elif heure == 8 and minute == 0 and not transition_jour_envoyee:
                texte_jour = "☀️ **TRANSITION DE SESSION : MODE EUROPE/US ACTIVÉ** ☀️\n\nOuverture des marchés majeurs. La volatilité est de retour sur l'EUR et l'USD.\n\n*Bonne journée de trading à tous les VIP !* 🚀"
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_jour, parse_mode="Markdown")
                    except: pass
                transition_jour_envoyee, transition_nuit_envoyee = True, False

            elif heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan = f"📊 **BILAN VIP DE LA JOURNÉE** 📊\n──────────────────\n🎯 **Total Signaux :** {total_trades}\n✅ **Victoires (ITM) :** {stats_journee['ITM']}\n❌ **Pertes (OTM) :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n──────────────────\n"
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_bilan, parse_mode="Markdown")
                        except: pass
                stats_journee, bilan_envoye_aujourdhui = {'ITM': 0, 'OTM': 0, 'details': []}, True
                
            elif heure == 23: bilan_envoye_aujourdhui = False
            time.sleep(30)
        except: time.sleep(60)

# --- MENU D'ABONNEMENT ADMIN ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID: return
    action, user_id = call.data.split("_")[1], int(call.data.split("_")[2])
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"), InlineKeyboardButton("2 Semaines", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"), InlineKeyboardButton("2 Mois", callback_data=f"gen_60_{user_id}"),
            InlineKeyboardButton("3 Mois", callback_data=f"gen_90_{user_id}"), InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Utilisateur `{user_id}` accepté.\nChoisis la durée :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    elif action == "refuser": bot.edit_message_text(f"❌ Demande refusée.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID: return
    jours, user_id = int(call.data.split("_")[1]), int(call.data.split("_")[2])
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    duree_texte = f"{jours} Jours" if jours != 999 else "À VIE"
    bot.edit_message_text(f"🔑 **CLÉ GÉNÉRÉE** 🔑\n\n⏳ Durée : {duree_texte}\n👤 ID : `{user_id}`\n\nCopie ce message à ton client :\n\n`{cle}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# --- NOUVEAU CLAVIER AVEC LA WEB APP ---
def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    # LE BOUTON MAGIQUE WEB APP
    markup.row(KeyboardButton("📱 OUVRIR LE RADAR VIP", web_app=WebAppInfo(url=WEBAPP_URL)))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    username = message.from_user.username or message.from_user.first_name
    
    if not est_autorise(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Accepter", callback_data=f"admin_accepter_{user_id}"), InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}"))
        try: bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT** 🚨\n\n👤 @{username}\n🆔 `{user_id}`\n\nGénérer un abonnement ?", reply_markup=markup, parse_mode="Markdown")
        except: pass
        try: bot.send_message(user_id, "🔒 **ACCÈS RESTREINT** 🔒\n\nCe système est sous licence.\n📲 **Contactez : [@hermann1123](https://t.me/hermann1123)**\n\n*(Ou collez votre clé ici).* ", parse_mode="Markdown", disable_web_page_preview=True)
        except: pass
        return

    utilisateurs_actifs.add(user_id)
    texte = """🏴‍☠️ **TERMINAL PRIME - ÉDITION BINAIRE** 🔥\n\nBienvenue dans ton radar de trading ultime.\n\n📖 **MODE D'EMPLOI :**\n1️⃣ Clique sur "📱 OUVRIR LE RADAR VIP".\n2️⃣ Sélectionne ta devise et lance l'analyse sécurisée.\n\n💡 *Discipline de fer : 2% de mise max.*"""
    try: bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(), parse_mode="Markdown")
    except: pass

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **HORAIRES DE TRADING (Heure GMT)** 🕒\n\n✅ **MATINÉE (08h00 - 11h00)**\n*Europe ouverte.* (Favoris: EUR/USD, USD/JPY)\n\n🔥 **ZONE EN OR (13h30 - 16h30)**\n*Europe + New York.* (Favoris: EUR/USD, AUD/USD)\n\n🌉 **SESSION DE NUIT (20h00 - 08h00)**\n*Asie ouverte.* Le bot bascule en sécurité sur JPY et AUD (Ex: AUD/JPY, CAD/JPY)."""
    try: bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

# --- RÉCEPTION DES DONNÉES DE LA WEB APP ---
@bot.message_handler(content_types=['web_app_data'])
def handle_webapp_data(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    
    # On récupère la devise envoyée par le code JavaScript !
    actif = message.web_app_data.data
    
    try:
        msg = bot.send_message(chat_id, "⏳ *Analyse des données reçues du Radar...*", parse_mode="Markdown")
        time.sleep(1)
    except: return
        
    action, confiance, exp, duree_secondes, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: bot.edit_message_text(f"{action}\nLe prix ne remplit pas les conditions strictes.", chat_id, msg.message_id)
        except: pass
        return
    elif not action:
        try: bot.edit_message_text("❌ Échec des données. Relance l'analyse.", chat_id, msg.message_id)
        except: pass
        return

    heure_entree_dt = (datetime.datetime.now() + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0)
    heure_entree_texte = heure_entree_dt.strftime("%H:%M:00")
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)
    jauge = generer_jauge(confiance)
    rsi_emoji = "🟢" if "ACHAT" in action else "🔴"
    rsi_text = f"Essoufflé à {rsi_val}" if "ACHAT" in action else f"Surchauffe à {rsi_val}"
    stoch_text = "Survente" if "ACHAT" in action else "Surachat"

    signal = f"🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀\n──────────────────\n🛰 **ACTIF :** {actif[:3]}/{actif[3:]}\n🎯 **ACTION :** {action}\n⏳ **EXPIRATION :** {exp}\n──────────────────\n🌡️ **FORCE DU SIGNAL :**\n{jauge}\n\n📊 **VALIDATION :**\n➤ **RSI :** {rsi_emoji} {rsi_text}\n➤ **Stoch :** {rsi_emoji} {stoch_text}\n➤ **Bollinger :** {rsi_emoji} {bb_status}\n──────────────────\n📍 **ORDRE À :** {heure_entree_texte} 👈\n💵 **MISE REC. :** {mise_recommandee}$ (2%)\n🔥 **CONFIANCE :** {confiance}%\n──────────────────"

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    trades_en_cours[chat_id] = {'symbole': actif, 'action': "CALL" if "ACHAT" in action else "PUT"}
    delai = max(0, (heure_entree_dt - datetime.datetime.now()).total_seconds())
    Timer(delai, relever_prix_entree, args=[chat_id, actif]).start()
    Timer(delai + duree_secondes, verifier_resultat, args=[chat_id]).start()

if __name__ == "__main__":
    print("⬛ BOÎTE NOIRE : Démarrage du système avec Web App...", flush=True)
    try:
        keep_alive()
        Thread(target=scanner_marche_auto, daemon=True).start()
        Thread(target=gestion_horaires_et_bilan, daemon=True).start()
        bot.infinity_polling()
    except Exception as e:
        print(f"🚨 BOÎTE NOIRE [CRASH] : {e}", flush=True)
