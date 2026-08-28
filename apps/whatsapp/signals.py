"""Notificaciones automáticas por WhatsApp al cambiar el estado de un pedido.

Solo aplica a pedidos creados por el agente (source=whatsapp): esos clientes
tienen una conversación de WhatsApp activa, así que el mensaje sale como
respuesta normal dentro de la ventana de 24 horas (gratis en Meta).
El envío corre en un hilo para no bloquear el request del staff/KDS.
"""

import logging
import threading

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.orders.models import Order

logger = logging.getLogger(__name__)

_SKIP = object()

STATUS_MESSAGES = {
    Order.Status.PREPARING: "👨‍🍳 ¡Tu pedido {order_number} ya está en preparación!",
    Order.Status.READY: (
        "🛵 ¡Tu pedido {order_number} va en camino! {payment_line}"
    ),
    Order.Status.DELIVERED: "✅ Pedido {order_number} entregado. ¡Gracias por pedir en Frostbyte! 💙",
    Order.Status.CANCELLED: "❌ Tu pedido {order_number} fue cancelado. Escríbenos si tienes alguna duda.",
}

# Un pedido para recoger no sale a ninguna parte: "listo" significa que el
# cliente ya puede pasar por él, y "entregado" que se lo llevó.
PICKUP_MESSAGES = {
    Order.Status.READY: (
        "🛍️ ¡Tu pedido {order_number} ya está listo! Puedes pasar por él cuando quieras. {payment_line}"
    ),
    Order.Status.DELIVERED: "✅ Pedido {order_number} entregado. ¡Gracias por pedir en Frostbyte! 💙",
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


@receiver(post_save, sender=Order)
def _notify_status_change(sender, instance, created, **kwargs):
    if created:
        return
    old_status = getattr(instance, "_old_status", _SKIP)
    if old_status is _SKIP or old_status is None or old_status == instance.status:
        return
    if instance.source != Order.Source.WHATSAPP or not instance.customer_phone:
        return
    if instance.order_type == Order.OrderType.PICKUP:
        template = PICKUP_MESSAGES.get(instance.status, STATUS_MESSAGES.get(instance.status))
    else:
        template = STATUS_MESSAGES.get(instance.status)
    if not template:
        return

    if instance.payment_method == Order.PaymentMethod.CASH:
        payment_line = "Ten listo el pago en efectivo, por favor."
    elif instance.is_paid:
        payment_line = "Tu pago ya está confirmado."
    elif instance.order_type == Order.OrderType.PICKUP and not instance.payment_method:
        payment_line = "Pagas al recogerlo."
    else:
        payment_line = "Recuerda enviar el comprobante si aún no has pagado."

    message = template.format(order_number=instance.order_number, payment_line=payment_line)
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
            kapso.send_text(phone_number_id, to, message)
        except Exception:
            logger.exception("Error notificando por WhatsApp el pedido %s", instance.order_number)
        finally:
            close_old_connections()

    threading.Thread(target=_send, daemon=True).start()
