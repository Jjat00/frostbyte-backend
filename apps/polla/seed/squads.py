"""Carga las convocatorias completas del Mundial 2026 desde squads_2026.json.

El JSON lo generan/actualizan las convocatorias oficiales (26 por selección,
arqueros marcados con ``gk``). Es la fuente de las opciones de Goleador / MVP
(jugadores de campo) y Guante de Oro (arqueros). Si el archivo no existe, el
seed cae a las listas curadas STAR_PLAYERS / STAR_KEEPERS de static_data.
"""
import json
from pathlib import Path

_PATH = Path(__file__).with_name("squads_2026.json")


def load_squads():
    """Devuelve {team_code: [{"name": str, "gk": bool}, ...]} o None."""
    if not _PATH.exists():
        return None
    with _PATH.open(encoding="utf-8") as f:
        return json.load(f)
