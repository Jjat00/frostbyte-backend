"""Avisos por WebSocket que no pueden colgar un request.

Las vistas notifican los cambios con ``channel_layer.group_send``, que es
asincrono, asi que cada aviso pasaba por ``async_to_sync``: el hilo del
request se quedaba esperando a Redis **sin limite de tiempo**. Mientras Redis
responda rapido no se nota, pero un Redis lento o una conexion TCP muerta
alcanzan para colgar el request entero, y daphne termina matando la conexion:

    WARNING  Application instance <Task pending ...> for connection
    <WebRequest ... method=POST uri=/api/v1/orders/5645/mark_paid/> took too
    long to shut down and was killed.

El pedido ya estaba guardado en la base; lo unico que faltaba era avisar. Por
eso aqui el aviso tiene techo: como mucho ``BROADCAST_TIMEOUT`` segundos, y si
falla se registra y se sigue. Perder un aviso es barato -el front refresca por
polling y al reconectar el WebSocket-, colgar la caja no lo es.

Este es el unico punto donde el codigo sincrono habla con el channel layer;
los helpers ``broadcast_*_update()`` de cada app pasan por aqui.
"""

import asyncio
import logging
import os

from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)

# Techo por aviso. 3 s es de sobra para un group_send sano (lo normal son
# milisegundos) y sigue siendo un tiempo que un cajero no alcanza a notar.
BROADCAST_TIMEOUT = float(os.getenv("BROADCAST_TIMEOUT_SECONDS", "3"))


async def _send_with_timeout(channel_layer, group, message, timeout):
    await asyncio.wait_for(channel_layer.group_send(group, message), timeout=timeout)


def broadcast(group, message, timeout=None):
    """Avisa al grupo ``group``. Nunca lanza y nunca bloquea mas de ``timeout``.

    Devuelve True si el aviso salio, False si se descarto. Quien llama no
    necesita mirar el resultado: la operacion de negocio ya termino y el aviso
    es best-effort.
    """
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return False

    limit = BROADCAST_TIMEOUT if timeout is None else timeout
    try:
        async_to_sync(_send_with_timeout)(channel_layer, group, message, limit)
        return True
    except asyncio.TimeoutError:
        logger.warning(
            "Aviso WebSocket a '%s' descartado: el channel layer no respondio en %ss",
            group,
            limit,
        )
    except Exception:
        logger.exception("Aviso WebSocket a '%s' fallo", group)
    return False
