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


class MatchProvider:
    """Contrato base. Las subclases sobreescriben lo que soporten."""

    available: bool = False

    def fetch_fixtures(self) -> list[FixtureUpdate]:  # pragma: no cover - interfaz
        return []

    def fetch_standings(self) -> list[StandingRow]:  # pragma: no cover - interfaz
        return []

    def fetch_top_scorers(self, limit: int = 20) -> list[ScorerRow]:  # pragma: no cover
        return []


class NullProvider(MatchProvider):
    """Proveedor inerte: se usa cuando no hay API key configurada.

    El backend sigue siendo totalmente funcional con datos sembrados; este
    proveedor simplemente no aporta actualizaciones en vivo.
    """

    available = False
