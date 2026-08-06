"""Cliente mínimo de la API de Kapso para enviar mensajes de WhatsApp.

Kapso expone un proxy de la Cloud API de Meta, así que los payloads son los
mismos de Meta (messaging_product/to/type). Auth por header X-API-Key.
Docs: https://docs.kapso.ai/
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MESSAGES_URL = "https://api.kapso.ai/meta/whatsapp/v24.0/{phone_number_id}/messages"
PLATFORM_MESSAGES_URL = "https://api.kapso.ai/platform/v1/whatsapp/messages"

# WhatsApp corta los mensajes de texto en 4096 caracteres
MAX_TEXT_LEN = 4000

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2

# Ventana para dar por "de esta conversación" un mensaje que no nos llegó
UNDELIVERED_LOOKBACK_MINUTES = 60


def _record_sent(data, to_phone, body=""):
    """Guarda el wamid de un mensaje enviado por el sistema.

    Los eventos whatsapp.message.sent cuyo id no esté registrado se tratan
    como intervención humana (worker._handle_outbound), así que aquí debe
    quedar TODO lo que envía el backend (agente y notificaciones). El texto se
    guarda aparte (ChatMessage) para poder resolver las respuestas citadas.
    """
    try:
        wamid = ((data or {}).get("messages") or [{}])[0].get("id")
        if wamid:
            from .models import ChatMessage, SentMessage

            SentMessage.objects.get_or_create(
                wamid=wamid[:128], defaults={"to_phone": str(to_phone or "")[:30]}
            )
            ChatMessage.remember(
                wamid, str(to_phone or ""), ChatMessage.Direction.OUTBOUND, body
            )
    except Exception:
        logger.exception("No se pudo registrar el wamid del mensaje enviado")


def recent_undelivered(phone, within_minutes=UNDELIVERED_LOOKBACK_MINUTES, limit=5):
    """Mensajes recientes del cliente que WhatsApp no nos pudo entregar.

    Llegan como tipo "unsupported" (error 131060, "This message is
    unavailable"): el contenido nunca sale del teléfono del cliente, pasa sobre
    todo con dispositivos vinculados y con números en coexistencia (app de
    WhatsApp Business + Cloud API). Kapso los guarda en la conversación pero NO
    los reenvía por webhook —su lista de tipos no los incluye—, así que la única
    forma de enterarse de que el cliente intentó mandar algo es preguntar.

    Devuelve los timestamps epoch (más reciente primero); [] ante cualquier
    fallo: esto solo informa al agente, nunca puede tumbar un turno.
    """
    if not settings.KAPSO_API_KEY or not phone:
        return []
    try:
        response = requests.get(
            PLATFORM_MESSAGES_URL,
            params={
                "phone_number": phone,
                "direction": "inbound",
                "message_type": "unsupported",
                "limit": limit,
            },
            headers={"X-API-Key": settings.KAPSO_API_KEY},
            timeout=8,
        )
        if response.status_code >= 300:
            logger.warning(
                "Kapso devolvió %s consultando los mensajes no entregados de %s",
                response.status_code,
                phone,
            )
            return []
        messages = (response.json() or {}).get("data") or []
    except (requests.RequestException, ValueError):
        logger.exception("No se pudieron consultar los mensajes no entregados de %s", phone)
        return []

    cutoff = time.time() - within_minutes * 60
    stamps = []
    for message in messages:
        try:
            stamp = int(message.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if stamp >= cutoff:
            stamps.append(stamp)
    return sorted(stamps, reverse=True)


def _post_message(phone_number_id, payload):
    """Envía un payload a la API de mensajes con reintentos ante 429/5xx."""
    if not settings.KAPSO_API_KEY:
        logger.warning("KAPSO_API_KEY no configurada; mensaje descartado: %s", payload.get("type"))
        return None

    url = MESSAGES_URL.format(phone_number_id=phone_number_id)
    headers = {
        "X-API-Key": settings.KAPSO_API_KEY,
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            if response.status_code < 300:
                data = response.json()
                if payload.get("to"):
                    _record_sent(
                        data,
                        payload.get("to"),
                        (payload.get("text") or {}).get("body", ""),
                    )
                return data
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            # Solo reintenta errores transitorios
            if response.status_code not in (429, 500, 502, 503, 504):
                break
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

    logger.error("Fallo enviando WhatsApp a %s vía %s: %s", payload.get("to"), phone_number_id, last_error)
    return None


def _split_text(body):
    """Divide un texto largo en partes que WhatsApp acepte, cortando por líneas."""
    if len(body) <= MAX_TEXT_LEN:
        return [body]
    parts, current = [], ""
    for line in body.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_TEXT_LEN:
            if current:
                parts.append(current)
            current = line[:MAX_TEXT_LEN]
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def send_text(phone_number_id, to, body):
    """Envía un mensaje de texto (dividido si supera el límite de WhatsApp)."""
    results = []
    for part in _split_text(body):
        results.append(
            _post_message(
                phone_number_id,
                {
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": part},
                },
            )
        )
    return results[-1] if results else None


def send_typing_indicator(phone_number_id, message_id):
    """Marca el mensaje como leído y muestra 'escribiendo…'.

    El indicador dura ~25 s o hasta que se envíe la respuesta.
    """
    return _post_message(
        phone_number_id,
        {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        },
    )


def send_buttons(phone_number_id, to, body, buttons):
    """Envía un mensaje con botones de respuesta rápida.

    buttons: lista de tuplas (id, titulo). WhatsApp admite máximo 3 y
    títulos de hasta 20 caracteres.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": btn_id, "title": title[:20]}}
                    for btn_id, title in buttons[:3]
                ]
            },
        },
    }
    return _post_message(phone_number_id, payload)
