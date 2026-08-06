"""Procesamiento en background de los webhooks de Kapso.

El webhook debe responder 200 en menos de 10 segundos, así que el turno del
agente (que llama al LLM) corre en un ThreadPoolExecutor in-process, igual que
el realtime loop de la Polla.

Los mensajes NO se responden uno a uno: cada contacto tiene una cola y un solo
loop que espera a que deje de escribir (ventana deslizante) antes de llamar al
agente, de modo que "Hola" + "quiero pedir" sean UN turno y UNA respuesta. Si
el cliente escribe mientras el agente ya está generando, esa respuesta se
descarta (nació incompleta) y se rehace con todo junto; salvo que el turno ya
haya tocado la base de datos, en cuyo caso se envía y lo nuevo va al siguiente.
El buffering de Kapso hace lo mismo aguas arriba, pero su ventana termina al
entregar el webhook: no cubre al cliente que escribe con el agente pensando.
"""

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import kapso
from . import media as wa_media
from .models import SentMessage, WebhookEvent, WhatsAppContact
from .tools import normalize_phone

logger = logging.getLogger(__name__)

# Dos pools separados a propósito: los turnos pasan la mayor parte del tiempo
# esperando (ventana deslizante + LLM), así que no pueden robarle los hilos al
# procesado de webhooks o los mensajes nuevos no llegarían a encolarse nunca
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wa-hook")
_turn_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="wa-agent")
_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()

# Cola de mensajes por contacto: {phone: {messages, event_ids, phone_number_id,
# first, last, aborts}}. _active marca los teléfonos con loop vivo.
_pending = {}
_pending_guard = threading.Lock()
_active = set()

# Tope de respuestas descartadas seguidas: con un cliente que escribe sin parar
# hay que contestar en algún momento aunque llegue otro mensaje justo después
MAX_ABORTS = 2


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
        media = None  # audio/imagen a resolver después (descarga + OpenAI)
        location_coords = None  # lat/lng compartidos, se guardan en el contacto
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
                f"nombre: {location.get('name') or 'N/A'}, dirección: {location.get('address') or 'N/A'}. "
                "Ya quedó registrada: verifícala con verificar_cobertura.]"
            )
            try:
                location_coords = {
                    "lat": float(location["latitude"]),
                    "lng": float(location["longitude"]),
                }
            except (KeyError, TypeError, ValueError):
                location_coords = None
        elif msg_type == "image":
            image = message.get("image") or {}
            caption = image.get("caption", "")
            if image.get("id"):
                media = {"kind": "image", "media_id": image["id"], "caption": caption}
            # Texto de respaldo si la descarga o la visión fallan
            text = (
                "[El cliente envió una imagen que no se pudo procesar"
                + (f'; escribió: "{caption}"' if caption else "")
                + ". Si esperabas un comprobante de pago, dile que el equipo lo verificará.]"
            )
        elif msg_type == "audio":
            audio = message.get("audio") or {}
            if audio.get("id"):
                media = {"kind": "audio", "media_id": audio["id"]}
            text = (
                "[El cliente envió una nota de voz que no se pudo transcribir. "
                "Pídele amablemente que lo escriba.]"
            )
        elif msg_type in ("video", "document", "sticker"):
            caption = (message.get(msg_type) or {}).get("caption", "")
            text = (
                f"[El cliente envió un(a) {msg_type} que no puedes ver"
                + (f'; escribió: "{caption}"' if caption else "")
                + ". Si esperabas un comprobante de pago, dile que el equipo lo verificará.]"
            )
        else:
            text = kapso_meta.get("content") or ""

        if not text.strip() and not media:
            continue
        results.append(
            {
                "phone": phone,
                "wa_user_id": wa_user_id,
                "contact_name": (conversation.get("contact_name") or "").strip(),
                "text": text.strip(),
                "media": media,
                "location": location_coords,
                "phone_number_id": phone_number_id,
                "message_id": message.get("id") or "",
            }
        )
    return results


def extract_outbound_messages(payload):
    """Extrae los mensajes salientes (whatsapp.message.sent) de un webhook.

    Devuelve dicts con phone (el cliente destinatario), wamid, origin
    (cloud_api | business_app | history_sync), text y phone_number_id; vacía
    si el evento no trae salientes.
    """
    event_type = str(
        payload.get("event_type") or payload.get("event") or payload.get("type") or ""
    )
    results = []
    for entry in _iter_entries(payload):
        message = entry.get("message")
        if not isinstance(message, dict) or not message:
            continue
        kapso_meta = message.get("kapso") or {}
        direction = kapso_meta.get("direction")
        if direction != "outbound" and not event_type.endswith("message.sent"):
            continue
        if direction and direction != "outbound":
            continue

        conversation = entry.get("conversation") or {}
        phone = normalize_phone(
            message.get("to")
            or kapso_meta.get("to_phone")
            or conversation.get("phone_number")
        )
        msg_type = message.get("type")
        if msg_type == "text":
            text = (message.get("text") or {}).get("body", "")
        else:
            text = kapso_meta.get("content") or ""
        if not text.strip() and msg_type:
            text = f"[El equipo le envió un(a) {msg_type} al cliente]"
        results.append(
            {
                "phone": phone,
                "wamid": message.get("id") or "",
                "origin": kapso_meta.get("origin") or "",
                "text": text.strip(),
                "phone_number_id": str(
                    entry.get("phone_number_id")
                    or payload.get("phone_number_id")
                    or conversation.get("phone_number_id")
                    or ""
                ),
            }
        )
    return results


def _resolve_media(msg, phone_number_id):
    """Convierte el media de un mensaje en texto para el agente.

    Audios -> transcripción; imágenes -> descripción (con extracción de datos
    si es un comprobante). Si algo falla se usa el texto de respaldo.
    """
    media = msg.get("media")
    if not media or not phone_number_id or not settings.OPENAI_API_KEY:
        return msg["text"]
    try:
        if media["kind"] == "audio":
            transcript = wa_media.transcribe_audio(media["media_id"], phone_number_id)
            if transcript:
                return f'[Nota de voz del cliente, transcrita]: "{transcript}"'
        elif media["kind"] == "image":
            description = wa_media.describe_image(media["media_id"], phone_number_id)
            if description:
                text = f"[El cliente envió una imagen. Contenido: {description}]"
                if media.get("caption"):
                    text += f'\nJunto a la imagen escribió: "{media["caption"]}"'
                return text
    except Exception:
        logger.exception("No se pudo procesar el media %s", media.get("media_id"))
    return msg["text"]


def _keep_typing(phone_number_id, message_id, stop_event, max_renewals=6):
    """Muestra 'escribiendo…' desde que llega el mensaje hasta la respuesta.

    Cubre también la espera de la ventana deslizante (si no, el cliente vería
    el chat muerto varios segundos). El indicador de WhatsApp expira a los
    ~25 s, así que se renueva hasta max_renewals veces o hasta que stop_event
    se active (respuesta enviada).
    """
    for _ in range(max_renewals):
        kapso.send_typing_indicator(phone_number_id, message_id)
        if stop_event.wait(20):
            return


def enqueue_event(event_id):
    """Encola el procesamiento de un WebhookEvent ya persistido."""
    _executor.submit(_process_event_safe, event_id)


def _enqueue_turn(contact, messages, phone_number_id, event_id):
    """Acumula mensajes del cliente y arranca su loop si no estaba vivo."""
    now = time.monotonic()
    with _pending_guard:
        slot = _pending.setdefault(
            contact.phone,
            {
                "messages": [],
                "event_ids": set(),
                "phone_number_id": "",
                "first": now,
                "aborts": 0,
            },
        )
        slot["messages"].extend(messages)
        slot["event_ids"].add(event_id)
        slot["last"] = now
        if phone_number_id:
            slot["phone_number_id"] = phone_number_id
        start = contact.phone not in _active
        if start:
            _active.add(contact.phone)
    if start:
        _turn_executor.submit(_turn_loop_safe, contact.phone)


def _wait_and_drain(phone):
    """Espera a que el cliente pare de escribir y devuelve todo lo acumulado.

    Ventana deslizante: cada mensaje nuevo reinicia la cuenta, con un tope duro
    desde el primero para que quien escribe sin parar no se quede sin
    respuesta. Devuelve None cuando ya no queda nada (fin del loop).
    """
    wait = settings.WHATSAPP_BATCH_WAIT_SECONDS
    max_wait = settings.WHATSAPP_BATCH_MAX_WAIT_SECONDS
    while True:
        with _pending_guard:
            slot = _pending.get(phone)
            if not slot or not slot["messages"]:
                _pending.pop(phone, None)
                _active.discard(phone)
                return None
            target = min(slot["last"] + wait, slot["first"] + max_wait)
            now = time.monotonic()
            if now >= target:
                return _pending.pop(phone)
        time.sleep(min(target - now, 0.5))


def _has_pending(phone):
    with _pending_guard:
        slot = _pending.get(phone)
        return bool(slot and slot["messages"])


def _requeue(phone, batch):
    """Devuelve a la cola un lote cuyo turno se descartó, sin perder el orden."""
    with _pending_guard:
        slot = _pending.get(phone)
        if slot is None:
            slot = dict(batch)
            slot["aborts"] = batch.get("aborts", 0) + 1
            _pending[phone] = slot
            return
        slot["messages"] = batch["messages"] + slot["messages"]
        slot["event_ids"] |= batch["event_ids"]
        slot["phone_number_id"] = slot["phone_number_id"] or batch["phone_number_id"]
        slot["first"] = min(slot["first"], batch["first"])
        slot["aborts"] = max(slot.get("aborts", 0), batch.get("aborts", 0)) + 1


def _start_typing(phone, stop_event):
    """Arranca el 'escribiendo…' para lo que ya hay en la cola de este contacto."""
    with _pending_guard:
        slot = _pending.get(phone)
        if not slot or not slot["messages"]:
            return
        phone_number_id = slot["phone_number_id"]
        message_id = next(
            (m["message_id"] for m in reversed(slot["messages"]) if m["message_id"]), ""
        )
    if phone_number_id and message_id:
        threading.Thread(
            target=_keep_typing,
            args=(phone_number_id, message_id, stop_event),
            daemon=True,
        ).start()


def _close_events(event_ids, status, error=""):
    WebhookEvent.objects.filter(pk__in=event_ids).update(status=status, error=error)


def _turn_loop_safe(phone):
    try:
        _turn_loop(phone)
    except Exception:
        logger.exception("Error en el loop de turnos de %s", phone)
        # Se libera el teléfono (no los mensajes): el siguiente que llegue
        # arranca un loop nuevo y arrastra lo que quedó pendiente
        with _pending_guard:
            _active.discard(phone)
    finally:
        close_old_connections()


def _turn_loop(phone):
    """Único loop por contacto: espera, corre el turno, repite hasta vaciar."""
    close_old_connections()
    while True:
        stop_typing = threading.Event()
        _start_typing(phone, stop_typing)
        try:
            batch = _wait_and_drain(phone)
            if batch is None:
                return
            _run_turn(phone, batch)
        finally:
            stop_typing.set()


def _run_turn(phone, batch):
    """Corre un turno del agente con todos los mensajes acumulados."""
    from .agent import discard_turn, record_messages, run_turn

    close_old_connections()
    try:
        contact = WhatsAppContact.objects.get(phone=phone)
    except WhatsAppContact.DoesNotExist:
        return
    phone_number_id = batch["phone_number_id"]
    messages = batch["messages"]

    # Un humano pudo intervenir mientras esperábamos: entonces el agente no
    # responde, solo deja lo que dijo el cliente en el hilo
    if contact.human_handoff or (contact.human_until and contact.human_until > timezone.now()):
        try:
            text = "\n".join(_resolve_media(m, phone_number_id) for m in messages)
            with _phone_lock(phone):
                record_messages(contact, [("user", text)])
        except Exception:
            logger.exception("No se pudo guardar el mensaje del cliente %s en pausa", phone)
        _close_events(batch["event_ids"], WebhookEvent.Status.IGNORED, "agente pausado: humano atendiendo")
        return

    with _phone_lock(phone):
        # Los audios/imágenes se resuelven aquí (descarga + OpenAI) para que el
        # 'escribiendo…' ya esté visible mientras tanto
        text = "\n".join(_resolve_media(m, phone_number_id) for m in messages)
        turn = run_turn(contact, text)

        # ¿Escribió mientras el agente pensaba? Entonces esta respuesta ya nació
        # incompleta: se descarta y el lote vuelve a la cola para rehacerse
        # entero. Un turno que ya creó o cambió un pedido NO se puede descartar.
        if _has_pending(phone) and not turn.mutated and batch.get("aborts", 0) < MAX_ABORTS:
            discard_turn(contact, turn.message_ids)
            _requeue(phone, batch)
            return

    kapso.send_text(phone_number_id, contact.phone, turn.reply)
    _close_events(batch["event_ids"], WebhookEvent.Status.PROCESSED)


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


def _handle_outbound(event, outbounds):
    """Detecta intervención humana en los eventos whatsapp.message.sent.

    Un saliente cuyo wamid no registró el backend (SentMessage) lo envió un
    humano del equipo (inbox de Kapso o app de WhatsApp Business): se pausa
    el agente para ese contacto (ventana deslizante) y el mensaje queda
    guardado en el hilo como respuesta del asistente.
    """
    human, unmatched = [], []
    for msg in outbounds:
        if msg["origin"] == "history_sync":
            continue
        if msg["wamid"] and SentMessage.objects.filter(wamid=msg["wamid"]).exists():
            continue
        if msg["origin"] == "business_app":
            human.append(msg)  # la app del celular siempre es un humano
        else:
            unmatched.append(msg)

    # El webhook puede llegar antes de que _record_sent guarde el wamid de un
    # envío del propio sistema: gracia corta y segunda verificación
    if unmatched:
        time.sleep(3)
        close_old_connections()
        for msg in unmatched:
            if msg["wamid"] and SentMessage.objects.filter(wamid=msg["wamid"]).exists():
                continue
            human.append(msg)

    if not human:
        event.status = WebhookEvent.Status.IGNORED
        event.error = "salientes del propio sistema"
        event.save(update_fields=["status", "error"])
        return

    from .agent import record_messages

    pause = timedelta(minutes=settings.WHATSAPP_HUMAN_PAUSE_MINUTES)
    groups = {}
    for msg in human:
        if msg["phone"]:
            groups.setdefault(msg["phone"], []).append(msg)

    for phone, messages in groups.items():
        contact, _ = WhatsAppContact.objects.get_or_create(phone=phone)
        contact.human_until = timezone.now() + pause
        contact.save(update_fields=["human_until", "updated_at"])
        event.contact_phone = phone
        try:
            with _phone_lock(contact.phone):
                record_messages(
                    contact, [("assistant", m["text"]) for m in messages]
                )
        except Exception:
            logger.exception("No se pudo guardar el mensaje humano en el hilo de %s", phone)

    # Los wamids viejos ya no se necesitan (los eventos llegan en segundos)
    SentMessage.objects.filter(created_at__lt=timezone.now() - timedelta(days=7)).delete()

    event.status = WebhookEvent.Status.PROCESSED
    event.error = ""
    event.save(update_fields=["status", "error", "contact_phone"])


def _process_event(event):
    allowed = settings.KAPSO_PHONE_NUMBER_IDS

    outbounds = extract_outbound_messages(event.payload)
    if allowed:
        outbounds = [
            m for m in outbounds if not m["phone_number_id"] or m["phone_number_id"] in allowed
        ]
    if outbounds:
        _handle_outbound(event, outbounds)
        return

    inbounds = extract_inbound_messages(event.payload)

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
        location = next(
            (m["location"] for m in reversed(messages) if m.get("location")), None
        )
        if location:
            contact.last_location_lat = Decimal(str(round(location["lat"], 7)))
            contact.last_location_lng = Decimal(str(round(location["lng"], 7)))
            contact.last_location_at = timezone.now()
            updates += ["last_location_lat", "last_location_lng", "last_location_at"]
        contact.save(update_fields=updates)

        event.contact_phone = contact.phone
        event.phone_number_id = phone_number_id

        if contact.is_blocked or not settings.WHATSAPP_AGENT_ENABLED:
            event.status = WebhookEvent.Status.IGNORED
            event.error = "contacto bloqueado" if contact.is_blocked else "agente desactivado"
            event.save(update_fields=["status", "error", "contact_phone", "phone_number_id"])
            continue

        paused_by_human = contact.human_until and contact.human_until > timezone.now()
        if contact.human_handoff or paused_by_human:
            # Un humano está atendiendo: no se responde, pero lo que dice el
            # cliente queda en el hilo para que el agente retome con contexto
            from .agent import record_messages

            try:
                text = "\n".join(_resolve_media(m, phone_number_id) for m in messages)
                with _phone_lock(contact.phone):
                    record_messages(contact, [("user", text)])
            except Exception:
                logger.exception("No se pudo guardar el mensaje del cliente %s en pausa", contact.phone)
            event.status = WebhookEvent.Status.IGNORED
            event.error = "agente pausado: humano atendiendo"
            event.save(update_fields=["status", "error", "contact_phone", "phone_number_id"])
            continue

        # No se responde aquí: los mensajes van a la cola del contacto y su
        # loop decide cuándo llamar al agente (una respuesta por ráfaga)
        _enqueue_turn(contact, messages, phone_number_id, event.pk)
        event.status = WebhookEvent.Status.PENDING
        event.error = "en cola: esperando a que el cliente termine de escribir"
        event.save(update_fields=["status", "error", "contact_phone", "phone_number_id"])
