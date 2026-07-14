import logging
import threading
import time

import spotipy
from spotipy.exceptions import SpotifyException

from .spotify_auth import get_valid_access_token

logger = logging.getLogger(__name__)

# Cache compartida para reducir llamadas a la API de Spotify, segmentada por
# piso (cada piso tiene su propia cuenta, con su propio playback y su propio
# rate limit). El hilo de sincronizacion refresca estos valores y los
# endpoints HTTP los reutilizan en vez de golpear Spotify en cada request.
_cache_lock = threading.Lock()
_playback_cache = {}  # floor -> {"data": ..., "fetched_at": epoch}

# Timestamp (epoch) por piso hasta el cual NO se debe llamar a Spotify por rate limit.
_rate_limited_until = {}  # floor -> epoch

PLAYBACK_CACHE_TTL = 4.0  # seg - los endpoints reutilizan el playback reciente

# Si Spotify devuelve 429 pero no podemos leer el header Retry-After
# (caso comun cuando spotipy convierte urllib3.RetryError en SpotifyException
# con headers=None), asumimos este cooldown por defecto.
DEFAULT_RATE_LIMIT_COOLDOWN = 60  # seg
MAX_RATE_LIMIT_COOLDOWN = 300  # seg - cap defensivo ante Retry-After de horas


def _get_spotify_client(floor):
    """Crea un cliente de Spotify autenticado con la cuenta del piso.

    retries=0 / status_retries=0 evita que spotipy bloquee el hilo durante horas
    cuando Spotify devuelve un Retry-After largo en un 429.
    """
    access_token = get_valid_access_token(floor)
    if not access_token:
        raise SpotifyNotConnectedError(f"Spotify no está conectado en el piso {floor}")
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


def _note_rate_limit(floor, retry_after):
    """Marca un cooldown para que nadie llame a Spotify en ese piso hasta que pase.

    retry_after puede ser None cuando spotipy no pudo leer el header. En ese
    caso usamos un default conservador para no reintentar en loop.
    """
    if retry_after is None or retry_after <= 0:
        effective = DEFAULT_RATE_LIMIT_COOLDOWN
    else:
        effective = max(retry_after, DEFAULT_RATE_LIMIT_COOLDOWN)
    capped = min(effective, MAX_RATE_LIMIT_COOLDOWN)
    _rate_limited_until[floor] = max(
        _rate_limited_until.get(floor, 0.0), time.time() + capped
    )


def is_rate_limited(floor) -> bool:
    return time.time() < _rate_limited_until.get(floor, 0.0)


def seconds_until_allowed(floor) -> float:
    return max(0.0, _rate_limited_until.get(floor, 0.0) - time.time())


def _handle_spotify_exception(floor, exc: SpotifyException):
    """Convierte SpotifyException 429 en SpotifyRateLimitedError y registra cooldown."""
    if exc.http_status == 429:
        retry_after = None
        headers = getattr(exc, "headers", None) or {}
        raw = headers.get("Retry-After") if headers else None
        try:
            retry_after = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            retry_after = None
        _note_rate_limit(floor, retry_after)
        logger.warning(
            f"[Spotify] Rate limited (piso {floor}). Retry-After={retry_after} "
            f"(cooldown {int(seconds_until_allowed(floor))}s)"
        )
        raise SpotifyRateLimitedError(retry_after or DEFAULT_RATE_LIMIT_COOLDOWN) from exc
    raise


def _call(floor, func, *args, **kwargs):
    """Invoca una funcion del cliente spotipy con manejo uniforme de 429."""
    if is_rate_limited(floor):
        raise SpotifyRateLimitedError(int(seconds_until_allowed(floor)) or 1)
    try:
        return func(*args, **kwargs)
    except SpotifyException as e:
        _handle_spotify_exception(floor, e)


def search_tracks(query, floor, limit=10):
    """Busca tracks en Spotify"""
    sp = _get_spotify_client(floor)
    results = _call(floor, sp.search, q=query, type="track", limit=limit)
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


def add_to_queue(track_uri, floor):
    """Agrega un track a la cola de reproducción del piso"""
    sp = _get_spotify_client(floor)
    _call(floor, sp.add_to_queue, uri=track_uri)


def _fetch_current_playback(floor):
    """Lee el playback actual del piso desde Spotify y actualiza la cache."""
    sp = _get_spotify_client(floor)
    current = _call(floor, sp.current_playback)
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
        _playback_cache[floor] = {"data": data, "fetched_at": time.time()}
    return data


def get_currently_playing(floor, force_refresh: bool = False):
    """Obtiene la canción que está sonando en el piso, usando cache compartida."""
    now = time.time()
    with _cache_lock:
        entry = _playback_cache.get(floor) or {"data": None, "fetched_at": 0.0}
        cached = entry["data"]
        age = now - entry["fetched_at"]

    if not force_refresh and age < PLAYBACK_CACHE_TTL:
        return cached

    if is_rate_limited(floor):
        # No golpees Spotify; devuelve el ultimo valor conocido.
        return cached

    try:
        return _fetch_current_playback(floor)
    except SpotifyRateLimitedError:
        return cached


def get_queue(floor):
    """Obtiene la cola de Spotify del piso, marcando cuales son solicitudes de clientes."""
    from apps.music.models import SongRequest

    sp = _get_spotify_client(floor)
    queue_data = _call(floor, sp.queue)

    queued_uris = set(
        SongRequest.objects.filter(
            floor=floor,
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


def pause_playback(floor):
    """Pausa la reproducción del piso"""
    sp = _get_spotify_client(floor)
    _call(floor, sp.pause_playback)


def resume_playback(floor):
    """Reanuda la reproducción del piso"""
    sp = _get_spotify_client(floor)
    _call(floor, sp.start_playback)


def skip_to_next(floor):
    """Salta a la siguiente canción del piso y marca la actual como completada"""
    from apps.music.models import SongRequest

    sp = _get_spotify_client(floor)

    # Marcar la canción actual como completada
    playing_requests = SongRequest.objects.filter(
        floor=floor, status=SongRequest.Status.PLAYING
    )
    for req in playing_requests:
        req.mark_as_completed()

    _call(floor, sp.next_track)


def skip_to_previous(floor):
    """Vuelve a la canción anterior en el piso"""
    sp = _get_spotify_client(floor)
    _call(floor, sp.previous_track)


def play_track(track_uri, floor):
    """Reproduce un track inmediatamente en el piso sin tocar la cola."""
    from apps.music.models import SongRequest

    sp = _get_spotify_client(floor)

    # Marcar la canción que estaba sonando como completada
    for req in SongRequest.objects.filter(floor=floor, status=SongRequest.Status.PLAYING):
        req.mark_as_completed()

    # Marcar esta solicitud como playing
    matching = (
        SongRequest.objects.filter(
            floor=floor,
            spotify_track_uri=track_uri,
            status__in=[SongRequest.Status.QUEUED, SongRequest.Status.PENDING],
        )
        .order_by("created_at")
        .first()
    )
    if matching:
        matching.mark_as_playing()

    # Reproducir inmediatamente. Las demás canciones siguen en la cola de Spotify.
    _call(floor, sp.start_playback, uris=[track_uri])


def set_volume(volume_percent, floor):
    """Ajusta el volumen del piso (0-100)"""
    sp = _get_spotify_client(floor)
    _call(floor, sp.volume, volume_percent)


def is_connected(floor):
    """Verifica si Spotify está conectado en el piso.

    Se basa en la existencia de un token en la DB en vez de hacer una llamada
    a Spotify. Esto evita gastar quota en un endpoint que se consulta cada vez
    que se abre la vista, y evita falsos negativos cuando Spotify nos tiene
    rate-limited (el token sigue siendo valido aunque no podamos consultar).
    Los errores reales de autenticacion (token revocado) se manifestaran en
    los endpoints que si usan la API, y el refresh fallara alli.
    """
    from apps.music.models import SpotifyToken

    return SpotifyToken.get_active_token(floor) is not None
