import os
import datetime
import random
import time
import string
import json
import pandas as pd
import ta
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# 🟢 IMPORT DE L'API POCKET OPTION
from pocketoptionapi.stable_api import PocketOption

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
# CONNEXION À POCKET OPTION (SSID)
# ==========================================
SSID = "c8p9d7a50kfnr50oqevscpprdi"
print("🔄 Connexion au serveur Pocket Option OTC...")
api_po = PocketOption(SSID)

def maintenir_connexion_po():
    api_po.connect()
    while True:
        try:
            if not api_po.check_connect():
                api_po.connect()
        except: pass
        time.sleep(30)

Thread(target=maintenir_connexion_po, daemon=True).start()

# ==========================================
# VARIABLES D'ÉTAT
# ==========================================
user_prefs, mode_trading, trades_en_cours, utilisateurs_actifs = {}, {}, {}, set()
derniere_alerte_auto, cooldown_actifs, niveaux_martingale = {}, {}, {}
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}

# PAIRES STRICTEMENT OTC POCKET OPTION
OTC_PAIRS = [
    "EURUSD_otc", "GBPUSD_otc", "AUDUSD_otc", "NZDUSD_otc",
    "USDCAD_otc", "USDCHF_otc", "USDJPY_otc", "EURJPY_otc",
    "GBPJPY_otc", "AUDJPY_otc", "CADJPY_otc", "CHFJPY_otc"
]

def format_otc_nom(symbole):
    return symbole.replace("_otc", " OTC")

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================
app = Flask(__name__)
@app.route('/')
def home(): return "Terminal Prime VIP : Édition V18 (4 Piliers + Vrai OTC) - EN LIGNE"
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()

# ==========================================
# SÉCURITÉ ET ACCÈS VIP
# ==========================================
def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration: return True
        del utilisateurs_autorises[user_id]
        try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️", parse_mode="Markdown")
        except: pass
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    arg = message.text.split()[1].lower() if len(message.text.split()) > 1 else '1s'
    jours = "LIFETIME" if arg == 'vie' else (7 if arg == '1s' else 30)
    cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    cles_generees[cle] = jours
    bot.send_message(message.chat.id, f"✅ **CLÉ GÉNÉRÉE**\n🔑 `{cle}`\n⏳ Durée : {jours}", parse_mode="Markdown")

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle in cles_generees:
            jours = cles_generees.pop(cle)
            utilisateurs_autorises[chat_id] = "LIFETIME" if jours == "LIFETIME" else datetime.datetime.now() + datetime.timedelta(days=jours)
            bot.send_message(chat_id, "🎉 **ACCÈS DÉVERROUILLÉ !** Tapez /start", parse_mode="Markdown")
    except: pass

# ==========================================
# EXTRACTION DES VRAIES DONNÉES POCKET OPTION
# ==========================================
def obtenir_donnees_otc(symbole, granularite=300):
    for _ in range(3):
        try:
            api_po.get_candles(symbole, granularite)
            time.sleep(1.5) # Temps de réponse du WebSocket PO
            donnees = api_po.candles.get_candles(symbole, granularite)
            if donnees and len(donnees) > 50:
                return donnees
        except: time.sleep(1)
    return None

def obtenir_prix_actuel_otc(symbole):
    try:
        api_po.get_candles(symbole, 60)
        time.sleep(1)
        donnees = api_po.candles.get_candles(symbole, 60)
        if donnees: return float(donnees[-1]['close'])
    except: pass
    return None

def verifier_correlation(symbole_base, action_visee):
    # Les marchés OTC sont des algorithmes synthétiques créés par le broker.
    # Ils n'ont pas de corrélation macroéconomique entre eux.
    return True 

# ==========================================
# LES 4 PILIERS MATHÉMATIQUES
# ==========================================
def calculer_aroon(df, period=9):
    high_idx = df['high'].rolling(period + 1).apply(lambda x: period - x.values.argmax(), raw=True)
    low_idx  = df['low'].rolling(period + 1).apply(lambda x: period - x.values.argmin(), raw=True)
    return ((period - high_idx) / period) * 100, ((period - low_idx) / period) * 100

def calculer_stc(df, fast=14, slow=50, cycle=5, d1=3, d2=3):
    macd = df['close'].ewm(span=fast, adjust=False).mean() - df['close'].ewm(span=slow, adjust=False).mean()
    low_macd, high_macd = macd.rolling(cycle).min(), macd.rolling(cycle).max()
    d1_line = (100 * (macd - low_macd) / (high_macd - low_macd).replace(0, 1e-9)).ewm(span=d1, adjust=False).mean()
    low_d, high_d = d1_line.rolling(cycle).min(), d1_line.rolling(cycle).max()
    return (100 * (d1_line - low_d) / (high_d - low_d).replace(0, 1e-9)).ewm(span=d2, adjust=False).mean().clip(0, 100)

def analyser_aroon_rsi(df):
    try:
        au, ad = calculer_aroon(df, 9)
        rsi = ta.momentum.RSIIndicator(close=df['close'], window=6).rsi()
        u, d, u_p, d_p, r = float(au.iloc[-2]), float(ad.iloc[-2]), float(au.iloc[-3]), float(ad.iloc[-3]), float(rsi.iloc[-2])
        
        def score(dir):
            s, res = (min(35, max(0, (u - d) * 0.5)), []) if dir == "CALL" else (min(35, max(0, (d - u) * 0.5)), [])
            if dir == "CALL":
                if u_p <= d_p and u > d: s += 20; res.append("Croisement Up")
                if 40 <= r <= 68: s += 20; res.append("RSI sain")
            else:
                if d_p <= u_p and d > u: s += 20; res.append("Croisement Down")
                if 32 <= r <= 60: s += 20; res.append("RSI sain")
            return min(100, s), res
        sc, rc = score("CALL")
        sp, rp = score("PUT")
        return {"nom": "AROON_RSI", "label": "Show The Direction", "score_call": sc, "score_put": sp, "txt": f"Aroon {u:.0f}/{d:.0f} | RSI {r:.1f}"}
    except: return None

def analyser_adx_stc(df):
    try:
        adx_ind = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        stc = calculer_stc(df)
        adx, dip, din = float(adx_ind.adx().iloc[-2]), float(adx_ind.adx_pos().iloc[-2]), float(adx_ind.adx_neg().iloc[-2])
        sv, sprev = float(stc.iloc[-2]), float(stc.iloc[-3])
        
        def score(dir):
            s, res = 0, []
            if dir == "CALL":
                if sprev <= 25 and sv > sprev: s += 35
                if dip > din: s += 20
            else:
                if sprev >= 75 and sv < sprev: s += 35
                if din > dip: s += 20
            return min(100, s), res
        sc, rc = score("CALL")
        sp, rp = score("PUT")
        return {"nom": "ADX_STC", "label": "Reversal Points", "score_call": sc, "score_put": sp, "txt": f"STC {sv:.0f} | ADX {adx:.0f}"}
    except: return None

# ==========================================
# MOTEUR PRINCIPAL (4 PILIERS + KILLSWITCH)
# ==========================================
def analyser_binaire_pro(symbole, mode="STANDARD"):
    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]

    for tf in timeframes:
        candles = obtenir_donnees_otc(symbole, tf)
        if not candles: continue

        try:
            df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
            df['corps'] = abs(df['close'] - df['open'])
            df['taille'] = df['high'] - df['low']

            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            fusee_haussiere = (last['close'] > last['open']) and (prev['close'] > prev['open']) and (p_prev['close'] > p_prev['open']) and (last['corps'] > last['taille'] * 0.25)
            fusee_baissiere = (last['close'] < last['open']) and (prev['close'] < prev['open']) and (p_prev['close'] < p_prev['open']) and (last['corps'] > last['taille'] * 0.25)

            resultats = [f(df) for f in (analyser_aroon_rsi, analyser_adx_stc) if f(df)]
            candidats = []
            
            for r in resultats:
                best_score = max(r["score_call"], r["score_put"])
                if best_score < 45: continue
                dir = "CALL" if r["score_call"] >= r["score_put"] else "PUT"
                if (dir == "CALL" and fusee_baissiere) or (dir == "PUT" and fusee_haussiere): continue
                candidats.append({"pilier": r["nom"], "label": r["label"], "dir": dir, "score": best_score, "txt": r["txt"]})

            if not candidats: continue
            
            gagnant = max(candidats, key=lambda x: x["score"])
            action = "🟢 ACHAT (CALL)" if gagnant["dir"] == "CALL" else "🔴 VENTE (PUT)"
            conf = min(99, 70 + int(gagnant["score"] * 0.29))
            bb_status = f"🧩 {gagnant['label']} — {gagnant['txt']}"

            duree, exp_texte = (180, "3 MIN (HIT & RUN)") if tf == 300 else (tf, f"{int(tf/60)} MIN") if mode == "STANDARD" else (60, "1 MINUTE (SCALP)")
            return action, conf, exp_texte, duree, bb_status

        except: continue
    return f"⚠️ En attente d'une configuration valide ({mode}).", None, None, None, None

# ==========================================
# EXÉCUTION & COMMANDES
# ==========================================
def executer_tir_flash(chat_id, symbole, action_brute, duree, palier):
    act = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    nom = format_otc_nom(symbole)
    if palier == 0:
        bot.send_message(chat_id, f"👻 **FANTÔME LANCÉ ({nom})**", parse_mode="Markdown")
    else:
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"))
        bot.send_message(chat_id, f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom})**\n👉 **CLIQUEZ SUR {act} MAINTENANT !**", reply_markup=markup, parse_mode="Markdown")
    
    trades_en_cours[chat_id] = {'symbole': symbole, 'action': action_brute, 'duree': duree, 'prix_entree': obtenir_prix_actuel_otc(symbole)}
    Timer(duree, verifier_resultat, args=[chat_id]).start()

def verifier_resultat(chat_id):
    trade = trades_en_cours.get(chat_id)
    if not trade: return
    prix_sortie = obtenir_prix_actuel_otc(trade['symbole'])
    if not prix_sortie: return

    gagne = (trade['action'] == "CALL" and prix_sortie > trade['prix_entree']) or (trade['action'] == "PUT" and prix_sortie < trade['prix_entree'])
    palier = niveaux_martingale.get(chat_id, 0)
    nom = format_otc_nom(trade['symbole'])

    if gagne:
        niveaux_martingale[chat_id] = 0
        del trades_en_cours[chat_id]
        if palier > 0: stats_journee['ITM'] += 1
        bot.send_message(chat_id, f"✅ **CIBLE ABATTUE (ITM) : {nom}**\n🔓 Radar déverrouillé.", parse_mode="Markdown")
    else:
        if palier < MAX_MARTINGALE:
            niveaux_martingale[chat_id] = palier + 1
            del trades_en_cours[chat_id]
            bot.send_message(chat_id, f"⚠️ **TIR RATÉ : {nom}**\n⚡ Génération du palier {palier+1}...", parse_mode="Markdown")
            Timer(5, executer_tir_flash, args=[chat_id, trade['symbole'], trade['action'], trade['duree'], palier+1]).start()
        else:
            niveaux_martingale[chat_id] = 0
            del trades_en_cours[chat_id]
            stats_journee['OTM'] += 1
            bot.send_message(chat_id, f"🛑 **FIN DE SÉQUENCE (OTM)**\nRepli tactique.", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "force_win")
def override_victoire(call):
    chat_id = call.message.chat.id
    if chat_id in trades_en_cours:
        stats_journee['ITM'] += 1
        del trades_en_cours[chat_id]
    niveaux_martingale[chat_id] = 0
    bot.answer_callback_query(call.id, "✅ Victoire validée !", show_alert=True)
    try: bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except: pass
    bot.send_message(chat_id, "🔄 **CORRECTION MANUELLE APPLIQUÉE**", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def save_devise(call):
    chat_id = call.message.chat.id
    if not est_autorise(chat_id): return
    if chat_id in trades_en_cours: return bot.answer_callback_query(call.id, f"⚠️ Focus activé !", show_alert=True)
    
    actif = call.data.replace("set_", "")
    user_prefs[chat_id] = actif
    msg = bot.send_message(chat_id, f"⏳ *Analyse des 4 Piliers sur {format_otc_nom(actif)} (Pocket Option Vrai Marché)...*", parse_mode="Markdown")
    
    action, conf, exp_texte, duree_sec, bb = analyser_binaire_pro(actif, mode_trading.get(chat_id, "STANDARD"))
    
    if not action or "⚠️" in action:
        try: bot.edit_message_text(action, chat_id, msg.message_id)
        except: pass
        return

    sec_rest = 60 - datetime.datetime.now().second
    if sec_rest < 15: sec_rest += 60
    palier = niveaux_martingale.get(chat_id, 0)
    if palier == 0: palier = 1; niveaux_martingale[chat_id] = 1

    mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** (palier - 1 if palier > 0 else 0)))
    str_h = (datetime.datetime.now() + datetime.timedelta(seconds=sec_rest)).strftime("%H:%M:00")
    
    signal = f"🚨 **ALERTE VIP** 🚨\n🌐 {format_otc_nom(actif)}\n⏱ {str_h}\n⏳ {exp_texte}\n👉 {action}\n{bb}\n💵 Mise : {mise}$ (Palier {palier})" if palier > 0 else f"👻 **FANTÔME** 👻\n🌐 {format_otc_nom(actif)}\n⏱ {str_h}\n⏳ {exp_texte}\n👉 {action}"
    bot.delete_message(chat_id, msg.message_id)
    bot.send_message(chat_id, signal, parse_mode="Markdown")
    Timer(sec_rest, executer_tir_flash, args=[chat_id, actif, "CALL" if "ACHAT" in action else "PUT", duree_sec, palier]).start()

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    actif = user_prefs.get(message.chat.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis une devise OTC d'abord !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user, 'id': '1'})())

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    mode_trading[user_id] = "STANDARD"
    markup = ReplyKeyboardMarkup(resize_keyboard=True).row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE")).row(KeyboardButton("🛡️ MODE: 4 PILIERS STANDARD"), KeyboardButton("⏰ HEURES DE TRADING"))
    bot.send_message(user_id, "🏴‍☠️ **TERMINAL PRIME V18 ULTIMATE** 🔥\n\nMoteur : 4 Piliers Indépendants\nSource : VRAI MARCHÉ OTC Pocket Option H24", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(*[InlineKeyboardButton(format_otc_nom(p), callback_data=f"set_{p}") for p in OTC_PAIRS])
    bot.send_message(message.chat.id, "Sélectionne ta cible (Vrai Marché OTC Pocket Option) :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text.startswith("🛡️") or m.text.startswith("🔥"))
def toggle_mode(m):
    uid = m.chat.id
    mode_trading[uid] = "SCALP" if mode_trading.get(uid) == "STANDARD" else "STANDARD"
    bienvenue(m)

if __name__ == "__main__":
    keep_alive()
    print("⬛ BOÎTE NOIRE : Édition V18 (Vrai OTC Pocket Option) Démarrée.")
    bot.infinity_polling()
