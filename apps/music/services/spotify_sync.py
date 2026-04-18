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

_sync_thread = None
_sync_running = False
_last_queue_check = 0.0


def sync_song_request_statuses():
    """
    Sincroniza los estados de las solicitudes con lo que realmente suena en Spotify.

    Lógica:
    1. Si una solicitud estaba "playing" pero ya no es el track actual → completed
    2. Si hay un track sonando que coincide con una solicitud queued/pending → playing
    3. Si no hay nada sonando (o Spotify está idle) y hay solicitudes queued → reproducir la siguiente
    """
    from apps.music.models import SongRequest, SpotifyToken
    from apps.music.services.spotify_client import (
        _fetch_current_playback,
        _get_spotify_client,
        SpotifyNotConnectedError,
        SpotifyRateLimitedError,
        is_rate_limited,
        seconds_until_allowed,
        _call,
    )
    from apps.music.consumers import broadcast_music_update

    global _last_queue_check

    token = SpotifyToken.get_active_token()
    if not token:
        return

    if is_rate_limited():
        logger.debug(
            f"[Spotify Sync] Rate limited. Esperando {int(seconds_until_allowed())}s"
        )
        return

    try:
        # Refresca el cache de playback para que lo usen los endpoints HTTP.
        current = _fetch_current_playback()
    except SpotifyNotConnectedError:
        return
    except SpotifyRateLimitedError:
        return
    except Exception as e:
        logger.debug(f"Error obteniendo cancion actual: {e}")
        return

    # current_uri refleja el track activo (incluso si esta pausado) para no perder
    # la transicion a PLAYING cuando el usuario pausa momentaneamente.
    current_uri = current["uri"] if current else None
    changed = False

    # 1. Las que estaban "playing" pero ya no son la canción actual → completed
    for req in SongRequest.objects.filter(status=SongRequest.Status.PLAYING):
        if req.spotify_track_uri != current_uri:
            req.status = SongRequest.Status.COMPLETED
            if not req.played_at:
                req.played_at = timezone.now()
            req.save(update_fields=["status", "played_at", "updated_at"])
            changed = True
            logger.info(f"[Spotify Sync] Completada: {req.song_name} - {req.artist_name}")

    # 2. Si hay un track sonando, buscar si coincide con alguna solicitud queued/pending
    if current_uri:
        pending_match = (
            SongRequest.objects.filter(
                spotify_track_uri=current_uri,
                status__in=[SongRequest.Status.QUEUED, SongRequest.Status.PENDING],
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

    # 3. Si no hay nada sonando y hay solicitudes en cola, reproducir la siguiente
    if not current_uri:
        next_queued = (
            SongRequest.objects.filter(
                status=SongRequest.Status.QUEUED,
                spotify_track_uri__gt="",
            )
            .order_by("created_at")
            .first()
        )
        if next_queued:
            try:
                sp = _get_spotify_client()
                _call(sp.start_playback, uris=[next_queued.spotify_track_uri])
                next_queued.status = SongRequest.Status.PLAYING
                next_queued.played_at = timezone.now()
                next_queued.save(update_fields=["status", "played_at", "updated_at"])
                changed = True
                logger.info(f"[Spotify Sync] Auto-reproduciendo siguiente: {next_queued.song_name}")
            except SpotifyRateLimitedError:
                return
            except Exception as e:
                logger.debug(f"Error auto-reproduciendo: {e}")

    # 4. Limpieza: solicitudes QUEUED que ya no estan en la cola de Spotify ni suenan
    #    Solo consultamos la cola de Spotify cada QUEUE_CHECK_INTERVAL segundos para
    #    no disparar rate limits: es una operacion de mantenimiento, no critica.
    now = time.time()
    if now - _last_queue_check < QUEUE_CHECK_INTERVAL:
        if changed:
            try:
                broadcast_music_update()
            except Exception:
                pass
        return

    grace_cutoff = timezone.now() - timedelta(seconds=QUEUED_GRACE_SECONDS)
    stale_queued = list(
        SongRequest.objects.filter(
            status=SongRequest.Status.QUEUED,
            spotify_track_uri__gt="",
            updated_at__lt=grace_cutoff,
        )
    )

    if stale_queued:
        try:
            sp = _get_spotify_client()
            spotify_queue = _call(sp.queue)
            queue_uris = {item.get("uri") for item in spotify_queue.get("queue", []) if item.get("uri")}
            _last_queue_check = now
        except SpotifyRateLimitedError:
            queue_uris = None
        except Exception as e:
            logger.debug(f"Error obteniendo cola de Spotify para limpieza: {e}")
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
        _last_queue_check = now

    if changed:
        try:
            broadcast_music_update()
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

        # Si estamos en cooldown por rate-limit, esperar hasta que pase
        # (con un cap para poder responder a stop_sync).
        from apps.music.services.spotify_client import (
            is_rate_limited,
            seconds_until_allowed,
        )
        if is_rate_limited():
            wait = min(seconds_until_allowed() + 1, 30)
            time.sleep(wait)
        else:
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
