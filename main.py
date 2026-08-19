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
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# 🟢 IMPORT DE L'API POCKET OPTION
from pocketoptionapi.stable_api import PocketOption

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================

TELEGRAM_TOKEN = "8000472746:AAGultX_p0P5uYEuNmTHUacO9hgWf6SM_RQ"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

# CONFIGURATION MARTINGALE SÉCURISÉE
COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3  

# ==========================================
# CONNEXION À POCKET OPTION (SSID)
# ==========================================
SSID = "c8p9d7a50kfnr50oqevscpprdi"
print("🔄 Connexion au serveur Pocket Option OTC...")
api_po = PocketOption(SSID)
api_po.connect()

# On force le compte DEMO pour la sécurité de l'algorithme
try:
    api_po.change_balance('PRACTICE')
    print(f"✅ PO Connecté ! Solde Démo : {api_po.get_balance()} $")
except Exception as e:
    print(f"⚠️ Erreur de connexion PO (SSID peut-être expiré) : {e}")

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

# NOUVELLES PAIRES OTC (Disponibles H24 et 7j/7)
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
    return "Terminal Prime VIP : Édition V18.0 POCKET OPTION (OTC)"

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
            try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès au Terminal Prime est terminé.", parse_mode="Markdown")
            except: pass
            return False
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    try:
        argument = message.text.split()[1].lower()
        if argument == '1s': jours = 7
        elif argument == '2s': jours = 14
        elif argument == '1m': jours = 30
        elif argument == '3m': jours = 90
        elif argument == 'vie': jours = "LIFETIME"
        else: jours = int(argument) 
            
        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours
        
        texte = f"✅ **CLÉ GÉNÉRÉE AVEC SUCCÈS**\n\n🔑 **Clé :** `{cle}`\n"
        texte += f"⏳ **Durée :** À VIE 👑\n\n" if jours == "LIFETIME" else f"⏳ **Durée :** {jours} Jours\n\n"
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle in cles_generees:
            jours = cles_generees[cle]
            if jours == "LIFETIME":
                utilisateurs_autorises[chat_id] = "LIFETIME"
                expiration_texte = "À VIE 👑"
            else:
                expiration = datetime.datetime.now() + datetime.timedelta(days=jours)
                utilisateurs_autorises[chat_id] = expiration
                expiration_texte = expiration.strftime('%d/%m/%Y à %H:%M')
            del cles_generees[cle] 
            texte = f"🎉 **ACCÈS TERMINAL PRIME DÉVERROUILLÉ !** 🎉\n\nBienvenue dans l'équipe.\n⏳ **Fin de l'abonnement :** {expiration_texte}\n\n👉 Tapez /start pour initialiser votre tableau de bord."
            bot.send_message(chat_id, texte, parse_mode="Markdown")
        else: bot.send_message(chat_id, "❌ **Clé invalide, expirée ou déjà utilisée.**", parse_mode="Markdown")
    except: pass

# ==========================================
# VERROUILLAGE TEMPOREL 
# ==========================================
def est_symbole_autorise(symbole):
    # L'OTC de Pocket Option tourne H24. Aucun couvre-feu n'est nécessaire.
    if "_otc" in symbole.lower(): 
        return "AUTORISE", ""
    return "BLOCAGE_TOTAL", "🛑 Seul l'OTC est activé sur cette version."

# ==========================================
# MOTEUR DE DONNÉES POCKET OPTION
# ==========================================
def obtenir_donnees_po(symbole, granularite=300):
    try:
        # Période requise par Pocket Option API
        api_po.get_candles(symbole, granularite)
        candles = api_po.candles.get_candles(symbole, granularite)
        if candles:
            # Conversion pour Pandas
            df = pd.DataFrame(candles)
            df.rename(columns={'open': 'open', 'close': 'close', 'high': 'high', 'low': 'low'}, inplace=True)
            # Renvoi sous forme de liste de dictionnaires pour coller à l'ancien code SMC
            return df.to_dict('records')
    except Exception as e:
        print(f"Erreur d'extraction PO sur {symbole} : {e}")
    return None

def obtenir_prix_actuel_po(symbole):
    try:
        # L'API Pocket Option gère un flux en temps réel
        return api_po.get_realtime_price(symbole)
    except:
        return None

def verifier_correlation(symbole_base, action_visee):
    # Les marchés OTC sont des algorithmes synthétiques, ils ne sont pas corrélés entre eux.
    return True 

def est_heure_de_news_dynamique():
    # Pas de calendrier économique en OTC
    return False

# ==========================================
# MOTEUR ULTIMATE V18.0 (SMC + KILLSWITCH + OTC)
# ==========================================
def analyser_binaire_pro(symbole, mode="STANDARD"):
    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]
    
    for tf in timeframes:
        candles = obtenir_donnees_po(symbole, tf)
        if not candles: continue
        
        try:
            df = pd.DataFrame(candles)
            df['corps_bougie'] = abs(df['close'] - df['open'])
            df['taille_bougie'] = df['high'] - df['low']
            df['meche_haute'] = df['high'] - df[['open', 'close']].max(axis=1)
            df['meche_basse'] = df[['open', 'close']].min(axis=1) - df['low']
            
            df['volume_proxy'] = df['high'] - df['low']
            df['volume_moyen'] = df['volume_proxy'].rolling(window=10).mean()
            
            vol_actuel = df['volume_proxy'].iloc[-1]
            vol_moyen = df['volume_moyen'].iloc[-1]
            
            volume_ok = (vol_actuel > vol_moyen) and (vol_actuel < (vol_moyen * 2.5))

            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
            df['stoch_k'] = ta.momentum.StochasticOscillator(high=df['high'], low=df['low'], close=df['close']).stoch()
            
            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            c = last['close']
            rsi_val, stoch_val = round(last['rsi'], 1), round(last['stoch_k'], 1)
            action, confiance, bb_status, score_algo = None, 0, "En Attente", 5
            
            vrai_corps = last['corps_bougie'] > (last['taille_bougie'] * 0.25)
            last_is_green = last['close'] > last['open']
            last_is_red = last['close'] < last['open']
            prev_is_green = prev['close'] > prev['open']
            prev_is_red = prev['close'] < prev['open']
            
            rejet_haussier = last['meche_basse'] > (last['corps_bougie'] * 1.5)
            rejet_baissier = last['meche_haute'] > (last['corps_bougie'] * 1.5)
            avalement_haussier = prev_is_red and last_is_green and (last['close'] > prev['open']) and (last['open'] <= prev['close'])
            avalement_baissier = prev_is_green and last_is_red and (last['close'] < prev['open']) and (last['open'] >= prev['close'])
            
            corps_prev = prev['corps_bougie']
            danger_rejet_baisse = prev['meche_haute'] > (corps_prev * 1.5) if corps_prev > 0 else False
            danger_rejet_hausse = prev['meche_basse'] > (corps_prev * 1.5) if corps_prev > 0 else False

            fusee_haussiere = last_is_green and prev_is_green and (p_prev['close'] > p_prev['open']) and vrai_corps
            fusee_baissiere = last_is_red and prev_is_red and (p_prev['close'] < p_prev['open']) and vrai_corps
            
            # SMC
            swing_high_1 = df['high'].iloc[-20:-10].max()
            swing_low_1 = df['low'].iloc[-20:-10].min()
            swing_high_2 = df['high'].iloc[-10:-1].max()
            swing_low_2 = df['low'].iloc[-10:-1].min()

            structure_haussiere = (swing_high_2 > swing_high_1) and (swing_low_2 >= swing_low_1)
            structure_baissiere = (swing_low_2 < swing_low_1) and (swing_high_2 <= swing_high_1)

            prix_moyen_recent = df['close'].iloc[-6:-1].mean()
            dans_zone_discount = c < prix_moyen_recent 
            dans_zone_premium = c > prix_moyen_recent 

            if mode == "STANDARD":
                if tf == 300:
                    duree_secondes, exp_texte = 180, "3 MIN (HIT & RUN ⚡)"
                else:
                    duree_secondes, exp_texte = tf, f"{int(tf/60)} MIN"
                
                if structure_haussiere and dans_zone_discount and volume_ok and vrai_corps and not danger_rejet_baisse and not fusee_baissiere:
                    if (stoch_val < 40) and (rsi_val > 40): 
                        action, confiance, score_algo = "🟢 ACHAT (CALL)", 85, 8.0
                        bb_status = f"🎯 SMC : Order Block (Zone Discount)"
                    if avalement_haussier or rejet_haussier:
                        action, confiance, score_algo = "🟢 ACHAT (CALL)", 99, 10.0
                        bb_status = f"👑 SMC ULTIME : Prise de Liquidité 🚀"
                        
                elif structure_baissiere and dans_zone_premium and volume_ok and vrai_corps and not danger_rejet_hausse and not fusee_haussiere:
                    if (stoch_val > 60) and (rsi_val < 60):
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 85, 8.0
                        bb_status = f"🎯 SMC : Order Block (Zone Premium)"
                    if avalement_baissier or rejet_baissier:
                        action, confiance, score_algo = "🔴 VENTE (PUT)", 99, 10.0
                        bb_status = f"👑 SMC ULTIME : Prise de Liquidité ☄️"

            elif mode == "SCALP":
                indicateur_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.2)
                bb_haute, bb_basse = indicateur_bb.bollinger_hband().iloc[-1], indicateur_bb.bollinger_lband().iloc[-1]
                df['bb_width'] = indicateur_bb.bollinger_wband()
                squeeze = df['bb_width'].iloc[-1] < (df['bb_width'].rolling(window=20).mean().iloc[-1] * 0.8)

                duree_secondes, exp_texte = 60, "1 MINUTE (SCALP 🛡️)"
                
                if not squeeze and volume_ok and vrai_corps:
                    if (last['low'] <= bb_basse) and rejet_haussier and not danger_rejet_baisse and not fusee_baissiere:
                        action, confiance, score_algo, bb_status = "🟢 ACHAT (CALL)", 95, 9.5, "🛡️ SMC Scalp : Chasse aux Stops Bas"
                    elif (last['high'] >= bb_haute) and rejet_baissier and not danger_rejet_hausse and not fusee_haussiere:
                        action, confiance, score_algo, bb_status = "🔴 VENTE (PUT)", 95, 9.5, "🛡️ SMC Scalp : Chasse aux Stops Haut"

            if action:
                action_simplifiee = "CALL" if "ACHAT" in action else "PUT"
                delai_blocage = 600 if mode == "SCALP" else 1800
                if symbole in cooldown_actifs and (time.time() - cooldown_actifs[symbole]['time'] < delai_blocage):
                    if action_simplifiee == cooldown_actifs[symbole]['action']:
                        return f"⚠️ **BLOCAGE ANTI-FAKEOUT**", None, None, None, None, None, None, None
                return action, min(confiance, 99), exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score_algo
                
        except: continue

    return f"⚠️ En attente d'une opportunité ({mode}).", None, None, None, None, None, None, None

# ==========================================
# MOTEUR DE TIR ET VÉRIFICATION POCKET OPTION
# ==========================================
def relever_prix_entree(chat_id, symbole):
    prix = obtenir_prix_actuel_po(symbole)
    if prix and chat_id in trades_en_cours and trades_en_cours[chat_id]['symbole'] == symbole:
        trades_en_cours[chat_id]['prix_entree'] = prix

def preparer_nouveau_palier(chat_id, symbole, action_brute, duree, palier):
    mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** palier))
    exp_texte = f"{int(duree/60)} MIN" if duree >= 60 else f"{duree} SEC"
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    
    maintenant = datetime.datetime.now()
    sec_rest = 60 - maintenant.second
    if sec_rest < 15: sec_rest += 60 
    
    heure_entree = maintenant + datetime.timedelta(seconds=sec_rest)
    heure_texte = heure_entree.strftime("%H:%M:00")
    
    texte = f"🚨 **SIGNAL DE TIR : PALIER {palier}** 🚨\n──────────────────\n🌐 **ACTIF :** {symbole.replace('_otc', ' OTC')}\n⏱ **ENTRÉE EXACTE :** `{heure_texte}`\n👉 **ACTION :** {action_affichage}\n⏳ **DURÉE :** {exp_texte}\n💵 **MISE :** `{mise}$`\n──────────────────\n⏳ *Préparez le broker.*"
    
    try: bot.send_message(chat_id, texte, parse_mode="Markdown")
    except: pass
    
    Timer(sec_rest, executer_tir_flash, args=[chat_id, symbole, action_brute, duree, palier]).start()

def executer_tir_flash(chat_id, symbole, action_brute, duree, palier):
    action_affichage = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    nom_paire = symbole.replace('_otc', ' OTC')
    
    if palier == 0:
        texte = f"👻 **LE FANTÔME EST LANCÉ ({nom_paire})** 👻\nL'IA observe le marché virtuellement..."
        markup = None
    else:
        texte = f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom_paire})** 🔥\n👉 **CLIQUEZ SUR {action_affichage} MAINTENANT !**"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"))
        
    try: bot.send_message(chat_id, texte, parse_mode="Markdown", reply_markup=markup)
    except: pass
    
    trades_en_cours[chat_id] = {'symbole': symbole, 'action': action_brute, 'duree': duree}
    Timer(2, relever_prix_entree, args=[chat_id, symbole]).start()
    Timer(duree, verifier_resultat, args=[chat_id]).start()

def verifier_resultat(chat_id):
    global stats_journee, cooldown_actifs, niveaux_martingale
    time.sleep(3)
    trade = trades_en_cours.get(chat_id)
    if not trade or not trade.get('prix_entree'): return

    symbole = trade['symbole']
    prix_sortie = obtenir_prix_actuel_po(symbole)
    if not prix_sortie: return

    prix_entree = trade['prix_entree']
    action = trade['action']
    palier_actuel = niveaux_martingale.get(chat_id, 0)
    gagne = (action == "CALL" and prix_sortie > prix_entree) or (action == "PUT" and prix_sortie < prix_entree)
    nom_paire = symbole.replace('_otc', ' OTC')

    if gagne:
        niveaux_martingale[chat_id] = 0 
        if palier_actuel == 0: texte = f"👻 **FANTÔME RÉUSSI (ITM)**\nLe trade virtuel sur {nom_paire} est passé sans nous.\n🔓 *Radar déverrouillé.*"
        else:
            texte = f"✅ **CIBLE ABATTUE (ITM)**\n🚀 {nom_paire} ({action})\n📈 Entrée : `{prix_entree}`\n📉 Sortie : `{prix_sortie}`\n🔓 *Radar déverrouillé.*"
            stats_journee['ITM'] += 1
            stats_journee['details'].append(f"✅ {nom_paire} ({action})")
            
        if symbole in cooldown_actifs: del cooldown_actifs[symbole]
        if chat_id in trades_en_cours: del trades_en_cours[chat_id]
        try: bot.send_message(chat_id, texte, parse_mode="Markdown")
        except: pass
    else:
        if palier_actuel < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier_actuel + 1
            if chat_id in trades_en_cours: del trades_en_cours[chat_id] 
            msg_fail = f"⚠️ **TIR RATÉ (Palier {palier_actuel} Échoué)**\n📉 Sortie : `{prix_sortie}`\n\n⚡ *Génération instantanée du palier suivant...*"
            bot.send_message(chat_id, msg_fail, parse_mode="Markdown")
            preparer_nouveau_palier(chat_id, symbole, action, trade['duree'], palier_actuel + 1)
        else:
            niveaux_martingale[chat_id] = 0
            texte = f"🛑 **FIN DE SÉQUENCE ATTEINTE (OTM)**\n⚠️ {nom_paire} ({action})\n📉 Sortie : `{prix_sortie}`\nRepli tactique."
            if palier_actuel > 0: stats_journee['OTM'] += 1
            cooldown_actifs[symbole] = {'time': time.time(), 'action': action}
            if chat_id in trades_en_cours: del trades_en_cours[chat_id]
            try: bot.send_message(chat_id, texte, parse_mode="Markdown")
            except: pass

@bot.callback_query_handler(func=lambda c: c.data == "force_win")
def override_victoire_manuelle(call):
    chat_id = call.message.chat.id
    if chat_id in trades_en_cours:
        stats_journee['ITM'] += 1
        del trades_en_cours[chat_id]
    niveaux_martingale[chat_id] = 0
    bot.answer_callback_query(call.id, "✅ Victoire validée ! Le radar est libéré.", show_alert=True)
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass
    bot.send_message(chat_id, "🔄 **CORRECTION MANUELLE APPLIQUÉE**", parse_mode="Markdown")

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

@bot.message_handler(func=lambda m: m.text.startswith("🛡️ MODE:") or m.text.startswith("🔥 MODE:"))
def toggle_mode(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return
    if user_id in trades_en_cours: return bot.send_message(user_id, "⚠️ Silence Radio actif.")
        
    mode_actuel = mode_trading.get(user_id, "STANDARD")
    if mode_actuel == "STANDARD":
        mode_trading[user_id] = "SCALP"
        bot.send_message(user_id, "🔥 **MODE SMC SCALPING (1 MIN) ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")
    else:
        mode_trading[user_id] = "STANDARD"
        bot.send_message(user_id, "🛡️ **MODE SMC STANDARD ACTIVÉ**", reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    niveaux_martingale[user_id] = niveaux_martingale.get(user_id, 0)
    mode_trading[user_id] = mode_trading.get(user_id, "STANDARD")
    texte = """🏴‍☠️ **TERMINAL PRIME - V18.0 POCKET OPTION 🛑** 🔥
    
Mise à jour activée : 📡 **Connexion direct à l'OTC Pocket Option**. 
Les horaires sont désactivés, le marché OTC est disponible 24h/24."""
    bot.send_message(message.chat.id, texte, reply_markup=obtenir_clavier(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours:
        bot.answer_callback_query(call.id, f"⚠️ Focus activé !", show_alert=True)
        return
    
    actif = call.data.replace("set_", "")
    user_prefs[call.from_user.id] = actif
    mode_actuel = mode_trading.get(chat_id, "STANDARD")
    nom_affiche = actif.replace("_otc", " OTC")
    
    try: msg = bot.send_message(chat_id, f"⏳ *Lecture des graphiques Pocket Option...*", parse_mode="Markdown")
    except: return
        
    action, confiance, exp_texte, duree_secondes, rsi_val, stoch_val, bb_status, score = analyser_binaire_pro(actif, mode_actuel)
    
    if not action or "⚠️" in action:
        try: bot.edit_message_text(f"{action}", chat_id, msg.message_id)
        except: pass
        return

    maintenant = datetime.datetime.now()
    sec_rest = (60 - maintenant.second)
    if mode_actuel == "SCALP" and sec_rest < 45: sec_rest += 60 
    elif mode_actuel == "STANDARD" and sec_rest < 15: sec_rest += 60
        
    palier = niveaux_martingale.get(chat_id, 0)
    
    if palier == 0 and score is not None and score >= 10.0:
        palier = 1 
        niveaux_martingale[chat_id] = 1 
        sec_rest += 60 
        fantome_texte = "🧠 **FANTÔME DÉSACTIVÉ PAR L'IA SMC (10/10)**\n*Prise de liquidité parfaite, on attaque en réel direct !*"
    elif palier == 0:
        fantome_texte = "*Le bot prend ce trade virtuellement (Fantôme SMC). NE RENTREZ PAS.*"
    else:
        fantome_texte = ""

    heure_entree_p0 = maintenant + datetime.timedelta(seconds=sec_rest)
    str_p0 = heure_entree_p0.strftime("%H:%M:00")
    mise_calculee = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** (palier - 1 if palier > 0 else 0)))

    if palier == 0:
        signal = f"""👻 **MODE FANTÔME (PALIER 0)** 👻\n──────────────────\n🌐 **ACTIF :** {nom_affiche}\n⏱ **ENTRÉE EXACTE :** `{str_p0}`\n👉 **ACTION :** {action}\n⏳ **DURÉE :** {exp_texte}\n\n{fantome_texte}\n──────────────────"""
    else:
        signal = f"""🚨 **ALERTE DE TIR RÉEL VIP 💎** 🚨\n──────────────────\n🌐 **ACTIF :** {nom_affiche}\n⏱ **ENTRÉE EXACTE :** `{str_p0}`\n⏳ **EXPIRATION :** {exp_texte}\n👉 **ACTION :** {action}\n🛡️ {bb_status}\n\n{fantome_texte if fantome_texte else ''}\n💵 **MISE CALCULÉE :** `{mise_calculee}$`\n*(Statut : Palier {palier})*"""

    try:
        bot.delete_message(chat_id, msg.message_id)
        bot.send_message(chat_id, signal, parse_mode="Markdown")
    except: pass

    action_brute = "CALL" if "ACHAT" in action else "PUT"
    Timer(sec_rest, executer_tir_flash, args=[chat_id, actif, action_brute, duree_secondes, palier]).start()

@bot.message_handler(func=lambda m: m.text == "⏰ HEURES DE TRADING")
def horaires_trading(message):
    if not est_autorise(message.chat.id): return
    texte = """🕒 **GUIDE DES HORAIRES (OTC)** 🕒\n\nLe marché OTC de Pocket Option est ouvert 24h/24 et 7j/7.\nLe bot ne se mettra plus en pause."""
    bot.send_message(message.chat.id, texte, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    if not est_autorise(message.chat.id): return
    markup = InlineKeyboardMarkup(row_width=3)
    # Remplacement du clavier avec les paires OTC
    boutons = [InlineKeyboardButton(paire.replace("_otc", " OTC"), callback_data=f"set_{paire}") for paire in OTC_PAIRS]
    markup.add(*boutons)
    bot.send_message(message.chat.id, "Sélectionne ta cible OTC :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    chat_id = message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours: return bot.send_message(chat_id, f"⚠️ Combat en cours sur **{trades_en_cours[chat_id]['symbole']}**.", parse_mode="Markdown")
    actif = user_prefs.get(message.from_user.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis d'abord une devise OTC dans le menu !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user})())

if __name__ == "__main__":
    keep_alive()
    print("⬛ BOÎTE NOIRE : Édition V18.0 POCKET OPTION OTC Démarrée.", flush=True)
    bot.infinity_polling()
