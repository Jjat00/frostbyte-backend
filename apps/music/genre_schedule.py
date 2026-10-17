"""Clasificación semanal del género de los artistas nuevos.

El género no viene de Spotify: lo pone el modelo con `classify_artist_genres`
(ver services/genre_classifier.py) y se guarda una vez por artista. Mientras
ese comando se corría a mano, cada artista que llegaba después quedaba como
"sin clasificar" para siempre: en octubre de 2026 ya era el 14 % de lo pedido.

Una vez por semana basta: las estadísticas se leen en meses y una tanda nueva
son unas pocas llamadas al modelo barato. Corre en un hilo del propio proceso,
como el sync de Spotify y el vigía de WhatsApp, y no en un servicio cron
aparte. Si un despliegue cae justo a esa hora, esa semana se salta y la
siguiente recoge todo lo pendiente, porque el comando solo mira lo que falta.
"""

import logging
import threading
import time
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

# Lunes a las 10:00, hora local: el local está cerrado y nadie está pidiendo.
RUN_WEEKDAY = 0
RUN_HOUR = 10


def next_run(now):
    """El próximo lunes a las 10:00 (hora local) estrictamente después de `now`."""
    local = timezone.localtime(now)
    dias = (RUN_WEEKDAY - local.weekday()) % 7
    objetivo = timezone.make_aware(
        datetime.combine(local.date() + timedelta(days=dias), datetime.min.time())
        + timedelta(hours=RUN_HOUR)
    )
    if objetivo <= now:
        objetivo += timedelta(days=7)
    return objetivo


def _loop():
    while True:
        espera = (next_run(timezone.now()) - timezone.now()).total_seconds()
        time.sleep(max(1, espera))
        try:
            close_old_connections()
            call_command("classify_artist_genres")
        except Exception:
            logger.exception("Error clasificando los géneros de la semana")
        finally:
            close_old_connections()


def start():
    """Arranca la tarea semanal en un hilo demonio, si está habilitada."""
    if not settings.MUSIC_GENRE_WEEKLY_ENABLED:
        return None
    thread = threading.Thread(target=_loop, name="music-generos", daemon=True)
    thread.start()
    return thread
