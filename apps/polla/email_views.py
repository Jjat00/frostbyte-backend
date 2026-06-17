"""Vista pública de baja de correos de la Polla.

El token es un valor firmado con ``django.core.signing`` (no se guarda en BD).
Soporta el flujo one-click de RFC 8058 (POST) que usan Gmail/Apple Mail para el
botón "Cancelar suscripción", y el GET para cuando la persona abre el enlace.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .email_reminders import UNSUB_SALT

User = get_user_model()


def _load_user(token):
    try:
        data = signing.loads(token, salt=UNSUB_SALT)  # sin caducidad
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=data.get("uid")).first()


def _set_opt_out(user, value):
    if user.email_opt_out != value:
        user.email_opt_out = value
        user.save(update_fields=["email_opt_out"])


@csrf_exempt
def email_unsubscribe(request, token):
    user = _load_user(token)

    # One-click (RFC 8058): dar de baja sin más interacción ni página.
    if request.method == "POST":
        if user:
            _set_opt_out(user, True)
        return HttpResponse("OK", content_type="text/plain")

    resubscribed = False
    if user:
        if request.GET.get("action") == "resubscribe":
            _set_opt_out(user, False)
            resubscribed = True
        else:
            _set_opt_out(user, True)

    return render(
        request,
        "polla/email/unsubscribe.html",
        {
            "ok": user is not None,
            "email": user.email if user else "",
            "resubscribed": resubscribed,
            "polla_url": settings.POLLA_PUBLIC_URL,
        },
    )
