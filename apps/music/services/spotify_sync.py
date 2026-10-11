import logging
import threading
import time
from datetime import timedelta

from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 8  # segundos entre cada sincronización
QUEUED_GRACE_SECONDS = 60  # margen antes de limpiar una solicitud QUEUED huerfana
QUEUE_CHECK_INTERVAL = 60  # solo pedir la cola de Spotify cada N seg (reduce llamadas)
# Una solicitud que no se pudo encolar (sin Spotify abierto, rate limit) se
# reintenta mientras sea reciente; mas vieja ya no tiene sentido que suene.
PENDING_MAX_AGE = timedelta(minutes=45)
# Una solicitud tocada hace poco puede estar a medio camino: la vista que la
# recibio la esta encolando (hasta 3 llamadas de 10 s). El sync solo toma las
# que llevan quietas mas que eso; ademas cada una se reclama de forma atomica.
SETTLE_TIME = timedelta(seconds=40)
# Una en cola tanto tiempo ya no va a sonar (se reinicio Spotify, la quitaron a
# mano): vence para no bloquear que alguien la vuelva a pedir.
QUEUED_MAX_AGE = timedelta(hours=3)
# Margen al comparar cuando se encolo una solicitud con cuando arranco el track.
TRACK_START_TOLERANCE = timedelta(seconds=2)
# Spotify tarda unos segundos en reportar lo que se acaba de poner a sonar en un
# equipo dormido; mientras, el playback llega vacio y no hay que darla por sonada.
JUST_STARTED = timedelta(seconds=30)

_sync_thread = None
_sync_running = False
_last_queue_check = {}  # floor -> epoch
# Cuando arranco de verdad el track que suena en cada piso. Se fija la primera
# vez que se le ve sonando y no se recalcula en cada lectura: tras una pausa,
# "ahora - progreso" se correria tanto como duro la pausa.
_track_start = {}  # floor -> {"uri", "started", "progress_ms"}
RESTART_MARGIN_MS = 5000  # el progreso retrocede mas que esto = volvio a empezar


def _track_started_at(floor, current, now_dt):
    """Instante en que arranco el track actual, o None si aun no se sabe."""
    uri = current["uri"]
    progress = current.get("progress_ms") or 0
    seen = _track_start.get(floor)
    if seen and seen["uri"] == uri and progress + RESTART_MARGIN_MS >= seen["progress_ms"]:
        seen["progress_ms"] = progress
        return seen["started"]
    if not current.get("is_playing"):
        # Track nuevo (o reiniciado) visto en pausa: el progreso congelado no
        # dice cuando arranco. Se espera a verlo sonar.
        _track_start.pop(floor, None)
        return None
    started = now_dt - timedelta(milliseconds=progress)
    _track_start[floor] = {"uri": uri, "started": started, "progress_ms": progress}
    return started


def sync_song_request_statuses():
    """Sincroniza cada piso con cuenta de Spotify conectada."""
    from apps.music.models import SpotifyToken

    for floor in SpotifyToken.objects.values_list("floor", flat=True):
        _sync_floor(floor)


def _sync_floor(floor):
    """
    Sincroniza los estados de las solicitudes de un piso con lo que realmente
    suena en la cuenta de Spotify de ese piso.

    Lógica:
    1. Si una solicitud estaba "playing" pero ya no es el track actual → completed
    2. Si hay un track sonando que coincide con una solicitud queued/pending → playing
    3. Las pending (no se pudieron encolar al pedirlas) se encolan; si no suena
       nada, la siguiente solicitud se reproduce de una vez
    4. Limpieza de queued que ya no estan en la cola de Spotify
    """
    from apps.music.models import SongRequest
    from apps.music.services.spotify_client import (
        _call,
        _call_on_device,
        _fetch_current_playback,
        _get_spotify_client,
        claim_request,
        release_request,
        SpotifyNoDeviceError,
        SpotifyNotConnectedError,
        SpotifyRateLimitedError,
        is_paused_by_staff,
        is_rate_limited,
        is_waiting_for_device,
        seconds_until_allowed,
    )
    from apps.music.consumers import broadcast_music_update
    from apps.orders.models import StoreSettings

    if is_rate_limited(floor):
        logger.debug(
            f"[Spotify Sync] Piso {floor} rate limited. "
            f"Esperando {int(seconds_until_allowed(floor))}s"
        )
        return

    try:
        # Refresca el cache de playback para que lo usen los endpoints HTTP.
        current = _fetch_current_playback(floor)
    except SpotifyNotConnectedError:
        return
    except SpotifyRateLimitedError:
        return
    except Exception as e:
        logger.debug(f"Error obteniendo cancion actual (piso {floor}): {e}")
        return

    # current_uri refleja el track activo (incluso si esta pausado) para no perder
    # la transicion a PLAYING cuando el usuario pausa momentaneamente.
    current_uri = current["uri"] if current else None
    changed = False

    # 1. Las que estaban "playing" pero ya no son la canción actual → completed
    now_dt = timezone.now()
    for req in SongRequest.objects.filter(floor=floor, status=SongRequest.Status.PLAYING):
        # Recien puesta a sonar: la lectura de Spotify puede ser de antes (otro
        # track o nada) y completarla aqui la daria por sonada sin sonar.
        if req.played_at and req.played_at > now_dt - JUST_STARTED:
            continue
        if req.spotify_track_uri != current_uri:
            req.status = SongRequest.Status.COMPLETED
            if not req.played_at:
                req.played_at = timezone.now()
            req.save(update_fields=["status", "played_at", "updated_at"])
            changed = True
            logger.info(f"[Spotify Sync] Completada: {req.song_name} - {req.artist_name}")

    # 2. Si hay un track sonando, buscar si coincide con alguna solicitud queued/pending.
    #    Solo cuenta una encolada ANTES de que arrancara el track: quien pide la
    #    cancion que ya suena la quiere otra vez, no esta.
    track_started = _track_started_at(floor, current, now_dt) if current_uri else None
    if track_started:
        pending_match = (
            SongRequest.objects.filter(
                floor=floor,
                spotify_track_uri=current_uri,
                status__in=[SongRequest.Status.QUEUED, SongRequest.Status.PENDING],
                updated_at__lt=track_started + TRACK_START_TOLERANCE,
            )
            .order_by("created_at")
            .first()
        )
        if pending_match:
            pending_match.status = SongRequest.Status.PLAYING
            pending_match.played_at = timezone.now()
            pending_match.save(update_fields=["status", "played_at", "updated_at"])
            changed = True
            logger.info(f"[Spotify Sync] Reproduciendo: {pending_match.song_name} - {pending_match.artist_name}")

    # 3. Solicitudes que aun no estan en Spotify. Las pending viejas se descartan
    #    (si no, bloquean para siempre que alguien vuelva a pedir esa cancion).
    stale = SongRequest.objects.filter(
        floor=floor,
        status=SongRequest.Status.PENDING,
        created_at__lt=timezone.now() - PENDING_MAX_AGE,
    ).update(status=SongRequest.Status.FAILED, updated_at=timezone.now())
    stale += SongRequest.objects.filter(
        floor=floor,
        status=SongRequest.Status.QUEUED,
        updated_at__lt=now_dt - QUEUED_MAX_AGE,
    ).update(status=SongRequest.Status.FAILED, updated_at=timezone.now())
    if stale:
        changed = True
        logger.info(f"[Spotify Sync] Piso {floor}: {stale} solicitudes vencidas")

    settled = SongRequest.objects.filter(
        floor=floor,
        spotify_track_uri__gt="",
        updated_at__lt=now_dt - SETTLE_TIME,
    )
    if not is_waiting_for_device(floor):
        try:
            sp = _get_spotify_client(floor)
            if not current_uri:
                # No suena nada: la siguiente solicitud reciente se reproduce ya,
                # despertando el equipo del piso. Nunca con el local cerrado ni
                # si el staff lo pauso: sonaria musica que nadie pidio poner.
                # Tampoco si una solicitud quedo sonando (recien arrancada): la
                # lectura vacia puede ser de antes y se la pisaria.
                next_req = None
                still_playing = SongRequest.objects.filter(
                    floor=floor, status=SongRequest.Status.PLAYING,
                ).exists()
                if (
                    not still_playing
                    and not is_paused_by_staff(floor)
                    and StoreSettings.load().is_open
                ):
                    next_req = (
                        settled.filter(
                            status__in=[SongRequest.Status.QUEUED, SongRequest.Status.PENDING],
                            created_at__gte=now_dt - PENDING_MAX_AGE,
                        )
                        .order_by("created_at")
                        .first()
                    )
                if next_req and claim_request(next_req.pk, next_req.status, SongRequest.Status.PLAYING):
                    try:
                        _call_on_device(floor, sp, sp.start_playback, uris=[next_req.spotify_track_uri])
                    except Exception:
                        release_request(next_req.pk, SongRequest.Status.PLAYING, next_req.status)
                        raise
                    current_uri = next_req.spotify_track_uri
                    changed = True
                    logger.info(f"[Spotify Sync] Auto-reproduciendo siguiente: {next_req.song_name}")
            else:
                for req in settled.filter(status=SongRequest.Status.PENDING).order_by("created_at"):
                    if not claim_request(req.pk, SongRequest.Status.PENDING, SongRequest.Status.QUEUED):
                        continue
                    try:
                        _call_on_device(floor, sp, sp.add_to_queue, uri=req.spotify_track_uri)
                    except Exception:
                        release_request(req.pk, SongRequest.Status.QUEUED, SongRequest.Status.PENDING)
                        raise
                    changed = True
                    logger.info(f"[Spotify Sync] Encolada pendiente: {req.song_name}")
        except (SpotifyNoDeviceError, SpotifyRateLimitedError):
            pass
        except Exception as e:
            logger.warning(f"[Spotify Sync] Error reproduciendo/encolando (piso {floor}): {e}")

    # Sin nada sonando, la cola que devuelve Spotify viene vacia aunque las
    # canciones sigan encoladas: limpiar ahi daba por sonadas canciones que no sonaron.
    if not current_uri:
        if changed:
            try:
                broadcast_music_update(floor=floor)
            except Exception:
                pass
        return

    # 4. Limpieza: solicitudes QUEUED que ya no estan en la cola de Spotify ni suenan
    #    Solo consultamos la cola de Spotify cada QUEUE_CHECK_INTERVAL segundos para
    #    no disparar rate limits: es una operacion de mantenimiento, no critica.
    now = time.time()
    if now - _last_queue_check.get(floor, 0.0) < QUEUE_CHECK_INTERVAL:
        if changed:
            try:
                broadcast_music_update(floor=floor)
            except Exception:
                pass
        return

    grace_cutoff = timezone.now() - timedelta(seconds=QUEUED_GRACE_SECONDS)
    stale_queued = list(
        SongRequest.objects.filter(
            floor=floor,
            status=SongRequest.Status.QUEUED,
            spotify_track_uri__gt="",
            updated_at__lt=grace_cutoff,
        )
    )

    if stale_queued:
        try:
            sp = _get_spotify_client(floor)
            spotify_queue = _call(floor, sp.queue)
            queue_uris = {item.get("uri") for item in spotify_queue.get("queue", []) if item.get("uri")}
            _last_queue_check[floor] = now
        except SpotifyRateLimitedError:
            queue_uris = None
        except Exception as e:
            logger.debug(f"Error obteniendo cola de Spotify para limpieza (piso {floor}): {e}")
            queue_uris = None

        if queue_uris is not None:
            for req in stale_queued:
                if req.spotify_track_uri in queue_uris:
                    continue
                if req.spotify_track_uri == current_uri:
                    continue
                req.status = SongRequest.Status.COMPLETED
                if not req.played_at:
                    req.played_at = timezone.now()
                req.save(update_fields=["status", "played_at", "updated_at"])
                changed = True
                logger.info(
                    f"[Spotify Sync] Marcada como completada (ya no esta en cola): "
                    f"{req.song_name} - {req.artist_name}"
                )
    else:
        _last_queue_check[floor] = now

    if changed:
        try:
            broadcast_music_update(floor=floor)
        except Exception:
            pass


def _sync_loop():
    """Loop que corre en background sincronizando estados"""
    global _sync_running
    logger.info("[Spotify Sync] Hilo de sincronizacion iniciado")

    # Esperar a que Django esté completamente listo
    time.sleep(3)

    while _sync_running:
        try:
            close_old_connections()
            sync_song_request_statuses()
        except Exception as e:
            logger.debug(f"Spotify sync error: {e}")
        finally:
            close_old_connections()

        # El chequeo de rate-limit por piso vive dentro de _sync_floor (skip
        # barato), asi que el loop puede dormir un intervalo fijo.
        time.sleep(SYNC_INTERVAL)

    logger.info("[Spotify Sync] Hilo de sincronizacion detenido")


def start_sync():
    """Inicia el hilo de sincronización en background"""
    global _sync_thread, _sync_running

    if _sync_thread and _sync_thread.is_alive():
        return

    _sync_running = True
    _sync_thread = threading.Thread(target=_sync_loop, daemon=True, name="spotify-sync")
    _sync_thread.start()
    logger.info("[Spotify Sync] Sincronizacion con Spotify activada")


def stop_sync():
    """Detiene el hilo de sincronización"""
    global _sync_running
    _sync_running = False
