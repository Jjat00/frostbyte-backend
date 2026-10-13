"""Vigía de vida del proceso web: si deja de responder, se reinicia solo.

El 12/10 a las 22:40 UTC un checkpoint de Postgres tardó 33 s (disco lento),
un POST a /orders/7176/mark_paid/ se quedó colgado y daphne lo mató. Desde ese
segundo el proceso no volvió a terminar ni un request -tampoco los OPTIONS,
que no tocan la base- durante seis horas, hasta que se reinició a mano.
Railway lo mostraba "Online" todo ese tiempo: solo mira la salud al desplegar,
y un proceso vivo pero congelado no le parece un fallo.

Este hilo se pide a sí mismo /healthz/ por el mismo camino que cualquier
request (daphne, el ASGIHandler de Django, el hilo de la vista). Si falla
FALLOS_SEGUIDOS veces seguidas, vuelca la pila de todos los hilos al log
-para saber la próxima vez dónde se trabó- y termina el proceso con código 1.
Railway lo levanta de nuevo (restartPolicyType ON_FAILURE en railway.toml).

/healthz/ no toca la base a propósito: si Postgres está caído, reiniciar no lo
arregla, y un reinicio en bucle que corre migrate en cada arranque solo
agotaría los reintentos de Railway y dejaría el servicio apagado.
"""

import faulthandler
import logging
import os
import sys
import threading
import time
import urllib.request

from django.http import HttpResponse

logger = logging.getLogger(__name__)

INTERVALO_SECONDS = float(os.getenv("LIVENESS_INTERVAL_SECONDS", "30"))
TIMEOUT_SECONDS = float(os.getenv("LIVENESS_TIMEOUT_SECONDS", "10"))
FALLOS_SEGUIDOS = int(os.getenv("LIVENESS_MAX_FAILURES", "4"))
# daphne tarda unos segundos en escuchar después de importar la app
GRACIA_SECONDS = float(os.getenv("LIVENESS_GRACE_SECONDS", "60"))


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def _responde(url):
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            return response.status == 200
    except Exception as exc:
        logger.warning("Vigía de vida: %s no respondió (%s)", url, exc)
        return False


def _morir():
    logger.error(
        "Vigía de vida: el proceso no responde hace %s intentos; se reinicia",
        FALLOS_SEGUIDOS,
    )
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    sys.stderr.flush()
    os._exit(1)


def _vigilar(url, dormir=time.sleep, responde=_responde, morir=_morir):
    dormir(GRACIA_SECONDS)
    fallos = 0
    while True:
        if responde(url):
            fallos = 0
        else:
            fallos += 1
            if fallos >= FALLOS_SEGUIDOS:
                morir()
                return
        dormir(INTERVALO_SECONDS)


def _es_el_servidor():
    """True solo si este proceso es daphne.

    PORT no basta: dentro del contenedor también lo heredan los tests y los
    comandos de manage.py, y si alguno importa config.asgi el vigía no
    encontraría a nadie en ese puerto y lo terminaría con os._exit.
    """
    return os.path.basename(sys.argv[0] or "") == "daphne"


def start():
    """Arranca el vigía si este proceso es el servidor web (daphne con PORT)."""
    port = os.getenv("PORT")
    if not port or os.getenv("DISABLE_LIVENESS") == "1" or not _es_el_servidor():
        return None
    url = f"http://127.0.0.1:{port}/healthz/"
    hilo = threading.Thread(target=_vigilar, args=(url,), name="liveness", daemon=True)
    hilo.start()
    logger.info("Vigía de vida activo sobre %s", url)
    return hilo
