"""Loop de sincronizacion en tiempo real de la Polla.

Se arranca UNA sola vez desde ``config/asgi.py`` (es decir, solo bajo daphne /
ASGI; este modulo no se importa en comandos de ``manage.py``, asi que no corre
en ``migrate``, ``collectstatic`` ni en el cron). Vive en un hilo daemon dentro
del proceso web que ya esta siempre encendido: no agrega servicios ni costo.

Sondea de forma adaptativa:
  - cada ``POLLA_LOOP_LIVE_SECONDS`` (def. 20s) cuando hay partidos en vivo o por
    arrancar (deteccion casi en tiempo real durante los partidos),
  - cada ``POLLA_LOOP_IDLE_SECONDS`` (def. 120s) en reposo.

El propio comando ``polla_sync`` decide si llama a API-Football (gating
adaptativo con marca compartida en Redis), asi que correr seguido NO multiplica
el consumo de la API. Cuando detecta un cambio emite la senal WebSocket
``polla_changed`` y los clientes refrescan al instante.

Apagar el loop (p.ej. si se corre el sync por cron/worker aparte):
    POLLA_REALTIME_LOOP=0

Nota multi-replica: si el servicio web escala a >1 replica, cada una corre su
loop. El marcador en Redis limita las llamadas a la API en reposo, pero durante
partidos en vivo cada replica sondearia. Para esta app (1 replica) no aplica; si
algun dia se escala, apagar el loop aqui y usar un worker dedicado.
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

ENABLED = os.environ.get("POLLA_REALTIME_LOOP", "1") != "0"
LIVE_SECONDS = int(os.environ.get("POLLA_LOOP_LIVE_SECONDS", "20"))
IDLE_SECONDS = int(os.environ.get("POLLA_LOOP_IDLE_SECONDS", "120"))

_started = False
_lock = threading.Lock()


def _has_live_or_imminent():
    """True si hay partidos en vivo o por arrancar (define el ritmo del loop)."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.polla.models import Match

    now = timezone.now()
    return (
        Match.objects.filter(status=Match.Status.LIVE).exists()
        or Match.objects.filter(
            status=Match.Status.UPCOMING,
            kickoff__lte=now + timedelta(minutes=15),
            kickoff__gte=now - timedelta(hours=3),
        ).exists()
    )


def _run():
    from django.core.management import call_command
    from django.db import close_old_connections

    logger.info(
        "Polla realtime loop iniciado (live=%ss, idle=%ss).",
        LIVE_SECONDS, IDLE_SECONDS,
    )
    # Espera breve para no competir con el arranque (migrate/collectstatic ya
    # corrieron antes de daphne, pero damos aire a que el server enlace el puerto).
    time.sleep(10)
    while True:
        interval = IDLE_SECONDS
        try:
            call_command("polla_sync")
            interval = LIVE_SECONDS if _has_live_or_imminent() else IDLE_SECONDS
        except Exception:  # noqa: BLE001 - el loop nunca debe morir por una iteracion
            logger.exception("Polla realtime loop: error en la iteracion")
        finally:
            # Evita conexiones a la BD colgadas entre iteraciones del hilo.
            close_old_connections()
        time.sleep(interval)


def start_realtime_loop():
    """Arranca el hilo del loop una sola vez por proceso."""
    global _started
    if not ENABLED:
        logger.info("Polla realtime loop deshabilitado (POLLA_REALTIME_LOOP=0).")
        return
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, name="polla-realtime-loop", daemon=True).start()
