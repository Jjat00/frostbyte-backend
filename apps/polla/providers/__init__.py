"""Proveedores de datos del Mundial.

El backend funciona con datos sembrados (seed) y, si se configura
``API_FOOTBALL_KEY``, sincroniza resultados/posiciones en vivo desde
API-Football. Ver ``get_provider``.
"""
from django.conf import settings

from .base import MatchProvider, NullProvider
from .api_football import APIFootballProvider


def get_provider():
    """Devuelve el proveedor activo segun la configuracion.

    Si hay ``API_FOOTBALL_KEY`` configurada -> ``APIFootballProvider``.
    Si no -> ``NullProvider`` (no trae datos en vivo; solo seed).
    """
    key = getattr(settings, "API_FOOTBALL_KEY", "") or ""
    if key:
        return APIFootballProvider(
            api_key=key,
            league_id=getattr(settings, "API_FOOTBALL_LEAGUE_ID", 1),
            season=getattr(settings, "API_FOOTBALL_SEASON", 2026),
            base_url=getattr(
                settings, "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
            ),
        )
    return NullProvider()


__all__ = ["MatchProvider", "NullProvider", "APIFootballProvider", "get_provider"]
