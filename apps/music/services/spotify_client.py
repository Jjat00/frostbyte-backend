import logging
import threading
import time

import spotipy
from django.core.cache import cache
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

# Equipos en los que ha sonado musica en cada piso (el ultimo y todos los
# conocidos). Cuando no hay dispositivo activo se despierta uno de ESOS: la
# cuenta puede tener otros conectados (un TV donde alguien inicio sesion) y
# mandar la musica ahi la saca del local. Vive en la cache (Redis en prod) para
# sobrevivir a un reinicio.
DEVICES_TTL = 60 * 60 * 24 * 60  # 60 dias
MAX_KNOWN_DEVICES = 10

# Tras comprobar que no hay ningun Spotify abierto, el sync no vuelve a
# intentarlo hasta que pase este tiempo (cada intento son dos llamadas).
_no_device_until = {}  # floor -> epoch
NO_DEVICE_RETRY_SECONDS = 30

# Cuando el staff pausa un piso desde el panel. Spotify suelta el dispositivo
# tras un rato en pausa y el playback vuelve vacio; sin esta marca el sync lo
# tomaba por "no suena nada" y despertaba el equipo con la siguiente solicitud.
# Tambien en la cache, para que un reinicio no la borre.
PAUSE_TTL = 60 * 60 * 12


def _cache_get(key, default=None):
    # Un Redis caido no puede tumbar la musica: sin cache se sigue sin memoria.
    try:
        return cache.get(key, default)
    except Exception:
        return default


def _cache_set(key, value, timeout):
    try:
        cache.set(key, value, timeout)
    except Exception:
        logger.debug(f"[Spotify] No se pudo guardar {key} en cache")


def _cache_delete(key):
    try:
        cache.delete(key)
    except Exception:
        logger.debug(f"[Spotify] No se pudo borrar {key} de cache")


def _devices_key(floor):
    return f"spotify:devices:{floor}"


def _pause_key(floor):
    return f"spotify:paused:{floor}"


def _remember_device(floor, device_id):
    memory = _cache_get(_devices_key(floor)) or {"last": None, "known": []}
    known = [d for d in memory["known"] if d != device_id] + [device_id]
    _cache_set(
        _devices_key(floor),
        {"last": device_id, "known": known[-MAX_KNOWN_DEVICES:]},
        DEVICES_TTL,
    )


def _mark_paused(floor):
    _cache_set(_pause_key(floor), time.time(), PAUSE_TTL)


def _clear_paused(floor, since=None):
    """Quita la marca de pausa. Con `since`, solo si la pausa es anterior a ese
    instante: una lectura que salio antes de pausar no puede deshacer la pausa."""
    if since is not None:
        paused_at = _cache_get(_pause_key(floor))
        if paused_at is None or paused_at >= since:
            return
    _cache_delete(_pause_key(floor))

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


class SpotifyNoDeviceError(Exception):
    """La cuenta del piso no tiene ningun Spotify abierto donde reproducir."""


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


def _call_on_device(floor, sp, func, **kwargs):
    """Ejecuta un comando de reproduccion aunque el equipo del piso este inactivo.

    Si Spotify lleva un rato sin sonar, deja de tener "dispositivo activo" y los
    comandos sin device_id fallan con 404 "No active device found". En ese caso
    se busca el Spotify abierto de la cuenta y se repite el comando apuntando a
    el, lo que ademas lo despierta. Si no hay ninguno abierto, SpotifyNoDeviceError.
    """
    try:
        result = _call(floor, func, **kwargs)
    except SpotifyException as e:
        if e.http_status != 404:
            raise
    else:
        _no_device_until.pop(floor, None)
        return result

    devices = (_call(floor, sp.devices) or {}).get("devices", [])
    device = _pick_device(floor, devices)
    if not device:
        _no_device_until[floor] = time.time() + NO_DEVICE_RETRY_SECONDS
        names = ", ".join(d.get("name", "?") for d in devices) or "ninguno"
        logger.warning(f"[Spotify] Piso {floor}: no hay Spotify abierto (equipos de la cuenta: {names})")
        raise SpotifyNoDeviceError(f"No hay ningún Spotify abierto en el piso {floor}")
    logger.info(f"[Spotify] Piso {floor}: sin dispositivo activo, uso '{device.get('name')}'")
    try:
        result = _call(floor, func, device_id=device["id"], **kwargs)
    except SpotifyException as e:
        if e.http_status != 404:
            raise
        # El equipo se cerro entre listar y reintentar: es "no hay Spotify
        # abierto" (recuperable), no un error que tumbe la solicitud.
        _no_device_until[floor] = time.time() + NO_DEVICE_RETRY_SECONDS
        raise SpotifyNoDeviceError(f"El Spotify del piso {floor} se cerró") from e
    _no_device_until.pop(floor, None)
    return result


def _pick_device(floor, devices):
    """Elige el equipo del piso donde reproducir, o None si no esta abierto.

    Primero el activo, luego el ultimo donde sono musica y despues cualquiera
    donde ya haya sonado. Si hay historial y ninguno de esos esta abierto, no se
    usa un equipo desconocido: puede ser el TV de alguien fuera del local. Sin
    historial (piso recien conectado) se prefiere un equipo que no sea TV, y el
    TV solo si es el unico abierto: en el local a veces suena en el TV y a veces
    en el celular.
    """
    usable = [d for d in devices if d.get("id") and not d.get("is_restricted")]
    active = next((d for d in usable if d.get("is_active")), None)
    if active:
        return active
    memory = _cache_get(_devices_key(floor)) or {"last": None, "known": []}
    by_id = {d["id"]: d for d in usable}
    if memory["last"] in by_id:
        return by_id[memory["last"]]
    for device_id in reversed(memory["known"]):
        if device_id in by_id:
            return by_id[device_id]
    if memory["known"]:
        return None
    not_tv = [d for d in usable if (d.get("type") or "").lower() != "tv"]
    return (not_tv or usable or [None])[0]


def is_paused_by_staff(floor) -> bool:
    """True si el staff pauso el piso y nadie lo ha vuelto a poner a sonar."""
    return _cache_get(_pause_key(floor)) is not None


def is_waiting_for_device(floor) -> bool:
    """True si hace poco se comprobo que el piso no tiene ningun Spotify abierto."""
    return time.time() < _no_device_until.get(floor, 0.0)


def claim_request(pk, expected_status, new_status) -> bool:
    """Pasa una solicitud de estado solo si nadie la toco antes (atomico).

    La vista que recibe la solicitud y el hilo de sync pueden ir por la misma a
    la vez; el que no la reclama no llama a Spotify, asi no se encola dos veces.
    """
    from django.utils import timezone

    from apps.music.models import SongRequest

    fields = {"status": new_status, "updated_at": timezone.now()}
    if new_status == SongRequest.Status.PLAYING:
        fields["played_at"] = fields["updated_at"]
    return SongRequest.objects.filter(pk=pk, status=expected_status).update(**fields) == 1


def release_request(pk, claimed_status, back_status):
    """Devuelve una solicitud reclamada a su estado anterior si Spotify fallo."""
    from django.utils import timezone

    from apps.music.models import SongRequest

    SongRequest.objects.filter(pk=pk, status=claimed_status).update(
        status=back_status, updated_at=timezone.now(),
        **({"played_at": None} if claimed_status == SongRequest.Status.PLAYING else {}),
    )


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
    _call_on_device(floor, sp, sp.add_to_queue, uri=track_uri)


def _fetch_current_playback(floor):
    """Lee el playback actual del piso desde Spotify y actualiza la cache."""
    sp = _get_spotify_client(floor)
    asked_at = time.time()
    current = _call(floor, sp.current_playback)
    device = (current or {}).get("device") or {}
    if current and current.get("is_playing"):
        # Volvio a sonar (desde el panel o desde el mismo Spotify): ya no esta
        # pausado, salvo que la pausa llegara mientras esta lectura viajaba.
        _clear_paused(floor, since=asked_at)
        if device.get("id"):
            _remember_device(floor, device["id"])
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
    _call_on_device(floor, sp, sp.pause_playback)
    _mark_paused(floor)


def resume_playback(floor):
    """Reanuda la reproducción del piso"""
    sp = _get_spotify_client(floor)
    _call_on_device(floor, sp, sp.start_playback)
    _clear_paused(floor)


def skip_to_next(floor):
    """Salta a la siguiente canción del piso y marca la actual como completada"""
    from apps.music.models import SongRequest

    sp = _get_spotify_client(floor)
    _call_on_device(floor, sp, sp.next_track)
    _clear_paused(floor)

    # Solo si Spotify salto de verdad: la actual queda completada
    for req in SongRequest.objects.filter(floor=floor, status=SongRequest.Status.PLAYING):
        req.mark_as_completed()


def skip_to_previous(floor):
    """Vuelve a la canción anterior en el piso"""
    sp = _get_spotify_client(floor)
    _call_on_device(floor, sp, sp.previous_track)
    _clear_paused(floor)


def play_track(track_uri, floor):
    """Reproduce un track inmediatamente en el piso sin tocar la cola."""
    from apps.music.models import SongRequest

    sp = _get_spotify_client(floor)

    # Reproducir inmediatamente. Las demás canciones siguen en la cola de Spotify.
    # Va antes de tocar la DB: si no hay equipo abierto, cada intento fallido
    # marcaba la solicitud como sonando y el sync la daba por completada.
    _call_on_device(floor, sp, sp.start_playback, uris=[track_uri])
    _clear_paused(floor)

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


def set_volume(volume_percent, floor):
    """Ajusta el volumen del piso (0-100)"""
    sp = _get_spotify_client(floor)
    _call_on_device(floor, sp, sp.volume, volume_percent=volume_percent)


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
