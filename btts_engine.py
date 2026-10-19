"""
MOTEUR SPÉCIALISÉ BBTS+2.5
============================
Analyse les 10 derniers matchs réels de chaque équipe (via football-data.co.uk).
Filtre uniquement les matchs où les 2 équipes marquent ET encaissent régulièrement.
Aucune dépendance à Bzzoiro ou API-Football.

Utilisation :
    from btts_engine import load_btts_engine
    engine = await load_btts_engine()
    if engine and engine.is_ready():
        analysis = engine.analyze_match("Bayern Munich", "Hoffenheim")
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import datetime
from typing import Optional

import httpx

# 22 championnats couverts (saisons 25/26 + 26/27 pour plus de données)
LEAGUES = {
    "EPL": "https://www.football-data.co.uk/mmz4281/2627/E0.csv",
    "EPL_25": "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
    "Championship": "https://www.football-data.co.uk/mmz4281/2627/E1.csv",
    "Championship_25": "https://www.football-data.co.uk/mmz4281/2526/E1.csv",
    "LeagueOne": "https://www.football-data.co.uk/mmz4281/2627/E2.csv",
    "LaLiga": "https://www.football-data.co.uk/mmz4281/2627/SP1.csv",
    "LaLiga_25": "https://www.football-data.co.uk/mmz4281/2526/SP1.csv",
    "Segunda": "https://www.football-data.co.uk/mmz4281/2627/SP2.csv",
    "SerieA": "https://www.football-data.co.uk/mmz4281/2627/I1.csv",
    "SerieA_25": "https://www.football-data.co.uk/mmz4281/2526/I1.csv",
    "SerieB": "https://www.football-data.co.uk/mmz4281/2627/I2.csv",
    "Bundesliga": "https://www.football-data.co.uk/mmz4281/2627/D1.csv",
    "Bundesliga_25": "https://www.football-data.co.uk/mmz4281/2526/D1.csv",
    "Bundesliga2": "https://www.football-data.co.uk/mmz4281/2627/D2.csv",
    "Ligue1": "https://www.football-data.co.uk/mmz4281/2627/F1.csv",
    "Ligue1_25": "https://www.football-data.co.uk/mmz4281/2526/F1.csv",
    "Ligue2": "https://www.football-data.co.uk/mmz4281/2627/F2.csv",
    "Eredivisie": "https://www.football-data.co.uk/mmz4281/2627/N1.csv",
    "Eredivisie_25": "https://www.football-data.co.uk/mmz4281/2526/N1.csv",
    "Primeira": "https://www.football-data.co.uk/mmz4281/2627/P1.csv",
    "Belgium": "https://www.football-data.co.uk/mmz4281/2627/B1.csv",
    "SuperLig": "https://www.football-data.co.uk/mmz4281/2627/T1.csv",
    "Greece": "https://www.football-data.co.uk/mmz4281/2627/G1.csv",
    "Scotland": "https://www.football-data.co.uk/mmz4281/2627/SC0.csv",
}

IGNORE_WORDS = {
    "fc", "cf", "sc", "ac", "afc", "as", "ss", "ssc", "rc", "cd", "fk",
    "sk", "jk", "sd", "ud", "ca", "cs", "us", "sv", "bv", "bsc", "fsv",
    "vfb", "vfl", "tsg", "spvgg", "club", "de", "the", "and", "of",
}

TEAM_ALIASES = {
    "manchester city": "man city", "manchester united": "man united",
    "man utd": "man united", "newcastle united": "newcastle",
    "tottenham hotspur": "tottenham", "brighton hove albion": "brighton",
    "brighton & hove albion": "brighton", "nottingham forest": "nott'm forest",
    "nottm forest": "nott'm forest", "wolverhampton wanderers": "wolves",
    "west ham united": "west ham", "atletico madrid": "ath madrid",
    "atletico de madrid": "ath madrid", "athletic bilbao": "ath bilbao",
    "real betis": "betis", "celta vigo": "celta", "real sociedad": "sociedad",
    "deportivo alaves": "alaves", "rayo vallecano": "vallecano",
    "fc barcelona": "barcelona", "bayern munich": "bayern munich",
    "bayern munchen": "bayern munich", "fc bayern": "bayern munich",
    "borussia dortmund": "dortmund", "bvb dortmund": "dortmund",
    "bayer leverkusen": "leverkusen", "bayer 04 leverkusen": "leverkusen",
    "eintracht frankfurt": "eintracht", "rb leipzig": "rb leipzig",
    "rasenballsport leipzig": "rb leipzig", "vfb stuttgart": "stuttgart",
    "sc freiburg": "freiburg", "mainz 05": "mainz", "1. fsv mainz 05": "mainz",
    "vfl wolfsburg": "wolfsburg", "fc augsburg": "augsburg",
    "werder bremen": "werder", "sv werder bremen": "werder",
    "union berlin": "union berlin", "1. fc union berlin": "union berlin",
    "vfl bochum": "bochum", "tsg hoffenheim": "hoffenheim",
    "inter milan": "inter", "internazionale": "inter", "ac milan": "milan",
    "as roma": "roma", "ss lazio": "lazio", "ssc napoli": "napoli",
    "juventus fc": "juventus", "atalanta bc": "atalanta",
    "acf fiorentina": "fiorentina", "torino fc": "torino",
    "paris saint germain": "paris sg", "psg": "paris sg",
    "olympique de marseille": "marseille", "olympique marseille": "marseille",
    "olympique lyonnais": "lyon", "as monaco": "monaco",
    "as saint-etienne": "saint-etienne", "losc lille": "lille",
    "lille osc": "lille", "stade rennais": "rennes", "rc lens": "lens",
    "rc strasbourg": "strasbourg", "fc nantes": "nantes",
    "ajax amsterdam": "ajax", "afc ajax": "ajax", "psv eindhoven": "psv",
    "feyenoord rotterdam": "feyenoord", "az alkmaar": "az alkmaar",
    "fc utrecht": "utrecht", "fc twente": "twente",
    "fc porto": "porto", "sl benfica": "benfica", "sporting cp": "sp lisbon",
    "sporting lisbon": "sp lisbon", "sc braga": "braga",
    "galatasaray sk": "galatasaray", "fenerbahce sk": "fenerbahce",
    "besiktas jk": "besiktas", "olympiacos": "olympiacos",
    "panathinaikos": "panathinaikos", "aek athens": "aek",
    "paok thessaloniki": "paok", "celtic fc": "celtic", "rangers fc": "rangers",
    "sparta rotterdam": "sparta rotterdam", "go ahead eagles": "go ahead eagles",
    "nec nijmegen": "nec", "fortuna sittard": "fortuna sittard",
    "heracles": "heracles", "pec zwolle": "zwolle", "willem ii": "willem ii",
}


def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    words = [w for w in s.split() if w not in IGNORE_WORDS and len(w) > 1]
    return " ".join(words).strip() if words else " ".join(s.split()).strip()


def _parse_date(d: str) -> Optional[datetime]:
    if not d:
        return None
    for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(d.strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


class BTTSEngine:
    def __init__(self):
        self.team_matches: dict = {}
        self.display_names: dict = {}
        self._loaded = False

    def add_match(self, home: str, away: str, hg: int, ag: int, date_str: str):
        for team, gf, ga in [(home, hg, ag), (away, ag, hg)]:
            n = _norm(team)
            if not n:
                continue
            if n not in self.team_matches:
                self.team_matches[n] = []
                self.display_names[n] = team
            self.team_matches[n].append({
                "date": date_str,
                "date_parsed": _parse_date(date_str),
                "gf": gf,
                "ga": ga,
                "scored": 1 if gf >= 1 else 0,
                "conceded": 1 if ga >= 1 else 0,
                "btts": 1 if (gf >= 1 and ga >= 1) else 0,
                "over25": 1 if (gf + ga) >= 3 else 0,
                "btts_over25": 1 if (gf >= 1 and ga >= 1 and (gf + ga) >= 3) else 0,
            })

    def _find_team(self, name: str) -> Optional[str]:
        if not name:
            return None
        n = _norm(name)
        if not n:
            return None

        alias = TEAM_ALIASES.get(str(name).lower().strip())
        if alias:
            n_alias = _norm(alias)
            if n_alias in self.team_matches:
                return n_alias

        if n in self.team_matches:
            return n

        for key in self.team_matches:
            if n in key or key in n:
                if min(len(n), len(key)) >= 5:
                    return key

        best_key, best_score = None, 0.0
        tokens_n = set(n.split())
        for key in self.team_matches:
            tokens_k = set(key.split())
            if not tokens_k:
                continue
            inter = tokens_n & tokens_k
            union = tokens_n | tokens_k
            score = len(inter) / len(union) if union else 0.0
            if score > best_score:
                best_score = score
                best_key = key
        if best_key and best_score >= 0.60:
            return best_key

        return None

    def get_team_stats(self, name: str, last_n: int = 10) -> Optional[dict]:
        key = self._find_team(name)
        if not key:
            return None
        matches = self.team_matches.get(key, [])
        if not matches:
            return None

        dated = [m for m in matches if m["date_parsed"] is not None]
        undated = [m for m in matches if m["date_parsed"] is None]
        dated.sort(key=lambda m: m["date_parsed"], reverse=True)
        ordered = dated + undated
        recent = ordered[:last_n]

        if len(recent) < 5:
            return None

        n = len(recent)
        return {
            "team_name": self.display_names.get(key, name),
            "matches_analyzed": n,
            "scored_pct": sum(m["scored"] for m in recent) / n,
            "conceded_pct": sum(m["conceded"] for m in recent) / n,
            "btts_pct": sum(m["btts"] for m in recent) / n,
            "over25_pct": sum(m["over25"] for m in recent) / n,
            "btts_over25_pct": sum(m["btts_over25"] for m in recent) / n,
            "goals_for_avg": round(sum(m["gf"] for m in recent) / n, 2),
            "goals_against_avg": round(sum(m["ga"] for m in recent) / n, 2),
            "total_goals_avg": round(sum(m["gf"] + m["ga"] for m in recent) / n, 2),
        }

    def analyze_match(self, home: str, away: str) -> Optional[dict]:
        home_stats = self.get_team_stats(home, 10)
        away_stats = self.get_team_stats(away, 10)
        if not home_stats or not away_stats:
            return None

        base_prob = (home_stats["btts_over25_pct"] + away_stats["btts_over25_pct"]) / 2.0

        if home_stats["goals_for_avg"] >= 1.5 and away_stats["goals_for_avg"] >= 1.5:
            base_prob += 0.04
        if home_stats["goals_against_avg"] >= 1.3 and away_stats["goals_against_avg"] >= 1.3:
            base_prob += 0.04

        avg_total = (home_stats["total_goals_avg"] + away_stats["total_goals_avg"]) / 2.0
        if avg_total >= 3.2:
            base_prob += 0.05
        elif avg_total >= 3.0:
            base_prob += 0.03

        final_prob = min(0.85, round(base_prob, 4))

        return {
            "home_stats": home_stats,
            "away_stats": away_stats,
            "probability": final_prob,
            "avg_total_goals": round(avg_total, 2),
        }

    def is_ready(self) -> bool:
        return self._loaded and len(self.team_matches) >= 20


async def load_btts_engine() -> Optional[BTTSEngine]:
    """
    Télécharge tous les CSVs football-data.co.uk et construit le moteur.
    """
    engine = BTTSEngine()
    total_matches = 0

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for league, url in LEAGUES.items():
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    print(f"  ⚠️ BTTS Engine {league}: HTTP {r.status_code}")
                    continue
                rows = list(csv.DictReader(io.StringIO(r.text)))
                added = 0
                for row in rows:
                    try:
                        hg = int(row.get("FTHG", ""))
                        ag = int(row.get("FTAG", ""))
                    except (ValueError, TypeError):
                        continue
                    h = row.get("HomeTeam", "").strip()
                    a = row.get("AwayTeam", "").strip()
                    d = row.get("Date", "").strip()
                    if not h or not a:
                        continue
                    engine.add_match(h, a, hg, ag, d)
                    added += 1
                if added > 0:
                    print(f"  📊 BTTS Engine {league}: {added} matchs")
                    total_matches += added
            except Exception as e:
                print(f"  ⚠️ BTTS Engine {league} crash: {e}")

    if total_matches < 500:
        print(f"❌ BTTS Engine : seulement {total_matches} matchs chargés, insuffisant")
        return None

    engine._loaded = True
    print(f"✅ BTTS Engine actif : {total_matches} matchs, {len(engine.team_matches)} équipes")
    return engine
