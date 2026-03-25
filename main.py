import os
import sys
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
import datetime
import random
import time
import string
from flask import Flask
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
TELEGRAM_TOKEN = "8658287331:AAGxo2mryCakfYLRwHZ6QnYUs6L5iucc7xQ"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# 👑 L'ID DU FONDATEUR (TOI) 👑
ADMIN_ID = 5968288964 

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

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Trading Prime VIP en ligne ! (Esthétique VIP Restaurée + Pocket Broker)"

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
            try:
                bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé. Veuillez contacter [@hermann1123](https://t.me/hermann1123) pour renouveler votre clé.", parse_mode="Markdown")
            except:
                pass
            return False
    return False

# --- GÉNÉRATEUR DE CLÉS ---
def generer_cle():
    caracteres = string.ascii_uppercase + string.digits
    aleatoire = ''.join(random.choice(caracteres) for _ in range(8))
    return f"PRIME-{aleatoire}"

# --- FONCTION DE RÉCUPÉRATION DU PRIX EXACT ---
def obtenir_prix_actuel(symbole):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=1d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=5)
        donnees = reponse.json()
        return round(float(donnees['chart']['result'][0]['meta']['regularMarketPrice']), 5)
    except:
        return None

# --- VÉRIFICATION AUTOMATIQUE (ITM/OTM) ---
def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel(symbole)
    if prix and chat_id in trades_en_cours:
        trades_en_cours[chat_id]['prix_entree'] = prix

def verifier_resultat(chat_id):
    global stats_journee
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'):
        try:
            bot.send_message(chat_id, "⚠️ **Trade terminé.** (Flux interrompu, résultat exact non vérifiable).", parse_mode="Markdown")
        except:
            pass
        return

    prix_sortie = obtenir_prix_actuel(trade['symbole'])
    if not prix_sortie:
        return

    prix_entree = trade['prix_entree']
    action = trade['action']
    symbole = trade['symbole']

    gagne = False
    if "CALL" in action and prix_sortie > prix_entree:
        gagne = True
    elif "PUT" in action and prix_sortie < prix_entree:
        gagne = True

    nom_paire = f"{symbole[:3]}/{symbole[3:]}"
    
    if gagne:
        texte = f"✅ **VICTOIRE (ITM) !**\n\nSignal passé avec succès 🎉\nLe trade sur {nom_paire} a été validé !\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`"
        stats_journee['ITM'] += 1
        stats_journee['details'].append(f"✅ {nom_paire} ({action})")
    else:
        texte = f"❌ **PERTE (OTM)** ⚠️\n\nLe marché s'est retourné sur {nom_paire}.\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n\n*Garde ton sang-froid, respecte ton Money Management.*"
        stats_journee['OTM'] += 1
        stats_journee['details'].append(f"❌ {nom_paire} ({action})")
    
    try:
        bot.send_message(chat_id, texte, parse_mode="Markdown")
    except:
        pass
    
    if chat_id in trades_en_cours:
        del trades_en_cours[chat_id]

# --- GÉNÉRATEUR DE JAUGE VISUELLE ---
def generer_jauge(pourcentage):
    if pourcentage == 99:
        return "[██████████] 👑 MAX"
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
        
        resultat = donnees['chart']['result'][0]
        quote = resultat['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'open': quote['open'],  
            'close': quote['close'],
            'high': quote['high'],
            'low': quote['low']
        }).dropna()
        
        if len(df) < 50:
            return "⚠️ Pas assez de données", None, None, None, None, None, None

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_haute'] = indicateur_bb.bollinger_hband()
        df['bb_basse'] = indicateur_bb.bollinger_lband()
        
        indicateur_stoch = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
        df['stoch_k'] = indicateur_stoch.stoch()
        
        indicateur_rsi = ta.momentum.RSIIndicator(close=df['close'], window=14)
        df['rsi'] = indicateur_rsi.rsi()
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
            
        bougie_mere = df.iloc[-3]
        bougie_enfant = df.iloc[-2] 
        
        ema_200 = bougie_enfant['ema_200']
        c = bougie_enfant['close']
        o = bougie_enfant['open']
        h = bougie_enfant['high']
        l = bougie_enfant['low']
        
        prev_o = bougie_mere['open']
        prev_c = bougie_mere['close']

        taille_totale = (h - l) if (h - l) > 0 else 0.00001
        corps = abs(c - o)
        meche_haute = h - max(o, c)
        meche_basse = min(o, c) - l
        
        est_marteau = meche_basse >= (corps * 1.5) and meche_haute <= corps
        est_etoile = meche_haute >= (corps * 1.5) and meche_basse <= corps
        
        bullish_engulfing = (prev_c < prev_o) and (c > o) and (c >= prev_o) and (o <= prev_c)
        bearish_engulfing = (prev_c > prev_o) and (c < o) and (c <= prev_o) and (o >= prev_c)

        est_inside_bar = (h < bougie_mere['high']) and (l > bougie_mere['low'])

        largeur_bande = (bougie_enfant['bb_haute'] - bougie_enfant['bb_basse']) / c
        if largeur_bande > 0.0025:
            expiration = "5 MINUTES ⏱"
            duree_secondes = 300
        else:
            expiration = "3 MINUTES ⏱"
            duree_secondes = 180
        
        action = None
        confiance = 0
        
        rsi_val = round(bougie_enfant['rsi'], 1)
        stoch_val = round(bougie_enfant['stoch_k'], 1)
        bb_status = ""
        
        if c >= bougie_enfant['bb_haute'] and bougie_enfant['stoch_k'] >= 80 and bougie_enfant['rsi'] >= 60:
            bb_status = "🔴 Rejet au Plafond"
            if c < ema_200: 
                if est_inside_bar:
                    action = "🔴 VENTE (PUT) 👑 [TITAN INSIDE BAR]"
                    confiance = 99
                elif est_etoile or bearish_engulfing:
                    action = "🔴 VENTE (PUT) ☄️ [PRICE ACTION VIP]"
                    confiance = random.randint(94, 98)
                else:
                    return "⚠️ Rejeté : Pas de bougie de retournement (Attente)", None, None, None, None, None, None
            else:
                return "⚠️ Tendance haussière forte (Attente)", None, None, None, None, None, None
                
        elif c <= bougie_enfant['bb_basse'] and bougie_enfant['stoch_k'] <= 20 and bougie_enfant['rsi'] <= 40:
            bb_status = "🟢 Rejet au Plancher"
            if c > ema_200: 
                if est_inside_bar:
                    action = "🟢 ACHAT (CALL) 👑 [TITAN INSIDE BAR]"
                    confiance = 99
                elif est_marteau or bullish_engulfing:
                    action = "🟢 ACHAT (CALL) 🔨 [PRICE ACTION VIP]"
                    confiance = random.randint(94, 98)
                else:
                    return "⚠️ Rejeté : Pas de bougie de retournement (Attente)", None, None, None, None, None, None
            else:
                return "⚠️ Tendance baissière forte (Attente)", None, None, None, None, None, None
            
        else:
            return "⚠️ Marché neutre (Attente d'opportunité)", None, None, None, None, None, None
            
        return action, confiance, expiration, duree_secondes, rsi_val, stoch_val, bb_status
        
    except Exception as e:
        return None, None, None, None, None, None, None

# --- SCANNER AUTOMATIQUE DYNAMIQUE (POCKET BROKER 100%) ---
def scanner_marche_auto():
    while True:
        try:
            time.sleep(60)
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]
            if not utilisateurs_a_alerter: 
                continue
            
            heure_actuelle = datetime.datetime.now().hour
            if 8 <= heure_actuelle < 20:
                # MODE JOUR : Plus de GBP. USD/CAD remplace.
                devises_a_surveiller = ["EURUSD", "USDCAD", "USDCHF", "EURJPY", "AUDUSD", "USDJPY", "AUDJPY"]
            else:
                # MODE NUIT : Plus de GBP ni de NZD. Focus JPY et AUD.
                devises_a_surveiller = ["AUDJPY", "USDJPY", "CHFJPY", "CADJPY", "AUDCAD", "EURAUD"]
            
            for actif in devises_a_surveiller:
                action, confiance, exp, duree, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
                
                if action and "⚠️" not in action and confiance:
                    maintenant = time.time()
                    
                    if actif in derniere_alerte_auto and (maintenant - derniere_alerte_auto[actif] < 900):
                        continue
                        
                    derniere_alerte_auto[actif] = maintenant
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(f"📊 Analyser {actif[:3]}/{actif[3:]}", callback_data=f"set_{actif}"))
                    
                    if "TITAN" in action:
                        alerte_msg = f"👑 **ALERTE TITAN DÉTECTÉE** 👑\n\nUne compression de marché rarissime vient d'apparaître sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Clique sur le bouton ci-dessous pour lancer l'analyse !*"
                    else:
                        alerte_msg = f"🚨 **NOUVELLE OPPORTUNITÉ VIP** 🚨\n\nL'algorithme a validé une figure de retournement sur **{actif[:3]}/{actif[3:]}** (Confiance : {confiance}%).\n\n👇 *Clique sur le bouton ci-dessous pour lancer l'analyse !*"
                    
                    for chat_id in utilisateurs_a_alerter:
                        try:
                            bot.send_message(chat_id, alerte_msg, reply_markup=markup, parse_mode="Markdown")
                        except:
                            pass
        except Exception as e:
            print(f"⬛ BOÎTE NOIRE [ERREUR SCANNER] : {e}", flush=True)

# --- GESTION DES HORAIRES (TRANSITIONS & BILAN) ---
def gestion_horaires_et_bilan():
    global stats_journee, bilan_envoye_aujourdhui, transition_nuit_envoyee, transition_jour_envoyee
    while True:
        try:
            maintenant = datetime.datetime.now()
            heure = maintenant.hour
            minute = maintenant.minute
            
            utilisateurs_a_alerter = [uid for uid in utilisateurs_actifs if est_autorise(uid)]

            if heure == 20 and minute == 0 and not transition_nuit_envoyee:
                texte_nuit = "🌉 **TRANSITION DE SESSION : MODE ASIATIQUE ACTIVÉ** 🌉\n\nLes volumes s'effondrent sur l'Europe et l'Amérique. Pour protéger votre capital, le Terminal Prime bascule ses radars sur l'Asie (Focus spécial JPY & AUD).\n\n*La chasse continue de nuit. Restez concentrés.* 🥷"
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_nuit, parse_mode="Markdown")
                    except: pass
                transition_nuit_envoyee = True
                transition_jour_envoyee = False

            elif heure == 8 and minute == 0 and not transition_jour_envoyee:
                texte_jour = "☀️ **TRANSITION DE SESSION : MODE EUROPE/US ACTIVÉ** ☀️\n\nOuverture des marchés majeurs. La volatilité est de retour sur l'EUR et l'USD. Le Terminal Prime repasse en mode offensif classique.\n\n*Bonne journée de trading à tous les VIP !* 🚀"
                for chat_id in utilisateurs_a_alerter:
                    try: bot.send_message(chat_id, texte_jour, parse_mode="Markdown")
                    except: pass
                transition_jour_envoyee = True
                transition_nuit_envoyee = False

            elif heure == 22 and minute == 0 and not bilan_envoye_aujourdhui:
                total_trades = stats_journee['ITM'] + stats_journee['OTM']
                if total_trades > 0:
                    winrate = round((stats_journee['ITM'] / total_trades) * 100)
                    texte_bilan = f"📊 **BILAN VIP DE LA JOURNÉE** 📊\n──────────────────\n🎯 **Total Signaux :** {total_trades}\n✅ **Victoires (ITM) :** {stats_journee['ITM']}\n❌ **Pertes (OTM) :** {stats_journee['OTM']}\n📈 **Winrate :** {winrate}%\n──────────────────\n📝 **Détail des tirs :**\n"
                    for detail in stats_journee['details']:
                        texte_bilan += f"{detail}\n"
                    texte_bilan += "\n💤 *Le radar continue sa surveillance nocturne. Excellente nuit à tous les VIP !*"
                    for chat_id in utilisateurs_a_alerter:
                        try: bot.send_message(chat_id, texte_bilan, parse_mode="Markdown")
                        except: pass
                
                stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}
                bilan_envoye_aujourdhui = True
                
            elif heure == 23:
                bilan_envoye_aujourdhui = False
                
            time.sleep(30)
        except Exception as e:
            time.sleep(60)

# --- ACTIVATION DE LA CLÉ ---
@bot.message_handler(func=lambda m: m.text and m.text.startswith("PRIME-"))
def activer_cle(message):
    cle = message.text.strip()
    
    if cle in cles_generees:
        infos_cle = cles_generees[cle]
        
        if infos_cle["user_id"] != message.chat.id:
            bot.send_message(message.chat.id, "❌ **ACCÈS REFUSÉ** ❌\n\nCette clé d'activation a été générée pour un autre compte Telegram.", parse_mode="Markdown")
            return
            
        jours = infos_cle["jours"]
        
        if jours == 999:
            utilisateurs_autorises[message.chat.id] = "LIFETIME"
            duree_texte = "À VIE 👑"
        else:
            expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
            utilisateurs_autorises[message.chat.id] = expiration
            duree_texte = f"jusqu'au {expiration.strftime('%d/%m/%Y à %H:%M')}"
            
        del cles_generees[cle] 
        bot.send_message(message.chat.id, f"✅ **CLÉ ACCEPTÉE !** 🎉\n\nVotre abonnement est activé {duree_texte}.\n\nTapez /start pour lancer le Terminal Prime.", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")

# --- MENU D'ABONNEMENT ADMIN ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_"))
def gerer_acces(call):
    if call.from_user.id != ADMIN_ID: return
    action = call.data.split("_")[1]
    user_id = int(call.data.split("_")[2])
    
    if action == "accepter":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("1 Semaine", callback_data=f"gen_7_{user_id}"),
            InlineKeyboardButton("2 Semaines", callback_data=f"gen_14_{user_id}"),
            InlineKeyboardButton("1 Mois", callback_data=f"gen_30_{user_id}"),
            InlineKeyboardButton("2 Mois", callback_data=f"gen_60_{user_id}"),
            InlineKeyboardButton("3 Mois", callback_data=f"gen_90_{user_id}"),
            InlineKeyboardButton("À Vie 👑", callback_data=f"gen_999_{user_id}")
        )
        bot.edit_message_text(f"✅ Utilisateur `{user_id}` accepté.\nChoisis la durée :", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "refuser":
        bot.edit_message_text(f"❌ Demande refusée.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("gen_"))
def creer_cle(call):
    if call.from_user.id != ADMIN_ID: return
    parts = call.data.split("_")
    jours = int(parts[1])
    user_id = int(parts[2])
    
    cle = generer_cle()
    cles_generees[cle] = {"jours": jours, "user_id": user_id}
    
    duree_texte = f"{jours} Jours" if jours != 999 else "À VIE"
    
    msg_cle = f"🔑 **CLÉ GÉNÉRÉE** 🔑\n\n⏳ Durée : {duree_texte}\n👤 ID : `{user_id}`\n\nCopie ce message à ton client :\n\n`{cle}`"
    
    bot.edit_message_text(msg_cle, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

def obtenir_clavier():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE"))
    markup.row(KeyboardButton("⏰ HEURES DE TRADING"))
    return markup

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    username = message.from_user.username or message.from_user.first_name
    
    if not est_autorise(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Accepter", callback_data=f"admin_accepter_{user_id}"),
            InlineKeyboardButton("❌ Ignorer", callback_data=f"admin_refuser_{user_id}")
        )
        try: 
            bot.send_message(ADMIN_ID, f"🚨 **NOUVEAU CLIENT POTENTIEL** 🚨\n\n👤 @{username}\n🆔 `{user_id}`\n\nGénérer un abonnement ?", reply_markup=markup, parse_mode="Markdown")
        except: 
            pass
            
        texte_intrus = """🔒 **ACCÈS RESTREINT - TERMINAL PRIVÉ** 🔒

Ce système est une intelligence artificielle de trading haute précision sous licence payante.

📲 **Pour obtenir votre clé d'accès (Abonnement), veuillez contacter le fondateur : [@hermann1123](https://t.me/hermann1123)**

*(Si vous avez déjà acheté une clé, collez-la simplement ici).*"""
        try: 
            bot.send_message(user_id, texte_intrus, parse_mode="Markdown", disable_web_page_preview=True)
        except: 
            pass
        return

    utilisateurs_actifs.add(user_id)
    
    texte_bienvenue = """🏴‍☠️ **TERMINAL PRIME - ÉDITION BINAIRE** 🔥
    
Bienvenue dans ton radar de trading ultime ! Ce bot est propulsé par un moteur d'intelligence mathématique (Pandas + TA) pour scanner les graphiques à la milliseconde.

📖 **MODE D'EMPLOI :**
1️⃣ **SÉLECTION :** Clique sur "📊 CHOISIR UNE DEVISE" et verrouille la paire que tu souhaites trader.
2️⃣ **RADAR :** Clique sur "🚀 LANCER L'ANALYSE" pour déclencher le scan et le verrouillage Sniper.
3️⃣ **STRATÉGIE :** Consulte les meilleures fenêtres de tir via le bouton "⏰ HEURES DE TRADING".
4️⃣ **DISCIPLINE :** N'oublie pas : 2% de mise maximum et stop total après 3 pertes dans une session.

💡 **LE MOT DU FONDATEUR :**
*Le marché ne ressent rien, n'aie aucune émotion face à lui. Le succès ne vient pas de la chance, mais d'une discipline de fer. Laisse l'algorithme faire les calculs, ne force jamais un trade et protège ton capital comme un tireur d'élite. Bon profit !* 🎯💸"""

    try: 
        bot.send_message(message.chat.id, texte_bienvenue, reply_markup=obtenir_clavier(), parse_mode="Markdown")
    except: 
        pass

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): 
        return
    
    texte = """🕒 **GUIDE DES HORAIRES DE TRADING (Heure GMT)** 🕒

✅ **SESSION 1 : MATINÉE (08h00 - 11h00)**
*Ouverture de l'Europe. Le vrai volume arrive sur les marchés.*
👍 **Devises Favorites :** EUR/USD, USD/JPY, AUD/USD
👎 **À Éviter :** Marchés lents

🔥 **SESSION 2 : ZONE EN OR (13h30 - 16h30)**
*Croisement Europe / New York. La volatilité est maximale et les tendances sont pures.*
👍 **Devises Favorites :** EUR/USD, USD/CAD, AUD/USD

🌉 **SESSION 3 : MODE NUIT (20h00 - 08h00)**
*L'Europe s'endort, l'Asie se réveille. Le bot bascule automatiquement en mode sécurité pour éviter les marchés plats.*
👍 **Devises Favorites :** AUD/JPY, USD/JPY, CAD/JPY, CHF/JPY

*Rappel de Discipline : Fixe-toi tes 2% de mise max et arrête-toi après 3 pertes dans la même session !*"""
    
    try: 
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: 
        pass

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): 
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    heure = datetime.datetime.now().hour
    
    if 8 <= heure < 20:
        markup.add(
            InlineKeyboardButton("🇪🇺 EUR/USD", callback_data="set_EURUSD"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"),
            InlineKeyboardButton("🇦🇺 AUD/USD", callback_data="set_AUDUSD"),
            InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇨🇦 USD/CAD", callback_data="set_USDCAD"),
            InlineKeyboardButton("🇪🇺 EUR/JPY", callback_data="set_EURJPY")
        )
    else:
        markup.add(
            InlineKeyboardButton("🇦🇺 AUD/JPY", callback_data="set_AUDJPY"),
            InlineKeyboardButton("🇯🇵 USD/JPY", callback_data="set_USDJPY"),
            InlineKeyboardButton("🇨🇦 CAD/JPY", callback_data="set_CADJPY"),
            InlineKeyboardButton("🇨🇭 CHF/JPY", callback_data="set_CHFJPY"),
            InlineKeyboardButton("🇦🇺 AUD/CAD", callback_data="set_AUDCAD"),
            InlineKeyboardButton("🇪🇺 EUR/AUD", callback_data="set_EURAUD")
        )
    try: 
        bot.send_message(message.chat.id, "Sélectionne l'actif à scanner :", reply_markup=markup)
    except: 
        pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): 
        return
    
    actif = call.data.split("_")[1]
    user_prefs[call.from_user.id] = actif
    
    try:
        msg = bot.send_message(chat_id, "⏳ *Initialisation du scan algorithmique rapide...*", parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text(f"📡 *Connexion au flux {actif[:3]}/{actif[3:]} et scan de la volatilité en cours...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text("⚙️ *Calcul des indicateurs avancés (BB, RSI, Stochastique)...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(2)
        bot.edit_message_text("💎 *Triple confirmation et analyse Sniper...*", chat_id, msg.message_id, parse_mode="Markdown")
        time.sleep(1)
    except:
        return
        
    action, confiance, exp, duree_secondes, rsi_val, stoch_val, bb_status = analyser_binaire_pro(actif)
    
    if action and "⚠️" in action:
        try: 
            bot.edit_message_text(f"{action}\nLe prix ne remplit pas les conditions strictes de l'algorithme. Patientez.", chat_id, msg.message_id)
        except: 
            pass
        return
    elif not action:
        try: 
            bot.edit_message_text("❌ Échec de la récupération des données. Relance l'analyse.", chat_id, msg.message_id)
        except: 
            pass
        return

    maintenant = datetime.datetime.now()
    heure_entree_dt = (maintenant + datetime.timedelta(minutes=2)).replace(second=0, microsecond=0)
    heure_entree_texte = heure_entree_dt.strftime("%H:%M:00")
    mise_recommandee = int(CAPITAL_ACTUEL * 0.02)

    jauge = generer_jauge(confiance)
    rsi_emoji = "🟢" if "ACHAT" in action else "🔴"
    rsi_text = f"Essoufflé à {rsi_val}" if "ACHAT" in action else f"Surchauffe à {rsi_val}"
    stoch_text = "Survente" if "ACHAT" in action else "Surachat"

    signal = f"""🚀 **SIGNAL SNIPER GÉNÉRÉ** 🚀
──────────────────
🛰 **ACTIF :** {actif[:3]}/{actif[3:]}
🎯 **ACTION :** {action}
⏳ **EXPIRATION :** {exp}
──────────────────
🌡️ **FORCE DU SIGNAL (ALGORITHME) :**
{jauge}

📊 **VALIDATION DES INDICATEURS :**
➤ **RSI :** {rsi_emoji} Validé ({rsi_text})
➤ **Stochastique :** {rsi_emoji} Validé ({stoch_text})
➤ **Bollinger :** {rsi_emoji} {bb_status}
──────────────────
📍 **ORDRE À :** {heure_entree_texte} 👈
💵 **MISE RECOMMANDÉE :** {mise_recommandee}$ (2%)
🔥 **CONFIANCE GLOBALE :** {confiance}%
──────────────────
💎 *Audit de résultat (ITM/OTM) activé en arrière-plan.*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except:
        pass

    action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
    trades_en_cours[chat_id] = {'symbole': actif, 'action': action_simplifiee}
    
    delai_attente_entree = (heure_entree_dt - datetime.datetime.now()).total_seconds()
    delai_attente_entree = max(0, delai_attente_entree)
    
    Timer(delai_attente_entree, relever_prix_entree, args=[chat_id, actif]).start()
    
    delai_verification = delai_attente_entree + duree_secondes
    Timer(delai_verification, verifier_resultat, args=[chat_id]).start()

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): 
        return
    
    actif = user_prefs.get(message.from_user.id)
    if not actif:
        try: 
            bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise !")
        except: 
            pass
        return
    call_mock = type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})()
    save_devise(call_mock)

# --- COMMANDE SECRÈTE : RADIOGRAPHIE DU MARCHÉ (RESTAURÉE) ---
@bot.message_handler(commands=['vision'])
def vision_marche(message):
    if not est_autorise(message.chat.id):
        return
        
    commande = message.text.split()
    if len(commande) < 2:
        try: 
            bot.send_message(message.chat.id, "⚠️ Précise la devise. Exemple : `/vision EURUSD`", parse_mode="Markdown")
        except: 
            pass
        return
        
    symbole = commande[1].upper()
    try: 
        msg = bot.send_message(message.chat.id, f"🔍 *Scan aux rayons X de {symbole}...*", parse_mode="Markdown")
    except: 
        return
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}=X?range=2d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        reponse = requests.get(url, headers=headers, timeout=10)
        donnees = reponse.json()
        quote = donnees['chart']['result'][0]['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'close': quote['close'],
            'high': quote['high'],
            'low': quote['low']
        }).dropna()
        
        if len(df) < 50:
            bot.edit_message_text("⚠️ Impossible de scanner (manque de données).", message.chat.id, msg.message_id)
            return

        indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_haute = indicateur_bb.bollinger_hband().iloc[-1]
        bb_basse = indicateur_bb.bollinger_lband().iloc[-1]
        
        stoch_k = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
        
        df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
        ema_200 = df['ema_200'].iloc[-1]
        
        prix_actuel = df['close'].iloc[-1]
        
        if prix_actuel >= bb_haute:
            position_bb = "🔴 Au Plafond (Touche la bande haute)"
        elif prix_actuel <= bb_basse:
            position_bb = "🟢 Au Plancher (Touche la bande basse)"
        else:
            position_bb = "⚪ Au Milieu (Zone neutre)"

        rapport = f"""👁️ **VISION RAYONS X : {symbole}** 👁️
──────────────────
💰 **Prix actuel :** `{prix_actuel:.5f}`
🛡️ **EMA 200 (Tendance) :** `{ema_200:.5f}`
📏 **Position Bollinger :** {position_bb}

📊 **Niveau RSI :** `{rsi:.2f}` 
*(Rappel: >60 = Surchauffe, <40 = Essoufflé)*

📉 **Niveau Stochastique :** `{stoch_k:.2f}`
*(Rappel: >80 = Surachat, <20 = Survente)*
──────────────────"""
        
        if position_bb != "⚪ Au Milieu (Zone neutre)":
            rapport += "\n⚠️ *Le prix teste les limites, tiens-toi prêt !*"
        else:
            rapport += "\n💤 *Le marché respire tranquillement.*"

        bot.edit_message_text(rapport, message.chat.id, msg.message_id, parse_mode="Markdown")
        
    except Exception as e:
        try: 
            bot.edit_message_text(f"❌ Erreur lors du scan : {e}", message.chat.id, msg.message_id)
        except: 
            pass

if __name__ == "__main__":
    print("⬛ BOÎTE NOIRE : Démarrage du système...", flush=True)
    try:
        keep_alive()
        Thread(target=scanner_marche_auto, daemon=True).start()
        Thread(target=gestion_horaires_et_bilan, daemon=True).start()
        print("⬛ BOÎTE NOIRE : Serveur et Scanner lancés.", flush=True)
        bot.infinity_polling()
    except Exception as e:
        print(f"🚨 BOÎTE NOIRE [CRASH] : {e}", flush=True)
