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

# WhatsApp corta los mensajes de texto en 4096 caracteres
MAX_TEXT_LEN = 4000

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2


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
                return response.json()
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
