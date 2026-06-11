"""Interfaz de proveedor de datos de partidos.

Un proveedor normaliza datos de una fuente externa (p.ej. API-Football) a
diccionarios simples que el comando ``polla_sync`` aplica sobre la base de
datos. Mantener la interfaz pequena permite cambiar de fuente sin tocar la
logica de sincronizacion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FixtureUpdate:
    """Estado normalizado de un partido traido de la fuente externa."""

    api_fixture_id: int
    status: str  # "upcoming" | "live" | "finished"
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    minute: Optional[int] = None
    # IDs de API-Football de los equipos que disputan el cruce (para resolver
    # eliminatorias) y quién avanzó ("home" | "away" | None, incluye penales).
    home_api_team_id: Optional[int] = None
    away_api_team_id: Optional[int] = None
    winner: Optional[str] = None


@dataclass
class FixtureMeta:
    """Metadatos de un fixture para mapear IDs de API-Football a la BD local.

    Se usa una sola vez (comando ``polla_map_api``) para poblar
    ``Match.api_fixture_id`` y ``Team.api_team_id``. El ancla principal de
    mapeo es ``kickoff`` (las horas oficiales son fijas); los nombres sirven de
    desempate/validación.
    """

    api_fixture_id: int
    kickoff: object  # datetime aware (UTC)
    home_api_team_id: Optional[int] = None
    away_api_team_id: Optional[int] = None
    home_name: str = ""
    away_name: str = ""
    round_label: str = ""


@dataclass
class StandingRow:
    """Fila de posiciones normalizada (opcional; tambien se computa local)."""

    api_team_id: int
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0


@dataclass
class ScorerRow:
    api_player_id: int
    name: str
    goals: int = 0
    assists: int = 0
    appearances: int = 0
    photo_url: str = ""
    api_team_id: Optional[int] = None


class MatchProvider:
    """Contrato base. Las subclases sobreescriben lo que soporten."""

    available: bool = False

    def fetch_fixtures(self) -> list[FixtureUpdate]:  # pragma: no cover - interfaz
        return []

    def fetch_fixture_index(self) -> "list[FixtureMeta]":  # pragma: no cover
        return []

    def fetch_standings(self) -> list[StandingRow]:  # pragma: no cover - interfaz
        return []

    def fetch_top_scorers(self, limit: int = 20) -> list[ScorerRow]:  # pragma: no cover
        return []

    def fetch_fixture_details(self, fixture_ids) -> dict:  # pragma: no cover
        """{api_fixture_id: {"events": [...], "statistics": {...}}}."""
        return {}

    def fetch_head_to_head(self, api_team_a, api_team_b, last=10):  # pragma: no cover
        """Enfrentamientos previos (finalizados) entre dos selecciones."""
        return []


class NullProvider(MatchProvider):
    """Proveedor inerte: se usa cuando no hay API key configurada.

    El backend sigue siendo totalmente funcional con datos sembrados; este
    proveedor simplemente no aporta actualizaciones en vivo.
    """

    available = False
