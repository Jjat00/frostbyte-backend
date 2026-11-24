"""Mensajes que Meta rechaza después de que Kapso los aceptó.

Kapso responde 200 con el wamid y el envío parece bueno, pero Meta puede
rechazarlo segundos después: la ventana de 24 h estaba cerrada (131047), la
cuenta no tenía facturación configurada (131042)... El rechazo llega como un
webhook más, con message.kapso.status = "failed", y antes nadie lo miraba: los
resúmenes al dueño del 20 y el 21 de noviembre de 2026 y el aviso de una
clienta que esperaba a una persona se perdieron sin dejar rastro en los logs.

Con cada rechazo se hacen tres cosas:

1. Queda en el log como error, con el código y el detalle de Meta (el mismo
   código 131042 cubre la moneda sin configurar y el método de pago: lo que
   dice qué pasó es el detalle).
2. Queda anotado en SentMessage (failed_at, error_code, error_detail).
3. Si iba para el dueño, se reintenta UNA vez por la otra vía: un texto
   rechazado (ventana cerrada) sale como la plantilla WHATSAPP_OWNER_ALERT_TEMPLATE,
   y una plantilla rechazada sale como texto libre, que llega si el dueño
   escribió en las últimas 24 h. El reenvío queda marcado (is_fallback) y, si
   también lo rechazan, solo se anota: no hay tercer intento.

A los clientes no se les reintenta: fuera de la ventana solo podría ir una
plantilla, y no hay una que sirva para cualquier respuesta del agente.
"""

import logging
import time

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import kapso
from .models import AgentSettings, SentMessage

logger = logging.getLogger(__name__)

# El rechazo puede llegar antes de que _record_sent guarde el wamid del envío
# (pasa lo mismo con los salientes, ver worker._handle_outbound): se espera
# esto y se vuelve a mirar.
RECORD_GRACE_SECONDS = 3

# El cuerpo de una plantilla admite 1024 caracteres en total; el texto fijo de
# aviso_dueno ocupa unos 120.
MAX_ALERT_CHARS = 850


def _code(error):
    try:
        return int(error.get("code"))
    except (TypeError, ValueError):
        return None


def extract_failures(entries):
    """Los mensajes salientes rechazados que trae un webhook.

    `entries` son los del sobre ya normalizado (worker._iter_entries). Cada
    rechazo es un dict con wamid, to, type, code, title, detail, content y
    phone_number_id.
    """
    failures = []
    for entry in entries:
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        meta = message.get("kapso") or {}
        statuses = [
            s for s in meta.get("statuses") or []
            if isinstance(s, dict) and s.get("status") == "failed"
        ]
        if meta.get("status") != "failed" and not statuses:
            continue
        errors = [e for s in statuses for e in s.get("errors") or [] if isinstance(e, dict)]
        error = errors[0] if errors else {}
        failures.append(
            {
                "wamid": message.get("id") or "",
                "to": message.get("to") or message.get("to_user_id") or "",
                "type": message.get("type") or "",
                "code": _code(error),
                "title": error.get("title") or error.get("message") or "",
                "detail": (error.get("error_data") or {}).get("details") or "",
                "content": meta.get("content") or (message.get("text") or {}).get("body", ""),
                "phone_number_id": str(entry.get("phone_number_id") or ""),
            }
        )
    return failures


def handle_failures(failures):
    for failure in failures:
        try:
            _handle(failure)
        except Exception:
            logger.exception("No se pudo tratar el rechazo del mensaje %s", failure.get("wamid"))


def _handle(failure):
    wamid = failure["wamid"][:128]
    if wamid and not SentMessage.objects.filter(wamid=wamid).exists():
        time.sleep(RECORD_GRACE_SECONDS)
        close_old_connections()
    # Solo el primer aviso del rechazo actúa: Meta y Kapso repiten estados.
    # Si no hay fila, el mensaje no lo mandó el backend (inbox o app) y no
    # hay nada que reintentar, pero igual queda en el log.
    first = wamid and SentMessage.objects.filter(wamid=wamid, failed_at__isnull=True).update(
        failed_at=timezone.now(),
        error_code=failure["code"],
        error_detail=(failure["detail"] or failure["title"])[:300],
    )
    if not first and wamid and SentMessage.objects.filter(wamid=wamid).exists():
        return  # el mismo rechazo otra vez
    logger.error(
        "Meta rechazó el mensaje %s (%s) a %s: %s %s. %s",
        wamid, failure["type"], failure["to"], failure["code"], failure["title"], failure["detail"],
    )
    if not first:
        return
    if SentMessage.objects.filter(wamid=wamid, is_fallback=True).exists():
        logger.error("También rechazaron el reenvío %s; no hay otro intento", wamid)
        return
    if AgentSettings.load().is_owner(failure["to"]):
        _resend_to_owner(failure)


def _resend_to_owner(failure):
    """Segundo intento al dueño por la vía que el primero no usó."""
    content = (failure["content"] or "").strip()
    if not content:
        logger.warning("Rechazo %s sin texto que reenviar al dueño", failure["wamid"])
        return
    ids = settings.KAPSO_PHONE_NUMBER_IDS
    phone_number_id = failure["phone_number_id"] or (ids[0] if ids else "")
    if not phone_number_id:
        return

    if failure["type"] == "template":
        data = kapso.send_text(phone_number_id, failure["to"], content, fallback=True)
    else:
        text = " ".join(content.split())
        if len(text) > MAX_ALERT_CHARS:
            text = text[: MAX_ALERT_CHARS - 1].rstrip() + "…"
        data = kapso.send_template(
            phone_number_id,
            failure["to"],
            settings.WHATSAPP_OWNER_ALERT_TEMPLATE,
            settings.WHATSAPP_REPORT_TEMPLATE_LANGUAGE,
            [text],
            fallback=True,
        )

    new_wamid = ((data or {}).get("messages") or [{}])[0].get("id")
    if new_wamid:
        logger.info("Rechazo %s reenviado al dueño como %s", failure["wamid"], new_wamid)
    else:
        logger.error("No se pudo reenviar al dueño el mensaje rechazado %s", failure["wamid"])
