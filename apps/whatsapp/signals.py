"""Notificaciones automáticas por WhatsApp al cambiar el estado de un pedido.

Solo aplica a pedidos creados por el agente (source=whatsapp): esos clientes
tienen una conversación de WhatsApp activa, así que el mensaje sale como
respuesta normal dentro de la ventana de 24 horas (gratis en Meta).
El envío corre en un hilo para no bloquear el request del staff/KDS.

Estos avisos los escribimos nosotros, no el modelo, pero el cliente no
distingue: le llegan por el mismo chat y con la misma voz. Así que pasan por
el mismo filtro de palabras prohibidas que las respuestas del agente (ver
banned.py). Sin eso, prohibir una muletilla en el panel la quitaba de lo que
el agente escribe y la dejaba viva justo aquí, que es donde el negocio la vio.
"""

import logging
import threading

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.orders.models import Order

from . import banned

logger = logging.getLogger(__name__)

_SKIP = object()

# Estos avisos los manda el sistema, pero el cliente los lee como si los
# escribiera el agente: van en la misma voz. El número del pedido y la línea de
# pago son el dato, así que se dicen igual de claro que en el chat.
STATUS_MESSAGES = {
    Order.Status.PREPARING: "👨‍🍳 ¡Listo! Tu pedido {order_number} ya está en la cocina.",
    Order.Status.READY: "🛵 ¡Salió! Tu pedido {order_number} ya va en camino. {payment_line}",
    Order.Status.DELIVERED: (
        "✅ Pedido {order_number} entregado. ¡Que lo disfrutes y gracias por pedir en Frostbyte! 💙"
    ),
    Order.Status.CANCELLED: (
        "Tu pedido {order_number} quedó cancelado. Cualquier cosa nos escribes y lo solucionamos."
    ),
}

# Un pedido para recoger no sale a ninguna parte: "listo" significa que el
# cliente ya puede pasar por él, y "entregado" que se lo llevó.
PICKUP_MESSAGES = {
    Order.Status.READY: (
        "🛍️ ¡Ya está listo tu pedido {order_number}! Pasa por él cuando quieras. {payment_line}"
    ),
    Order.Status.DELIVERED: (
        "✅ Pedido {order_number} entregado. ¡Que lo disfrutes y gracias por pedir en Frostbyte! 💙"
    ),
}


def _destination(phone):
    """Contacto al que notificar y su destino de WhatsApp.

    El pedido guarda el número que el staff puede llamar. Si WhatsApp oculta el
    del cliente (contacto por BSUID), ese celular lo dio él y el mensaje debe
    salir por su identidad de WhatsApp, no por el celular.
    """
    from .models import WhatsAppContact

    contact = WhatsAppContact.objects.filter(phone=phone).first()
    if contact is None and phone:
        contact = WhatsAppContact.objects.filter(contact_phone=phone).first()
    return contact, (contact.phone if contact else phone)


@receiver(pre_save, sender=Order)
def _stash_old_status(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "status" not in update_fields:
        instance._old_status = _SKIP
        return
    if instance.pk:
        instance._old_status = (
            sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        )
    else:
        instance._old_status = None


def message_for(order):
    """El aviso que le toca a este pedido, ya listo para enviarlo.

    Devuelve "" cuando no hay nada que avisar. Sale de aquí y no del receiver
    para poder leerlo tal cual en las pruebas: lo que el cliente recibe es
    exactamente esto.
    """
    if order.order_type == Order.OrderType.PICKUP:
        template = PICKUP_MESSAGES.get(order.status, STATUS_MESSAGES.get(order.status))
    else:
        template = STATUS_MESSAGES.get(order.status)
    if not template:
        return ""

    if order.payment_method == Order.PaymentMethod.CASH:
        payment_line = "Ten listico el efectivo, porfa."
    elif order.is_paid:
        payment_line = "Tu pago ya quedó confirmado."
    elif order.order_type == Order.OrderType.PICKUP and not order.payment_method:
        payment_line = "Pagas al recogerlo."
    elif not order.payment_method:
        # El pedido se creó sin método de pago (ver missing.py): pedirle el
        # comprobante a quien nunca dijo que pagaba por Nequi lo confundiría.
        payment_line = "Cuando llegue cuadramos el pago."
    else:
        payment_line = "Si todavía no has pagado, mándanos el comprobante."

    from .models import AgentSettings

    message = template.format(order_number=order.order_number, payment_line=payment_line)
    # Lo prohibido se quita aquí y no de las plantillas de arriba: la lista la
    # escribe el negocio en el panel y cambia cuando él quiera.
    return banned.clean(message, AgentSettings.load().forbidden_words())


@receiver(post_save, sender=Order)
def _notify_status_change(sender, instance, created, **kwargs):
    if created:
        return
    old_status = getattr(instance, "_old_status", _SKIP)
    if old_status is _SKIP or old_status is None or old_status == instance.status:
        return
    if instance.source != Order.Source.WHATSAPP or not instance.customer_phone:
        return
    body = message_for(instance)
    if not body:
        return
    phone = instance.customer_phone

    def _send():
        from django.conf import settings
        from django.db import close_old_connections

        from . import kapso

        close_old_connections()
        try:
            contact, to = _destination(phone)
            phone_number_id = (contact and contact.last_phone_number_id) or (
                settings.KAPSO_PHONE_NUMBER_IDS[0] if settings.KAPSO_PHONE_NUMBER_IDS else ""
            )
            if not phone_number_id:
                logger.warning("Sin phone_number_id para notificar el pedido %s", instance.order_number)
                return
            kapso.send_text(phone_number_id, to, body)
        except Exception:
            logger.exception("Error notificando por WhatsApp el pedido %s", instance.order_number)
        finally:
            close_old_connections()

    threading.Thread(target=_send, daemon=True).start()
