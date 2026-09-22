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
modelo lee la conversación y decide si de verdad quedó un pedido en el aire.

Escribirle primero a alguien que no está esperando nada es peor que perder la
venta, así que el candidato pasa por dos filtros y no por uno: el determinista
de aquí —lo pidió él, con sus palabras, dentro de la ventana en la que WhatsApp
nos deja escribir— y después el criterio del modelo con el hilo delante. El
primero falla hacia el silencio: una forma de pedirlo que no esté en la lista
cuesta un aviso que no sale, no un mensaje de más.

Quién NO entra:

- Quien nunca pidió que se lo llevaran: preguntó el horario, el menú o la
  dirección, o venía a recoger desde el principio.
- Quien escribió después de que los domicilios volvieran: a ese ya se le
  atendió con el servicio activo.
- Quien ya hizo un pedido (la marca se borra al crearlo): su caso está cerrado.
- Quien no ha escrito en las últimas VENTANA_MAXIMA_HORAS. WhatsApp solo deja
  escribir primero dentro de las 24 h siguientes al mensaje del cliente; fuera
  de ahí haría falta una plantilla aprobada y la cuenta no tiene.
- Todo lo que el vigía ya respeta: bloqueados, agente apagado, una persona del
  equipo atendiendo, un turno vivo en memoria.

Y a nadie dos veces: la marca se consume al reclamarla, con un UPDATE
condicional que también sirve de candado entre procesos (dos réplicas, o el
viejo y el nuevo durante un despliegue, se pisan el reclamo y solo uno gana).
Contar el cupo y reclamar van juntos en una transacción, con la fila de la
tienda bloqueada: sueltos, dos barridos con el último cupo cada uno se llevarían
un contacto distinto y la tanda se pasaría del tope.

Entre reclamar y enviar pasa lo que tarde el modelo en pensar, y en ese rato el
mundo cambia: si apagaron los domicilios, cerraron el local, alguien del equipo
entró a atenderlo o el propio cliente escribió, el turno se descarta sin
mandarse. Lo último no se puede mirar en la memoria del proceso —el turno normal
pudo entrar, contestar y terminar mientras tanto—, así que se relee de la base.

El tope por reactivación es el corte que en Ungga faltó: allí la primera
corrida habría mandado ochenta avisos de golpe. Y como la marca es un campo
nuevo, el día que esto se despliegue nadie la tiene: el pasado no se barre.
"""

import logging
import re
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ChatMessage, WhatsAppContact

logger = logging.getLogger(__name__)

# Tope duro bajo las 24 h de WhatsApp, con margen para que el barrido, el
# modelo y el envío quepan dentro aunque el reloj vaya justo. Manda sobre
# WHATSAPP_DELIVERY_REENGAGE_WINDOW_HOURS: una variable mal puesta en Railway
# no puede hacernos escribir fuera de la ventana.
VENTANA_MAXIMA_HORAS = 20

# Cuántos mensajes suyos se miran hacia atrás buscando la intención.
MENSAJES_QUE_SE_MIRAN = 12

# Cuántos avisos como mucho salen en una misma vuelta del vigía. El rescate
# corre detrás de este barrido en el mismo hilo: diez turnos seguidos de modelo
# lo dejarían esperando minutos. Como el vigía vuelve cada minuto, esto reparte
# el tope de la reactivación en vez de recortarlo.
POR_VUELTA = 3

# Cómo pide un cliente que se lo lleven. La lista falla hacia el silencio a
# propósito: lo que no reconoce se queda sin aviso, que es el lado barato del
# error. Quien sí decide si escribir es el modelo, después, con el hilo
# delante; esto solo evita molestar a quien nunca habló de domicilios.
INTENCION = re.compile(
    r"\b("
    r"domicilio|domicilios|domi|"
    r"envio|envios|enviar|envien|envia|"
    r"lleva|llevan|llevar|lleven|llevas|llevarian|llevarias|"
    r"trae|traen|traer|traiga|traigan|"
    r"manda|mandan|mandar|manden|"
    r"reparto|delivery"
    r")\b"
    r"|a\s+mi\s+casa|a\s+la\s+casa|hasta\s+mi\s+casa|hasta\s+la\s+casa|"
    r"para\s+la\s+casa|hasta\s+aca|hasta\s+aqui|hasta\s+donde\s+estoy"
)

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


def _sin_tildes(texto):
    plano = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in plano if unicodedata.category(c) != "Mn")


def quiere_domicilio(texto):
    """True si el cliente pidió con sus palabras que se lo llevaran."""
    return bool(INTENCION.search(_sin_tildes(texto)))


def anotar(contact):
    """Marca que este contacto chocó con los domicilios apagados.

    Se guarda con un UPDATE y no con save() porque esto corre en mitad de un
    turno: el contacto en memoria tiene otros campos a medio escribir y no hay
    por qué arrastrarlos. Y no marca dos veces: la primera vez es la que fecha
    el problema, y pisarla en cada tool call alargaría la ventana sola.

    Con la función apagada no se anota nada. Si solo callara al barrido, el
    día que se prenda el interruptor saldrían avisos por cosas que pasaron
    mientras estaba apagado: el pasado no se barre en cada encendido, no solo
    el día del despliegue.
    """
    if not settings.WHATSAPP_DELIVERY_REENGAGE_ENABLED:
        return
    if contact.delivery_missed_at is not None:
        return
    ahora = timezone.now()
    WhatsAppContact.objects.filter(pk=contact.pk, delivery_missed_at__isnull=True).update(
        delivery_missed_at=ahora
    )
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


def _ventana(ahora):
    """Desde cuándo cuenta un mensaje suyo, sin salirse de la ventana de WhatsApp."""
    horas = min(settings.WHATSAPP_DELIVERY_REENGAGE_WINDOW_HOURS, VENTANA_MAXIMA_HORAS)
    return ahora - timedelta(hours=horas)


def _mensajes_suyos(contact, desde):
    """Sus últimos mensajes desde esa hora, del más nuevo al más viejo.

    Se cuelga del ChatMessage y no de la marca porque la ventana de WhatsApp se
    mide desde que el cliente escribió, no desde que nuestro turno alcanzó a
    correr: un mensaje procesado tarde no estira el permiso para escribirle.
    """
    return list(
        ChatMessage.objects.filter(
            # [:30] porque es lo que cabe en ChatMessage.phone (ver watchdog)
            phone=contact.phone[:30],
            direction=ChatMessage.Direction.INBOUND,
            created_at__gte=desde,
        ).order_by("-created_at")[:MENSAJES_QUE_SE_MIRAN]
    )


def pendientes(ahora=None):
    """Los contactos a los que hay que contarles que los domicilios volvieron."""
    from .worker import turno_vivo

    ahora = ahora or timezone.now()
    desde = reactivacion(ahora=ahora)
    if desde is None:
        return []

    cupo = min(_cupo(desde), POR_VUELTA)
    if cupo <= 0:
        return []

    candidatos = WhatsAppContact.objects.filter(
        delivery_missed_at__gte=_ventana(ahora),
        # Chocó con la puerta cerrada ANTES de que volvieran a prenderse; a
        # quien escribió después ya se le atendió con el servicio activo
        delivery_missed_at__lt=desde,
        is_blocked=False,
        human_handoff=False,
    ).order_by("delivery_missed_at")

    listos = []
    for contact in candidatos:
        if contact.human_until and contact.human_until > ahora:
            continue  # alguien del equipo está atendiendo ahora mismo
        if not contact.last_phone_number_id:
            continue  # sin número propio por donde escribirle no hay aviso
        if turno_vivo(contact.phone):
            continue  # está escribiendo ahora: lo que haya que decirle va ahí
        mensajes = _mensajes_suyos(contact, _ventana(ahora))
        if not mensajes:
            continue  # fuera de la ventana de WhatsApp: escribir primero no sale
        if mensajes[0].created_at >= desde:
            # Volvió a escribir con los domicilios ya activos: el camino normal
            # lo atendió sabiendo que hay servicio, y si se quedó sin respuesta
            # el rescate es quien tiene que ir. La marca vieja no autoriza a
            # escribirle encima de una conversación que ya siguió.
            #
            # Esto cubre además al que acaba de escribir y tiene un turno
            # corriendo: los domicilios llevan prendidos al menos el margen de
            # reactivación, así que cualquier mensaje más nuevo que ese margen
            # cae aquí. turno_vivo() solo ve la memoria de este proceso; la
            # hora de su último mensaje la ve cualquiera.
            continue
        if not any(quiere_domicilio(m.body) for m in mensajes):
            continue  # nunca pidió que se lo llevaran
        listos.append(contact)
        if len(listos) >= cupo:
            break
    return listos


def _cupo(desde):
    """Cuántos avisos quedan para esta reactivación."""
    ya_avisados = WhatsAppContact.objects.filter(delivery_notified_at__gte=desde).count()
    return settings.WHATSAPP_DELIVERY_REENGAGE_MAX - ya_avisados


def _cuanto(delta):
    """La espera en palabras: "40 minutos", "3 horas"."""
    minutos = int(delta.total_seconds() // 60)
    if minutos >= 120:
        return f"{minutos // 60} horas"
    if minutos >= 60:
        return "una hora"
    return "un minuto" if minutos <= 1 else f"{minutos} minutos"


def reclamar(contact, ahora):
    """Se queda con este contacto, o devuelve False si otro se le adelantó.

    Un solo UPDATE condicionado a la marca que se leyó hace de candado entre
    procesos —dos réplicas, o el viejo y el nuevo mientras se despliega— sin
    tener una transacción abierta mientras piensa el modelo. Y consume la
    marca: si los domicilios se apagan y se prenden otra vez esta noche, este
    cliente no vuelve a entrar salvo que él vuelva a escribir con la puerta
    cerrada.

    De paso deja el último mensaje suyo como rescatado: lo que el barrido de
    domicilios ya atendió no lo vuelve a tocar el vigía, aunque el modelo haya
    decidido callarse y no haya quedado ningún mensaje enviado de por medio.
    """
    ultimo = (
        ChatMessage.objects.filter(
            phone=contact.phone[:30], direction=ChatMessage.Direction.INBOUND
        )
        .order_by("-created_at")
        .first()
    )
    campos = {"delivery_missed_at": None, "delivery_notified_at": ahora}
    if ultimo is not None:
        campos["rescued_wamid"] = ultimo.wamid[:128]
    reclamados = WhatsAppContact.objects.filter(
        pk=contact.pk, delivery_missed_at=contact.delivery_missed_at
    ).update(**campos)
    if not reclamados:
        return False
    contact.delivery_missed_at = None
    contact.delivery_notified_at = ahora
    if ultimo is not None:
        contact.rescued_wamid = ultimo.wamid[:128]
    return True


def _escribio_mientras_tanto(contact):
    """True si entró un mensaje suyo después del que se reclamó.

    turno_vivo() no alcanza: el turno normal puede entrar, contestarle y
    terminar mientras el modelo piensa este aviso, y para cuando se mira, la
    memoria del proceso ya está limpia. El mensaje suyo, en cambio, queda en la
    base. Si escribió, su conversación siguió sin este aviso y mandarlo sería
    el segundo mensaje sobre lo mismo.
    """
    ultimo = (
        ChatMessage.objects.filter(
            phone=contact.phone[:30], direction=ChatMessage.Direction.INBOUND
        )
        .order_by("-created_at")
        .values_list("wamid", flat=True)
        .first()
    )
    return ultimo is not None and ultimo[:128] != contact.rescued_wamid


def _lo_atiende_alguien(contact, ahora=None):
    """True si desde la base se ve que este contacto ya no es del agente."""
    ahora = ahora or timezone.now()
    estado = (
        WhatsAppContact.objects.filter(pk=contact.pk)
        .values("is_blocked", "human_handoff", "human_until")
        .first()
    )
    if estado is None:
        return True
    if estado["is_blocked"] or estado["human_handoff"]:
        return True
    return bool(estado["human_until"] and estado["human_until"] > ahora)


def avisar(contact, ahora=None):
    """Corre el turno que le cuenta a ese cliente que los domicilios volvieron."""
    from apps.orders.models import StoreSettings

    from .agent import discard_turn, run_turn
    from .worker import _deliver, _phone_lock, turno_vivo

    ahora = ahora or timezone.now()
    if turno_vivo(contact.phone):
        # Escribió entre el barrido y este momento: lo atiende el camino
        # normal, que además ya sabe que hay domicilios
        return None
    marca = contact.delivery_missed_at
    if marca is None:
        return None
    desde = reactivacion(ahora=ahora)
    if desde is None:
        return None
    # El cupo se cuenta otra vez aquí, y no solo al armar la lista: entre lo
    # uno y lo otro pueden haber salido avisos de esta misma vuelta o de una
    # corrida a mano del command. Contar y reclamar van en la misma
    # transacción, con la fila de la tienda de por medio: si fueran dos pasos
    # sueltos, dos barridos con el último cupo cada uno reclamarían contactos
    # distintos y la tanda se pasaría del tope.
    #
    # Se reclama ANTES de escribir, igual que el vigía: si esto revienta a
    # mitad el cliente se queda como estaba, pero reclamarlo después
    # convertiría un fallo repetido en un mensaje cada minuto.
    with transaction.atomic():
        StoreSettings.objects.select_for_update().filter(pk=1).first()
        if _cupo(desde) <= 0:
            return None
        if not reclamar(contact, ahora):
            return None

    phone_number_id = contact.last_phone_number_id
    nota = NOTA_DOMICILIOS.format(cuanto=_cuanto(ahora - marca))
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
    # El modelo tardó lo suyo en pensar y el mundo pudo cambiar: si mientras
    # tanto apagaron los domicilios, cerraron el local, el cliente escribió o
    # alguien del equipo entró a atenderlo, este mensaje ya no es verdad o
    # sobra. Se tira antes de mandarlo. El estado del contacto se relee de la
    # base: el que hay en memoria es el de antes de pensar.
    if (
        reactivacion() is None
        or turno_vivo(contact.phone)
        or _lo_atiende_alguien(contact)
        or _escribio_mientras_tanto(contact)
    ):
        logger.info("El aviso a %s se descarta: el local cambió mientras tanto", contact.phone)
        if not turn.mutated:
            discard_turn(contact, turn.message_ids)
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
            if avisar(contact, ahora) is not None:
                avisados += 1
        except Exception:
            logger.exception("No se le pudo avisar a %s que hay domicilios", contact.phone)
    return avisados
