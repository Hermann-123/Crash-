import os
import re
import json
import math
import time
import unicodedata
import difflib
import requests
from datetime import datetime

# ==============================================================================
# 1. CONFIGURATION & VERROUS DE SÉCURITÉ BANKROLL
# ==============================================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

CONFIG = {
    # Seuils par match individuel (aucun match faible ne passe)
    "MIN_SINGLE_PROB": 0.72,       # 72% min pour un ticket Simple VIP
    "MIN_SINGLE_EV": 1.05,         # +5% de value min pour un Simple
    
    "MIN_COMBO_LEG_PROB": 0.67,    # 67% min par match dans un Duo Haute Confiance
    "MIN_COMBO_LEG_EV": 1.03,      # +3% de value min PAR MATCH (bloque les cotes à EV 0.98)
    
    "MIN_ODDS3_LEG_PROB": 0.60,    # 60% min par match pour le ticket ODDS +3 AUTH
    "MIN_ODDS3_LEG_EV": 1.04,      # +4% de value min par match pour ODDS +3
    "MIN_ODDS3_TOTAL": 3.00,       # Cote totale obligatoirement >= 3.00
    
    # Tolérance Double Pass Gemini
    "MAX_GEMINI_DRIFT": 0.035,     # Écart max 3.5% entre Pass 1 et Pass 2 pour être jugé "stable"
    "MAX_GEMINI_SHIFT": 0.04,      # Gemini peut ajuster la proba de +/- 4% max
}

# ==============================================================================
# 2. NORMALISATION & MATCHING FLOU (RÉPARE LE BUG 1/16 INFSORTS <-> BZZOIRO)
# ==============================================================================
STOP_WORDS = {
    "fc", "cf", "sc", "ac", "as", "us", "ss", "afc", "cfc", "fk", "sk", "bk",
    "ik", "if", "nk", "gnk", "hnk", "pfc", "cska", "rc", "rcd", "cd", "ud",
    "sd", "ca", "club", "de", "futbol", "football", "athletic", "atletico",
    "sporting", "real", "united", "city", "town", "rovers", "wanderers",
    "albion", "hotspur", "saint", "st", "olympique", "calcio", "verein",
    "borussia", "eintracht", "vfl", "vfb", "tsg", "rb", "red", "bull", "1", "04", "05"
}

ALIASES = {
    "brighton hove": "brighton",
    "paris saint germain": "psg",
    "paris sg": "psg",
    "bayer 04 leverkusen": "leverkusen",
    "bayer leverkusen": "leverkusen",
    "dinamo zagreb": "dinamo zagreb",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "sporting braga": "braga",
    "sc braga": "braga",
    "kuopion palloseura": "kups",
    "universitatea craiova": "craiova",
    "ararat armenia": "ararat",
    "salzburg": "salzburg",
}

def normalize_team(name: str) -> str:
    if not name:
        return ""
    # Suppression accents & caractères spéciaux
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    tokens = [t for t in text.split() if t not in STOP_WORDS]
    cleaned = " ".join(tokens).strip()
    return ALIASES.get(cleaned, cleaned if cleaned else text.strip())

def teams_match_score(home_a: str, away_a: str, home_b: str, away_b: str) -> float:
    ha, aa = normalize_team(home_a), normalize_team(away_a)
    hb, ab = normalize_team(home_b), normalize_team(away_b)
    
    score_h = difflib.SequenceMatcher(None, ha, hb).ratio()
    score_a = difflib.SequenceMatcher(None, aa, ab).ratio()
    
    # Bonus si l'un est inclus dans l'autre (ex: "brighton" dans "brighton hove")
    if ha and hb and (ha in hb or hb in ha):
        score_h = max(score_h, 0.92)
    if aa and ab and (aa in ab or ab in aa):
        score_a = max(score_a, 0.92)
        
    return (score_h + score_a) / 2.0

def merge_predictions_with_odds(bzzoiro_matches: list, odds_events: list) -> list:
    """Rattache les vraies cotes InferSports/ESPN aux prédictions Bzzoiro."""
    merged = []
    matched_count = 0
    
    for pred in bzzoiro_matches:
        best_event = None
        best_score = 0.0
        
        for ev in odds_events:
            score = teams_match_score(pred["home"], pred["away"], ev["home"], ev["away"])
            if score > best_score:
                best_score = score
                best_event = ev
                
        if best_event and best_score >= 0.76:
            matched_count += 1
            pred["odds_1x2"] = best_event["odds_1x2"]  # ex: {"1": 1.45, "X": 4.20, "2": 6.50}
            pred["odds_ou25"] = best_event.get("odds_ou25", {})
            pred["league"] = pred.get("league") or best_event.get("league", "Multi-Ligues")
            pred["kickoff"] = pred.get("kickoff") or best_event.get("kickoff", "")
            merged.append(pred)
            print(f"🔗 MATCH ({best_score:.2f}) : {pred['home']} vs {pred['away']} <-> {best_event['home']} vs {best_event['away']} | Cotes: {pred['odds_1x2']}", flush=True)
        else:
            print(f"⚠️ SANS COTE (score {best_score:.2f}) : {pred['home']} vs {pred['away']}", flush=True)
            
    print(f"📊 Taux de récupération cotes : {matched_count}/{len(bzzoiro_matches)} matchs", flush=True)
    return merged

# ==============================================================================
# 3. CALIBRATION MATHÉMATIQUE (DE-VIG + POISSON + BZZOIRO)
# ==============================================================================
def calculate_fair_probs(odds_1x2: dict) -> dict:
    """Supprime la marge du bookmaker (overround) pour obtenir les probas réelles du marché."""
    try:
        o1, ox, o2 = float(odds_1x2["1"]), float(odds_1x2["X"]), float(odds_1x2["2"])
        if min(o1, ox, o2) <= 1.01:
            return {}
        inv_sum = (1.0 / o1) + (1.0 / ox) + (1.0 / o2)
        return {
            "1": (1.0 / o1) / inv_sum,
            "X": (1.0 / ox) / inv_sum,
            "2": (1.0 / o2) / inv_sum,
        }
    except (KeyError, ValueError, TypeError):
        return {}

def calibrate_candidates(merged_matches: list) -> list:
    """Calcule la probabilité calibrée et l'EV réelle pour chaque sélection candidate."""
    candidates = []
    
    for m in merged_matches:
        fair_probs = calculate_fair_probs(m.get("odds_1x2", {}))
        if not fair_probs:
            continue
            
        pick = m["pick"]  # "1", "X", ou "2"
        book_odds = float(m["odds_1x2"][pick])
        p_bzzoiro = float(m["bzzoiro_prob"])     # ex: 0.814
        p_poisson = float(m.get("poisson_prob", p_bzzoiro))
        p_market = fair_probs[pick]
        
        # Fusion pondérée : 45% Bzzoiro + 30% Poisson + 25% Marché dé-margé
        p_calibrated = (0.45 * p_bzzoiro) + (0.30 * p_poisson) + (0.25 * p_market)
        ev = p_calibrated * book_odds
        
        # Pré-filtre : on garde uniquement les matchs avec proba >= 60% et EV >= 1.01 avant audit Gemini
        if p_calibrated >= 0.60 and ev >= 1.01:
            candidates.append({
                "id": f"{m['home']}_vs_{m['away']}",
                "home": m["home"],
                "away": m["away"],
                "league": m.get("league", "Football"),
                "kickoff": m.get("kickoff", ""),
                "pick": pick,
                "pick_label": get_pick_label(pick, m["home"], m["away"]),
                "odds": book_odds,
                "p_bzzoiro": round(p_bzzoiro, 4),
                "p_poisson": round(p_poisson, 4),
                "p_market": round(p_market, 4),
                "prob": round(p_calibrated, 4),
                "ev": round(ev, 3),
                "xg_home": m.get("xg_home"),  # None si non fourni (plus de faux 2.35)
                "xg_away": m.get("xg_away"),
            })
            
    # Tri par score composite (Proba * EV) décroissant
    candidates.sort(key=lambda x: x["prob"] * x["ev"], reverse=True)
    return candidates[:12]  # Top 12 réels envoyés à Gemini

def get_pick_label(pick: str, home: str, away: str) -> str:
    if pick == "1":
        return f"Victoire {home} (1)"
    elif pick == "2":
        return f"Victoire {away} (2)"
    return "Match Nul (X)"

# ==============================================================================
# 4. DOUBLE ANALYSE GEMINI AI (VISIBLE DANS LES LOGS + ANTI-BLABLA)
# ==============================================================================
def call_gemini_single_pass(candidate: dict, pass_num: int) -> dict:
    """Interroge Gemini 2.5 Flash en JSON strict pour auditer la probabilité et rédiger l'analyse."""
    if not GEMINI_API_KEY:
        return {"adjustment": 0.0, "risk_flag": False, "analysis": "Validation statistique multi-modèles."}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    xg_info = ""
    if candidate.get("xg_home") and candidate.get("xg_away"):
        xg_info = f"xG attendus : {candidate['xg_home']} vs {candidate['xg_away']}."

    prompt = f"""
Tu es un analyste quantitatif en paris sportifs. Audite ce pronostic (Passe {pass_num}/2) :
- Match : {candidate['home']} vs {candidate['away']} ({candidate['league']})
- Pari : {candidate['pick_label']} @ {candidate['odds']}
- Probabilités : Bzzoiro={candidate['p_bzzoiro']*100:.1f}%, Poisson={candidate['p_poisson']*100:.1f}%, Marché={candidate['p_market']*100:.1f}%, Calibrée={candidate['prob']*100:.1f}% (EV={candidate['ev']:.2f})
- {xg_info}

Règles strictes :
1. "adjustment" : flottant entre -0.04 et +0.04 représentant ton ajustement sur la probabilité calibrée (ex: -0.02 si risque de rotation/piège Coupe d'Europe, +0.015 si domination nette).
2. "risk_flag" : true uniquement s'il y a un piège évident (match sans enjeu, équipe B probable, derby imprévisible), sinon false.
3. "analysis" : 2 phrases courtes, percutantes et spécifiques à cette affiche. Interdiction d'utiliser des phrases toutes faites comme "offre une value statistique maîtrisée" ou de répéter tous les chiffres déjà affichés.
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25 if pass_num == 1 else 0.35,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "adjustment": {"type": "NUMBER"},
                    "risk_flag": {"type": "BOOLEAN"},
                    "analysis": {"type": "STRING"}
                },
                "required": ["adjustment", "risk_flag", "analysis"]
            }
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw_text)
    except Exception as e:
        print(f"❌ Erreur Gemini Pass {pass_num} ({candidate['id']}) : {e}", flush=True)
        return None

def audit_with_gemini_double_pass(candidates: list) -> list:
    """Exécute la Double Analyse Gemini et affiche le détail exact dans Render."""
    print(f"🎯 Top {len(candidates)} matchs sélectionnés pour Double Analyse Gemini", flush=True)
    verified = []
    stats = {"stable": 0, "unstable": 0, "rejected_trap": 0, "error": 0}

    for c in candidates:
        p1 = call_gemini_single_pass(c, pass_num=1)
        time.sleep(0.4)  # Évite le rate-limit API
        p2 = call_gemini_single_pass(c, pass_num=2)

        if not p1 or not p2:
            stats["error"] += 1
            continue

        adj1 = max(-CONFIG["MAX_GEMINI_SHIFT"], min(CONFIG["MAX_GEMINI_SHIFT"], float(p1["adjustment"])))
        adj2 = max(-CONFIG["MAX_GEMINI_SHIFT"], min(CONFIG["MAX_GEMINI_SHIFT"], float(p2["adjustment"])))
        drift = abs(adj1 - adj2)

        if p1["risk_flag"] or p2["risk_flag"]:
            stats["rejected_trap"] += 1
            print(f"🚫 Gemini PIÈGE détecté [{c['home']} vs {c['away']}] : {p1['analysis']}", flush=True)
            continue

        if drift > CONFIG["MAX_GEMINI_DRIFT"]:
            stats["unstable"] += 1
            print(f"⚠️ Gemini INSTABLE [{c['home']} vs {c['away']}] : drift {drift*100:.1f}%", flush=True)
            continue

        stats["stable"] += 1
        avg_adj = (adj1 + adj2) / 2.0
        old_prob = c["prob"]
        new_prob = round(min(0.92, max(0.05, old_prob + avg_adj)), 4)
        new_ev = round(new_prob * c["odds"], 3)

        c["prob"] = new_prob
        c["ev"] = new_ev
        c["ai_analysis"] = p1["analysis"]
        verified.append(c)

        print(
            f"🤖 Gemini OK [{c['home']} vs {c['away']}] : "
            f"proba {old_prob*100:.1f}% -> {new_prob*100:.1f}% | EV {new_ev:.2f} | {p1['analysis']}",
            flush=True
        )

    print(f"⚡ Double Scan Stats : {stats}", flush=True)
    return verified

# ==============================================================================
# 5. CONSTRUCTION DES COUPONS VIP (ZÉRO COMPROMIS SUR L'EV)
# ==============================================================================
def build_vip_coupons(verified_matches: list) -> list:
    """Construit uniquement des tickets mathématiquement rentables."""
    coupons = []
    used_ids = set()

    # A. SINGLE SNIPER VIP (La pépite la plus sûre du jour)
    singles = [
        m for m in verified_matches
        if m["prob"] >= CONFIG["MIN_SINGLE_PROB"] and m["ev"] >= CONFIG["MIN_SINGLE_EV"]
    ]
    if singles:
        best_single = max(singles, key=lambda x: x["prob"] * x["ev"])
        used_ids.add(best_single["id"])
        coupons.append({
            "badge": "🎯 SINGLE SNIPER VIP",
            "subtitle": "Sélection Élite • Haute Probabilité & Value Réelle",
            "legs": [best_single],
            "total_odds": round(best_single["odds"], 2),
            "total_prob": round(best_single["prob"], 4),
            "total_ev": round(best_single["ev"], 2),
        })

    # B. DUO HAUTE CONFIANCE (Chaque match doit avoir EV >= 1.03 et Proba >= 67%)
    duo_pool = [
        m for m in verified_matches
        if m["id"] not in used_ids
        and m["prob"] >= CONFIG["MIN_COMBO_LEG_PROB"]
        and m["ev"] >= CONFIG["MIN_COMBO_LEG_EV"]
    ]
    if len(duo_pool) >= 2:
        leg1, leg2 = duo_pool[0], duo_pool[1]
        used_ids.update([leg1["id"], leg2["id"]])
        t_odds = round(leg1["odds"] * leg2["odds"], 2)
        t_prob = round(leg1["prob"] * leg2["prob"], 4)
        t_ev = round(t_prob * t_odds, 2)
        coupons.append({
            "badge": "🛡️ DUO HAUTE CONFIANCE",
            "subtitle": "Double Verrou EV+ • 2 Sélections Calibrées",
            "legs": [leg1, leg2],
            "total_odds": t_odds,
            "total_prob": t_prob,
            "total_ev": t_ev,
        })

    # C. ODDS +3 AUTH (Uniquement si la cote combinée atteint VRAIMENT >= 3.00)
    odds3_pool = [
        m for m in verified_matches
        if m["id"] not in used_ids
        and m["prob"] >= CONFIG["MIN_ODDS3_LEG_PROB"]
        and m["ev"] >= CONFIG["MIN_ODDS3_LEG_EV"]
    ]
    if len(odds3_pool) >= 2:
        for i in range(len(odds3_pool)):
            for j in range(i + 1, len(odds3_pool)):
                l1, l2 = odds3_pool[i], odds3_pool[j]
                t_odds = round(l1["odds"] * l2["odds"], 2)
                if t_odds >= CONFIG["MIN_ODDS3_TOTAL"]:
                    t_prob = round(l1["prob"] * l2["prob"], 4)
                    t_ev = round(t_prob * t_odds, 2)
                    coupons.append({
                        "badge": "🚀 ODDS +3 AUTH",
                        "subtitle": "Value Boostée • Cote >= 3.00 Certifiée",
                        "legs": [l1, l2],
                        "total_odds": t_odds,
                        "total_prob": t_prob,
                        "total_ev": t_ev,
                    })
                    break
            if any(c["badge"] == "🚀 ODDS +3 AUTH" for c in coupons):
                break

    return coupons

# ==============================================================================
# 6. FORMATAGE & ENVOI TELEGRAM TRACÉ DANS LES LOGS
# ==============================================================================
def confidence_bar(prob: float) -> str:
    if prob >= 0.75:
        return "🟢🟢🟢"
    elif prob >= 0.67:
        return "🟢🟢⚪"
    return "🟢⚪⚪"

def format_coupon_message(coupon: dict) -> str:
    today_str = datetime.now().strftime("%d/%m/%Y")
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "     ⚡ VOLATILITY INDEX v2 ⚡",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"Analyse IA • VIP | 📅 {today_str}",
        "",
        f"{coupon['badge']}",
        f"{coupon['subtitle']}",
        "",
        "┏━━━━━━━━━━━━━━━┓",
        f"┃ 🎫 Cote totale : {coupon['total_odds']:.2f}",
        f"┃ 📈 Réussite est. : {coupon['total_prob']*100:.1f}%",
        f"┃ 💎 EV Ticket : {coupon['total_ev']:.2f} (+{(coupon['total_ev']-1)*100:.1f}%)",
        "┗━━━━━━━━━━━━━━━┛",
        ""
    ]

    for idx, leg in enumerate(coupon["legs"], 1):
        lines.extend([
            f"┌─ #{idx} {leg['home']} vs {leg['away']}",
            f"│ 🏆 {leg['league']} {('• ' + leg['kickoff']) if leg['kickoff'] else ''}",
            f"│ 🎯 {leg['pick_label']}",
            f"│ 📊 Confiance : {leg['prob']*100:.1f}% {confidence_bar(leg['prob'])}",
            f"│ 💰 Cote : {leg['odds']:.2f} (EV {leg['ev']:.2f})",
            f"│ 🧠 {leg['ai_analysis']}",
            "└─────────────",
            ""
        ])

    lines.append("⚠️ Gestion de bankroll stricte : 1% à 3% max par ticket.")
    return "\n".join(lines)

def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant dans Render !", flush=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True
        }, timeout=15)
        if resp.status_code == 200:
            print("✅ Message Telegram envoyé avec succès !", flush=True)
            return True
        else:
            print(f"❌ Échec envoi Telegram ({resp.status_code}) : {resp.text}", flush=True)
            return False
    except Exception as e:
        print(f"❌ Exception envoi Telegram : {e}", flush=True)
        return False

# ==============================================================================
# 7. POINT D'ENTRÉE PRINCIPAL DU PIPELINE
# ==============================================================================
def run_volatility_pipeline(bzzoiro_raw: list, infersports_raw: list):
    print("🚀 Démarrage VOLATILITY INDEX v2.0...", flush=True)
    
    # 1. Fusion des pronostics et des vraies cotes
    merged = merge_predictions_with_odds(bzzoiro_raw, infersports_raw)
    if not merged:
        print("🛑 Aucun match avec cote valide trouvé.", flush=True)
        return

    # 2. Calibration mathématique (De-vig + Poisson + Bzzoiro)
    candidates = calibrate_candidates(merged)
    print(f"📐 {len(candidates)} candidats retenus après calibration mathématique.", flush=True)

    # 3. Double Analyse Gemini avec logs détaillés
    verified = audit_with_gemini_double_pass(candidates)

    # 4. Assemblage des coupons VIP
    coupons = build_vip_coupons(verified)
    print(f"🎟️ {len(coupons)} coupon(s) VIP généré(s).", flush=True)

    # 5. Envoi Telegram
    if not coupons:
        msg = (
            "🛡️ VOLATILITY INDEX • PROTECTION BANKROLL\n\n"
            "Aucun match ne respecte nos seuils stricts d'espérance de gain (EV >= 1.03) aujourd'hui.\n"
            "Capital préservé à 100%."
        )
        send_telegram_message(msg)
    else:
        for coupon in coupons:
            msg = format_coupon_message(coupon)
            send_telegram_message(msg)
            time.sleep(1)
