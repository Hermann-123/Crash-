# ml_engine.py
import os
import numpy as np
import pandas as pd
import requests
import io

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("❌ XGBoost non installé.")

ML_MODEL = None

def train_model_if_needed():
    global ML_MODEL
    if not XGB_AVAILABLE:
        return
    
    print("🧠 [ML] Téléchargement des données historiques et entraînement...")
    leagues = ["E0", "SP1", "I1", "D1", "F1"]
    seasons = ["2122", "2223", "2324"]
    all_data = []
    
    for league in leagues:
        for season in seasons:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    df = pd.read_csv(io.StringIO(r.text))
                    df = df[['FTHG', 'FTAG', 'FTR', 'B365H', 'B365D', 'B365A']]
                    all_data.append(df)
            except Exception:
                pass
    
    if not all_data:
        print("⚠️ [ML] Impossible de télécharger les données. Le bot utilisera 100% Dixon.")
        return

    data = pd.concat(all_data, ignore_index=True).dropna()
    print(f"✅ [ML] {len(data)} matchs historiques téléchargés.")

    # Création des features (on utilise les cotes réelles pour apprendre au ML à corriger les erreurs)
    X = []
    y = []
    for idx, row in data.iterrows():
        try:
            h = 1 / float(row['B365H'])
            d = 1 / float(row['B365D'])
            a = 1 / float(row['B365A'])
            total = h + d + a
            # On simule les entrées que le bot aura en live (probas + lambdas factices)
            # En production, on pourrait affiner avec plus de features
            X.append([h/total, d/total, a/total, 1.4, 1.2, 0.5])
            y.append({'H': 2, 'D': 1, 'A': 0}[row['FTR']])
        except:
            continue

    if len(X) < 100:
        print("⚠️ [ML] Pas assez de données valides. Le bot utilisera 100% Dixon.")
        return

    print("🧠 [ML] Entraînement du modèle XGBoost en cours...")
    model = xgb.XGBClassifier(n_estimators=80, max_depth=3, learning_rate=0.1, objective='multi:softprob')
    model.fit(np.array(X), np.array(y))
    
    ML_MODEL = model
    print("✅ [ML] Modèle entraîné et prêt en mémoire ! Le bot est maintenant hybride.")

def predict_ml(dixon_pred, bzz_confidence):
    global ML_MODEL
    if ML_MODEL is None or not dixon_pred:
        return None

    try:
        p_home = dixon_pred.get("p_home", 0.0)
        p_draw = dixon_pred.get("p_draw", 0.0)
        p_away = dixon_pred.get("p_away", 0.0)
        lambda_h = dixon_pred.get("lambda_home", 1.0)
        lambda_a = dixon_pred.get("lambda_away", 1.0)
        bzz = bzz_confidence / 100.0 if bzz_confidence else 0.5

        features = np.array([[p_home, p_draw, p_away, lambda_h, lambda_a, bzz]])
        
        # Prédiction [Away, Draw, Home]
        probas = ML_MODEL.predict_proba(features)[0]
        return {
            "home": float(probas[2]),
            "draw": float(probas[1]),
            "away": float(probas[0])
        }
    except Exception:
        return None

def fuse_probas(dixon_pred, ml_probas):
    if not dixon_pred or ml_probas is None:
        return dixon_pred

    # Fusion 40% Dixon / 60% ML
    return {
        "p_home": (dixon_pred.get("p_home", 0.0) * 0.40) + (ml_probas["home"] * 0.60),
        "p_draw": (dixon_pred.get("p_draw", 0.0) * 0.40) + (ml_probas["draw"] * 0.60),
        "p_away": (dixon_pred.get("p_away", 0.0) * 0.40) + (ml_probas["away"] * 0.60),
        # On garde le reste de Dixon intact pour les autres moteurs
        "lambda_home": dixon_pred.get("lambda_home", 1.0),
        "lambda_away": dixon_pred.get("lambda_away", 1.0),
        "over_2.5": dixon_pred.get("over_2.5", 0.5),
        "under_2.5": dixon_pred.get("under_2.5", 0.5),
        "btts_yes": dixon_pred.get("btts_yes", 0.5),
        "btts_no": dixon_pred.get("btts_no", 0.5),
    }
