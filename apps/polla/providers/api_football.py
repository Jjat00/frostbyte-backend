"""Adaptador de API-Football (api-sports.io) para el Mundial 2026.

Se activa solo si ``API_FOOTBALL_KEY`` esta configurada. Mapea fixtures,
posiciones y goleadores al formato normalizado de ``base.py``.

Mapeo de estados de API-Football -> estado interno:
  - NS, TBD               -> "upcoming"
  - 1H, HT, 2H, ET, P, LIVE, BT, SUSP, INT -> "live"
  - FT, AET, PEN          -> "finished"

Nota: ``requests`` se importa de forma perezosa para que la app cargue aunque
la dependencia no este instalada cuando no se usa el proveedor.
"""
from __future__ import annotations

import logging

from .base import FixtureMeta, FixtureUpdate, MatchProvider, ScorerRow, StandingRow

logger = logging.getLogger(__name__)

_LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
_FINISHED = {"FT", "AET", "PEN"}


class APIFootballProvider(MatchProvider):
    available = True

    def __init__(self, api_key, league_id=1, season=2026,
                 base_url="https://v3.football.api-sports.io", timeout=15):
        self.api_key = api_key
        self.league_id = league_id
        self.season = season
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Cache por instancia de la respuesta de /fixtures: dentro de un mismo
        # sync, fetch_fixtures() y fetch_fixture_index() comparten una sola
        # llamada HTTP (no gasta el doble de cuota de la API).
        self._fixtures_cache = None

    # -- HTTP helper -------------------------------------------------------
    def _get(self, path, params=None):
        import requests  # import perezoso

        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"x-apisports-key": self.api_key}
        resp = requests.get(url, headers=headers, params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            logger.warning("API-Football devolvio errores: %s", data["errors"])
        return data.get("response", [])

    @staticmethod
    def _norm_status(short):
        if short in _FINISHED:
            return "finished"
        if short in _LIVE:
            return "live"
        return "upcoming"

    def _fixtures_response(self):
        """Respuesta cruda de /fixtures, cacheada por instancia."""
        if self._fixtures_cache is None:
            self._fixtures_cache = self._get(
                "fixtures", {"league": self.league_id, "season": self.season})
        return self._fixtures_cache

    # -- API publica del proveedor ----------------------------------------
    def fetch_fixtures(self):
        rows = self._fixtures_response()
        out = []
        for r in rows:
            fixture = r.get("fixture", {})
            goals = r.get("goals", {})
            status = fixture.get("status", {}) or {}
            teams = r.get("teams", {}) or {}
            home_t = teams.get("home", {}) or {}
            away_t = teams.get("away", {}) or {}
            # API-Football marca el equipo que avanza con winner=true (incluye
            # definicion por penales, aunque 'goals' quede empatado).
            if home_t.get("winner"):
                winner = "home"
            elif away_t.get("winner"):
                winner = "away"
            else:
                winner = None
            out.append(
                FixtureUpdate(
                    api_fixture_id=fixture.get("id"),
                    status=self._norm_status(status.get("short", "NS")),
                    home_score=goals.get("home"),
                    away_score=goals.get("away"),
                    minute=status.get("elapsed"),
                    home_api_team_id=home_t.get("id"),
                    away_api_team_id=away_t.get("id"),
                    winner=winner,
                )
            )
        return out

    def fetch_fixture_index(self):
        """Índice de fixtures (fecha + IDs + nombres) para el mapeo inicial."""
        from datetime import datetime, timezone as _tz

        rows = self._fixtures_response()
        out = []
        for r in rows:
            fixture = r.get("fixture", {}) or {}
            teams = r.get("teams", {}) or {}
            home_t = teams.get("home", {}) or {}
            away_t = teams.get("away", {}) or {}
            league = r.get("league", {}) or {}
            raw_date = fixture.get("date")
            kickoff = None
            if raw_date:
                try:
                    kickoff = datetime.fromisoformat(raw_date).astimezone(_tz.utc)
                except ValueError:
                    kickoff = None
            out.append(
                FixtureMeta(
                    api_fixture_id=fixture.get("id"),
                    kickoff=kickoff,
                    home_api_team_id=home_t.get("id"),
                    away_api_team_id=away_t.get("id"),
                    home_name=home_t.get("name", "") or "",
                    away_name=away_t.get("name", "") or "",
                    round_label=league.get("round", "") or "",
                )
            )
        return out

    def fetch_standings(self):
        rows = self._get("standings", {"league": self.league_id, "season": self.season})
        out = []
        for league in rows:
            groups = (league.get("league", {}) or {}).get("standings", []) or []
            for group in groups:
                for team in group:
                    all_ = team.get("all", {}) or {}
                    goals = all_.get("goals", {}) or {}
                    out.append(
                        StandingRow(
                            api_team_id=(team.get("team", {}) or {}).get("id"),
                            played=all_.get("played", 0) or 0,
                            won=all_.get("win", 0) or 0,
                            drawn=all_.get("draw", 0) or 0,
                            lost=all_.get("lose", 0) or 0,
                            goals_for=goals.get("for", 0) or 0,
                            goals_against=goals.get("against", 0) or 0,
                            points=team.get("points", 0) or 0,
                        )
                    )
        return out

    def fetch_top_scorers(self, limit=20):
        rows = self._get("players/topscorers", {"league": self.league_id, "season": self.season})
        out = []
        for r in rows[:limit]:
            player = r.get("player", {}) or {}
            stats = (r.get("statistics") or [{}])[0]
            goals = stats.get("goals", {}) or {}
            games = stats.get("games", {}) or {}
            team = stats.get("team", {}) or {}
            out.append(
                ScorerRow(
                    api_player_id=player.get("id"),
                    name=player.get("name", ""),
                    goals=goals.get("total", 0) or 0,
                    assists=goals.get("assists", 0) or 0,
                    # "appearences" (sic): asi viene escrito en API-Football.
                    appearances=games.get("appearences", 0) or 0,
                    photo_url=player.get("photo", "") or "",
                    api_team_id=team.get("id"),
                )
            )
        return out
