import os
import urllib.parse
from datetime import timedelta

from django.utils import timezone


SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

SPOTIFY_SCOPES = [
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-currently-playing",
]


def get_spotify_config():
    return {
        "client_id": os.getenv("SPOTIFY_CLIENT_ID", ""),
        "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("SPOTIFY_REDIRECT_URI", ""),
    }


def get_authorize_url(state=None):
    """Genera la URL de autorización de Spotify"""
    config = get_spotify_config()
    params = {
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": config["redirect_uri"],
        "scope": " ".join(SPOTIFY_SCOPES),
        # Sin esto, Spotify reutiliza la sesión del navegador y auto-autoriza
        # sin preguntar: imposible conectar una cuenta distinta por piso.
        # Con show_dialog el diálogo siempre aparece y ofrece "¿No eres tú?".
        "show_dialog": "true",
    }
    if state:
        params["state"] = state
    return f"{SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code):
    """Intercambia el código de autorización por tokens"""
    import requests

    config = get_spotify_config()
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
        },
        auth=(config["client_id"], config["client_secret"]),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token):
    """Renueva el access token usando el refresh token"""
    import requests

    config = get_spotify_config()
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(config["client_id"], config["client_secret"]),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def save_tokens(token_data, floor):
    """Guarda o actualiza los tokens de la cuenta del piso indicado"""
    from apps.music.models import SpotifyToken

    expires_at = timezone.now() + timedelta(seconds=token_data["expires_in"])

    existing = SpotifyToken.get_active_token(floor)
    token, _ = SpotifyToken.objects.update_or_create(
        floor=floor,
        defaults={
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get(
                "refresh_token",
                existing.refresh_token if existing else "",
            ),
            "token_type": token_data.get("token_type", "Bearer"),
            "expires_at": expires_at,
            "scope": token_data.get("scope", ""),
        },
    )
    return token


def get_valid_access_token(floor):
    """Obtiene un access token válido del piso, renovándolo si es necesario"""
    from apps.music.models import SpotifyToken

    token = SpotifyToken.get_active_token(floor)
    if not token:
        return None

    if token.is_expired:
        token_data = refresh_access_token(token.refresh_token)
        token = save_tokens(token_data, floor=floor)

    return token.access_token
