"""
Bzzoiro Direct — Filtre matchs terminés, extrait date réelle, vraie confiance et cotes bookmakers.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import httpx

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
BZZOIRO_BASE = "https://sports.bzzoiro.com/api/v2"


def _headers():
    return {"Authorization": f"Token {BZZOIRO_API_KEY}"}


def _safe_float(v):
    if v is None:
        return 0.0
    try:
        f = float(v)
        if 0 <= f <= 1:
            return f * 100
        if 0 < f <= 100:
            return f
        return 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_odd(v):
    if v is None:
        return None
    try:
        f = float(v)
        return round(f, 2) if 1.02 <= f <= 50.0 else None
    except (ValueError, TypeError):
        return None


async def _get(client, path, params=None):
    try:
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=20.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro {path} -> HTTP {r.status_code}")
            return {}
        return r.json()
    except Exception as e:
        print(f"⚠️ Bzzoiro {path} crash: {e}")
        return {}


def _parse_markets(mk):
    if not isinstance(mk, dict):
        return {}
    result = {}
    mr = mk.get("match_result")
    if isinstance(mr, dict):
        h = _safe_float(mr.get("prob_home"))
        d = _safe_float(mr.get("prob_draw"))
        a = _safe_float(mr.get("prob_away"))
        if h > 0:
            result["victoire_home"] = round(h, 1)
        if d > 0:
            result["match_nul"] = round(d, 1)
        if a > 0:
            result["victoire_away"] = round(a, 1)
    ou = mk.get("over_under")
    if isinstance(ou, dict):
        o25 = _safe_float(ou.get("prob_over_25"))
        if o25 > 0:
            result["over_2.5"] = round(o25, 1)
            result["under_2.5"] = round(100 - o25, 1)
        o35 = _safe_float(ou.get("prob_over_35"))
        if o35 > 0:
            result["over_3.5"] = round(o35, 1)
    btts = mk.get("btts")
    if isinstance(btts, dict):
        y = _safe_float(btts.get("prob_yes"))
        if y > 0:
            result["btts_yes"] = round(y, 1)
            result["btts_no"] = round(100 - y, 1)
    if "victoire_home" in result and "match_nul" in result:
        result["double_chance_1X"] = round(result["victoire_home"] + result["match_nul"], 1)
    if "victoire_home" in result and "victoire_away" in result:
        result["double_chance_12"] = round(result["victoire_home"] + result["victoire_away"], 1)
    if "match_nul" in result and "victoire_away" in result:
        result["double_chance_X2"] = round(result["match_nul"] + result["victoire_away"], 1)
    if "btts_yes" in result and "over_2.5" in result:
        result["btts_over_25"] = round(min(result["btts_yes"], result["over_2.5"]) * 0.85, 1)
    return result


def _extract_odds_from_obj(obj) -> dict:
    """Extrait les cotes bookmakers depuis un dict event/prediction Bzzoiro."""
    odds = {}
    if not isinstance(obj, dict):
        return odds

    # 1. Champs directs sur l'objet
    direct_map = {
        "home": ["odds_home", "home_odds", "odd_1", "odd_home", "price_home"],
        "draw": ["odds_draw", "draw_odds", "odd_x", "odd_draw", "price_draw"],
        "away": ["odds_away", "away_odds", "odd_2", "odd_away", "price_away"],
        "over_2.5": ["odds_over_25", "over_25_odds", "odd_over_25", "odds_over_2_5"],
        "under_2.5": ["odds_under_25", "under_25_odds", "odd_under_25", "odds_under_2_5"],
        "btts_yes": ["odds_btts_yes", "btts_yes_odds", "odd_btts_yes", "odds_gg"],
        "btts_no": ["odds_btts_no", "btts_no_odds", "odd_btts_no", "odds_ng"],
    }
    for std_key, candidates in direct_map.items():
        for ck in candidates:
            val = _safe_odd(obj.get(ck))
            if val:
                odds[std_key] = val
                break

    # 2. Sous-objet "odds" ou "bookmaker_odds"
    sub = obj.get("odds") or obj.get("bookmaker_odds") or obj.get("market_odds")
    if isinstance(sub, dict):
        for std_key, candidates in {
            "home": ["home", "1", "home_win", "odds_home"],
            "draw": ["draw", "X", "x", "odds_draw"],
            "away": ["away", "2", "away_win", "odds_away"],
            "over_2.5": ["over_25", "over_2.5", "o25", "over"],
            "under_2.5": ["under_25", "under_2.5", "u25", "under"],
            "btts_yes": ["btts_yes", "yes", "gg"],
            "btts_no": ["btts_no", "no", "ng"],
        }.items():
            if std_key in odds:
                continue
            for ck in candidates:
                val = _safe_odd(sub.get(ck))
                if val:
                    odds[std_key] = val
                    break

    # 3. Liste de bookmakers éventuelle
    bks = obj.get("bookmakers")
    if isinstance(bks, list):
        for bk in bks:
            if not isinstance(bk, dict):
                continue
            extracted = _extract_odds_from_obj(bk)
            for k, v in extracted.items():
                if k not in odds:
                    odds[k] = v
    return odds


def _is_finished_or_live(ev, pred):
    if isinstance(ev, dict):
        for k in ["status", "state", "match_status", "live_status"]:
            v = str(ev.get(k, "")).lower().strip()
            if v in ("finished", "ft", "ended", "final", "completed", "live", "in_progress", "inprogress", "ht", "1h", "2h", "playing"):
                return True
        for k in ["finished", "is_finished", "is_live", "live", "ended"]:
            if ev.get(k) is True:
                return True
        if ev.get("home_score") is not None and ev.get("away_score") is not None:
            return True
        if ev.get("score_home") is not None and ev.get("score_away") is not None:
            return True
    if isinstance(pred, dict):
        for k in ["status", "state", "match_status"]:
            v = str(pred.get(k, "")).lower().strip()
            if v in ("finished", "ft", "ended", "final", "completed", "live", "in_progress"):
                return True
        for k in ["finished", "is_finished", "is_live", "live"]:
            if pred.get(k) is True:
                return True
    return False


def _extract_kickoff(ev, pred):
    """Retourne (kickoff_iso_si_dispo, date_only_yyyy_mm_dd)."""
    for source in [ev, pred]:
        if not isinstance(source, dict):
            continue
        for k in ["commence_time", "kickoff", "start_time", "datetime", "match_date", "date", "start", "event_date"]:
            v = source.get(k)
            if not v:
                continue
            s = str(v)
            if re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s):
                m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
                return s, m.group(1) if m else ""
            m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
            if m:
                return "", m.group(1)
    return "", datetime.now().strftime("%Y-%m-%d")


def _compute_real_confidence(pred: dict, parsed: dict) -> float:
    for k in ("confidence", "model_confidence", "max_prob", "score"):
        v = _safe_float(pred.get(k))
        if v > 0:
            return round(v, 1)
    core_keys = ["victoire_home", "victoire_away", "over_2.5", "under_2.5", "btts_yes", "btts_no"]
    vals = [parsed[k] for k in core_keys if k in parsed]
    if vals:
        return round(max(vals), 1)
    return round(max(parsed.values()), 1) if parsed else 0.0


async def fetch_bzzoiro_predictions_direct(min_confidence=65.0):
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante")
        return []
    print("🔮 Bzzoiro : fetching predictions + odds...")
    date_from = datetime.now().strftime("%Y-%m-%d")
    date_to = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            data = await _get(client, "/predictions/", {
                "date_from": date_from,
                "date_to": date_to,
                "limit": "500",
            })
            preds = data.get("results") or data.get("data") or []

            # Récupération complémentaire via /events/ si les cotes ne sont pas imbriquées
            events_odds_by_id = {}
            ev_data = await _get(client, "/events/", {
                "date_from": date_from,
                "date_to": date_to,
                "limit": "500",
            })
            for ev_item in (ev_data.get("results") or ev_data.get("data") or []):
                if isinstance(ev_item, dict) and ev_item.get("id") is not None:
                    extracted = _extract_odds_from_obj(ev_item)
                    if extracted:
                        events_odds_by_id[str(ev_item["id"])] = extracted
    except Exception as e:
        print(f"❌ Fetch Bzzoiro crash: {e}")
        return []

    print(f"🔮 Bzzoiro : {len(preds)} brutes | {len(events_odds_by_id)} matchs avec cotes via /events/")
    if not preds:
        return []

    enriched = []
    rejected_status = 0
    rejected_conf = 0
    odds_found_count = 0

    for idx, pred in enumerate(preds):
        if not isinstance(pred, dict):
            continue
        ev = pred.get("event") or {}
        if _is_finished_or_live(ev, pred):
            rejected_status += 1
            continue
        mk = pred.get("markets")
        parsed = _parse_markets(mk)
        if not parsed:
            continue

        conf = _compute_real_confidence(pred, parsed)
        if conf < min_confidence:
            rejected_conf += 1
            continue

        home = ""
        away = ""
        league = ""
        ev_id_raw = None
        if isinstance(ev, dict):
            ev_id_raw = ev.get("id")
            for k in ["home_team", "home", "team_a", "homeTeam"]:
                v = ev.get(k)
                if v:
                    home = v if isinstance(v, str) else (v.get("name", "") if isinstance(v, dict) else str(v))
                    break
            for k in ["away_team", "away", "team_b", "awayTeam"]:
                v = ev.get(k)
                if v:
                    away = v if isinstance(v, str) else (v.get("name", "") if isinstance(v, dict) else str(v))
                    break
            lg = ev.get("league_name") or ev.get("league") or ""
            league = lg.get("name", "") if isinstance(lg, dict) else str(lg)
        if not home or not away:
            continue

        # Extraction cotes réelles depuis pred, ev, ou events_odds_by_id
        real_odds = {}
        real_odds.update(_extract_odds_from_obj(pred))
        real_odds.update(_extract_odds_from_obj(ev))
        if ev_id_raw is not None and str(ev_id_raw) in events_odds_by_id:
            for k, v in events_odds_by_id[str(ev_id_raw)].items():
                if k not in real_odds:
                    real_odds[k] = v

        has_odds = bool(real_odds)
        if has_odds:
            odds_found_count += 1

        kickoff_full, match_date = _extract_kickoff(ev, pred)
        enriched.append({
            "event_id": f"bzz_{pred.get('id', idx)}",
            "home": home,
            "away": away,
            "league": league,
            "kickoff": kickoff_full if kickoff_full else match_date,
            "match_date": match_date,
            "bzz_markets": parsed,
            "bzz_confidence": conf,
            "bzz_raw": pred,
            "bzz_score_exact": {},
            "has_odds": has_odds,
            "real_odds": real_odds,
            "odds_sport_key": "",
        })

    enriched.sort(key=lambda x: (1 if x["has_odds"] else 0, x["bzz_confidence"]), reverse=True)
    if rejected_status > 0 or rejected_conf > 0:
        print(f"🚫 Rejetés Bzzoiro : {rejected_status} terminés/live | {rejected_conf} confiance < {min_confidence}%")
    print(f"🔮 Bzzoiro : {len(enriched)} retenues (dont {odds_found_count} avec vraies cotes)")
    for p in enriched[:3]:
        print(f"   ✅ [{p['bzz_confidence']:.1f}%] {p['home']} vs {p['away']} • odds={p['real_odds']}")
    return enriched
