import os
import time
import threading
import json
import websocket
from flask import Flask

# 1. Serveur web pour Render
app = Flask(__name__)
@app.route('/')
def home(): return "Test de flux OTC Pocket Option en cours..."
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
threading.Thread(target=run, daemon=True).start()

# 2. TON SSID ACTUEL
SSID = "c8p9d7a50kfnr50oqevscpprdi" # Remplace si besoin par ton nouveau SSID

def on_message(ws, message):
    if message == "2":
        ws.send("3")
        return
    
    if message.startswith("42"):
        try:
            data = json.loads(message[2:])
            event = data[0]
            payload = data[1]
            
            # On affiche tout ce qui arrive pour voir les prix ou les réponses du serveur
            print(f"📡 ÉVÉNEMENT [{event}] : {str(payload)[:200]}")
        except:
            print(f"📡 BRUT : {message[:150]}")

def on_error(ws, error):
    print(f"❌ ERREUR : {error}")

def on_close(ws, close_status_code, close_msg):
    print("⚠️ Connexion fermée par Pocket Option.")

def on_open(ws):
    print("🔄 Connexion physique établie...")
    ws.send("40")
    time.sleep(1)
    
    # 1. Authentification
    ws.send(f'42["auth",{{"session":"{SSID}","isDemo":1}}]')
    print("✅ SSID envoyé...")
    
    time.sleep(2)
    
    # 2. Demande d'abonnement au flux de prix pour l'EURUSD_otc (Asset ID 1 par exemple, ou abonnement direct)
    # Sur Socket.IO, on envoie un ordre d'inscription au canal de l'actif
    abonnement = '42["subscribe",{"asset":"EURUSD_otc"}]'
    ws.send(abonnement)
    print("🎯 Demande de flux envoyée pour EURUSD OTC...")

def demarrer_test():
    print("⬛ Lancement de l'espion de flux OTC...")
    while True:
        ws = websocket.WebSocketApp(
            "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket",
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        ws.run_forever()
        print("🔄 Reconnexion dans 5 secondes...")
        time.sleep(5)

demarrer_test()
