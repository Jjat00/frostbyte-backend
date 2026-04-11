import logging
import threading
import time

from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 5  # segundos entre cada sincronización

_sync_thread = None
_sync_running = False


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
        get_currently_playing,
        SpotifyNotConnectedError,
    )
    from apps.music.consumers import broadcast_music_update

    token = SpotifyToken.get_active_token()
    if not token:
        return

    try:
        current = get_currently_playing()
    except SpotifyNotConnectedError:
        return
    except Exception as e:
        logger.debug(f"Error obteniendo cancion actual: {e}")
        return

    current_uri = current["uri"] if current and current.get("is_playing") else None
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
                from apps.music.services.spotify_client import _get_spotify_client
                sp = _get_spotify_client()
                sp.start_playback(uris=[next_queued.spotify_track_uri])
                next_queued.status = SongRequest.Status.PLAYING
                next_queued.played_at = timezone.now()
                next_queued.save(update_fields=["status", "played_at", "updated_at"])
                changed = True
                logger.info(f"[Spotify Sync] Auto-reproduciendo siguiente: {next_queued.song_name}")
            except Exception as e:
                logger.debug(f"Error auto-reproduciendo: {e}")

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
