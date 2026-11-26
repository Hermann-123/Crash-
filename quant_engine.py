"""
quant_engine.py — Moteur Quantitatif Multi-Compétitions & Anti-Risque pour VOLATILITY INDEX
Sources de données :
- Bzzoiro Sports Data (matchs du jour & xG pré-match toutes compétitions)
- ESPN Public API (stade réel, terrain neutre, classement, forme, blessures)
- The Odds API (découverte dynamique des compétitions actives + cotes marché)
- Gemini AI (analyse verrouillée sur les faits et le marché sélectionné)
"""

from __future__ import annotations

import logging
import math
import os
import unicodedata
from itertools import combinations
from typing import Any

import requests

logger = logging.getLogger("volatility_index.quant")

ESPN_BASE = "https://site.api.espn.com/apis"
BZZOIRO_EVENTS_URL = "https://sports.bzzoiro.com/api/events/"
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"

# Table étendue des compétitions mondiales -> Slug ESPN & Sport Key Odds API
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

# Mots-clés des compétitions mineures / jeunes / réserves à exclure par sécurité
EXCLUDED_LEAGUE_KEYWORDS = (
    "u17", "u18", "u19", "u20", "u21", "u23", "youth", "reserve",
    "friendly", "amical", "women", "feminin", "oberliga", "regionalliga",
    "national league north", "national league south", "istmian",
)

_DYNAMIC_ODDS_SPORTS_CACHE: list[dict[str, Any]] | None = None


def _normalize_name(name: str) -> str:
    """Normalise un nom d'équipe ou de compétition pour faciliter le matching multi-sources."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    stop_words = {"fc", "jk", "sk", "sc", "cf", "ac", "as", "olympique", "de", "the", "club"}
    tokens = [t for t in cleaned.split() if t not in stop_words]
    return " ".join(tokens)


def _teams_match(name_a: str, name_b: str) -> bool:
    """Vérifie si deux noms d'équipes désignent le même club."""
    na, nb = _normalize_name(name_a), _normalize_name(name_b)
    if not na or not nb:
        return False
    return na in nb or nb in na or bool(set(na.split()) & set(nb.split()))


def is_eligible_competition(league_name: str) -> bool:
    """Filtre les compétitions amateurs, jeunes ou amicales instables."""
    low = (league_name or "").lower()
    if not low:
        return False
    return not any(bad in low for bad in EXCLUDED_LEAGUE_KEYWORDS)


def discover_active_odds_sports(api_key: str | None = None) -> list[dict[str, Any]]:
    """
    Interroge /v4/sports/ (gratuit, 0 quota consommé) pour récupérer toutes
    les compétitions de football actives sur The Odds API.
    """
    global _DYNAMIC_ODDS_SPORTS_CACHE
    if _DYNAMIC_ODDS_SPORTS_CACHE is not None:
        return _DYNAMIC_ODDS_SPORTS_CACHE

    key = api_key or os.getenv("ODDS_API_KEY", "")
    if not key:
        return []

    try:
        resp = requests.get(f"{ODDS_API_BASE}/", params={"apiKey": key}, timeout=10)
        if resp.ok:
            soccer_sports = [
                s for s in resp.json()
                if str(s.get("key", "")).startswith("soccer_") and s.get("active", True)
            ]
            _DYNAMIC_ODDS_SPORTS_CACHE = soccer_sports
            return soccer_sports
    except Exception as exc:
        logger.warning("Impossible de charger la liste dynamique des ligues Odds API: %s", exc)

    return []


def resolve_league_config(league_name: str, country_name: str = "") -> dict[str, str]:
    """
    Résout dynamiquement le slug ESPN et la clé Odds API pour TOUTES les compétitions :
    1. Correspondance exacte ou partielle dans LEAGUE_CONFIG (45+ compétitions)
    2. Recherche dynamique dans le catalogue actif The Odds API (/v4/sports/)
    """
    clean_league = (league_name or "").strip()
    if clean_league in LEAGUE_CONFIG:
        return LEAGUE_CONFIG[clean_league]

    low = clean_league.lower()
    for key, cfg in LEAGUE_CONFIG.items():
        if key.lower() == low or key.lower() in low or low in key.lower():
            return cfg

    # Recherche dynamique dans les sports actifs The Odds API
    active_sports = discover_active_odds_sports()
    norm_target = _normalize_name(f"{country_name} {clean_league}".strip())
    target_tokens = set(norm_target.split())

    best_sport_key = ""
    best_overlap = 0
    for sp in active_sports:
        sp_title = _normalize_name(f"{sp.get('group', '')} {sp.get('title', '')}")
        sp_tokens = set(sp_title.split())
        overlap = len(target_tokens & sp_tokens)
        if overlap > best_overlap and overlap >= 1:
            best_overlap = overlap
            best_sport_key = sp.get("key", "")

    return {
        "espn": "",
        "odds": best_sport_key,
    }


# ==============================================================================
# 1. VÉRIFICATION PRÉ-MATCH VIA ESPN (SANS API-FOOTBALL)
# ==============================================================================

def fetch_espn_prematch_context(
    slug: str,
    date_yyyymmdd: str,
    event_id: str | None = None,
    home_team: str = "",
    away_team: str = "",
) -> dict[str, Any]:
    """
    Récupère stade réel, terrain neutre (neutralSite), classement, forme récente,
    blessures et cotes de clôture ESPN. Si `slug` est vide (compétition secondaire),
    retourne un contexte neutre valide pour ne pas bloquer l'analyse Bzzoiro + Odds API.
    """
    context: dict[str, Any] = {
        "espn_event_id": event_id,
        "venue_name": "Stade officiel",
        "venue_city": "Domicile",
        "neutral_venue": False,
        "home_rank": "N/A",
        "away_rank": "N/A",
        "home_form": "",
        "away_form": "",
        "injuries": [],
        "espn_closing_odds": {},
    }

    if not slug:
        return context

    home_id: str | None = None
    away_id: str | None = None

    try:
        sb_url = f"{ESPN_BASE}/site/v2/sports/soccer/{slug}/scoreboard?dates={date_yyyymmdd}"
        resp = requests.get(sb_url, timeout=10)
        resp.raise_for_status()
        sb_data = resp.json()

        for event in sb_data.get("events", []):
            ev_id = str(event.get("id", ""))
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])

            ev_home = next((c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "home"), "")
            ev_away = next((c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "away"), "")

            id_matches = bool(event_id and ev_id == str(event_id))
            name_matches = bool(home_team and away_team and _teams_match(home_team, ev_home) and _teams_match(away_team, ev_away))

            if not (id_matches or name_matches):
                continue

            context["espn_event_id"] = ev_id
            venue = comp.get("venue", {})
            context["neutral_venue"] = bool(comp.get("neutralSite", False))
            context["venue_name"] = venue.get("fullName", "Stade officiel")
            context["venue_city"] = venue.get("address", {}).get("city", "Domicile")

            for c in competitors:
                if c.get("homeAway") == "home":
                    home_id = str(c.get("team", {}).get("id", ""))
                    context["home_form"] = c.get("form", "")
                elif c.get("homeAway") == "away":
                    away_id = str(c.get("team", {}).get("id", ""))
                    context["away_form"] = c.get("form", "")

            odds_list = comp.get("odds", [])
            if odds_list:
                context["espn_closing_odds"] = odds_list[0]
            break
    except Exception as exc:
        logger.warning("Erreur ESPN Scoreboard (%s): %s", slug, exc)

    # Classement exact (/standings)
    if home_id or away_id:
        try:
            st_url = f"{ESPN_BASE}/v2/sports/soccer/{slug}/standings"
            st_res = requests.get(st_url, timeout=10)
            if st_res.ok:
                for child in st_res.json().get("children", []):
                    for entry in child.get("standings", {}).get("entries", []):
                        tid = str(entry.get("team", {}).get("id", ""))
                        stats = {s["name"]: s.get("displayValue") for s in entry.get("stats", [])}
                        if tid == home_id:
                            context["home_rank"] = stats.get("rank", "N/A")
                        elif tid == away_id:
                            context["away_rank"] = stats.get("rank", "N/A")
        except Exception as exc:
            logger.warning("Erreur ESPN Standings (%s): %s", slug, exc)

    # Blessures & absents (/summary)
    resolved_id = context.get("espn_event_id")
    if resolved_id:
        try:
            sum_url = f"{ESPN_BASE}/site/v2/sports/soccer/{slug}/summary?event={resolved_id}"
            sum_res = requests.get(sum_url, timeout=10)
            if sum_res.ok:
                for team_inj in sum_res.json().get("injuries", []):
                    t_name = team_inj.get("team", {}).get("displayName", "")
                    for item in team_inj.get("injuries", []):
                        athlete = item.get("athlete", {}).get("displayName", "")
                        status = item.get("status", "")
                        if athlete:
                            context["injuries"].append(f"{athlete} ({t_name} - {status})")
        except Exception as exc:
            logger.warning("Erreur ESPN Summary (%s): %s", resolved_id, exc)

    return context


# ==============================================================================
# 2. RÉCUPÉRATION xG (BZZOIRO) & COTES MARCHÉ (THE ODDS API)
# ==============================================================================

def fetch_bzzoiro_matches(date_iso: str, api_key: str | None = None) -> list[dict[str, Any]]:
    """
    Récupère les matchs éligibles du jour sur toutes les compétitions depuis Bzzoiro.
    Format date_iso : 'YYYY-MM-DD'
    """
    headers = {}
    key = api_key or os.getenv("BZZOIRO_API_KEY", "")
    if key:
        headers["Authorization"] = f"Token {key}"

    try:
        resp = requests.get(
            BZZOIRO_EVENTS_URL,
            params={"date": date_iso},
            headers=headers,
            timeout=12,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw_events = payload.get("results", payload) if isinstance(payload, dict) else payload

        matches: list[dict[str, Any]] = []
        for ev in raw_events:
            home = ev.get("home_team") or ev.get("home", {}).get("name", "")
            away = ev.get("away_team") or ev.get("away", {}).get("name", "")
            league_obj = ev.get("league") if isinstance(ev.get("league"), dict) else {}
            league = ev.get("league_name") or league_obj.get("name", "Football")
            country = ev.get("country") or league_obj.get("country", "")

            if not (home and away) or not is_eligible_competition(league):
                continue

            home_xg = float(ev.get("home_xg") or ev.get("xg_home") or 1.25)
            away_xg = float(ev.get("away_xg") or ev.get("xg_away") or 1.05)

            matches.append(
                {
                    "bzzoiro_id": ev.get("id"),
                    "home_team": home,
                    "away_team": away,
                    "league": league,
                    "country": country,
                    "kickoff": ev.get("start_time") or ev.get("date", date_iso),
                    "home_xg": home_xg,
                    "away_xg": away_xg,
                }
            )
        return matches
    except Exception as exc:
        logger.error("Erreur récupération Bzzoiro (%s): %s", date_iso, exc)
        return []


def fetch_market_odds(sport_key: str, api_key: str | None = None) -> list[dict[str, Any]]:
    """Récupère les cotes h2h (1X2) et totals (Over/Under) via The Odds API."""
    if not sport_key:
        return []
    key = api_key or os.getenv("ODDS_API_KEY", "")
    if not key:
        return []

    try:
        url = f"{ODDS_API_BASE}/{sport_key}/odds/"
        params = {
            "apiKey": key,
            "regions": "eu",
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        }
        resp = requests.get(url, params=params, timeout=12)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Erreur The Odds API (%s): %s", sport_key, exc)
        return []


def extract_odds_for_match(
    home_team: str,
    away_team: str,
    odds_events: list[dict[str, Any]],
) -> dict[str, tuple[float, list[float]]]:
    """
    Extrait les cotes moyennes par marché pour un match donné :
    retourne {market_key: (target_odd, all_market_odds)}.
    """
    extracted: dict[str, tuple[float, list[float]]] = {}
    for ev in odds_events:
        ev_home = ev.get("home_team", "")
        ev_away = ev.get("away_team", "")
        if not (_teams_match(home_team, ev_home) and _teams_match(away_team, ev_away)):
            continue

        h2h_1, h2h_x, h2h_2 = [], [], []
        ou_15_over, ou_15_under = [], []
        ou_25_over, ou_25_under = [], []

        for bk in ev.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                mkey = mkt.get("key")
                outcomes = mkt.get("outcomes", [])
                if mkey == "h2h":
                    for o in outcomes:
                        name = o.get("name", "")
                        price = float(o.get("price", 0))
                        if price <= 1.0:
                            continue
                        if name == ev_home:
                            h2h_1.append(price)
                        elif name == ev_away:
                            h2h_2.append(price)
                        elif name.lower() == "draw":
                            h2h_x.append(price)
                elif mkey == "totals":
                    for o in outcomes:
                        point = float(o.get("point", 0))
                        price = float(o.get("price", 0))
                        side = o.get("name", "").lower()
                        if price <= 1.0:
                            continue
                        if point == 1.5:
                            if side == "over":
                                ou_15_over.append(price)
                            elif side == "under":
                                ou_15_under.append(price)
                        elif point == 2.5:
                            if side == "over":
                                ou_25_over.append(price)
                            elif side == "under":
                                ou_25_under.append(price)

        if h2h_1 and h2h_x and h2h_2:
            avg_1 = round(sum(h2h_1) / len(h2h_1), 2)
            avg_x = round(sum(h2h_x) / len(h2h_x), 2)
            avg_2 = round(sum(h2h_2) / len(h2h_2), 2)
            all_1x2 = [avg_1, avg_x, avg_2]
            extracted["1"] = (avg_1, all_1x2)
            extracted["X"] = (avg_x, all_1x2)
            extracted["2"] = (avg_2, all_1x2)

        if ou_15_over and ou_15_under:
            avg_o15 = round(sum(ou_15_over) / len(ou_15_over), 2)
            avg_u15 = round(sum(ou_15_under) / len(ou_15_under), 2)
            extracted["OVER_1.5"] = (avg_o15, [avg_o15, avg_u15])

        if ou_25_over and ou_25_under:
            avg_o25 = round(sum(ou_25_over) / len(ou_25_over), 2)
            avg_u25 = round(sum(ou_25_under) / len(ou_25_under), 2)
            extracted["OVER_2.5"] = (avg_o25, [avg_o25, avg_u25])
            extracted["UNDER_2.5"] = (avg_u25, [avg_o25, avg_u25])
        break

    return extracted


# ==============================================================================
# 3. MODÈLE DIXON-COLES CALIBRÉ & BOUCLIER ANTI-RISQUE
# ==============================================================================

def dixon_coles_probabilities(
    home_xg: float,
    away_xg: float,
    neutral_venue: bool = False,
    rho: float = -0.11,
    max_goals: int = 8,
) -> dict[str, float]:
    """
    Calcule les probabilités 1X2, Over/Under 1.5 et Over/Under 2.5 via Dixon-Coles.
    Neutralise automatiquement l'avantage domicile (gamma = 1.00) sur terrain neutre.
    """
    home_adv = 1.00 if neutral_venue else 1.12
    lam = max(0.20, home_xg * home_adv)
    mu = max(0.20, away_xg)

    prob_1, prob_x, prob_2 = 0.0, 0.0, 0.0
    prob_over_15, prob_under_15 = 0.0, 0.0
    prob_under_25, prob_over_25 = 0.0, 0.0

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

            p_score = max(0.0, p_h * p_a * tau)
            total_goals = h + a

            if h > a:
                prob_1 += p_score
            elif h == a:
                prob_x += p_score
            else:
                prob_2 += p_score

            if total_goals >= 2:
                prob_over_15 += p_score
            else:
                prob_under_15 += p_score

            if total_goals <= 2:
                prob_under_25 += p_score
            else:
                prob_over_25 += p_score

    total = prob_1 + prob_x + prob_2
    return {
        "1": prob_1 / total,
        "X": prob_x / total,
        "2": prob_2 / total,
        "OVER_1.5": prob_over_15 / total,
        "UNDER_1.5": prob_under_15 / total,
        "UNDER_2.5": prob_under_25 / total,
        "OVER_2.5": prob_over_25 / total,
    }


def evaluate_selection_tiered(
    match: dict[str, Any],
    espn_ctx: dict[str, Any],
    market_key: str,
    target_odd: float,
    all_market_odds: list[float],
) -> dict[str, Any] | None:
    """
    Filtre anti-risque multi-compétitions avec lissage bayésien (30% Dixon-Coles / 70% marché)
    et classement par niveau de confiance ('VIP_VALUE' ou 'SAFE_BACKUP').
    """
    if not all_market_odds or target_odd <= 1.01:
        return None

    total_xg = match["home_xg"] + match["away_xg"]

    # 1. Plage de cotes & conditions structurelles par marché
    if market_key == "OVER_1.5":
        if not (1.28 <= target_odd <= 1.50):
            return None
        if total_xg < 2.75 or match["home_xg"] < 0.85 or match["away_xg"] < 0.85:
            return None
    else:
        if not (1.35 <= target_odd <= 1.90):
            return None

    # 2. Filtre blessures ciblé sur l'équipe jouée (max 3 absents)
    if market_key in ("1", "2"):
        target_team = match["home_team"] if market_key == "1" else match["away_team"]
        team_injuries = [
            inj for inj in espn_ctx.get("injuries", []) if _teams_match(target_team, inj)
        ]
        if len(team_injuries) >= 4:
            return None

    # 3. Verrou anti-faux Under 2.5 (bloque les matchs ouverts ou équipes dos au mur > 24e)
    if market_key == "UNDER_2.5":
        if total_xg > 2.55:
            return None
        home_rank = int(espn_ctx["home_rank"]) if str(espn_ctx.get("home_rank")).isdigit() else 10
        away_rank = int(espn_ctx["away_rank"]) if str(espn_ctx.get("away_rank")).isdigit() else 10
        if home_rank > 24 or away_rank > 24:
            return None

    # 4. Probabilité Dixon-Coles corrigée terrain neutre
    dc_probs = dixon_coles_probabilities(
        home_xg=match["home_xg"],
        away_xg=match["away_xg"],
        neutral_venue=espn_ctx.get("neutral_venue", False),
    )
    if market_key not in dc_probs:
        return None
    raw_model_prob = dc_probs[market_key]

    # 5. De-vigging + Lissage bayésien (30% modèle / 70% marché)
    overround = sum(1.0 / o for o in all_market_odds if o > 1.0)
    if overround <= 0:
        return None
    true_market_prob = (1.0 / target_odd) / overround
    calibrated_prob = (0.30 * raw_model_prob) + (0.70 * true_market_prob)

    edge = calibrated_prob - true_market_prob
    ev = calibrated_prob * target_odd

    # 6. Plafonds de sécurité : rejette tout edge > 11% ou EV irréaliste (> 1.18)
    if edge > 0.11 or ev > 1.18 or ev < 0.95:
        return None

    tier = "VIP_VALUE" if ev >= 1.02 else "SAFE_BACKUP"
    family = "GOALS" if ("OVER" in market_key or "UNDER" in market_key) else "RESULT"

    ranks_label = (
        f"{espn_ctx['home_rank']}e vs {espn_ctx['away_rank']}e"
        if str(espn_ctx.get("home_rank")).isdigit() and str(espn_ctx.get("away_rank")).isdigit()
        else "Coupe / Phase directe"
    )

    return {
        "match_name": f"{match['home_team']} vs {match['away_team']}",
        "home_team": match["home_team"],
        "away_team": match["away_team"],
        "league": match.get("league", "Football"),
        "kickoff": match.get("kickoff", ""),
        "market": market_key,
        "family": family,
        "odd": round(target_odd, 2),
        "calibrated_prob": round(calibrated_prob * 100, 1),
        "market_prob": round(true_market_prob * 100, 1),
        "ev": round(ev, 2),
        "tier": tier,
        "home_xg": round(match["home_xg"], 2),
        "away_xg": round(match["away_xg"], 2),
        "venue": f"{espn_ctx['venue_name']} ({espn_ctx['venue_city']})",
        "neutral_venue": espn_ctx.get("neutral_venue", False),
        "ranks": ranks_label,
        "injuries": espn_ctx.get("injuries", []),
    }


def build_daily_odds3_combo(valid_picks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Assemble le combiné quotidien ODDS +3 (cote 2.55 à 3.40) sur toutes les compétitions :
    - Priorité 1 : Duo Haute Confiance (2 sélections)
    - Priorité 2 : Trio Sécurité (3 sélections incluant au max 1 pari BUTS)
    """
    unique_by_match: dict[str, dict[str, Any]] = {}
    for p in sorted(
        valid_picks,
        key=lambda x: (x["tier"] == "VIP_VALUE", x["calibrated_prob"]),
        reverse=True,
    ):
        if p["match_name"] not in unique_by_match:
            unique_by_match[p["match_name"]] = p

    pool = list(unique_by_match.values())

    # Tentative 1 : Duo (2 sélections)
    for p1, p2 in combinations(pool, 2):
        if sum(1 for x in (p1, p2) if x["family"] == "GOALS") > 1:
            continue
        combo_odd = round(p1["odd"] * p2["odd"], 2)
        if 2.55 <= combo_odd <= 3.35:
            combo_prob = round((p1["calibrated_prob"] / 100) * (p2["calibrated_prob"] / 100) * 100, 1)
            return {
                "legs": [p1, p2],
                "combo_odd": combo_odd,
                "combo_prob": combo_prob,
                "combo_ev": round((combo_prob / 100) * combo_odd, 2),
                "mode": "DUO HAUTE CONFIANCE",
            }

    # Tentative 2 : Trio Sécurité (3 sélections)
    for p1, p2, p3 in combinations(pool, 3):
        if sum(1 for x in (p1, p2, p3) if x["family"] == "GOALS") > 1:
            continue
        combo_odd = round(p1["odd"] * p2["odd"] * p3["odd"], 2)
        if 2.60 <= combo_odd <= 3.40:
            combo_prob = round(
                (p1["calibrated_prob"] / 100)
                * (p2["calibrated_prob"] / 100)
                * (p3["calibrated_prob"] / 100)
                * 100,
                1,
            )
            return {
                "legs": [p1, p2, p3],
                "combo_odd": combo_odd,
                "combo_prob": combo_prob,
                "combo_ev": round((combo_prob / 100) * combo_odd, 2),
                "mode": "TRIO SÉCURITÉ",
            }

    return None


# ==============================================================================
# 4. ANALYSE IA GEMINI VERROUILLÉE SUR LE MARCHÉ & FORMATAGE TELEGRAM
# ==============================================================================

MARKET_LABELS: dict[str, str] = {
    "1": "Victoire Domicile (1)",
    "X": "Match Nul (X)",
    "2": "Victoire Extérieur (2)",
    "OVER_1.5": "Plus de 1.5 buts",
    "UNDER_1.5": "Moins de 1.5 buts",
    "OVER_2.5": "Plus de 2.5 buts",
    "UNDER_2.5": "Moins de 2.5 buts",
}


def generate_gemini_analysis(pick: dict[str, Any], api_key: str | None = None) -> str:
    """
    Génère un commentaire analytique court (3 phrases max) strictement aligné
    sur le marché retenu, le stade réel et le classement vérifié sur ESPN.
    """
    market_rules = {
        "1": f"Justifie uniquement l'avantage de {pick['home_team']} (efficacité offensive, xG, dynamique).",
        "2": f"Justifie uniquement la capacité de {pick['away_team']} à s'imposer à l'extérieur.",
        "OVER_1.5": "Justifie uniquement un match avec au moins 2 buts attendus (xG cumulés élevés et double menace offensive).",
        "OVER_2.5": "Justifie uniquement un match ouvert avec des occasions franches des deux côtés.",
        "UNDER_2.5": "Justifie uniquement un match tactique fermé à faible score. INTERDICTION d'évoquer un festival offensif.",
    }

    prompt = f"""
Tu es l'analyste quantitatif du canal VIP VOLATILITY INDEX. Rédige une analyse percutante de 3 phrases maximum en français.

RÈGLES ABSOLUES :
1. Utilise UNIQUEMENT les faits ci-dessous. N'invente jamais un classement ou un stade.
2. {market_rules.get(pick['market'], 'Justifie strictement le pari sélectionné.')}
3. Si Terrain neutre = True, précise explicitement que la rencontre est délocalisée à {pick['venue']} sans avantage du public.

DONNÉES VÉRIFIÉES :
- Match : {pick['match_name']} ({pick['league']})
- Contexte / Classement : {pick['ranks']}
- Stade : {pick['venue']} | Terrain neutre : {pick['neutral_venue']}
- Pari : {MARKET_LABELS.get(pick['market'], pick['market'])} @ {pick['odd']}
- Probabilité calibrée : {pick['calibrated_prob']}% (Marché : {pick['market_prob']}%)
- xG Bzzoiro : {pick['home_team']} {pick['home_xg']} vs {pick['away_team']} {pick['away_xg']}
""".strip()

    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 220},
            }
            res = requests.post(url, json=body, timeout=15)
            if res.ok:
                candidates = res.json().get("candidates", [])
                if candidates:
                    text = candidates[0]["content"]["parts"][0]["text"].strip()
                    if text:
                        return text
        except Exception as exc:
            logger.warning("Fallback commentaire local (Gemini indisponible): %s", exc)

    neutral_note = f" (match délocalisé à {pick['venue']})" if pick.get("neutral_venue") else ""
    return (
        f"Les indicateurs Bzzoiro ({pick['home_xg']} vs {pick['away_xg']} xG) et le contexte sportif ({pick['ranks']}) "
        f"valident l'option {MARKET_LABELS.get(pick['market'], pick['market'])}{neutral_note}. "
        f"Avec une probabilité calibrée de {pick['calibrated_prob']}% face à une cote de {pick['odd']}, "
        f"la sélection présente une valeur statistique maîtrisée (EV {pick['ev']})."
    )


def format_telegram_vip_ticket(combo: dict[str, Any], date_label: str) -> str:
    """Formate le message VIP Telegram VOLATILITY INDEX prêt à publier."""
    legs = combo["legs"]
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "     ⚡ VOLATILITY INDEX ⚡",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "Analyse IA • VIP",
        f"📅 {date_label}",
        "🟢 Mode : MULTI-COMPÉTITIONS CALIBRÉ",
        "",
        "🛡️ ODDS +3 AUTH",
        f"Sélection Pro VIP • {combo['mode']} • {len(legs)} sélections",
        "",
        "┏━━━━━━━━━━━━━━━┓",
        f"┃ 🎫 Cote : {combo['combo_odd']}",
        f"┃ 📈 Réussite est. : {combo['combo_prob']}%",
        f"┃ 💎 EV : {combo['combo_ev']}",
        "┗━━━━━━━━━━━━━━━┛",
        "",
    ]

    for idx, leg in enumerate(legs, start=1):
        dots = "🟢🟢🟢" if leg["calibrated_prob"] >= 68 else "🟢🟢⚪"
        label = MARKET_LABELS.get(leg["market"], leg["market"])
        if leg["market"] == "1":
            label = f"Victoire {leg['home_team']} (1)"
        elif leg["market"] == "2":
            label = f"Victoire {leg['away_team']} (2)"

        commentary = leg.get("commentary") or generate_gemini_analysis(leg)
        lines.extend(
            [
                f"┌─ #{idx} {leg['match_name']}",
                f"│ 📅 {leg.get('kickoff', date_label)} • {leg['league']}",
                f"│ 🎯 {label}",
                f"│ 📊 Confiance : {leg['calibrated_prob']}% {dots}",
                f"│ 💰 Cote : {leg['odd']}",
                f"│ 🧠 {commentary}",
                "└─────────────",
                "",
            ]
        )

    lines.append("⚠️ Analyse statistique calibrée (ESPN + Bzzoiro). Gestion de bankroll recommandée (1-3% par ticket).")
    return "\n".join(lines)
