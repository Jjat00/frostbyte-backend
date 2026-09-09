"""Los datos que un pedido tomado por WhatsApp puede no traer todavía.

Un pedido a domicilio quiere ubicación, método de pago y (si WhatsApp nos
oculta el número) un celular. Pedirlos es lo correcto; condicionar el pedido a
que lleguen, no: el sábado 06/09 un cliente compartió su ubicación, WhatsApp no
nos la entregó (error 131060, ni siquiera llega el webhook) y la conversación
se murió ahí, con el agente esperando algo que nunca iba a llegar y el cliente
creyendo que ya la había mandado. Se perdió el pedido entero por un dato que el
equipo pide en diez segundos por el mismo chat.

Así que el pedido se crea igual y lo que falta queda escrito en la primera
línea de sus notas, que es lo que el equipo lee en la tarjeta sin abrirla. Y si
el dato llega después —la ubicación que el cliente reenvía— se completa solo,
sin depender de que el agente se acuerde de nada.
"""

import logging

logger = logging.getLogger(__name__)

PREFIX = "FALTA POR CONFIRMAR CON EL CLIENTE: "
LOCATION = "la ubicación"


def note(missing, notes=""):
    """Las notas del pedido con la lista de pendientes en la primera línea."""
    notes = (notes or "").strip()
    if not missing:
        return notes
    header = PREFIX + ", ".join(missing) + "."
    return header + ("\n" + notes if notes else "")


def without(notes, item):
    """Las mismas notas sin el pendiente que ya se resolvió.

    Quita solo ese renglón de la lista; si era el único, se va la línea entera
    y las notas del cliente quedan como las escribió.
    """
    notes = notes or ""
    if not notes.startswith(PREFIX):
        return notes
    head, _, rest = notes.partition("\n")
    left = [
        part
        for part in head[len(PREFIX) :].rstrip(".").split(", ")
        if part and not part.startswith(item)
    ]
    if not left:
        return rest.strip()
    return note(left, rest)


def attach_location(contact, lat, lng):
    """Pone la ubicación recién compartida en los pedidos que la esperaban.

    El cliente la manda cuando puede —a veces después de que el pedido ya
    entró—, y para él eso ya es haberla dado: nadie va a repetirla porque el
    sistema la pidió tarde. Toca solo pedidos suyos, a domicilio, todavía en
    curso y sin coordenadas.
    """
    from apps.orders.models import Order

    phones = {contact.phone}
    if contact.contact_phone:
        phones.add(contact.contact_phone)
    pending = Order.objects.filter(
        source=Order.Source.WHATSAPP,
        order_type=Order.OrderType.DELIVERY,
        customer_phone__in=phones,
        delivery_lat__isnull=True,
        status__in=[Order.Status.PENDING, Order.Status.PREPARING, Order.Status.READY],
    )
    updated = []
    for order in pending:
        order.delivery_lat = lat
        order.delivery_lng = lng
        order.customer_notes = without(order.customer_notes, LOCATION)
        # update_fields sin 'status': el aviso de WhatsApp al cliente solo sale
        # cuando cambia el estado (ver signals), y aquí no cambia nada suyo.
        order.save(update_fields=["delivery_lat", "delivery_lng", "customer_notes", "updated_at"])
        updated.append(order.order_number)
    if updated:
        logger.info("Ubicación tardía aplicada a los pedidos %s", ", ".join(updated))
        from apps.orders.consumers import broadcast_orders_update

        broadcast_orders_update()
    return updated
