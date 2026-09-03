import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Sticker, WebhookEvent
from .worker import enqueue_event

logger = logging.getLogger(__name__)


def _valid_signature(raw_body, signature):
    """Verifica la firma HMAC-SHA256 de Kapso sobre el body crudo."""
    secret = settings.KAPSO_WEBHOOK_SECRET
    if not secret:
        # Sin secret configurado solo se acepta en desarrollo
        return settings.DEBUG
    if not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.strip(), expected)


class KapsoWebhookView(APIView):
    """Recibe los webhooks de Kapso (mensajes de WhatsApp).

    Responde 200 de inmediato (Kapso exige < 10 s) y delega el turno del
    agente al worker in-process. Idempotente por X-Idempotency-Key.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw_body = request.body
        signature = request.headers.get("X-Webhook-Signature", "")
        if not _valid_signature(raw_body, signature):
            logger.warning("Webhook de Kapso con firma inválida")
            return Response({"detail": "firma inválida"}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response({"detail": "JSON inválido"}, status=status.HTTP_400_BAD_REQUEST)

        idempotency_key = (
            request.headers.get("X-Idempotency-Key")
            or hashlib.sha256(raw_body).hexdigest()
        )
        event_type = str(payload.get("event_type") or payload.get("event") or payload.get("type") or "")[:64]

        try:
            event, created = WebhookEvent.objects.get_or_create(
                idempotency_key=idempotency_key[:128],
                defaults={"payload": payload, "event_type": event_type},
            )
        except IntegrityError:
            created = False

        if created:
            enqueue_event(event.pk)
        return Response({"ok": True})


@require_GET
def sticker_file(request, pk):
    """Sirve un sticker del banco para que WhatsApp lo descargue.

    Es público a propósito: Meta busca el archivo desde sus servidores con un
    GET anónimo, así que una URL protegida haría fallar el envío. Lo único que
    hay detrás es un dibujo del negocio.
    """
    sticker = get_object_or_404(Sticker, pk=pk, is_active=True)
    response = HttpResponse(bytes(sticker.data), content_type="image/webp")
    # Meta cachea el archivo por sticker; un sticker no cambia sin cambiar de id
    response["Cache-Control"] = "public, max-age=86400"
    return response
