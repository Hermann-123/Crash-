from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Optional, List, Callable
from zoneinfo import ZoneInfo

import httpx

BZZOIRO_API_KEY = os.getenv("BZZOIRO_API_KEY", "")
NOTIFY_TZ = os.getenv("NOTIFY_TZ", "Africa/Abidjan")
TZ = ZoneInfo(NOTIFY_TZ)

MATCH_START_BUFFER_MINUTES = 10
RESERVE_KEYWORDS = ["jong", " fc b", " ii", " u21", " u19", " castilla", " reserves", " b team"]

# Cible par sélection pour atteindre une cote globale de 20 à 35 sur 12-15 matchs
WEEKLY_MIN_LEG_ODD = 1.22
WEEKLY_IDEAL_MIN_ODD = 1.25
WEEKLY_IDEAL_MAX_ODD = 1.62

PRO_TRAP_PHRASES = [
    "gestion d'effectif",
    "rotation",
    "effectif remanié",
    "nombreux absents",
    "blessures majeures",
    "déplacement piège",
    "match piège",
    "irrégularité chronique",
    "très imprévisible",
    "aucun enjeu",
    "sans enjeu",
    "en pleine crise",
]


def _normalize_name(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    for sfx in [" fc", " cf", " sc", " ac", " afc", " united", " utd", " city"]:
        s = s.replace(sfx, "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s)).strip()


def _is_reserve_team(name: str) -> bool:
    return any(kw in name.lower() for kw in RESERVE_KEYWORDS)


def _has_pro_red_flags(analysis_text: str) -> bool:
    if not analysis_text:
        return False
    low = analysis_text.lower()
    return any(phrase in low for phrase in PRO_TRAP_PHRASES)


def _clean_analysis_text(text: str, max_len: int = 230) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > 80:
            return cut[: idx + 1]
    return cut.rsplit(" ", 1)[0] + "..."


def _parse_dt_safe(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        s = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return None


def _is_match_not_started(kickoff_str) -> bool:
    if not kickoff_str:
        return True
    dt = _parse_dt_safe(kickoff_str)
    if not dt or (dt.hour == 0 and dt.minute == 0):
        return True
    return dt > (datetime.now(TZ) + timedelta(minutes=MATCH_START_BUFFER_MINUTES))


async def fetch_bzzoiro_horizon(days: int = 3, min_conf: float = 55.0) -> list[dict]:
    """Récupère les prédictions Bzzoiro sur les N prochains jours."""
    if not BZZOIRO_API_KEY:
        return []
    now = datetime.now(TZ)
    end_dt = now + timedelta(days=days)
    d_from = now.strftime("%Y-%m-%d")
    d_to = end_dt.strftime("%Y-%m-%d")
    headers = {"Authorization": f"Token {BZZOIRO_API_KEY}"}
    url = "https://sports.bzzoiro.com/api/predictions/"
    collected = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for params in (
            {"date_from": d_from, "date_to": d_to, "limit": 200},
            {"limit": 200},
        ):
            try:
                r = await client.get(url, headers=headers, params=params)
                if r.status_code != 200:
                    continue
                payload = r.json()
                items = payload.get("results", payload) if isinstance(payload, dict) else payload
                if not isinstance(items, list) or not items:
                    continue

                for item in items:
                    ev = item.get("event") if isinstance(item.get("event"), dict) else item
                    h = ev.get("home_team") or item.get("home_team") or ""
                    a = ev.get("away_team") or item.get("away_team") or ""
                    if isinstance(h, dict):
                        h = h.get("name", "")
                    if isinstance(a, dict):
                        a = a.get("name", "")
                    if not h or not a or _is_reserve_team(h) or _is_reserve_team(a):
                        continue

                    kickoff = ev.get("event_date") or ev.get("commence_time") or item.get("event_date") or ""
                    dt_k = _parse_dt_safe(kickoff)
                    if dt_k:
                        if dt_k <= (now + timedelta(minutes=MATCH_START_BUFFER_MINUTES)) or dt_k > end_dt:
                            continue

                    lg = ev.get("league") or item.get("league") or ""
                    if isinstance(lg, dict):
                        lg = lg.get("name", "")

                    bzz_m = {}
                    for k_src, k_dst in [
                        ("prob_home_win", "victoire_home"), ("home_win_prob", "victoire_home"),
                        ("prob_draw", "match_nul"), ("draw_prob", "match_nul"),
                        ("prob_away_win", "victoire_away"), ("away_win_prob", "victoire_away"),
                        ("prob_over_25", "over_2.5"), ("over_2_5_prob", "over_2.5"),
                        ("prob_under_25", "under_2.5"), ("under_2_5_prob", "under_2.5"),
                        ("prob_btts_yes", "btts_yes"), ("btts_yes_prob", "btts_yes"),
                    ]:
                        val = item.get(k_src)
                        if val is not None:
                            try:
                                fval = float(val)
                                bzz_m[k_dst] = fval * 100.0 if fval <= 1.0 else fval
                            except Exception:
                                pass

                    conf = float(item.get("confidence") or max(bzz_m.values(), default=0.0))
                    if conf <= 1.0:
                        conf *= 100.0
                    if conf < min_conf and not bzz_m:
                        continue

                    r_odds = {}
                    for ok_src, ok_dst in [
                        ("odds_home", "home"),
                        ("odds_draw", "draw"),
                        ("odds_away", "away"),
                        ("odds_over_25", "over_2.5"),
                    ]:
                        ov = ev.get(ok_src) or item.get(ok_src)
                        if ov:
                            try:
                                r_odds[ok_dst] = float(ov)
                            except Exception:
                                pass

                    eid = str(ev.get("id") or item.get("id") or f"{_normalize_name(h)}_{_normalize_name(a)}")
                    collected.append({
                        "event_id": eid,
                        "league": str(lg),
                        "home": str(h),
                        "away": str(a),
                        "kickoff": str(kickoff),
                        "bzz_markets": bzz_m,
                        "bzz_confidence": conf,
                        "real_odds": r_odds,
                    })
                if collected:
                    break
            except Exception as e:
                print(f"⚠️ weekly_analyzer Bzzoiro ({d_from}->{d_to}) : {e}")

    seen = set()
    unique = []
    for c in collected:
        nk = f"{_normalize_name(c['home'])}__{_normalize_name(c['away'])}"
        if nk not in seen:
            seen.add(nk)
            unique.append(c)
    return unique


async def analyze_weekly_top_matches(
    days: int,
    limit: int,
    match_cls,
    dixon_model,
    fetch_odds_fn: Callable,
    inject_odds_fn: Callable,
    prescore_fn: Callable,
    enrich_pass_fn: Callable,
    compute_prob_fn: Callable,
    fallback_bzz_fn: Optional[Callable] = None,
) -> List[dict]:
    """
    Sélectionne les N meilleurs matchs sur 2-3 jours calibrés pour un combiné rentable (cote 20 à 35) :
    - Match à favori net (>= 64 %) : Victoire sèche (1 ou 2)
    - Match serré / équilibré (écart < 18 %) : Double Chance Victoire ou Nul (1X ou X2) à cote >= 1.22
    - Match offensif : Plus de 2.5 buts
    """
    ws_matches, _ = await fetch_odds_fn()
    raw_items = await fetch_bzzoiro_horizon(days=days, min_conf=55.0)
    if not raw_items and fallback_bzz_fn:
        raw_items = await fallback_bzz_fn(min_confidence=55.0)

    pool = []
    for bz in raw_items:
        md = match_cls(
            event_id=bz["event_id"],
            league=bz.get("league", ""),
            home=bz["home"],
            away=bz["away"],
            kickoff=bz.get("kickoff", ""),
            real_odds=dict(bz.get("real_odds", {})),
            bzz_markets=bz.get("bzz_markets", {}),
            bzz_confidence=bz.get("bzz_confidence", 0),
        )
        if dixon_model:
            try:
                md.dixon_pred = dixon_model.predict_all(md.home, md.away)
            except Exception:
                pass
        pool.append(md)

    inject_odds_fn(pool, ws_matches)
    pool = [md for md in pool if _is_match_not_started(md.kickoff)]
    pool.sort(key=prescore_fn, reverse=True)

    scan_cap = min(len(pool), max(limit + 12, 25))
    targets = pool[:scan_cap]
    if targets:
        r1 = await enrich_pass_fn(targets, 1)
        for md in targets:
            p1 = r1.get(md.event_id)
            if p1:
                md.gemini_markets = p1.get("markets", {})
                md.analysis = p1.get("analysis", "")

    markets_map = [
        ("victoire_home", "home", "result", lambda m: f"Victoire {m.home} (1)"),
        ("victoire_away", "away", "result", lambda m: f"Victoire {m.away} (2)"),
        ("double_chance_1X", "dc_1x", "dc", lambda m: f"Double Chance {m.home} ou Nul (1X)"),
        ("double_chance_X2", "dc_x2", "dc", lambda m: f"Double Chance Nul ou {m.away} (X2)"),
        ("over_2.5", "over_2.5", "goals", lambda m: "Plus de 2.5 buts"),
        ("btts_yes", "btts_yes", "btts", lambda m: "Les 2 équipes marquent (BTTS Oui)"),
        ("under_2.5", "under_2.5", "goals", lambda m: "Moins de 2.5 buts"),
    ]

    ranked_picks = []
    for md in targets:
        if not md.analysis or not md.gemini_markets:
            continue
        if _has_pro_red_flags(md.analysis):
            continue

        p_home_est, g_h, _, _, _ = compute_prob_fn(md, "victoire_home")
        p_away_est, g_a, _, _, _ = compute_prob_fn(md, "victoire_away")
        one_sided = (p_home_est >= 0.72) or (p_away_est >= 0.72)
        tight_match = abs(p_home_est - p_away_est) < 0.18

        real_h_odd = md.real_odds.get("home")
        real_a_odd = md.real_odds.get("away")

        best = None
        for mk, odd_key, family, label_fn in markets_map:
            if mk == "btts_yes" and one_sided:
                continue

            # Vérification de cohérence sur les Double Chances (1X / X2)
            if mk == "double_chance_1X":
                if real_a_odd and real_a_odd < 1.95:
                    continue
                if g_a > 0 and g_h > 0 and g_a > (g_h + 0.08):
                    continue
            elif mk == "double_chance_X2":
                if real_h_odd and real_h_odd < 1.95:
                    continue
                if g_h > 0 and g_a > 0 and g_h > (g_a + 0.08):
                    continue

            prob, g, d, b, emoji = compute_prob_fn(md, mk)
            proba_pct = prob * 100.0
            if proba_pct < 62.0 or proba_pct > 82.0 or emoji == "⚠️":
                continue

            real_odd = md.real_odds.get(odd_key)
            odd = real_odd if real_odd else (round(1.0 / prob, 2) if prob > 0 else 0.0)
            if odd < WEEKLY_MIN_LEG_ODD:
                continue

            in_ideal_odd = 1 if (WEEKLY_IDEAL_MIN_ODD <= odd <= WEEKLY_IDEAL_MAX_ODD) else 0

            # Logique Pro : Victoire sèche si favori net, Victoire ou Nul (1X/X2) si match serré
            if tight_match and family == "dc":
                strategy_fit = 3
            elif not tight_match and family == "result":
                strategy_fit = 3
            elif family == "goals" and mk == "over_2.5":
                strategy_fit = 2
            else:
                strategy_fit = 1

            consensus_bonus = 1 if emoji in ("🤝", "🤝🤝") else 0
            score = (in_ideal_odd, strategy_fit, consensus_bonus, round(proba_pct, 1))

            if not best or score > best["_score"]:
                best = {
                    "home": md.home,
                    "away": md.away,
                    "league": md.league,
                    "kickoff": md.kickoff,
                    "pick": label_fn(md),
                    "proba": proba_pct,
                    "odd": odd,
                    "is_real": bool(real_odd),
                    "ai": _clean_analysis_text(md.analysis, 230),
                    "_score": score,
                }
        if best:
            best.pop("_score", None)
            ranked_picks.append(best)

    ranked_picks.sort(key=lambda x: (x["proba"], x["odd"]), reverse=True)
    selected = ranked_picks[:limit]
    selected.sort(key=lambda c: _parse_dt_safe(c.get("kickoff")) or datetime.max.replace(tzinfo=TZ))
    return selected
