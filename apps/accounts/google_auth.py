"""Utilidades para autenticar clientes mediante Google Identity Services.

El frontend usa Google Identity Services (botón "Sign in with Google"), que
entrega un ``credential`` (un id_token en formato JWT). Aquí verificamos ese
token contra Google y resolvemos el usuario local correspondiente.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import get_random_string

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

User = get_user_model()


class GoogleAuthError(Exception):
    """Error de verificación o configuración del login con Google."""


def verify_google_credential(credential):
    """Verifica el id_token de Google y devuelve su payload.

    Lanza ``GoogleAuthError`` si el servidor no está configurado o si el
    token es inválido/expirado/de otra audiencia.
    """
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise GoogleAuthError(
            "El login con Google no está configurado en el servidor "
            "(falta GOOGLE_CLIENT_ID)."
        )

    try:
        idinfo = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            client_id,
        )
    except ValueError as exc:
        raise GoogleAuthError("Token de Google inválido o expirado.") from exc

    if not idinfo.get("email_verified", False):
        raise GoogleAuthError("El correo de Google no está verificado.")

    return idinfo


def _unique_username(base):
    """Genera un username único a partir de una base (email o nombre)."""
    base = (base or "cliente").split("@")[0]
    # Mantener solo caracteres válidos para AbstractUser.username
    base = "".join(c for c in base if c.isalnum() or c in "._-") or "cliente"
    base = base[:140]
    username = base
    while User.objects.filter(username=username).exists():
        username = f"{base}_{get_random_string(5)}"[:150]
    return username


@transaction.atomic
def get_or_create_google_user(idinfo):
    """Resuelve (o crea) el usuario local asociado a la cuenta de Google.

    Estrategia de vinculación:
    1. Por ``google_sub`` (cuenta ya enlazada).
    2. Por email (enlaza una cuenta existente sin degradar su rol).
    3. Si no existe, crea un usuario nuevo con rol ``customer``.

    Devuelve ``(user, created)``.
    """
    sub = idinfo["sub"]
    email = idinfo.get("email", "") or ""
    first_name = idinfo.get("given_name", "") or ""
    last_name = idinfo.get("family_name", "") or ""
    avatar_url = idinfo.get("picture", "") or ""

    # 1. Ya enlazado por google_sub
    user = User.objects.filter(google_sub=sub).first()
    if user:
        _refresh_profile(user, first_name, last_name, avatar_url)
        return user, False

    # 2. Existe un usuario con ese email -> enlazar sin cambiar su rol
    if email:
        user = User.objects.filter(email__iexact=email).first()
        if user:
            user.google_sub = sub
            if not user.avatar_url:
                user.avatar_url = avatar_url
            user.save(update_fields=["google_sub", "avatar_url", "updated_at"])
            return user, False

    # 3. Crear cliente nuevo
    user = User.objects.create(
        username=_unique_username(email or first_name),
        email=email,
        first_name=first_name,
        last_name=last_name,
        role=User.Role.CUSTOMER,
        provider=User.Provider.GOOGLE,
        google_sub=sub,
        avatar_url=avatar_url,
    )
    # Cuenta sin contraseña utilizable: solo entra por Google
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user, True


def _refresh_profile(user, first_name, last_name, avatar_url):
    """Actualiza nombre/avatar desde Google si llegaron datos nuevos."""
    fields = []
    if avatar_url and avatar_url != user.avatar_url:
        user.avatar_url = avatar_url
        fields.append("avatar_url")
    if first_name and not user.first_name:
        user.first_name = first_name
        fields.append("first_name")
    if last_name and not user.last_name:
        user.last_name = last_name
        fields.append("last_name")
    if fields:
        fields.append("updated_at")
        user.save(update_fields=fields)
