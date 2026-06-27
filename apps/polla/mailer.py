"""Cliente mínimo de Resend (API REST) para los correos de la Polla.

No usamos el SDK oficial: ``requests`` ya es dependencia y así controlamos las
cabeceras de entregabilidad (``List-Unsubscribe``) y los reintentos. La API key
vive en ``settings.RESEND_API_KEY`` (variable de entorno ``RESEND_API_KEY``).

Resend exige enviar desde un dominio verificado (SPF/DKIM/DMARC); el remitente
y el Reply-To se configuran en settings.
"""
from __future__ import annotations

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


class EmailError(Exception):
    """Falló el envío de un correo por Resend."""


def send_email(
    *,
    to,
    subject,
    html,
    text=None,
    headers=None,
    reply_to=None,
    from_email=None,
    tags=None,
    attachments=None,
    max_retries=4,
    timeout=20,
):
    """Envía un correo por Resend y devuelve el id del mensaje.

    Reintenta con backoff exponencial ante rate-limit (429) o errores 5xx;
    levanta ``EmailError`` en fallos 4xx no recuperables o tras agotar los
    reintentos.

    ``attachments``: lista de dicts con el formato de la API REST de Resend
    (``content`` en base64, ``filename``, ``content_type`` y, para imágenes
    embebidas, ``content_id`` referenciado en el HTML como ``cid:<id>``).
    """
    api_key = getattr(settings, "RESEND_API_KEY", "")
    if not api_key:
        raise EmailError("RESEND_API_KEY no está configurada.")

    payload = {
        "from": from_email or settings.POLLA_EMAIL_FROM,
        "to": [to] if isinstance(to, str) else list(to),
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    reply = reply_to or getattr(settings, "POLLA_EMAIL_REPLY_TO", "")
    if reply:
        payload["reply_to"] = reply
    if headers:
        payload["headers"] = headers
    if tags:
        payload["tags"] = tags
    if attachments:
        payload["attachments"] = list(attachments)

    auth = {"Authorization": f"Bearer {api_key}"}
    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(RESEND_ENDPOINT, json=payload, headers=auth, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise EmailError(f"Error de red al enviar a {payload['to']}: {exc}") from exc
            time.sleep(backoff)
            backoff *= 2
            continue

        if resp.status_code == 200:
            return (resp.json() or {}).get("id", "")

        # Rate limit (429) o caída temporal (5xx): reintentar.
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == max_retries:
                raise EmailError(
                    f"Resend {resp.status_code} tras {max_retries} intentos: {resp.text[:300]}"
                )
            retry_after = resp.headers.get("retry-after")
            try:
                wait = float(retry_after) if retry_after else backoff
            except ValueError:
                wait = backoff
            time.sleep(wait)
            backoff *= 2
            continue

        # 4xx no recuperable (dominio no verificado, payload inválido, etc.).
        raise EmailError(f"Resend {resp.status_code}: {resp.text[:300]}")

    raise EmailError("No se pudo enviar el correo (agotados los reintentos).")
