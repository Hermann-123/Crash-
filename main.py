import os
import time
import threading
import websocket
from flask import Flask

# 1. On garde le serveur web juste pour que Render soit content
app = Flask(__name__)
@app.route('/')
def home(): return "Test de connexion Pocket Option en cours..."
def run(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
threading.Thread(target=run, daemon=True).start()

# 2. NOTRE TEST DE CONNEXION BRUTE
SSID = "c8p9d7a50kfnr50oqevscpprdi" # Ton dernier SSID

def on_message(ws, message):
    # C'est ici qu'on espionne ce que Pocket Option nous répond
    if message == "2":
        ws.send("3") # On dit à Pocket Option "Je suis toujours là"
        return
    if message.startswith("42"):
        print(f"📡 DONNÉES REÇUES : {message[:150]}") # On affiche les données dans les logs

def on_open(ws):
    print("🔄 Connexion physique établie...")
    ws.send("40")
    time.sleep(1)
    # On donne ta clé secrète à Pocket Option
    ws.send(f'42["auth",{{"ssid":"{SSID}"}}]')
    print("✅ Clé SSID envoyée. En attente des prix...")

def demarrer_test():
    print("⬛ Lancement de l'espion Pocket Option...")
    ws = websocket.WebSocketApp(
        "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket",
        on_message=on_message,
        on_open=on_open
    )
    ws.run_forever()

# On lance le test
demarrer_test()
