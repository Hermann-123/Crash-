import os
import datetime
import random
import string
import json
import websocket
import pandas as pd
import ta
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from threading import Thread, Timer

# ==========================================
# CONFIGURATION PRINCIPALE ET SÉCURITÉ
# ==========================================
TELEGRAM_TOKEN = "8000472746:AAGsb319CKyUqYEwyTM5uF_Ykjx4dnHA-ts"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

ADMIN_ID = 5968288964 
CAPITAL_ACTUEL = 40650 
FMP_API_KEY = os.environ.get("FMP_API_KEY", "D0srw6sB3otYTc00UdBE9otPIbhkKV8X")

COEF_MARTINGALE = 2.5
MAX_MARTINGALE = 3  

# ==========================================
# VARIABLES D'ÉTAT
# ==========================================
user_prefs, mode_trading, trades_en_cours, utilisateurs_actifs = {}, {}, {}, set()
derniere_alerte_auto, cooldown_actifs, niveaux_martingale = {}, {}, {}
utilisateurs_autorises = {ADMIN_ID: "LIFETIME"}
cles_generees = {}
stats_journee = {'ITM': 0, 'OTM': 0, 'details': []}

CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "LTCUSD"]
FOREX_PAIRS = [
    "AUDUSD", "CADJPY", "CHFJPY", "EURJPY", "USDCAD", 
    "AUDJPY", "EURAUD", "EURUSD", "AUDCAD", "USDCHF", 
    "CADCHF", "EURCHF", "USDJPY"
]

def nom_otc(symbole):
    return f"{symbole[:3]}/{symbole[3:]} OTC"

# ==========================================
# SERVEUR WEB (KEEP ALIVE RENDER)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Terminal Prime VIP : Édition V18.5 ULTIMATE (Stable & Rapide) - EN LIGNE"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    Thread(target=run, daemon=True).start()

# ==========================================
# SÉCURITÉ ET ACCÈS VIP
# ==========================================
def est_autorise(user_id):
    if user_id == ADMIN_ID: return True
    if user_id in utilisateurs_autorises:
        expiration = utilisateurs_autorises[user_id]
        if expiration == "LIFETIME" or datetime.datetime.now() < expiration: return True
        del utilisateurs_autorises[user_id]
        try: bot.send_message(user_id, "⚠️ **ABONNEMENT EXPIRÉ** ⚠️\n\nVotre accès est terminé.", parse_mode="Markdown")
        except: pass
    return False

@bot.message_handler(commands=['keygen'])
def generer_cle(message):
    if message.chat.id != ADMIN_ID: return
    try:
        arg = message.text.split()[1].lower()
        jours = "LIFETIME" if arg == 'vie' else (7 if arg == '1s' else (14 if arg == '2s' else (30 if arg == '1m' else (90 if arg == '3m' else int(arg)))))
        cle = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cles_generees[cle] = jours
        texte = f"✅ **CLÉ GÉNÉRÉE**\n🔑 `{cle}`\n⏳ Durée : {'À VIE 👑' if jours == 'LIFETIME' else f'{jours} Jours'}"
        bot.send_message(message.chat.id, texte, parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=['vip'])
def activer_vip(message):
    chat_id = message.chat.id
    try:
        cle = message.text.split()[1]
        if cle in cles_generees:
            jours = cles_generees.pop(cle)
            utilisateurs_autorises[chat_id] = "LIFETIME" if jours == "LIFETIME" else datetime.datetime.now() + datetime.timedelta(days=jours)
            bot.send_message(chat_id, "🎉 **ACCÈS DÉVERROUILLÉ !** Tapez /start", parse_mode="Markdown")
        else: 
            bot.send_message(chat_id, "❌ **Clé invalide.**", parse_mode="Markdown")
    except: pass

# ==========================================
# ROUTEUR DE DONNÉES WEBSOCKET
# ==========================================
def est_symbole_autorise(symbole):
    # En mode OTC simulé, on donne un accès total H24 pour trader l'interface
    return "AUTORISE", ""

def prefixer_symbole(symbole): 
    return f"cry{symbole}" if symbole in CRYPTO_PAIRS else f"frx{symbole}"

def obtenir_donnees_deriv(symbole, granularite=300):
    for _ in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            ws.send(json.dumps({"ticks_history": prefixer_symbole(symbole), "end": "latest", "count": 250, "style": "candles", "granularity": granularite}))
            res = json.loads(ws.recv())
            ws.close()
            if "candles" in res: return res['candles']
        except: time.sleep(1)
    return None

def obtenir_prix_actuel_deriv(symbole):
    for _ in range(3):
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=5)
            ws.send(json.dumps({"ticks_history": prefixer_symbole(symbole), "end": "latest", "count": 1, "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            if "history" in res: return float(res["history"]["prices"][0])
        except: time.sleep(1)
    return None

def verifier_correlation(symbole_base, action_visee):
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
                if 40 <= r <= 68: s += 20; res.append(f"RSI sain")
                if u >= 70: s += 15
            else:
                if d_p <= u_p and d > u: s += 20; res.append("Croisement Down")
                if 32 <= r <= 60: s += 20; res.append(f"RSI sain")
                if d >= 70: s += 15
            return min(100, s), res
        sc, rc = score("CALL")
        sp, rp = score("PUT")
        return {"nom": "AROON_RSI", "label": "Show The Direction", "score_call": sc, "score_put": sp, "raisons_call": rc, "raisons_put": rp, "txt": f"Aroon {u:.0f}/{d:.0f} | RSI {r:.1f}"}
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
                if sprev <= 25 and sv > sprev: s += 35; res.append("STC Rebond Bas")
                elif sv < 40: s += 15
                if dip > din: s += 20
                if adx >= 15: s += min(20, (adx - 15) * 1.2)
            else:
                if sprev >= 75 and sv < sprev: s += 35; res.append("STC Rebond Haut")
                elif sv > 60: s += 15
                if din > dip: s += 20
                if adx >= 15: s += min(20, (adx - 15) * 1.2)
            return min(100, s), res
        sc, rc = score("CALL")
        sp, rp = score("PUT")
        return {"nom": "ADX_STC", "label": "Identifies Reversal", "score_call": sc, "score_put": sp, "raisons_call": rc, "raisons_put": rp, "txt": f"STC {sv:.0f} | ADX {adx:.0f}"}
    except: return None

def analyser_cci_macd(df):
    try:
        cci = ta.trend.CCIIndicator(high=df['high'], low=df['low'], close=df['close'], window=10).cci()
        macd = ta.trend.MACD(close=df['close'], window_slow=25, window_fast=10, window_sign=5).macd_diff()
        c, cp, m, mp = float(cci.iloc[-2]), float(cci.iloc[-3]), float(macd.iloc[-2]), float(macd.iloc[-3])
        
        def score(dir):
            s, res = 0, []
            if dir == "CALL":
                if cp <= -100 and c > cp: s += 30; res.append("CCI Survente")
                elif c < -50: s += 12
                if m > 0: s += 20
                if m > mp: s += 15
            else:
                if cp >= 100 and c < cp: s += 30; res.append("CCI Surachat")
                elif c > 50: s += 12
                if m < 0: s += 20
                if m < mp: s += 15
            return min(100, s), res
        sc, rc = score("CALL")
        sp, rp = score("PUT")
        return {"nom": "CCI_MACD", "label": "A Moment When", "score_call": sc, "score_put": sp, "raisons_call": rc, "raisons_put": rp, "txt": f"CCI {c:.0f} | MACD {m:.4f}"}
    except: return None

def analyser_donchian_cci(df):
    try:
        up, low = df['high'].rolling(20).max(), df['low'].rolling(20).min()
        cci = ta.trend.CCIIndicator(high=df['high'], low=df['low'], close=df['close'], window=11).cci()
        px, u, l = float(df['close'].iloc[-2]), float(up.iloc[-2]), float(low.iloc[-2])
        c, cp = float(cci.iloc[-2]), float(cci.iloc[-3])
        pct = (px - l) / (u - l) if (u - l) > 0 else 1e-9
        
        def score(dir):
            s, res = 0, []
            if dir == "CALL":
                prox = max(0, 1 - pct * 2.5)
                s += prox * 35
                if cp <= -100 and c > cp: s += 30
            else:
                prox = max(0, (pct - 0.6) * 2.5)
                s += prox * 35
                if cp >= 100 and c < cp: s += 30
            return min(100, s), res
        sc, rc = score("CALL")
        sp, rp = score("PUT")
        return {"nom": "DONCHIAN", "label": "You Know And", "score_call": sc, "score_put": sp, "raisons_call": rc, "raisons_put": rp, "txt": f"Canal {pct*100:.0f}% | CCI {c:.0f}"}
    except: return None

# ==========================================
# MOTEUR PRINCIPAL (4 PILIERS + KILLSWITCH)
# ==========================================
def analyser_binaire_pro(symbole, mode="STANDARD"):
    timeframes = [600, 300, 120] if mode == "STANDARD" else [60]

    for tf in timeframes:
        candles = obtenir_donnees_deriv(symbole, tf)
        if not candles or len(candles) < 60: continue

        try:
            df = pd.DataFrame([{'open': float(c['open']), 'close': float(c['close']), 'high': float(c['high']), 'low': float(c['low'])} for c in candles])
            df['corps'] = abs(df['close'] - df['open'])
            df['taille'] = df['high'] - df['low']

            if df['corps'].iloc[-4:-1].mean() > 0 and (df['taille'].iloc[-4:-1].mean() > df['corps'].iloc[-4:-1].mean() * 3.5):
                return "⚠️ Filtre Anti-Chaos activé.", None, None, None, None, None, None, None

            last, prev, p_prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            fusee_haussiere = (last['close'] > last['open']) and (prev['close'] > prev['open']) and (p_prev['close'] > p_prev['open']) and (last['corps'] > last['taille'] * 0.25)
            fusee_baissiere = (last['close'] < last['open']) and (prev['close'] < prev['open']) and (p_prev['close'] < p_prev['open']) and (last['corps'] > last['taille'] * 0.25)

            resultats = [f(df) for f in (analyser_aroon_rsi, analyser_adx_stc, analyser_cci_macd, analyser_donchian_cci) if f(df)]
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
            score_algo = round(5 + (gagnant["score"] / 100) * 5, 1)

            if not verifier_correlation(symbole, action): return f"⚠️ **FAKEOUT DÉTECTÉ**", None, None, None, None, None, None, None
            
            duree, exp_texte = (180, "3 MIN (HIT & RUN)") if tf == 300 else (tf, f"{int(tf/60)} MIN") if mode == "STANDARD" else (60, "1 MINUTE (SCALP)")
            return action, conf, exp_texte, duree, 0, 0, bb_status, score_algo

        except: continue
    return f"⚠️ En attente ({mode}).", None, None, None, None, None, None, None

# ==========================================
# EXÉCUTION & COMMANDES
# ==========================================
def executer_tir_flash(chat_id, symbole, action_brute, duree, palier):
    nom_paire = nom_otc(symbole)
    act = "🟢 ACHAT (CALL)" if action_brute == "CALL" else "🔴 VENTE (PUT)"
    if palier == 0:
        bot.send_message(chat_id, f"👻 **FANTÔME LANCÉ ({nom_paire})**", parse_mode="Markdown")
    else:
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ GAGNÉ SUR POCKET", callback_data="force_win"))
        bot.send_message(chat_id, f"🔥 **TIR IMMÉDIAT : PALIER {palier} ({nom_paire})**\n👉 **CLIQUEZ SUR {act} MAINTENANT !**", reply_markup=markup, parse_mode="Markdown")
    
    trades_en_cours[chat_id] = {'symbole': symbole, 'action': action_brute, 'duree': duree, 'prix_entree': obtenir_prix_actuel_deriv(symbole)}
    Timer(duree, verifier_resultat, args=[chat_id]).start()

def verifier_resultat(chat_id):
    trade = trades_en_cours.get(chat_id)
    if not trade: return
    prix_sortie = obtenir_prix_actuel_deriv(trade['symbole'])
    if not prix_sortie: return

    gagne = (trade['action'] == "CALL" and prix_sortie > trade['prix_entree']) or (trade['action'] == "PUT" and prix_sortie < trade['prix_entree'])
    palier = niveaux_martingale.get(chat_id, 0)
    nom = nom_otc(trade['symbole'])

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
    msg = bot.send_message(chat_id, f"⏳ *Analyse des 4 Piliers sur {nom_otc(actif)}...*", parse_mode="Markdown")
    action, conf, exp_texte, duree_sec, r, s, bb, score = analyser_binaire_pro(actif, mode_trading.get(chat_id, "STANDARD"))
    
    if not action or "⚠️" in action:
        try: bot.edit_message_text(action, chat_id, msg.message_id)
        except: pass
        return

    sec_rest = 60 - datetime.datetime.now().second
    if sec_rest < 15: sec_rest += 60
    palier = niveaux_martingale.get(chat_id, 0)
    
    if palier == 0 and score and score >= 10.0: palier = 1; niveaux_martingale[chat_id] = 1

    mise = int((CAPITAL_ACTUEL * 0.02) * (COEF_MARTINGALE ** (palier - 1 if palier > 0 else 0)))
    str_h = (datetime.datetime.now() + datetime.timedelta(seconds=sec_rest)).strftime("%H:%M:00")
    
    signal = f"🚨 **ALERTE VIP** 🚨\n🌐 {nom_otc(actif)}\n⏱ {str_h}\n⏳ {exp_texte}\n👉 {action}\n{bb}\n💵 Mise : {mise}$ (Palier {palier})" if palier > 0 else f"👻 **FANTÔME** 👻\n🌐 {nom_otc(actif)}\n⏱ {str_h}\n⏳ {exp_texte}\n👉 {action}\n*(Le bot prend le trade virtuellement)*"
    bot.delete_message(chat_id, msg.message_id)
    bot.send_message(chat_id, signal, parse_mode="Markdown")
    Timer(sec_rest, executer_tir_flash, args=[chat_id, actif, "CALL" if "ACHAT" in action else "PUT", duree_sec, palier]).start()

@bot.message_handler(func=lambda m: m.text == "🚀 LANCER L'ANALYSE")
def lancer(message):
    if not est_autorise(message.chat.id): return
    actif = user_prefs.get(message.chat.id)
    if not actif: return bot.send_message(message.chat.id, "⚠️ Choisis une devise d'abord !")
    save_devise(type('obj', (object,), {'data': f"set_{actif}", 'message': message, 'from_user': message.from_user, 'id': '1'})())

@bot.message_handler(commands=['start'])
def bienvenue(message):
    user_id = message.chat.id
    if not est_autorise(user_id): return bot.send_message(user_id, "🔒 **ACCÈS RESTREINT**", parse_mode="Markdown")
    utilisateurs_actifs.add(user_id)
    mode_trading[user_id] = "STANDARD"
    markup = ReplyKeyboardMarkup(resize_keyboard=True).row(KeyboardButton("📊 CHOISIR UNE DEVISE"), KeyboardButton("🚀 LANCER L'ANALYSE")).row(KeyboardButton("🛡️ MODE: 4 PILIERS STANDARD"), KeyboardButton("⏰ HEURES DE TRADING"))
    bot.send_message(user_id, "🏴‍☠️ **TERMINAL PRIME V18 ULTIMATE (OTC READY)** 🔥\n\nMoteur : 4 Piliers Indépendants\nMode : Affichage OTC 24/7", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 CHOISIR UNE DEVISE")
def devises(message):
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(*[InlineKeyboardButton(nom_otc(p), callback_data=f"set_{p}") for p in FOREX_PAIRS])
    bot.send_message(message.chat.id, "Sélectionne ta cible (Affichage OTC) :", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text.startswith("🛡️") or m.text.startswith("🔥"))
def toggle_mode(m):
    uid = m.chat.id
    mode_trading[uid] = "SCALP" if mode_trading.get(uid) == "STANDARD" else "STANDARD"
    bienvenue(m)

if __name__ == "__main__":
    keep_alive()
    print("⬛ BOÎTE NOIRE : Édition V18 Stable Démarrée.")
    bot.infinity_polling()
