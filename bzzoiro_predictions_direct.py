"""
Bzzoiro Direct — Filtre les matchs terminés/live + date fiable.
"""
from __future__ import annotations

import os
import re
import unicodedata
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


async def _get(client, path, params=None):
    try:
        r = await client.get(f"{BZZOIRO_BASE}{path}", headers=_headers(), params=params, timeout=20.0)
        if r.status_code >= 400:
            print(f"⚠️ Bzzoiro {path} -> {r.status_code}")
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


def _is_finished_or_live(ev: dict, pred: dict) -> bool:
    """Retourne True si le match est terminé ou en cours (à rejeter)."""
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


def _extract_match_date(ev: dict, pred: dict) -> str:
    """Retourne juste la date (YYYY-MM-DD) du match."""
    for source in [ev, pred]:
        if not isinstance(source, dict):
            continue
        for k in ["commence_time", "kickoff", "start_time", "match_date", "date", "datetime", "start"]:
            v = source.get(k)
            if v:
                s = str(v)
                m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
                if m:
                    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return datetime.now().strftime("%Y-%m-%d")


async def fetch_bzzoiro_predictions_direct(min_confidence=65.0):
    if not BZZOIRO_API_KEY:
        print("⚠️ BZZOIRO_API_KEY manquante")
        return []

    print(f"🔮 Bzzoiro : fetching...")

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            data = await _get(client, "/predictions/", {
                "date_from": datetime.now().strftime("%Y-%m-%d"),
                "date_to": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
                "limit": "500",
            })
            preds = data.get("results") or data.get("data") or []
    except Exception as e:
        print(f"❌ Fetch crash: {e}")
        return []

    print(f"🔮 Bzzoiro : {len(preds)} brutes")
    if not preds:
        return []

    enriched = []
    rejected_status = 0

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

        home = ""
        away = ""
        league = ""

        if isinstance(ev, dict):
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
            league = ev.get("league_name") or ev.get("league") or ""

        if not home or not away:
            continue

        match_date = _extract_match_date(ev, pred)

        enriched.append({
            "event_id": f"bzz_{pred.get('id', idx)}",
            "home": home,
            "away": away,
            "league": league,
            "kickoff": match_date,
            "match_date": match_date,
            "bzz_markets": parsed,
            "bzz_confidence": 70.0,
            "bzz_raw": pred,
            "bzz_score_exact": {},
            "has_odds": False,
            "real_odds": {},
            "odds_sport_key": "",
        })

    if rejected_status > 0:
        print(f"🚫 {rejected_status} rejetés (matchs terminés/live)")

    print(f"🔮 Bzzoiro : {len(enriched)} retenues")

    for p in enriched[:3]:
        print(f"   ✅ [{p['bzz_confidence']:.0f}%] {p['home']} vs {p['away']} • 📅 {p['match_date']}")

    return enriched
