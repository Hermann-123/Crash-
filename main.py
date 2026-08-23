"""
╔════════════════════════════════════════════════════════════════════════╗
║  BACKTESTER — Terminal Prime V56                                        ║
║                                                                          ║
║  Rejoue la stratégie (analyser_trend_pullback_confluence + moteur IA    ║
║  déterministe) sur plusieurs semaines de données historiques Deriv,     ║
║  pour mesurer le VRAI winrate et R-multiple avant de régler les seuils  ║
║  à l'aveugle.                                                           ║
║                                                                          ║
║  ⚠️ Le second avis Groq n'est PAS inclus dans ce backtest (il coûterait ║
║  trop d'appels réseau sur des milliers de bougies). Les résultats ici   ║
║  reflètent donc le calcul déterministe seul — un peu plus optimistes    ║
║  que ce que donnerait le bot en direct avec Groq actif.                 ║
║                                                                          ║
║  USAGE (en local ou dans un One-Off Job Render) :                      ║
║      python backtest_strategie.py XAUUSD 60                            ║
║      (symbole, nombre de jours d'historique à tester)                  ║
║                                                                          ║
║  Ce script IMPORTE le fichier principal du bot comme un module — donc   ║
║  aucune duplication de la logique de stratégie. Placer ce fichier dans ║
║  le même dossier que terminal_prime_v55_deriv.py sur GitHub.           ║
╚════════════════════════════════════════════════════════════════════════╝
"""

import sys
import time
import json
import datetime
import websocket
import pandas as pd

# Import du bot principal comme module — ne démarre ni Flask ni Telegram
# (ces éléments ne s'activent que sous `if __name__ == "__main__":`).
import terminal_prime_v55_deriv as bot_core


# ==========================================
# RÉCUPÉRATION D'HISTORIQUE PAGINÉ (au-delà des 250 dernières bougies)
# ==========================================

def obtenir_historique_paginee(symbole_bot, granularite, nb_bougies_cible):
    """
    Récupère nb_bougies_cible bougies en remontant dans le temps par appels
    successifs (paramètre "end" de l'API Deriv ticks_history), jusqu'à
    5000 bougies par appel — bien au-delà de la fenêtre "temps réel" (250)
    utilisée par le bot en direct.
    """
    sym = bot_core.prefixer_symbole(symbole_bot)
    toutes_bougies = []
    fin = "latest"

    while len(toutes_bougies) < nb_bougies_cible:
        ws = None
        try:
            ws = websocket.create_connection(
                "wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=8)
            ws.send(json.dumps({
                "ticks_history": sym, "end": fin, "count": 5000,
                "style": "candles", "granularity": granularite
            }))
            res = json.loads(ws.recv())
            ws.close()
        except Exception as e:
            print(f"[Backtest] Erreur réseau récupération historique: {e}", flush=True)
            break

        if "error" in res or "candles" not in res:
            print(f"[Backtest] Réponse invalide: {res.get('error', res)}", flush=True)
            break

        lot = res["candles"]
        if not lot:
            break

        toutes_bougies = lot + toutes_bougies
        fin = lot[0]["epoch"] - 1  # prochaine requête : juste avant la plus ancienne bougie reçue

        if len(lot) < 2:
            break  # plus rien à récupérer plus loin dans le passé

        time.sleep(0.3)  # éviter de spammer l'API publique

    return toutes_bougies[-nb_bougies_cible:] if len(toutes_bougies) > nb_bougies_cible else toutes_bougies


# ==========================================
# SIMULATION D'UN TRADE (marche-avant, sans lookahead)
# ==========================================

def simuler_issue_trade(bougies_futures, direction, sl, tp, max_bougies=200):
    """
    Parcourt les bougies APRÈS le signal (jamais avant, pour éviter tout
    biais de lookahead) et détermine si le SL ou le TP est touché en
    premier. Retourne ("WIN"|"LOSS"|"AUCUN", nb_bougies_ecoulees).
    """
    for i, b in enumerate(bougies_futures[:max_bougies]):
        haut, bas = float(b["high"]), float(b["low"])
        if direction == "BULL":
            if bas <= sl:
                return "LOSS", i
            if haut >= tp:
                return "WIN", i
        else:
            if haut >= sl:
                return "LOSS", i
            if bas <= tp:
                return "WIN", i
    return "AUCUN", max_bougies  # ni SL ni TP touché dans la fenêtre observée


# ==========================================
# BOUCLE DE BACKTEST
# ==========================================

def backtester(symbole, nb_jours=20, seuil_ia_teste=None):
    """
    ✅ V58: rejoue la stratégie de SCALPING M15 (biais M30) en avançant
    bougie par bougie sur l'historique M15. Plus rapide que la version
    M1→M30 précédente (beaucoup moins de données à récupérer/traiter),
    et cohérent avec le fait que l'entrée elle-même est maintenant M15.
    """
    print(f"\n{'='*70}\nBACKTEST SCALPING M15 {symbole} — {nb_jours} jours d'historique\n{'='*70}", flush=True)

    print("Récupération de l'historique M15...", flush=True)
    m15 = obtenir_historique_paginee(symbole, 900, min(nb_jours * 96 + 300, 20000))
    print(f"  → {len(m15)} bougies M15 récupérées", flush=True)

    print("Récupération de l'historique M30...", flush=True)
    m30 = obtenir_historique_paginee(symbole, 1800, min(nb_jours * 48 + 300, 20000))
    print(f"  → {len(m30)} bougies M30 récupérées", flush=True)

    print("Récupération de l'historique M5 (requis par le moteur IA)...", flush=True)
    m5 = obtenir_historique_paginee(symbole, 300, min(nb_jours * 288 + 300, 20000))
    print(f"  → {len(m5)} bougies M5 récupérées", flush=True)

    print("Récupération de l'historique H1 (filtre macro du moteur IA)...", flush=True)
    h1 = obtenir_historique_paginee(symbole, 3600, min(nb_jours * 24 + 300, 20000))
    print(f"  → {len(h1)} bougies H1 récupérées", flush=True)

    if any(len(x) < 100 for x in (m15, m30, m5, h1)):
        print("❌ Pas assez de données récupérées pour un backtest fiable.", flush=True)
        return

    if seuil_ia_teste is not None:
        bot_core.IA_CONFIG["seuil_acceptation"] = seuil_ia_teste

    resultats = []
    fenetre_min = 40  # minimum de bougies connues requis sur chaque timeframe avant de tester

    # On avance bougie par bougie sur le M15 (l'entrée se décide à cette
    # résolution maintenant) — pour chaque pas, fenêtres M30/M5/H1 "connues".
    for i in range(fenetre_min, len(m15) - 1):
        epoch_actuel = m15[i]["epoch"]

        m30_connu = [c for c in m30 if c["epoch"] <= epoch_actuel]
        m5_connu  = [c for c in m5  if c["epoch"] <= epoch_actuel]
        h1_connu  = [c for c in h1  if c["epoch"] <= epoch_actuel]
        m15_connu = m15[:i+1]

        if any(len(x) < fenetre_min for x in (m30_connu, m5_connu, h1_connu)):
            continue

        # ✅ FIX: le moteur IA (moteur_ia_valider_signal) a aussi besoin de
        # données M5 (granularité 300) pour son propre calcul — sans ça il
        # retourne systématiquement "Données insuffisantes" → score 0% →
        # rejet automatique de TOUS les signaux, peu importe le seuil.
        # gran=14400 (H4) retourne None exprès → déclenche le repli normal
        # du bot (agrégation H1×4) déjà codé dans obtenir_donnees_h4().
        original_fn = bot_core.obtenir_donnees_deriv
        def _fake_obtenir_donnees(sym, gran, _m15=m15_connu, _m30=m30_connu, _m5=m5_connu, _h1=h1_connu):
            if gran == 900:  return _m15[-250:]
            if gran == 1800: return _m30[-250:]
            if gran == 300:  return _m5[-250:]
            if gran == 3600: return _h1[-250:]
            return None
        bot_core.obtenir_donnees_deriv = _fake_obtenir_donnees

        signal, verdict = None, None
        try:
            signal = bot_core.analyser_order_block_engine(symbole)
            if signal:
                verdict = bot_core.moteur_ia_valider_signal(symbole, signal, "ORDER_BLOCK")
        finally:
            bot_core.obtenir_donnees_deriv = original_fn  # toujours restaurer

        # ✅ Diagnostic : affiche le score IA de CHAQUE signal, accepté ou non
        # (sans ça, un blocage silencieux au niveau IA est invisible dans les logs)
        if signal and verdict:
            statut = "✅ ACCEPTÉ" if verdict["accepte"] else "❌ rejeté"
            print(f"    → score IA={verdict['score']}% (seuil={bot_core.IA_CONFIG['seuil_acceptation']}%) {statut}", flush=True)

        if not signal or not verdict or not verdict["accepte"]:
            continue

        direction = signal["tendance"]
        sl, tp = signal["sl"], signal["tp"]
        futures_m15 = m15[i+1:]
        issue, duree = simuler_issue_trade(futures_m15, direction, sl, tp, max_bougies=200)

        resultats.append({
            "epoch": epoch_actuel, "direction": direction,
            "score_ia": verdict["score"], "rr": signal["rr"],
            "issue": issue, "duree_minutes": duree * 15,
        })
        print(f"  [{datetime.datetime.utcfromtimestamp(epoch_actuel)}] "
              f"{direction} score={verdict['score']}% rr={signal['rr']} → {issue} "
              f"({duree * 15} min)", flush=True)

    # ── Résumé ──
    exploitables = [r for r in resultats if r["issue"] in ("WIN", "LOSS")]
    if not exploitables:
        print("\n❌ Aucun trade complet simulé sur cette période.", flush=True)
        return

    wins = [r for r in exploitables if r["issue"] == "WIN"]
    winrate = len(wins) / len(exploitables) * 100
    rr_moyen = sum(r["rr"] for r in exploitables) / len(exploitables)
    duree_moyenne = sum(r["duree_minutes"] for r in exploitables) / len(exploitables)

    # Espérance simple : winrate% * RR - (1-winrate%) * 1 (risque = 1 unité par trade)
    esperance = (winrate/100 * rr_moyen) - ((1 - winrate/100) * 1)

    print(f"\n{'='*70}")
    print(f"RÉSUMÉ ORDER BLOCK — {symbole} sur {nb_jours} jours "
          f"(seuil IA={bot_core.IA_CONFIG['seuil_acceptation']}%)")
    print(f"{'='*70}")
    print(f"Trades simulés (complets)  : {len(exploitables)}")
    print(f"Trades en cours (ignorés)  : {len(resultats) - len(exploitables)}")
    print(f"Winrate                    : {winrate:.1f}%")
    print(f"R/R moyen réalisé          : {rr_moyen:.2f}")
    print(f"Durée moyenne d'un trade   : {duree_moyenne:.0f} minutes")
    print(f"Espérance par trade (en R) : {esperance:+.2f}")
    print(f"{'='*70}\n")

    return {"symbole": symbole, "nb_trades": len(exploitables),
            "winrate": winrate, "rr_moyen": rr_moyen, "esperance": esperance}


if __name__ == "__main__":
    # ✅ Marqueur de version — vérifie dans les logs Render que c'est bien
    # CETTE version qui tourne (utile si Auto-Deploy est sur "Off" et qu'un
    # ancien build tourne encore sans qu'on s'en rende compte).
    print(f"\n{'#'*70}", flush=True)
    print(f"# BACKTEST_STRATEGIE.PY — ORDER BLOCK ENGINE — multi-symboles", flush=True)
    print(f"# Lancé le : {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC", flush=True)
    print(f"# Arguments reçus : {sys.argv[1:]}", flush=True)
    print(f"{'#'*70}\n", flush=True)

    symboles_arg = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
    nb_jours = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    seuil = int(sys.argv[3]) if len(sys.argv) > 3 else None

    symboles = [s.strip().upper() for s in symboles_arg.split(",") if s.strip()]
    print(f"Symboles à tester ({len(symboles)}) : {symboles}", flush=True)
    tous_resultats = []

    for sym in symboles:
        res = backtester(sym, nb_jours, seuil)
        if res:
            tous_resultats.append(res)
        time.sleep(1)  # petite pause entre deux symboles, courtoisie API

    if len(tous_resultats) > 1:
        print(f"\n{'='*70}")
        print(f"TABLEAU COMPARATIF — {nb_jours} jours (seuil IA={bot_core.IA_CONFIG['seuil_acceptation']}%)")
        print(f"{'='*70}")
        print(f"{'Symbole':<10} {'Trades':<8} {'Winrate':<10} {'R/R moy':<10} {'Espérance':<10}")
        print(f"{'-'*70}")
        for r in sorted(tous_resultats, key=lambda x: x['esperance'], reverse=True):
            print(f"{r['symbole']:<10} {r['nb_trades']:<8} {r['winrate']:.1f}%{'':<5} "
                  f"{r['rr_moyen']:.2f}{'':<6} {r['esperance']:+.2f}")
        print(f"{'='*70}\n")
        print("⚠️ Reteste la meilleure combinaison SEULE sur une période différente")
        print("   avant d'y faire confiance.\n")
