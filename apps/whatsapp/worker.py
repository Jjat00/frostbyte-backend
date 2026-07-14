"""Procesamiento en background de los webhooks de Kapso.

El webhook debe responder 200 en menos de 10 segundos, así que el turno del
agente (que llama al LLM) corre en un ThreadPoolExecutor in-process, igual que
el realtime loop de la Polla. Un lock por teléfono garantiza que los mensajes
de un mismo cliente se procesen en orden.
"""

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import kapso
from .models import WebhookEvent, WhatsAppContact
from .tools import normalize_phone

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wa-agent")
_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _phone_lock(phone):
    with _locks_guard:
        return _locks[phone]


def extract_inbound_message(payload):
    """Extrae (phone, wa_user_id, text, phone_number_id) de un webhook de Kapso.

    Devuelve None si el evento no es un mensaje entrante procesable (estados de
    entrega, mensajes salientes, tipos sin texto útil).
    """
    message = payload.get("message") or payload.get("data", {}).get("message") or {}
    if not message:
        return None
    kapso_meta = message.get("kapso") or {}
    if kapso_meta.get("direction") == "outbound":
        return None

    conversation = payload.get("conversation") or payload.get("data", {}).get("conversation") or {}
    phone = normalize_phone(
        message.get("from")
        or kapso_meta.get("from_phone")
        or conversation.get("phone_number")
    )
    wa_user_id = kapso_meta.get("from_user_id") or ""
    phone_number_id = str(
        payload.get("phone_number_id")
        or payload.get("data", {}).get("phone_number_id")
        or ""
    )
    if not phone and not wa_user_id:
        return None

    msg_type = message.get("type")
    text = ""
    if msg_type == "text":
        text = (message.get("text") or {}).get("body", "")
    elif msg_type == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = reply.get("title") or reply.get("id") or ""
    elif msg_type == "location":
        location = message.get("location") or {}
        text = (
            "[El cliente compartió su ubicación por WhatsApp: "
            f"lat {location.get('latitude')}, lng {location.get('longitude')}, "
            f"nombre: {location.get('name') or 'N/A'}, dirección: {location.get('address') or 'N/A'}]"
        )
    elif msg_type in ("image", "video", "document", "sticker"):
        caption = (message.get(msg_type) or {}).get("caption", "")
        text = (
            f"[El cliente envió un(a) {msg_type} que no puedes ver"
            + (f'; escribió: "{caption}"' if caption else "")
            + ". Si esperabas un comprobante de pago, dile que el equipo lo verificará.]"
        )
    elif msg_type == "audio":
        text = "[El cliente envió un audio que no puedes escuchar. Pídele amablemente que lo escriba.]"
    else:
        text = kapso_meta.get("content") or ""

    if not text.strip():
        return None
    return phone, wa_user_id, text.strip(), phone_number_id


def enqueue_event(event_id):
    """Encola el procesamiento de un WebhookEvent ya persistido."""
    _executor.submit(_process_event_safe, event_id)


def _process_event_safe(event_id):
    close_old_connections()
    try:
        event = WebhookEvent.objects.get(pk=event_id)
    except WebhookEvent.DoesNotExist:
        return
    try:
        _process_event(event)
    except Exception:
        logger.exception("Error procesando webhook %s", event_id)
        event.status = WebhookEvent.Status.FAILED
        import traceback

        event.error = traceback.format_exc()[-2000:]
        event.save(update_fields=["status", "error"])
    finally:
        close_old_connections()


def _process_event(event):
    inbound = extract_inbound_message(event.payload)
    if inbound is None:
        event.status = WebhookEvent.Status.IGNORED
        event.save(update_fields=["status"])
        return

    phone, wa_user_id, text, phone_number_id = inbound

    allowed = settings.KAPSO_PHONE_NUMBER_IDS
    if allowed and phone_number_id and phone_number_id not in allowed:
        event.status = WebhookEvent.Status.IGNORED
        event.error = f"phone_number_id desconocido: {phone_number_id}"
        event.save(update_fields=["status", "error"])
        return

    contact, _ = WhatsAppContact.objects.get_or_create(
        phone=phone or wa_user_id,
        defaults={"wa_user_id": wa_user_id},
    )
    updates = ["last_message_at", "updated_at"]
    contact.last_message_at = timezone.now()
    if wa_user_id and contact.wa_user_id != wa_user_id:
        contact.wa_user_id = wa_user_id
        updates.append("wa_user_id")
    if phone_number_id and contact.last_phone_number_id != phone_number_id:
        contact.last_phone_number_id = phone_number_id
        updates.append("last_phone_number_id")
    contact.save(update_fields=updates)

    event.contact_phone = contact.phone
    event.phone_number_id = phone_number_id

    if contact.is_blocked or contact.human_handoff or not settings.WHATSAPP_AGENT_ENABLED:
        event.status = WebhookEvent.Status.IGNORED
        event.error = "agente pausado para este contacto" if not contact.is_blocked else "contacto bloqueado"
        event.save(update_fields=["status", "error", "contact_phone", "phone_number_id"])
        return

    from .agent import run_agent

    with _phone_lock(contact.phone):
        reply = run_agent(contact, text)

    kapso.send_text(phone_number_id, phone or contact.phone, reply)

    event.status = WebhookEvent.Status.PROCESSED
    event.save(update_fields=["status", "contact_phone", "phone_number_id"])
