import logging
import threading
import time

import spotipy
from spotipy.exceptions import SpotifyException

from .spotify_auth import get_valid_access_token

logger = logging.getLogger(__name__)

# Cache compartida para reducir llamadas a la API de Spotify.
# El hilo de sincronizacion refresca estos valores y los endpoints HTTP
# los reutilizan en vez de golpear Spotify en cada request.
_cache_lock = threading.Lock()
_playback_cache = {"data": None, "fetched_at": 0.0}
_connected_cache = {"value": False, "fetched_at": 0.0}

# Timestamp (epoch) hasta el cual NO se debe llamar a Spotify por rate limit.
_rate_limited_until = 0.0

PLAYBACK_CACHE_TTL = 4.0  # seg - los endpoints reutilizan el playback reciente
CONNECTED_CACHE_TTL = 30.0  # seg


def _get_spotify_client():
    """Crea un cliente de Spotify autenticado.

    retries=0 / status_retries=0 evita que spotipy bloquee el hilo durante horas
    cuando Spotify devuelve un Retry-After largo en un 429.
    """
    access_token = get_valid_access_token()
    if not access_token:
        raise SpotifyNotConnectedError("Spotify no está conectado")
    return spotipy.Spotify(
        auth=access_token,
        requests_timeout=10,
        retries=0,
        status_retries=0,
        backoff_factor=0,
    )


class SpotifyNotConnectedError(Exception):
    pass


class SpotifyRateLimitedError(Exception):
    """Se levanto un 429 de Spotify. retry_after en segundos."""

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Spotify rate limited. Retry after {retry_after}s")


def _note_rate_limit(retry_after: int):
    """Marca un cooldown global para que nadie llame a Spotify hasta que pase."""
    global _rate_limited_until
    # Cap defensivo: nunca dormir mas de 5 min aunque Spotify pida horas.
    # Si sigue devolviendo 429 tras 5 min, volveremos a respetarlo.
    capped = min(max(retry_after, 1), 300)
    _rate_limited_until = max(_rate_limited_until, time.time() + capped)


def is_rate_limited() -> bool:
    return time.time() < _rate_limited_until


def seconds_until_allowed() -> float:
    return max(0.0, _rate_limited_until - time.time())


def _handle_spotify_exception(exc: SpotifyException):
    """Convierte SpotifyException 429 en SpotifyRateLimitedError y registra cooldown."""
    if exc.http_status == 429:
        retry_after = 1
        headers = getattr(exc, "headers", None) or {}
        try:
            retry_after = int(headers.get("Retry-After", 1))
        except (TypeError, ValueError):
            retry_after = 1
        _note_rate_limit(retry_after)
        logger.warning(
            f"[Spotify] Rate limited. Retry-After={retry_after}s (capped to 300s)"
        )
        raise SpotifyRateLimitedError(retry_after) from exc
    raise


def _call(func, *args, **kwargs):
    """Invoca una funcion del cliente spotipy con manejo uniforme de 429."""
    if is_rate_limited():
        raise SpotifyRateLimitedError(int(seconds_until_allowed()) or 1)
    try:
        return func(*args, **kwargs)
    except SpotifyException as e:
        _handle_spotify_exception(e)


def search_tracks(query, limit=10):
    """Busca tracks en Spotify"""
    sp = _get_spotify_client()
    results = _call(sp.search, q=query, type="track", limit=limit)
    tracks = []
    for item in results["tracks"]["items"]:
        tracks.append({
            "uri": item["uri"],
            "name": item["name"],
            "artists": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "image": item["album"]["images"][0]["url"] if item["album"]["images"] else "",
            "duration_ms": item["duration_ms"],
            "preview_url": item.get("preview_url"),
        })
    return tracks


def add_to_queue(track_uri):
    """Agrega un track a la cola de reproducción"""
    sp = _get_spotify_client()
    _call(sp.add_to_queue, uri=track_uri)


def _fetch_current_playback():
    """Lee el playback actual desde Spotify y actualiza la cache."""
    sp = _get_spotify_client()
    current = _call(sp.current_playback)
    if not current or not current.get("item"):
        data = None
    else:
        item = current["item"]
        data = {
            "uri": item["uri"],
            "name": item["name"],
            "artists": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "image": item["album"]["images"][0]["url"] if item["album"]["images"] else "",
            "duration_ms": item["duration_ms"],
            "progress_ms": current.get("progress_ms", 0),
            "is_playing": current.get("is_playing", False),
        }
    with _cache_lock:
        _playback_cache["data"] = data
        _playback_cache["fetched_at"] = time.time()
    return data


def get_currently_playing(force_refresh: bool = False):
    """Obtiene la canción que está sonando, usando cache compartida."""
    now = time.time()
    with _cache_lock:
        cached = _playback_cache["data"]
        age = now - _playback_cache["fetched_at"]

    if not force_refresh and age < PLAYBACK_CACHE_TTL:
        return cached

    if is_rate_limited():
        # No golpees Spotify; devuelve el ultimo valor conocido.
        return cached

    try:
        return _fetch_current_playback()
    except SpotifyRateLimitedError:
        return cached


def get_queue():
    """Obtiene la cola completa de Spotify, marcando cuales son solicitudes de clientes."""
    from apps.music.models import SongRequest

    sp = _get_spotify_client()
    queue_data = _call(sp.queue)

    queued_uris = set(
        SongRequest.objects.filter(
            status=SongRequest.Status.QUEUED,
            spotify_track_uri__gt="",
        ).values_list("spotify_track_uri", flat=True)
    )

    tracks = []
    for item in queue_data.get("queue", []):
        uri = item["uri"]
        tracks.append({
            "uri": uri,
            "name": item["name"],
            "artists": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "image": item["album"]["images"][0]["url"] if item["album"]["images"] else "",
            "duration_ms": item["duration_ms"],
            "is_request": uri in queued_uris,
        })
    return tracks


def pause_playback():
    """Pausa la reproducción"""
    sp = _get_spotify_client()
    _call(sp.pause_playback)


def resume_playback():
    """Reanuda la reproducción"""
    sp = _get_spotify_client()
    _call(sp.start_playback)


def skip_to_next():
    """Salta a la siguiente canción y marca la actual como completada"""
    from apps.music.models import SongRequest

    sp = _get_spotify_client()

    # Marcar la canción actual como completada
    playing_requests = SongRequest.objects.filter(status=SongRequest.Status.PLAYING)
    for req in playing_requests:
        req.mark_as_completed()

    _call(sp.next_track)


def skip_to_previous():
    """Vuelve a la canción anterior"""
    sp = _get_spotify_client()
    _call(sp.previous_track)


def play_track(track_uri):
    """Reproduce un track inmediatamente sin tocar la cola."""
    from apps.music.models import SongRequest

    sp = _get_spotify_client()

    # Marcar la canción que estaba sonando como completada
    for req in SongRequest.objects.filter(status=SongRequest.Status.PLAYING):
        req.mark_as_completed()

    # Marcar esta solicitud como playing
    matching = (
        SongRequest.objects.filter(
            spotify_track_uri=track_uri,
            status__in=[SongRequest.Status.QUEUED, SongRequest.Status.PENDING],
        )
        .order_by("created_at")
        .first()
    )
    if matching:
        matching.mark_as_playing()

    # Reproducir inmediatamente. Las demás canciones siguen en la cola de Spotify.
    _call(sp.start_playback, uris=[track_uri])


def set_volume(volume_percent):
    """Ajusta el volumen (0-100)"""
    sp = _get_spotify_client()
    _call(sp.volume, volume_percent)


def is_connected():
    """Verifica si Spotify está conectado y activo, con cache."""
    now = time.time()
    with _cache_lock:
        cached_value = _connected_cache["value"]
        age = now - _connected_cache["fetched_at"]

    if age < CONNECTED_CACHE_TTL:
        return cached_value

    if is_rate_limited():
        return cached_value

    try:
        sp = _get_spotify_client()
        _call(sp.current_user)
        value = True
    except Exception:
        value = False

    with _cache_lock:
        _connected_cache["value"] = value
        _connected_cache["fetched_at"] = time.time()
    return value
