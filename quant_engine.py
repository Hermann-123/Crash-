"""
quant_engine.py — Moteur Quantitatif Multi-Compétitions & Anti-Risque pour VOLATILITY INDEX
- Résolution dynamique de toutes les compétitions mondiales (ESPN + The Odds API + Bzzoiro)
- Vérification pré-match ESPN (stade réel, terrain neutre neutralSite, classement, blessures)
- Modèle Dixon-Coles corrigé terrain neutre + Lissage bayésien contre les cotes bookmaker
- Verrous anti-faux Under 2.5, anti-EV gonflée et anti-hallucination
"""

from __future__ import annotations

import logging
import math
import os
import unicodedata
from typing import Any

import httpx

logger = logging.getLogger("volatility_index.quant")

ESPN_BASE = "https://site.api.espn.com/apis"
BZZOIRO_EVENTS_URL = "https://sports.bzzoiro.com/api/events/"
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"

LEAGUE_CONFIG: dict[str, dict[str, str]] = {
    # Coupes d'Europe & Compétitions Internationales
    "UEFA Champions League": {"espn": "uefa.champions", "odds": "soccer_uefa_champs_league"},
    "Champions League": {"espn": "uefa.champions", "odds": "soccer_uefa_champs_league"},
    "UEFA Europa League": {"espn": "uefa.europa", "odds": "soccer_uefa_europa_league"},
    "Europa League": {"espn": "uefa.europa", "odds": "soccer_uefa_europa_league"},
    "UEFA Conference League": {"espn": "uefa.europa.conf", "odds": "soccer_uefa_europa_conference_league"},
    "Conference League": {"espn": "uefa.europa.conf", "odds": "soccer_uefa_europa_conference_league"},
    "UEFA Nations League": {"espn": "uefa.nations", "odds": "soccer_uefa_nations_league"},
    "FIFA World Cup": {"espn": "fifa.world", "odds": "soccer_fifa_world_cup"},
    "World Cup Qualification": {"espn": "fifa.worldq.uefa", "odds": "soccer_fifa_world_cup_qualifiers_europe"},
    "Africa Cup of Nations": {"espn": "caf.nations", "odds": "soccer_africa_cup_of_nations"},
    "Copa Libertadores": {"espn": "conmebol.libertadores", "odds": "soccer_conmebol_copa_libertadores"},
    "Copa Sudamericana": {"espn": "conmebol.sudamericana", "odds": "soccer_conmebol_copa_sudamericana"},
    # Angleterre
    "Premier League": {"espn": "eng.1", "odds": "soccer_epl"},
    "Championship": {"espn": "eng.2", "odds": "soccer_efl_champ"},
    "League One": {"espn": "eng.3", "odds": "soccer_england_league1"},
    "League Two": {"espn": "eng.4", "odds": "soccer_england_league2"},
    "FA Cup": {"espn": "eng.fa", "odds": "soccer_fa_cup"},
    "EFL Cup": {"espn": "eng.league_cup", "odds": "soccer_england_efl_cup"},
    "Carabao Cup": {"espn": "eng.league_cup", "odds": "soccer_england_efl_cup"},
    # France
    "Ligue 1": {"espn": "fra.1", "odds": "soccer_france_ligue_one"},
    "Ligue 2": {"espn": "fra.2", "odds": "soccer_france_ligue_two"},
    "Coupe de France": {"espn": "fra.coupe_de_france", "odds": "soccer_france_coupe_de_france"},
    # Espagne
    "La Liga": {"espn": "esp.1", "odds": "soccer_spain_la_liga"},
    "LaLiga": {"espn": "esp.1", "odds": "soccer_spain_la_liga"},
    "Segunda Division": {"espn": "esp.2", "odds": "soccer_spain_segunda_division"},
    "Copa del Rey": {"espn": "esp.copa_del_rey", "odds": "soccer_spain_copa_del_rey"},
    # Italie
    "Serie A": {"espn": "ita.1", "odds": "soccer_italy_serie_a"},
    "Serie B": {"espn": "ita.2", "odds": "soccer_italy_serie_b"},
    "Coppa Italia": {"espn": "ita.coppa_italia", "odds": "soccer_italy_coppa_italia"},
    # Allemagne
    "Bundesliga": {"espn": "ger.1", "odds": "soccer_germany_bundesliga"},
    "2. Bundesliga": {"espn": "ger.2", "odds": "soccer_germany_bundesliga2"},
    "DFB-Pokal": {"espn": "ger.dfb_pokal", "odds": "soccer_germany_dfb_pokal"},
    "DFB Pokal": {"espn": "ger.dfb_pokal", "odds": "soccer_germany_dfb_pokal"},
    # Autres Championnats Européens
    "Primeira Liga": {"espn": "por.1", "odds": "soccer_portugal_primeira_liga"},
    "Liga Portugal": {"espn": "por.1", "odds": "soccer_portugal_primeira_liga"},
    "Eredivisie": {"espn": "ned.1", "odds": "soccer_netherlands_eredivisie"},
    "Jupiler Pro League": {"espn": "bel.1", "odds": "soccer_belgium_first_div"},
    "First Division A": {"espn": "bel.1", "odds": "soccer_belgium_first_div"},
    "Super Lig": {"espn": "tur.1", "odds": "soccer_turkey_super_league"},
    "Süper Lig": {"espn": "tur.1", "odds": "soccer_turkey_super_league"},
    "Scottish Premiership": {"espn": "sco.1", "odds": "soccer_spl"},
    "Premiership": {"espn": "sco.1", "odds": "soccer_spl"},
    "Super League": {"espn": "sui.1", "odds": "soccer_switzerland_superleague"},
    "Austrian Bundesliga": {"espn": "aut.1", "odds": "soccer_austria_bundesliga"},
    "Super League Greece": {"espn": "gre.1", "odds": "soccer_greece_super_league"},
    "Eliteserien": {"espn": "nor.1", "odds": "soccer_norway_eliteserien"},
    "Allsvenskan": {"espn": "swe.1", "odds": "soccer_sweden_allsvenskan"},
    "Superliga": {"espn": "den.1", "odds": "soccer_denmark_superliga"},
    # Amériques & Monde
    "Brasileirao": {"espn": "bra.1", "odds": "soccer_brazil_campeonato"},
    "Serie A Brazil": {"espn": "bra.1", "odds": "soccer_brazil_campeonato"},
    "Liga Profesional": {"espn": "arg.1", "odds": "soccer_argentina_primera_division"},
    "MLS": {"espn": "usa.1", "odds": "soccer_usa_mls"},
    "Major League Soccer": {"espn": "usa.1", "odds": "soccer_usa_mls"},
    "Liga MX": {"espn": "mex.1", "odds": "soccer_mexico_ligamx"},
    "Saudi Pro League": {"espn": "sau.1", "odds": "soccer_saudi_arabia_pro_league"},
    "J1 League": {"espn": "jpn.1", "odds": "soccer_japan_j_league"},
}

ALL_ESPN_SLUGS: list[str] = [
    "all",
    "uefa.champions", "uefa.europa", "uefa.europa.conf", "uefa.nations",
    "eng.1", "eng.2", "eng.3", "eng.fa", "eng.league_cup",
    "esp.1", "esp.2", "esp.copa_del_rey",
    "ita.1", "ita.2", "ita.coppa_italia",
    "ger.1", "ger.2", "ger.dfb_pokal",
    "fra.1", "fra.2", "fra.coupe_de_france",
    "ned.1", "por.1", "bel.1", "tur.1", "sco.1", "sui.1", "aut.1", "gre.1",
    "nor.1", "swe.1", "den.1",
    "conmebol.libertadores", "conmebol.sudamericana",
    "bra.1", "arg.1", "usa.1", "mex.1", "sau.1", "jpn.1",
    "fifa.world", "caf.nations",
]

EXCLUDED_LEAGUE_KEYWORDS = (
    "u17", "u18", "u19", "u20", "u21", "u23", "youth", "reserve",
    "friendly", "amical", "club friendlies", "women", "feminin",
    "oberliga", "regionalliga", "national league north", "national league south",
    "istmian", "efl trophy", "checkatrade", "premier league 2", "premier league cup",
)

_DYNAMIC_ODDS_SPORTS_CACHE: list[dict[str, Any]] | None = None


def normalize_team_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    stop_words = {"fc", "jk", "sk", "sc", "cf", "ac", "as", "afc", "olympique", "de", "the", "club", "united", "utd", "city"}
    tokens = [t for t in cleaned.split() if t not in stop_words]
    return " ".join(tokens)


def teams_match_strict(name_a: str, name_b: str) -> bool:
    na, nb = normalize_team_name(name_a), normalize_team_name(name_b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
        return True
    ta, tb = set(na.split()), set(nb.split())
    return bool(ta & tb and max(len(t) for t in (ta & tb)) >= 5)


def is_eligible_competition(league_name: str) -> bool:
    low = (league_name or "").lower().strip()
    if not low:
        return True
    return not any(bad in low for bad in EXCLUDED_LEAGUE_KEYWORDS)


async def discover_active_odds_sports_async(api_key: str | None = None) -> list[dict[str, Any]]:
    """Interroge /v4/sports/ (0 crédit consommé) pour découvrir toutes les ligues foot actives."""
    global _DYNAMIC_ODDS_SPORTS_CACHE
    if _DYNAMIC_ODDS_SPORTS_CACHE is not None:
        return _DYNAMIC_ODDS_SPORTS_CACHE

    key = api_key or os.getenv("THE_ODDS_API_KEY", "") or os.getenv("ODDS_API_KEY", "")
    if not key:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{ODDS_API_BASE}/", params={"apiKey": key})
            if resp.status_code == 200:
                soccer_sports = [
                    s for s in resp.json()
                    if str(s.get("key", "")).startswith("soccer_") and s.get("active", True)
                ]
                _DYNAMIC_ODDS_SPORTS_CACHE = soccer_sports
                return soccer_sports
    except Exception as exc:
        logger.warning("Erreur découverte dynamique Odds API: %s", exc)
    return []


def resolve_league_config(league_name: str, country_name: str = "") -> dict[str, str]:
    clean_league = (league_name or "").strip()
    if clean_league in LEAGUE_CONFIG:
        return LEAGUE_CONFIG[clean_league]

    low = clean_league.lower()
    for key, cfg in LEAGUE_CONFIG.items():
        if key.lower() == low or key.lower() in low or low in key.lower():
            return cfg

    active_sports = _DYNAMIC_ODDS_SPORTS_CACHE or []
    norm_target = normalize_team_name(f"{country_name} {clean_league}".strip())
    target_tokens = set(norm_target.split())
    best_sport_key = ""
    best_overlap = 0
    for sp in active_sports:
        sp_title = normalize_team_name(f"{sp.get('group', '')} {sp.get('title', '')}")
        overlap = len(target_tokens & set(sp_title.split()))
        if overlap > best_overlap:
            best_overlap = overlap
            best_sport_key = sp.get("key", "")

    return {"espn": "", "odds": best_sport_key}


# ==============================================================================
# 1. VÉRIFICATION PRÉ-MATCH ESPN (STADE, TERRAIN NEUTRE, CLASSEMENT, BLESSURES)
# ==============================================================================

async def fetch_espn_prematch_context_async(
    league_name: str,
    date_yyyymmdd: str,
    home_team: str,
    away_team: str,
    event_id: str | None = None,
) -> dict[str, Any]:
    cfg = resolve_league_config(league_name)
    slug = cfg.get("espn", "")
    slugs_to_try = [slug] if slug else ["all", "uefa.champions", "uefa.europa", "uefa.europa.conf"]

    context: dict[str, Any] = {
        "espn_event_id": None,
        "resolved_slug": slug,
        "venue_name": "Stade officiel",
        "venue_city": "Domicile",
        "neutral_venue": False,
        "home_rank": "N/A",
        "away_rank": "N/A",
        "home_form": "",
        "away_form": "",
        "injuries": [],
    }

    home_id: str | None = None
    away_id: str | None = None
    found_slug = ""

    async with httpx.AsyncClient(timeout=10.0) as client:
        for s in slugs_to_try:
            if not s:
                continue
            try:
                sb_url = f"{ESPN_BASE}/site/v2/sports/soccer/{s}/scoreboard"
                resp = await client.get(sb_url, params={"dates": date_yyyymmdd, "limit": 200})
                if resp.status_code != 200:
                    continue
                for event in resp.json().get("events", []):
                    ev_id = str(event.get("id", ""))
                    comp = (event.get("competitions") or [{}])[0]
                    competitors = comp.get("competitors", [])
                    ev_home = next((c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "home"), "")
                    ev_away = next((c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "away"), "")

                    clean_ev_id = str(event_id).replace("espn_", "") if event_id else ""
                    id_match = bool(clean_ev_id and ev_id == clean_ev_id)
                    name_match = bool(teams_match_strict(home_team, ev_home) and teams_match_strict(away_team, ev_away))

                    if not (id_match or name_match):
                        continue

                    found_slug = s if s != "all" else slug
                    context["espn_event_id"] = ev_id
                    context["resolved_slug"] = found_slug
                    venue = comp.get("venue") or {}
                    context["neutral_venue"] = bool(comp.get("neutralSite", False))
                    if venue.get("fullName"):
                        context["venue_name"] = venue["fullName"]
                    city = (venue.get("address") or {}).get("city")
                    if city:
                        context["venue_city"] = city

                    for c in competitors:
                        if c.get("homeAway") == "home":
                            home_id = str(c.get("team", {}).get("id", ""))
                            context["home_form"] = c.get("form", "")
                        elif c.get("homeAway") == "away":
                            away_id = str(c.get("team", {}).get("id", ""))
                            context["away_form"] = c.get("form", "")
                    break
                if context["espn_event_id"]:
                    break
            except Exception:
                continue

        if found_slug and (home_id or away_id):
            try:
                st_res = await client.get(f"{ESPN_BASE}/v2/sports/soccer/{found_slug}/standings")
                if st_res.status_code == 200:
                    for child in st_res.json().get("children", []):
                        for entry in (child.get("standings") or {}).get("entries", []):
                            tid = str((entry.get("team") or {}).get("id", ""))
                            stats = {st["name"]: st.get("displayValue") for st in entry.get("stats", []) if "name" in st}
                            if tid == home_id and stats.get("rank"):
                                context["home_rank"] = stats["rank"]
                            elif tid == away_id and stats.get("rank"):
                                context["away_rank"] = stats["rank"]
            except Exception:
                pass

        if found_slug and context["espn_event_id"]:
            try:
                sum_res = await client.get(
                    f"{ESPN_BASE}/site/v2/sports/soccer/{found_slug}/summary",
                    params={"event": context["espn_event_id"]},
                )
                if sum_res.status_code == 200:
                    for team_inj in sum_res.json().get("injuries", []):
                        t_name = (team_inj.get("team") or {}).get("displayName", "")
                        for item in team_inj.get("injuries", []):
                            athlete = (item.get("athlete") or {}).get("displayName", "")
                            status = item.get("status", "")
                            if athlete:
                                context["injuries"].append(f"{athlete} ({t_name} - {status})")
            except Exception:
                pass

    return context


# ==============================================================================
# 2. DIXON-COLES CALIBRÉ & LISSAGE BAYÉSIEN ANTI-HALLUCINATION
# ==============================================================================

def estimate_lambdas_from_odds(
    home_odd: float,
    away_odd: float,
    draw_odd: float | None = None,
    over_25_odd: float | None = None,
    under_25_odd: float | None = None,
    neutral_venue: bool = False,
) -> tuple[float, float]:
    odds_list = [o for o in (home_odd, draw_odd, away_odd) if o and o > 1.01]
    if len(odds_list) == 3:
        inv_sum = sum(1.0 / o for o in odds_list)
        p_h = (1.0 / home_odd) / inv_sum
        p_a = (1.0 / away_odd) / inv_sum
    else:
        inv_h = 1.0 / max(1.05, home_odd)
        inv_a = 1.0 / max(1.05, away_odd)
        p_h = 0.74 * (inv_h / (inv_h + inv_a))
        p_a = 0.74 * (inv_a / (inv_h + inv_a))

    expected_total_goals = 2.65
    if over_25_odd and under_25_odd and over_25_odd > 1.01 and under_25_odd > 1.01:
        inv_ou = (1.0 / over_25_odd) + (1.0 / under_25_odd)
        p_over25 = (1.0 / over_25_odd) / inv_ou
        expected_total_goals = max(1.85, min(3.45, 0.85 + 3.45 * p_over25))

    share_h = max(0.18, min(0.82, p_h / max(0.05, p_h + p_a)))
    if not neutral_venue:
        share_h = min(0.84, share_h * 1.03)
    share_a = 1.0 - share_h

    lam_h = round(max(0.25, expected_total_goals * share_h), 2)
    lam_a = round(max(0.20, expected_total_goals * share_a), 2)
    return lam_h, lam_a


def dixon_coles_probabilities(
    home_xg: float,
    away_xg: float,
    neutral_venue: bool = False,
    rho: float = -0.11,
    max_goals: int = 8,
) -> dict[str, float]:
    home_adv = 1.00 if neutral_venue else 1.08
    lam = max(0.20, home_xg * home_adv)
    mu = max(0.20, away_xg)

    prob_1 = prob_x = prob_2 = 0.0
    prob_o15 = prob_u15 = prob_o25 = prob_u25 = prob_o35 = prob_btts = 0.0

    for h in range(max_goals + 1):
        p_h = (math.exp(-lam) * (lam ** h)) / math.factorial(h)
        for a in range(max_goals + 1):
            p_a = (math.exp(-mu) * (mu ** a)) / math.factorial(a)
            if h == 0 and a == 0:
                tau = 1.0 - (lam * mu * rho)
            elif h == 1 and a == 0:
                tau = 1.0 + (mu * rho)
            elif h == 0 and a == 1:
                tau = 1.0 + (lam * rho)
            elif h == 1 and a == 1:
                tau = 1.0 - rho
            else:
                tau = 1.0

            p_sc = max(0.0, p_h * p_a * tau)
            tot = h + a
            if h > a:
                prob_1 += p_sc
            elif h == a:
                prob_x += p_sc
            else:
                prob_2 += p_sc

            if tot >= 2:
                prob_o15 += p_sc
            else:
                prob_u15 += p_sc
            if tot >= 3:
                prob_o25 += p_sc
            else:
                prob_u25 += p_sc
            if tot >= 4:
                prob_o35 += p_sc
            if h >= 1 and a >= 1:
                prob_btts += p_sc

    norm = max(1e-9, prob_1 + prob_x + prob_2)
    p1, px, p2 = prob_1 / norm, prob_x / norm, prob_2 / norm
    return {
        "p_home": round(p1, 4),
        "p_draw": round(px, 4),
        "p_away": round(p2, 4),
        "double_chance_1X": round(min(0.96, p1 + px), 4),
        "double_chance_X2": round(min(0.96, p2 + px), 4),
        "double_chance_12": round(min(0.96, p1 + p2), 4),
        "over_1.5": round(prob_o15 / norm, 4),
        "under_1.5": round(prob_u15 / norm, 4),
        "over_2.5": round(prob_o25 / norm, 4),
        "under_2.5": round(prob_u25 / norm, 4),
        "over_3.5": round(prob_o35 / norm, 4),
        "btts_yes": round(prob_btts / norm, 4),
        "btts_no": round(max(0.02, 1.0 - (prob_btts / norm)), 4),
    }


def calibrate_prob_with_market(
    raw_model_prob: float,
    target_odd: float,
    opposing_odds: list[float] | None = None,
    model_weight: float = 0.35,
) -> tuple[float, float, float]:
    if target_odd <= 1.01 or raw_model_prob <= 0:
        return 0.0, 0.0, 0.0

    if opposing_odds and all(o > 1.01 for o in opposing_odds):
        overround = sum(1.0 / o for o in opposing_odds)
        true_market_prob = (1.0 / target_odd) / overround
    else:
        true_market_prob = min(0.92, (1.0 / target_odd) * 0.945)

    calibrated = (model_weight * raw_model_prob) + ((1.0 - model_weight) * true_market_prob)
    calibrated = max(0.05, min(0.90, calibrated))
    ev = calibrated * target_odd
    return calibrated, true_market_prob, ev


def passes_anti_trap_checks(
    market_key: str,
    target_odd: float,
    calibrated_prob: float,
    true_market_prob: float,
    total_xg: float,
    espn_ctx: dict[str, Any] | None = None,
    home_team: str = "",
    away_team: str = "",
) -> tuple[bool, str]:
    if market_key in ("over_1.5", "double_chance_1X", "double_chance_X2"):
        if not (1.25 <= target_odd <= 1.60):
            return False, f"cote hors plage sécurité ({target_odd})"
    else:
        if not (1.30 <= target_odd <= 1.95):
            return False, f"cote hors plage pro ({target_odd})"

    edge = calibrated_prob - true_market_prob
    ev = calibrated_prob * target_odd
    if edge > 0.10 or ev > 1.16 or ev < 0.96:
        return False, f"edge/EV hors bornes (edge={edge:+.2f}, EV={ev:.2f})"

    if market_key == "under_2.5":
        if total_xg > 2.50:
            return False, f"faux Under 2.5 (xG total={total_xg:.2f} > 2.50)"
        if target_odd > 1.92:
            return False, f"faux Under 2.5 (cote marché trop haute={target_odd})"

    if espn_ctx and market_key in ("victoire_home", "victoire_away"):
        target_team = home_team if market_key == "victoire_home" else away_team
        team_inj = [inj for inj in espn_ctx.get("injuries", []) if teams_match_strict(target_team, inj)]
        if len(team_inj) >= 4:
            return False, f"trop d'absents ({len(team_inj)} blessés)"

    return True, "ok"
