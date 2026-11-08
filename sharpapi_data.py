"""
SharpAPI Data — Cotes réelles de bookmakers (DraftKings + FanDuel sur free tier)
Docs : https://sharpapi.io
Free tier : 12 req/min, délai 60s, DraftKings + FanDuel uniquement

API publique :
- fetch_all_odds(matches=None) :
    * matches=None  -> retourne list[dict] de TOUS les matchs (avec cotes)
    * matches=list  -> retourne {event_id: odds_dict} matché par noms
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
import httpx
from typing import Optional
from collections import defaultdict

SHARPAPI_BASE_URL = "https://api.sharpapi.io/api/v1"
SHARPAPI_KEY = os.getenv("SHARPAPI_KEY", "")

CACHE: dict = {}
_DEBUG_RAW_DONE = False
_DEBUG_SPORTS_DONE = False


def _headers() -> dict:
    return {
        "X-API-Key": SHARPAPI_KEY,
        "Accept": "application/json",
    }


def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city", " club"]:
        s = s.replace(sfx, "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 1.01 else None
    except (ValueError, TypeError):
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    try:
        r = await client.get(
            f"{SHARPAPI_BASE_URL}{path}",
            headers=_headers(),
            params=params or {},
            timeout=25.0,
        )
        if r.status_code == 429:
            print(f"⚠️ SharpAPI RATE LIMIT (429) sur {path}")
            return {}
        if r.status_code >= 400:
            print(f"⚠️ SharpAPI {path} ({params}) -> {r.status_code} | {r.text[:200]}")
            return {}
        return r.json() if r.text else {}
    except Exception as e:
        print(f"⚠️ SharpAPI {path} crash: {e}")
        return {}


async def _debug_list_sports(client: httpx.AsyncClient):
    """Debug : liste les sports disponibles sur l'API."""
    global _DEBUG_SPORTS_DONE
    if _DEBUG_SPORTS_DONE:
        return
    data = await _get(client, "/sports")
    if data:
        print(f"🔎 SharpAPI sports disponibles : {data}")
    _DEBUG_SPORTS_DONE = True


def _parse_market_kind(market_field: str, selection_field: str) -> str:
    """Normalise le type de marché : '1x2', 'totals', 'btts' ou ''."""
    m = (market_field or "").lower().strip()
    s = (selection_field or "").lower().strip()
    combined = f"{m} {s}"

    if any(k in combined for k in ["moneyline", "match_winner", "1x2", "match winner", "winner", "h2h"]):
        return "1x2"
    if any(k in combined for k in ["total", "over_under", "over/under", "ou "]) or "2.5" in combined:
        return "totals"
    if any(k in combined for k in ["both_teams", "btts", "both teams"]):
        return "btts"
    return ""


def _extract_row_fields(row: dict) -> dict:
    """Extrait les champs utiles d'une ligne brute (avec fallback sur plusieurs noms)."""
    home_team = row.get("home_team") or row.get("home") or ""
    away_team = row.get("away_team") or row.get("away") or ""
    commence_time = row.get("commence_time") or row.get("start_time") or row.get("commence") or ""
    league = row.get("league") or row.get("sport_title") or row.get("competition") or "Soccer"

    odd = (
        row.get("odds_decimal")
        or row.get("decimal_odds")
        or row.get("odds")
        or row.get("price")
        or row.get("decimal_price")
    )
    odd = _safe_float(odd)

    market = (
        row.get("market_type")
        or row.get("market")
        or row.get("bet_type")
        or row.get("type")
        or ""
    )
    selection = (
        row.get("selection")
        or row.get("outcome")
        or row.get("name")
        or row.get("label")
        or ""
    )
    line = row.get("line") or row.get("point") or row.get("handicap") or ""

    return {
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": commence_time,
        "league": league,
        "odd": odd,
        "market_kind": _parse_market_kind(market, selection),
        "selection": selection,
        "line": str(line) if line is not None else "",
    }


def _build_match_odds(match_rows: list[dict]) -> dict:
    """Assemble les cotes 1X2 / O/U 2.5 / BTTS à partir des lignes d'un même match."""
    home_team = match_rows[0]["home_team"]
    away_team = match_rows[0]["away_team"]
    home_norm = _norm(home_team)
    away_norm = _norm(away_team)

    out = {
        "home_odds": None, "draw_odds": None, "away_odds": None,
        "over_2_5": None, "under_2_5": None,
        "btts_yes": None, "btts_no": None,
    }

    for r in match_rows:
        odd = r["odd"]
        if odd is None:
            continue
        kind = r["market_kind"]
        sel = _norm(r["selection"])
        line = r["line"].lower()

        if kind == "1x2":
            if sel in ("home", home_norm) or "home" in sel or home_norm in sel:
                if not out["home_odds"]: out["home_odds"] = odd
            elif sel in ("draw", "tie", "x", "nul") or "draw" in sel:
                if not out["draw_odds"]: out["draw_odds"] = odd
            elif sel in ("away", away_norm) or "away" in sel or away_norm in sel:
                if not out["away_odds"]: out["away_odds"] = odd

        elif kind == "totals":
            if "2.5" in line or "2.5" in sel:
                if "over" in sel or "plus" in sel or "o " in sel:
                    if not out["over_2_5"]: out["over_2_5"] = odd
                elif "under" in sel or "moins" in sel or "u " in sel:
                    if not out["under_2_5"]: out["under_2_5"] = odd

        elif kind == "btts":
            if sel in ("yes", "oui", "o", "true"):
                if not out["btts_yes"]: out["btts_yes"] = odd
            elif sel in ("no", "non", "n", "false"):
                if not out["btts_no"]: out["btts_no"] = odd

    return out


async def fetch_all_events_raw(client: httpx.AsyncClient, sport: str = "soccer", max_pages: int = 10) -> list[dict]:
    """Récupère toutes les lignes de cotes SharpAPI (paginé, avec rate limit géré)."""
    all_rows = []
    offset = 0
    limit = 100

    for page in range(max_pages):
        data = await _get(client, "/odds", {
            "sport": sport,
            "is_live": "false",
            "limit": limit,
            "offset": offset,
        })
        if not data:
            break

        rows = data.get("data", []) or data.get("odds", []) or []
        if not rows:
            break

        all_rows.extend(rows)
        meta = data.get("meta", {}) or {}
        pagination = meta.get("pagination", {}) or {}
        total = meta.get("total", len(all_rows))
        print(f"  📊 SharpAPI page {page+1} : {len(rows)} lignes (cumul {len(all_rows)}/{total})")

        if not pagination.get("has_more", False):
            break

        offset = pagination.get("next_offset", offset + limit)
        await asyncio.sleep(5.5)  # respecte 12 req/min

    return all_rows


async def fetch_all_odds(matches: list[dict] = None) -> object:
    """
    API publique :
      - sans matches  -> list[dict] (tous les matchs avec cotes)
      - avec matches  -> {event_id: odds_dict} matché par noms
    """
    if not SHARPAPI_KEY:
        print("⚠️ SharpAPI : SHARPAPI_KEY manquante")
        return [] if matches is None else {}

    async with httpx.AsyncClient(timeout=25.0) as client:
        await _debug_list_sports(client)
        rows = await fetch_all_events_raw(client, sport="soccer", max_pages=10)

    if not rows:
        print("⚠️ SharpAPI : aucune ligne de cotes récupérée")
        return [] if matches is None else {}

    # Debug une seule fois : afficher les 3 premières lignes brutes
    global _DEBUG_RAW_DONE
    if not _DEBUG_RAW_DONE:
        print("🔎 SharpAPI — exemples de lignes brutes :")
        for r in rows[:3]:
            print(f"   {r}")
        _DEBUG_RAW_DONE = True

    # Grouper par (home_norm, away_norm, date)
    grouped = defaultdict(list)
    for row in rows:
        parsed = _extract_row_fields(row)
        if not parsed["home_team"] or not parsed["away_team"]:
            continue
        key = (
            _norm(parsed["home_team"]),
            _norm(parsed["away_team"]),
            (parsed["commence_time"] or "")[:10],
        )
        grouped[key].append(parsed)

    print(f"🔎 SharpAPI : {len(grouped)} matchs uniques détectés dans {len(rows)} lignes")

    # Construire les events avec cotes
    events_data = []
    stats = {"total": 0, "with_1x2": 0, "with_ou": 0, "with_btts": 0}

    for key, match_rows in grouped.items():
        stats["total"] += 1
        odds = _build_match_odds(match_rows)

        if odds["home_odds"] and odds["draw_odds"] and odds["away_odds"]:
            stats["with_1x2"] += 1
        if odds["over_2_5"] and odds["under_2_5"]:
            stats["with_ou"] += 1
        if odds["btts_yes"] and odds["btts_no"]:
            stats["with_btts"] += 1

        # On garde seulement ceux avec 1X2 complet
        if not (odds["home_odds"] and odds["draw_odds"] and odds["away_odds"]):
            continue

        events_data.append({
            "match_id": f"sharp_{key[0]}__{key[1]}",
            "league": match_rows[0]["league"],
            "home_team": match_rows[0]["home_team"],
            "away_team": match_rows[0]["away_team"],
            "home_odds": odds["home_odds"],
            "draw_odds": odds["draw_odds"],
            "away_odds": odds["away_odds"],
            "over_2_5": odds["over_2_5"],
            "under_2_5": odds["under_2_5"],
            "btts_yes": odds["btts_yes"],
            "btts_no": odds["btts_no"],
            "commence_time": match_rows[0]["commence_time"],
        })

    print(f"📊 SharpAPI STATS : {stats['total']} matchs bruts | "
          f"{stats['with_1x2']} avec 1X2 complet | "
          f"{stats['with_ou']} avec O/U 2.5 | "
          f"{stats['with_btts']} avec BTTS")
    print(f"✅ SharpAPI : {len(events_data)} matchs exploitables (1X2 complet)")

    # Mode liste (sans matches fournis)
    if matches is None:
        return events_data

    # Mode matching par noms -> {event_id: odds_dict}
    results = {}
    for m in matches:
        target_home = _norm(m.get("home", ""))
        target_away = _norm(m.get("away", ""))
        if not target_home or not target_away:
            continue

        best, best_score = None, 0
        for ev in events_data:
            h = _norm(ev["home_team"])
            a = _norm(ev["away_team"])
            score = 0
            if target_home in h or h in target_home:
                score += 1
            if target_away in a or a in target_away:
                score += 1
            if score > best_score:
                best_score, best = score, ev

        if best and best_score >= 2:
            results[m["event_id"]] = {
                "home": best["home_odds"],
                "draw": best["draw_odds"],
                "away": best["away_odds"],
                "over_2.5": best["over_2_5"],
                "under_2.5": best["under_2_5"],
                "btts_yes": best["btts_yes"],
                "btts_no": best["btts_no"],
            }

    print(f"📊 SharpAPI matching : {len(results)}/{len(matches)} matchs avec cotes")
    return results


# --- Test standalone ---
if __name__ == "__main__":
    async def _main():
        print("Test SharpAPI — récupération brute...")
        events = await fetch_all_odds()
        print(f"\nRésultat : {len(events)} matchs avec 1X2 complet")
        for e in events[:5]:
            print(f"  • {e['home_team']} vs {e['away_team']} "
                  f"({e['home_odds']}/{e['draw_odds']}/{e['away_odds']})")
    asyncio.run(_main())
