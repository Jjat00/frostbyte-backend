"""Al que se quedó sin domicilio se le avisa cuando vuelven a haberlos.

Los domicilios se prenden y se apagan a mano desde el panel: dependen de que
haya un domiciliario. Mientras están apagados, Frosty le dice al cliente "justo
en este momento no tenemos servicio de domicilios" y le ofrece pasar a recoger.
Muchos contestan "ah bueno, gracias" y ahí se acaba la conversación: querían su
pedido en la casa, y esa venta se pierde aunque media hora después vuelva a
haber quien lo lleve.

Este barrido cierra ese hueco. Corre dentro del vigía (watchdog._loop), mira si
los domicilios volvieron a prenderse y, para cada cliente que chocó con la
puerta cerrada mientras estaban apagados, corre un turno con silence_ok: el
modelo lee la conversación y decide si de verdad quedó un pedido en el aire. Si
el cliente venía a preguntar la dirección o ya pasó a recoger, se calla.

Quién NO entra:

- Quien escribió después de que los domicilios volvieran: a ese ya se le
  atendió con el servicio activo.
- Quien ya hizo un pedido (la marca se borra al crearlo): su caso está cerrado.
- Quien se quedó esperando fuera de la ventana. Son dos límites en uno: pasada
  media jornada el aviso ya no es una atención sino una interrupción, y sobre
  todo WhatsApp solo deja escribir primero dentro de las 24 h siguientes al
  mensaje del cliente — fuera de ahí haría falta una plantilla aprobada, y la
  cuenta no tiene (ver WHATSAPP_DELIVERY_REENGAGE_WINDOW_HOURS).
- Todo lo que el vigía ya respeta: bloqueados, agente apagado, una persona del
  equipo atendiendo, un turno vivo en memoria.

El tope por reactivación es el corte que en Ungga faltó: allí la primera
corrida habría mandado ochenta avisos de golpe. Y como la marca es un campo
nuevo, el día que esto se despliegue nadie la tiene: el pasado no se barre.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import WhatsAppContact

logger = logging.getLogger(__name__)

NOTA_DOMICILIOS = (
    "[Aviso del sistema, no del cliente: hace {cuanto} este cliente te escribió "
    "cuando NO había servicio de domicilios, y ya volvió a haberlo. Lee la "
    "conversación y decide: si se quedó sin su pedido por eso, o si dejó de "
    "decidir porque no había domicilio, escríbele tú primero —salúdalo, cuéntale "
    "en una línea que los domicilios ya están activos y ofrécele retomar justo lo "
    "que quería, con sus mismas palabras—. Si venía a otra cosa, si prefirió pasar "
    "a recoger, si ya le resolviste lo que pedía o si nunca hubo un pedido en "
    "juego, NO escribas nada. No te disculpes ni menciones fallas nuestras, y no "
    "prometas tiempos.]"
)


def anotar(contact):
    """Marca que este contacto chocó con los domicilios apagados.

    Se guarda con un UPDATE y no con save() porque esto corre en mitad de un
    turno: el contacto en memoria tiene otros campos a medio escribir y no hay
    por qué arrastrarlos.
    """
    ahora = timezone.now()
    WhatsAppContact.objects.filter(pk=contact.pk).update(delivery_missed_at=ahora)
    contact.delivery_missed_at = ahora


def olvidar(contact):
    """Borra la marca: este cliente ya hizo su pedido, no hay nada que avisarle."""
    if contact.delivery_missed_at is None:
        return
    WhatsAppContact.objects.filter(pk=contact.pk).update(delivery_missed_at=None)
    contact.delivery_missed_at = None


def reactivacion(cfg=None, ahora=None):
    """Cuándo se prendieron los domicilios, si hay un aviso que dar por eso.

    Devuelve None mientras no haya nada que hacer: domicilios apagados, local
    cerrado, o recién prendidos (el margen existe por si fue un toque sin
    querer: apagarlos otra vez dentro del margen no le cuesta un mensaje a
    nadie).
    """
    from apps.orders.models import StoreSettings

    ahora = ahora or timezone.now()
    cfg = cfg or StoreSettings.load()
    if not cfg.is_open or not cfg.customer_ordering_enabled:
        return None
    desde = cfg.ordering_changed_at
    if desde is None:
        return None
    if ahora - desde < timedelta(minutes=settings.WHATSAPP_DELIVERY_REENGAGE_AFTER_MINUTES):
        return None
    return desde


def pendientes(ahora=None):
    """Los contactos a los que hay que contarles que los domicilios volvieron."""
    from .worker import turno_vivo

    ahora = ahora or timezone.now()
    desde = reactivacion(ahora=ahora)
    if desde is None:
        return []

    ya_avisados = WhatsAppContact.objects.filter(delivery_notified_at__gte=desde).count()
    cupo = settings.WHATSAPP_DELIVERY_REENGAGE_MAX - ya_avisados
    if cupo <= 0:
        return []

    ventana = ahora - timedelta(hours=settings.WHATSAPP_DELIVERY_REENGAGE_WINDOW_HOURS)
    candidatos = WhatsAppContact.objects.filter(
        delivery_missed_at__gte=ventana,
        # Chocó con la puerta cerrada ANTES de que volvieran a prenderse; a
        # quien escribió después ya se le atendió con el servicio activo
        delivery_missed_at__lt=desde,
        is_blocked=False,
        human_handoff=False,
    ).exclude(delivery_notified_at__gte=desde).order_by("delivery_missed_at")

    listos = []
    for contact in candidatos:
        if contact.human_until and contact.human_until > ahora:
            continue  # alguien del equipo está atendiendo ahora mismo
        if not contact.last_phone_number_id:
            continue  # sin número propio por donde escribirle no hay aviso
        if turno_vivo(contact.phone):
            continue  # está escribiendo ahora: lo que haya que decirle va ahí
        listos.append(contact)
        if len(listos) >= cupo:
            break
    return listos


def _cuanto(delta):
    """La espera en palabras: "40 minutos", "3 horas"."""
    minutos = int(delta.total_seconds() // 60)
    if minutos >= 120:
        return f"{minutos // 60} horas"
    if minutos >= 60:
        return "una hora"
    return "un minuto" if minutos <= 1 else f"{minutos} minutos"


def avisar(contact, ahora=None):
    """Corre el turno que le cuenta a ese cliente que los domicilios volvieron."""
    from .agent import run_turn
    from .worker import _deliver, _phone_lock, turno_vivo

    ahora = ahora or timezone.now()
    if turno_vivo(contact.phone):
        # Escribió entre el barrido y este momento: lo atiende el camino
        # normal, que además ya sabe que hay domicilios
        return None
    # Se marca ANTES de escribir, igual que el vigía: si esto revienta a mitad
    # el cliente se queda como estaba, pero marcarlo después convertiría un
    # fallo repetido en un mensaje cada minuto
    WhatsAppContact.objects.filter(pk=contact.pk).update(delivery_notified_at=ahora)
    contact.delivery_notified_at = ahora

    phone_number_id = contact.last_phone_number_id
    nota = NOTA_DOMICILIOS.format(cuanto=_cuanto(ahora - contact.delivery_missed_at))
    with _phone_lock(contact.phone):
        turn = run_turn(
            contact,
            nota,
            phone_number_id=phone_number_id,
            # Nadie preguntó nada ahora mismo: el modelo puede mirar el hilo y
            # decidir que este cliente no se quedó esperando ningún domicilio
            silence_ok=True,
        )
    if not turn.replies and turn.sticker is None:
        logger.info("Los domicilios volvieron pero %s no esperaba ninguno", contact.phone)
        return turn
    logger.info("Se le avisó a %s que los domicilios ya están activos", contact.phone)
    _deliver(contact, phone_number_id, turn)
    return turn


def barrer(ahora=None):
    """Una pasada: avisa a todos los que quedaron sin domicilio. Devuelve cuántos."""
    if not settings.WHATSAPP_AGENT_ENABLED:
        return 0
    if not settings.WHATSAPP_DELIVERY_REENGAGE_ENABLED:
        return 0
    ahora = ahora or timezone.now()
    avisados = 0
    for contact in pendientes(ahora):
        try:
            avisar(contact, ahora)
            avisados += 1
        except Exception:
            logger.exception("No se le pudo avisar a %s que hay domicilios", contact.phone)
    return avisados
