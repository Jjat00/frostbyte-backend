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

from .base import FixtureUpdate, MatchProvider, ScorerRow, StandingRow

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

    # -- API publica del proveedor ----------------------------------------
    def fetch_fixtures(self):
        rows = self._get("fixtures", {"league": self.league_id, "season": self.season})
        out = []
        for r in rows:
            fixture = r.get("fixture", {})
            goals = r.get("goals", {})
            status = fixture.get("status", {}) or {}
            out.append(
                FixtureUpdate(
                    api_fixture_id=fixture.get("id"),
                    status=self._norm_status(status.get("short", "NS")),
                    home_score=goals.get("home"),
                    away_score=goals.get("away"),
                    minute=status.get("elapsed"),
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
            goals = (stats.get("goals", {}) or {}).get("total", 0) or 0
            out.append(
                ScorerRow(
                    api_player_id=player.get("id"),
                    name=player.get("name", ""),
                    goals=goals,
                )
            )
        return out
