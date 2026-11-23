from __future__ import annotations

import math
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

# Calibrage Pronostiqueur Pro (20 ans d'expérience) :
# - Confiance >= 72 % par sélection
# - Priorité aux compétitions majeures, exclusion des divisions inférieures (National League, League Two...)
# - Équilibrage strict des marchés (Ancres 1/2, Boucliers 1X/X2, Moteurs offensifs Over 1.5/2.5)
WEEKLY_MIN_LEG_ODD = 1.18
WEEKLY_MIN_PROBA_PCT = 72.0
WEEKLY_MAX_PROBA_PCT = 86.0

TIER1_LEAGUES = [
    "champions league", "europa league", "conference league",
    "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1",
    "eredivisie", "primeira liga", "liga portugal", "scottish premiership",
    "pro league", "jupiler", "super lig", "super league",
]

TIER2_LEAGUES = [
    "championship", "2. bundesliga", "serie b", "segunda", "ligue 2",
    "league one", "ekstraklasa", "allsvenskan", "eliteserien", "superliga",
]

EXCLUDED_LOWER_LEAGUES = [
    "national league", "league two", "serie c", "3. liga", "national 2",
    "national 3", "regionalliga", "tercera", "non league", "isthmian", "southern league",
    "northern premier", "youth", "reserve", "amateur",
]

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


def _league_tier(league: str) -> int:
    """Retourne 1 (Majeure), 2 (Solide D2/D3 pro), ou 0 (Division inférieure à exclure)."""
    low = (league or "").lower().strip()
    if any(ex in low for ex in EXCLUDED_LOWER_LEAGUES):
        return 0
    if any(t1 in low for t1 in TIER1_LEAGUES):
        return 1
    if any(t2 in low for t2 in TIER2_LEAGUES):
        return 2
    return 1


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


async def fetch_bzzoiro_horizon(
    days: int = 3,
    min_conf: float = 58.0,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
) -> list[dict]:
    """Récupère les prédictions Bzzoiro sur une plage de dates précise ou sur les N prochains jours."""
    if not BZZOIRO_API_KEY:
        return []
    now = datetime.now(TZ)
    eff_start = start_dt if start_dt else (now + timedelta(minutes=MATCH_START_BUFFER_MINUTES))
    eff_end = end_dt if end_dt else (now + timedelta(days=days))
    d_from = eff_start.strftime("%Y-%m-%d")
    d_to = eff_end.strftime("%Y-%m-%d")
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

                    lg = ev.get("league") or item.get("league") or ""
                    if isinstance(lg, dict):
                        lg = lg.get("name", "")
                    if _league_tier(str(lg)) == 0:
                        continue

                    kickoff = ev.get("event_date") or ev.get("commence_time") or item.get("event_date") or ""
                    dt_k = _parse_dt_safe(kickoff)
                    if dt_k:
                        if dt_k < eff_start or dt_k <= (now + timedelta(minutes=MATCH_START_BUFFER_MINUTES)) or dt_k > eff_end:
                            continue

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
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
) -> List[dict]:
    """
    Architecture Combiné Pro (20 ans d'expérience) :
    1. Exclut les divisions inférieures (National League, League Two...) et priorise les ligues majeures.
    2. Équilibre le ticket entre :
       - Ancres : Victoires sèches à domicile (>= 73 %)
       - Boucliers : Double Chance 1X / X2 alignée avec le favori (>= 74 %)
       - Moteurs offensifs : Plus de 1.5 buts / Plus de 2.5 buts sur matchs à forts xG
    3. Plafonne chaque famille de marché (max ~40 % du ticket par famille) pour éviter l'effet domino.
    """
    ws_matches, _ = await fetch_odds_fn()
    raw_items = await fetch_bzzoiro_horizon(days=days, min_conf=58.0, start_dt=start_dt, end_dt=end_dt)
    if not raw_items and fallback_bzz_fn:
        raw_items = await fallback_bzz_fn(min_confidence=58.0)

    pool = []
    for bz in raw_items:
        if _league_tier(bz.get("league", "")) == 0:
            continue
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
    if start_dt and end_dt:
        filtered = []
        for md in pool:
            dt_k = _parse_dt_safe(md.kickoff)
            if dt_k and start_dt <= dt_k <= end_dt:
                filtered.append(md)
        pool = filtered

    # Priorité aux ligues majeures (Tier 1) avant les ligues secondaires (Tier 2)
    pool.sort(key=lambda m: (1 if _league_tier(m.league) == 1 else 0, prescore_fn(m)), reverse=True)

    scan_cap = min(len(pool), max(limit + 14, 28))
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
        ("over_1.5", "over_1.5", "goals", lambda m: "Plus de 1.5 buts"),
        ("over_2.5", "over_2.5", "goals", lambda m: "Plus de 2.5 buts"),
        ("under_2.5", "under_2.5", "goals", lambda m: "Moins de 2.5 buts"),
        ("btts_yes", "btts_yes", "btts", lambda m: "Les 2 équipes marquent (BTTS Oui)"),
    ]

    ranked_picks = []
    for md in targets:
        if not md.analysis or not md.gemini_markets:
            continue
        if _has_pro_red_flags(md.analysis):
            continue

        tier = _league_tier(md.league)
        if tier == 0:
            continue

        p_home_est, g_h, _, _, _ = compute_prob_fn(md, "victoire_home")
        p_away_est, g_a, _, _, _ = compute_prob_fn(md, "victoire_away")
        one_sided = (p_home_est >= 0.72) or (p_away_est >= 0.72)
        tight_match = abs(p_home_est - p_away_est) < 0.22

        real_h_odd = md.real_odds.get("home")
        real_a_odd = md.real_odds.get("away")

        best = None
        for mk, odd_key, family, label_fn in markets_map:
            if mk == "btts_yes" and one_sided:
                continue

            if mk == "victoire_away" and p_away_est < 0.74:
                continue

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
            min_req = 74.0 if family == "dc" else (73.0 if family == "result" else WEEKLY_MIN_PROBA_PCT)
            if proba_pct < min_req or proba_pct > WEEKLY_MAX_PROBA_PCT or emoji == "⚠️":
                continue

            real_odd = md.real_odds.get(odd_key)
            odd = real_odd if real_odd else (round(1.0 / prob, 2) if prob > 0 else 0.0)
            if odd < WEEKLY_MIN_LEG_ODD:
                continue

            if not tight_match and family == "result":
                strategy_fit = 4
            elif tight_match and family == "dc":
                strategy_fit = 4
            elif family == "goals":
                strategy_fit = 2
            else:
                strategy_fit = 1

            tier_bonus = 1 if tier == 1 else 0
            consensus_bonus = 1 if emoji in ("🤝", "🤝🤝") else 0
            score = (tier_bonus, strategy_fit, consensus_bonus, round(proba_pct, 1), 1 if real_odd else 0)

            if not best or score > best["_score"]:
                best = {
                    "home": md.home,
                    "away": md.away,
                    "league": md.league,
                    "kickoff": md.kickoff,
                    "pick": label_fn(md),
                    "family": family,
                    "tier": tier,
                    "proba": proba_pct,
                    "odd": odd,
                    "is_real": bool(real_odd),
                    "ai": _clean_analysis_text(md.analysis, 230),
                    "_score": score,
                }
        if best:
            best.pop("_score", None)
            ranked_picks.append(best)

    ranked_picks.sort(
        key=lambda x: (1 if x["tier"] == 1 else 0, x["proba"], 1 if x["is_real"] else 0),
        reverse=True,
    )

    max_per_family = max(3, math.ceil(limit * 0.40))
    family_counts = {"result": 0, "dc": 0, "goals": 0, "btts": 0}
    selected = []
    overflow = []

    for item in ranked_picks:
        fam = item.get("family", "goals")
        if family_counts.get(fam, 0) < max_per_family:
            selected.append(item)
            family_counts[fam] = family_counts.get(fam, 0) + 1
            if len(selected) >= limit:
                break
        else:
            overflow.append(item)

    if len(selected) < limit:
        for item in overflow:
            selected.append(item)
            if len(selected) >= limit:
                break

    for item in selected:
        item.pop("family", None)
        item.pop("tier", None)

    selected.sort(key=lambda c: _parse_dt_safe(c.get("kickoff")) or datetime.max.replace(tzinfo=TZ))
    return selected
