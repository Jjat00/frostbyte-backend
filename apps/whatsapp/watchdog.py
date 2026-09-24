"""Vigía de los clientes que se quedaron esperando respuesta.

Todo el camino normal vive en la memoria del proceso: el webhook responde 200
y deja el mensaje en la cola del contacto (worker._pending), y un hilo decide
cuándo llamar al agente. Si el proceso se reinicia —un despliegue en hora de
servicio— esa cola se pierde y nadie reintenta nada: el cliente se queda
esperando y el WebhookEvent en "pendiente" para siempre.

Este barrido es la red debajo de eso, y cubre tres huecos que el camino normal
no puede cubrir por sí mismo:

1. El turno se perdió (reinicio del proceso o excepción en el loop).
2. Una persona del equipo intervino, la pausa venció y nunca contestó.
3. El envío a Kapso falló tras sus reintentos: el hilo del modelo cree que
   respondió y en el chat del cliente no hay nada.

Lo que NO hace: escribirle a quien no está esperando. Si lo último fue
un "gracias", el propio modelo se calla (y callarse no manda nada),
que es más barato y más fino que una lista de palabras de despedida. Y la
ventana máxima impide revivir una conversación de ayer: pasado ese punto, el
silencio ya es parte de la historia del chat y meterse sería peor.

Corre en un hilo del propio proceso (como el sync de Spotify en apps.music) y
no en un servicio cron aparte: es una consulta por minuto. Si algún día el
backend corre con réplicas hay que moverlo a un command con lock, o dos
procesos contestarían lo mismo.

El mismo barrido lleva encima el aviso de los domicilios que vuelven a
prenderse (domicilios.py): es otra forma del mismo cliente esperando algo que
nunca llega.
"""

import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from .models import ChatMessage, SentMessage, WhatsAppContact

logger = logging.getLogger(__name__)

# Margen desde que arranca el proceso hasta el primer barrido: los webhooks
# que se estaban procesando cuando se reinició tienen que llegar y encolarse
# antes de que esto decida que nadie les contestó.
ARRANQUE_GRACIA_SECONDS = 120

NOTA_PENDIENTE = (
    "[Aviso del sistema, no del cliente: lo último que te escribió lleva {cuanto} sin "
    "respuesta. Puede que un compañero estuviera atendiendo y ya no esté, o que algo "
    "nuestro fallara. El cliente sigue esperando: retoma donde quedó y contéstale. No "
    "le cuentes que hubo un problema ni te disculpes más de una vez, y si de verdad no "
    "hacía falta responder nada, no escribas.]"
)

NOTA_PERDIDA = (
    "[Aviso del sistema, no del cliente: este mensaje suyo llegó hace {cuanto} y se "
    "quedó sin responder por algo nuestro. Atiéndelo como si acabara de llegar. No le "
    "cuentes que hubo un problema ni te disculpes más de una vez, y si de verdad no "
    "hacía falta responder nada, no escribas.]\n{texto}"
)


# Cuando un compañero estuvo en el chat, el vigía no puede entrar como si
# llegara a una conversación intacta. Chat real del 19-09: el equipo cerró el
# precio ("26.000") y el cliente respondió "Gracias"; diez minutos después el
# vigía retomó con "¿Con qué billete vas a pagar, veci?", una pregunta de un
# flujo que el humano ya había dejado resuelto.
NOTA_HUMANO = (
    " Ojo: un compañero del equipo estuvo atendiendo este chat hace poco, y lo que él dijo "
    "vale. Lee lo que ya quedó resuelto entre ellos: no repitas sus preguntas, no vuelvas a "
    "empezar el flujo del pedido y no le pidas datos que él ya cerró. Si lo último del "
    "cliente fue solo un agradecimiento o una despedida, no escribas nada. Pero si estaba "
    "contestando algo que el compañero le preguntó —confirmando el pedido, dando un dato—, "
    "eso sí se atiende: ahí lo que falte lo haces tú."
)


def _cuanto(delta):
    """La espera en palabras: "4 minutos", "una hora"."""
    minutos = int(delta.total_seconds() // 60)
    if minutos >= 120:
        return f"{minutos // 60} horas"
    if minutos >= 60:
        return "una hora"
    return "un minuto" if minutos <= 1 else f"{minutos} minutos"


def esperando(ahora=None):
    """Los contactos cuyo último mensaje sigue sin respuesta, con ese mensaje.

    Devuelve una lista de tuplas (contacto, mensaje). Un contacto entra solo
    si el agente debería haberle contestado: no está bloqueado ni en manos de
    una persona, el mensaje es suyo y no nuestro, y no hay ya un turno vivo
    atendiéndolo.
    """
    from .worker import turno_vivo

    ahora = ahora or timezone.now()
    limite = ahora - timedelta(minutes=settings.WHATSAPP_RESCUE_AFTER_MINUTES)
    desde = ahora - timedelta(minutes=settings.WHATSAPP_RESCUE_WINDOW_MINUTES)

    pendientes = []
    contactos = WhatsAppContact.objects.filter(
        last_message_at__gte=desde,
        last_message_at__lte=limite,
        is_blocked=False,
        human_handoff=False,
    )
    for contact in contactos:
        if contact.human_until and contact.human_until > ahora:
            continue  # alguien del equipo está atendiendo ahora mismo
        ultimo = (
            # [:30] porque es lo que cabe en ChatMessage.phone: un BSUID largo
            # se guarda truncado y por el número entero no lo encontraría
            ChatMessage.objects.filter(phone=contact.phone[:30])
            .order_by("-created_at")
            .first()
        )
        if ultimo is None or ultimo.direction != ChatMessage.Direction.INBOUND:
            continue  # ya le respondimos, o no hay nada guardado
        if ultimo.created_at > limite:
            continue  # todavía es pronto: el turno normal puede estar corriendo
        if contact.rescued_wamid == ultimo.wamid:
            continue  # ya lo intentamos una vez; callarse pudo ser la respuesta
        if SentMessage.objects.filter(
            to_phone=contact.phone[:30], created_at__gt=ultimo.created_at
        ).exists():
            # Un turno puede contestar solo con un sticker, una foto o unos
            # botones, y eso no deja texto en el archivo de la conversación.
            # Sí deja el wamid de lo enviado, que es lo que se mira aquí.
            continue
        if not contact.last_phone_number_id:
            continue  # sin número propio por donde escribirle no hay rescate
        if turno_vivo(contact.phone):
            continue
        pendientes.append((contact, ultimo))
    return pendientes


def _texto_del_turno(contact, ultimo, ahora):
    """Lo que se le da al agente para que retome ese mensaje.

    El mensaje del cliente solo se repite cuando el hilo no lo tiene: si el
    turno alcanzó a metérselo antes de morir, dárselo otra vez sería
    contestarle dos veces lo mismo.
    """
    from .agent import ultimo_del_cliente

    cuanto = _cuanto(ahora - ultimo.created_at)
    if ultimo.body.strip() and ultimo.body.strip() in ultimo_del_cliente(contact):
        nota = NOTA_PENDIENTE.format(cuanto=cuanto)
    else:
        nota = NOTA_PERDIDA.format(cuanto=cuanto, texto=ultimo.body)
    if _atendio_un_humano(contact, ultimo, ahora):
        # La nota va dentro de los corchetes del aviso, que es donde el modelo
        # lee las instrucciones del sistema; fuera sonaría a mensaje del cliente
        nota = nota.replace("]", NOTA_HUMANO + "]", 1)
    return nota


def _atendio_un_humano(contact, ultimo, ahora):
    """Si alguien del equipo escribió en este chat dentro de la ventana."""
    desde = ahora - timedelta(minutes=settings.WHATSAPP_RESCUE_WINDOW_MINUTES)
    return ChatMessage.objects.filter(
        phone=contact.phone[:30],
        author=ChatMessage.Author.HUMAN,
        created_at__gte=desde,
        created_at__lte=ultimo.created_at,
    ).exists()


def rescatar(contact, ultimo, ahora=None):
    """Corre el turno que le faltaba a ese cliente y le manda la respuesta."""
    from .agent import run_turn
    from .worker import _deliver, _phone_lock, turno_vivo

    ahora = ahora or timezone.now()
    if turno_vivo(contact.phone):
        # Escribió entre el barrido y este momento: el camino normal ya lo
        # atiende y lo que tenga que decir irá en ese turno, con todo junto
        return None
    # Se marca ANTES de responder: si esto revienta a mitad, el cliente se
    # queda como estaba, pero si se marcara después un fallo repetido lo
    # convertiría en un bucle de mensajes cada minuto
    contact.rescued_wamid = ultimo.wamid
    contact.save(update_fields=["rescued_wamid", "updated_at"])

    phone_number_id = contact.last_phone_number_id
    with _phone_lock(contact.phone):
        turn = run_turn(
            contact,
            _texto_del_turno(contact, ultimo, ahora),
            phone_number_id=phone_number_id,
            message_id=ultimo.wamid,
        )
    if not turn.replies and turn.sticker is None:
        logger.info("El vigía no tenía nada que contestarle a %s", contact.phone)
        return turn
    logger.info("El vigía retomó la conversación de %s", contact.phone)
    _deliver(contact, phone_number_id, turn)
    return turn


def barrer(ahora=None):
    """Una pasada: rescata a todos los que quedaron esperando. Devuelve cuántos.

    No toca las conexiones a propósito: de eso se encarga quien la llame en
    bucle (_loop). Así el barrido se puede correr a mano desde un command o un
    test sin romperles la transacción.
    """
    if not settings.WHATSAPP_AGENT_ENABLED:
        return 0
    ahora = ahora or timezone.now()
    rescatados = 0
    for contact, ultimo in esperando(ahora):
        try:
            rescatar(contact, ultimo, ahora)
            rescatados += 1
        except Exception:
            logger.exception("El vigía no pudo retomar la conversación de %s", contact.phone)
    return rescatados


def _una_vuelta():
    """Los dos barridos de una pasada. Separada de _loop para poder probarla."""
    from . import domicilios

    # Primero el aviso de los domicilios y después el rescate: los dos pueden
    # querer escribirle al mismo cliente, y el aviso es el que sabe más
    # (retoma el pedido Y cuenta que ya hay servicio). Al reclamar al contacto
    # deja su último mensaje como rescatado, así que el vigía lo ve atendido
    # aunque el modelo se haya callado. Cada uno con su try: el barrido nuevo
    # no puede dejar sin correr al rescate, que ya estaba en producción.
    #
    # Los dos miran la misma hora, la del principio de la vuelta. El rescate
    # tiene ventana máxima: si contara desde que le toca el turno, un aviso de
    # domicilios lento podría dejar fuera de ella a quien estaba justo en el
    # borde, y ese cliente no se rescataría nunca.
    ahora = timezone.now()
    try:
        domicilios.barrer(ahora)
    except Exception:
        logger.exception("Error en el aviso de los domicilios")
    try:
        barrer(ahora)
    except Exception:
        logger.exception("Error en el barrido del vigía")


def _loop():
    time.sleep(ARRANQUE_GRACIA_SECONDS)
    while True:
        try:
            close_old_connections()
            _una_vuelta()
        except Exception:
            logger.exception("Error en el barrido del vigía")
        finally:
            close_old_connections()
        time.sleep(settings.WHATSAPP_RESCUE_INTERVAL_SECONDS)


def start():
    """Arranca el vigía en un hilo demonio, si está habilitado."""
    if not settings.WHATSAPP_RESCUE_ENABLED:
        return None
    thread = threading.Thread(target=_loop, name="wa-vigia", daemon=True)
    thread.start()
    return thread
