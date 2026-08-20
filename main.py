import os
import time
import threading
import websocket
from flask import Flask

# 1. Serveur web pour garder Render en vie
app = Flask(__name__)
@app.route('/')
def home(): return "Test de connexion Pocket Option en cours..."
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
threading.Thread(target=run, daemon=True).start()

# 2. NOTRE TEST DE CONNEXION BRUTE (AVEC RECONNEXION AUTO)
# ⚠️ REMPLACE CECI PAR TON NOUVEAU SSID TOUT FRAIS :
SSID = "c8p9d7a50kfnr50oqevscpprdi" 

def on_message(ws, message):
    if message == "2":
        ws.send("3") # Maintien de la connexion
        return
    if message.startswith("42"):
        print(f"📡 DONNÉES REÇUES : {message[:150]}")

def on_error(ws, error):
    print(f"❌ ERREUR : {error}")

def on_close(ws, close_status_code, close_msg):
    print("⚠️ Pocket Option a coupé la connexion. (Le SSID est-il invalide ?)")

def on_open(ws):
    print("🔄 Connexion physique établie...")
    ws.send("40")
    time.sleep(1)
    # Format d'authentification officiel de Pocket Option
    ws.send(f'42["auth",{{"session":"{SSID}","isDemo":1}}]')
    print("✅ Clé SSID envoyée. En attente des prix...")

def demarrer_test():
    print("⬛ Lancement de l'espion Pocket Option...")
    while True:
        ws = websocket.WebSocketApp(
            "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket",
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        ws.run_forever()
        print("🔄 Tentative de reconnexion dans 5 secondes...")
        time.sleep(5)

# Lancement
demarrer_test()
