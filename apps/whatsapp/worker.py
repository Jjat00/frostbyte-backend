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


def _iter_entries(payload):
    """Normaliza el sobre del webhook de Kapso a una lista de entries.

    Con message buffering Kapso manda lotes: {"type": "...", "batch": true,
    "data": [{message, conversation, phone_number_id}, ...]}. Sin lote puede
    venir el entry directo en el payload o en data como dict.
    """
    data = payload.get("data")
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        return [data]
    return [payload]


def extract_inbound_messages(payload):
    """Extrae los mensajes entrantes procesables de un webhook de Kapso.

    Devuelve una lista (en orden) de dicts con phone, wa_user_id,
    contact_name, text y phone_number_id; vacía si el evento no trae mensajes
    entrantes útiles (estados de entrega, salientes, etc.).
    """
    results = []
    for entry in _iter_entries(payload):
        message = entry.get("message")
        if not isinstance(message, dict) or not message:
            continue
        kapso_meta = message.get("kapso") or {}
        if kapso_meta.get("direction") == "outbound":
            continue

        conversation = entry.get("conversation") or {}
        phone = normalize_phone(
            message.get("from")
            or kapso_meta.get("from_phone")
            or conversation.get("phone_number")
        )
        wa_user_id = (
            message.get("from_user_id")
            or kapso_meta.get("from_user_id")
            or conversation.get("business_scoped_user_id")
            or ""
        )
        phone_number_id = str(
            entry.get("phone_number_id")
            or payload.get("phone_number_id")
            or conversation.get("phone_number_id")
            or ""
        )
        if not phone and not wa_user_id:
            continue

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
            continue
        results.append(
            {
                "phone": phone,
                "wa_user_id": wa_user_id,
                "contact_name": (conversation.get("contact_name") or "").strip(),
                "text": text.strip(),
                "phone_number_id": phone_number_id,
                "message_id": message.get("id") or "",
            }
        )
    return results


def _keep_typing(phone_number_id, message_id, stop_event, max_renewals=4):
    """Muestra 'escribiendo…' mientras el agente genera la respuesta.

    El indicador de WhatsApp expira a los ~25 s, así que se renueva hasta
    max_renewals veces o hasta que stop_event se active (respuesta lista).
    """
    for _ in range(max_renewals):
        kapso.send_typing_indicator(phone_number_id, message_id)
        if stop_event.wait(20):
            return


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
    inbounds = extract_inbound_messages(event.payload)

    allowed = settings.KAPSO_PHONE_NUMBER_IDS
    if allowed:
        known = [m for m in inbounds if not m["phone_number_id"] or m["phone_number_id"] in allowed]
        if inbounds and not known:
            event.status = WebhookEvent.Status.IGNORED
            event.error = f"phone_number_id desconocido: {inbounds[0]['phone_number_id']}"
            event.save(update_fields=["status", "error"])
            return
        inbounds = known

    if not inbounds:
        event.status = WebhookEvent.Status.IGNORED
        event.save(update_fields=["status"])
        return

    # Agrupa el lote por contacto conservando el orden: los mensajes seguidos
    # de una misma persona (buffering de Kapso) van en UN solo turno del agente
    groups = {}
    for msg in inbounds:
        key = msg["phone"] or msg["wa_user_id"]
        groups.setdefault(key, []).append(msg)

    for key, messages in groups.items():
        first = messages[0]
        phone_number_id = next((m["phone_number_id"] for m in messages if m["phone_number_id"]), "")

        contact, _ = WhatsAppContact.objects.get_or_create(
            phone=key,
            defaults={"wa_user_id": first["wa_user_id"]},
        )
        updates = ["last_message_at", "updated_at"]
        contact.last_message_at = timezone.now()
        if first["wa_user_id"] and contact.wa_user_id != first["wa_user_id"]:
            contact.wa_user_id = first["wa_user_id"]
            updates.append("wa_user_id")
        if first["contact_name"] and contact.profile_name != first["contact_name"]:
            contact.profile_name = first["contact_name"][:200]
            updates.append("profile_name")
        if phone_number_id and contact.last_phone_number_id != phone_number_id:
            contact.last_phone_number_id = phone_number_id
            updates.append("last_phone_number_id")
        contact.save(update_fields=updates)

        event.contact_phone = contact.phone
        event.phone_number_id = phone_number_id

        if contact.is_blocked or contact.human_handoff or not settings.WHATSAPP_AGENT_ENABLED:
            event.status = WebhookEvent.Status.IGNORED
            event.error = "contacto bloqueado" if contact.is_blocked else "agente pausado para este contacto"
            event.save(update_fields=["status", "error", "contact_phone", "phone_number_id"])
            continue

        from .agent import run_agent

        text = "\n".join(m["text"] for m in messages)
        last_message_id = next(
            (m["message_id"] for m in reversed(messages) if m["message_id"]), ""
        )
        stop_typing = threading.Event()
        if phone_number_id and last_message_id:
            threading.Thread(
                target=_keep_typing,
                args=(phone_number_id, last_message_id, stop_typing),
                daemon=True,
            ).start()
        try:
            with _phone_lock(contact.phone):
                reply = run_agent(contact, text)
        finally:
            stop_typing.set()

        kapso.send_text(phone_number_id, contact.phone, reply)

        event.status = WebhookEvent.Status.PROCESSED
        event.error = ""
        event.save(update_fields=["status", "error", "contact_phone", "phone_number_id"])
