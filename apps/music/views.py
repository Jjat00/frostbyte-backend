import logging
import os
from datetime import date, timedelta

from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from .models import SongRequest, MusicSettings, FLOOR_CHOICES, DEFAULT_FLOOR
from .stats import build_stats
from .consumers import broadcast_music_update
from .serializers import (
    SongRequestSerializer,
    SongRequestCreateSerializer,
    SongRequestStatusUpdateSerializer,
    SpotifyTrackSerializer,
    MusicSettingsSerializer,
)
from .services.spotify_client import (
    search_tracks,
    add_to_queue,
    get_currently_playing,
    get_queue,
    is_connected,
    pause_playback,
    resume_playback,
    skip_to_next,
    skip_to_previous,
    play_track,
    set_volume,
    SpotifyNotConnectedError,
    SpotifyRateLimitedError,
)
from .services.spotify_auth import (
    get_authorize_url,
    exchange_code_for_tokens,
    save_tokens,
)

logger = logging.getLogger(__name__)

VALID_FLOORS = {choice[0] for choice in FLOOR_CHOICES}


def _parse_floor(value, default=DEFAULT_FLOOR):
    """Normaliza el piso recibido por query param o body.

    Default piso 2: los QR legacy y clientes viejos no envían piso.
    """
    try:
        floor = int(value)
    except (TypeError, ValueError):
        return default
    return floor if floor in VALID_FLOORS else default


def _request_floor(request):
    """Extrae el piso de un request (body o query param), default piso 2."""
    raw = None
    if isinstance(getattr(request, "data", None), dict):
        raw = request.data.get("floor")
    if raw is None:
        raw = request.query_params.get("floor")
    return _parse_floor(raw)


class SongRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gestionar solicitudes de canciones.

    list: Listar solicitudes (público para ver, autenticado para gestionar)
    retrieve: Detalle de una solicitud
    create: Crear nueva solicitud (público) + encolar en Spotify
    update: Actualizar solicitud (solo autenticado)
    destroy: Eliminar solicitud (solo autenticado)
    """

    queryset = SongRequest.objects.all()
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "create":
            return SongRequestCreateSerializer
        elif self.action == "update_status":
            return SongRequestStatusUpdateSerializer
        return SongRequestSerializer

    def get_permissions(self):
        if self.action in ["create", "list", "retrieve", "search", "now_playing", "queue_status", "spotify_status"]:
            return [AllowAny()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Rechazar si la misma cancion ya esta en cola o reproduciendose en ese piso
        track_uri = serializer.validated_data.get("spotify_track_uri")
        floor = serializer.validated_data.get("floor", DEFAULT_FLOOR)
        if track_uri:
            duplicate = SongRequest.objects.filter(
                floor=floor,
                spotify_track_uri=track_uri,
                status__in=[
                    SongRequest.Status.PENDING,
                    SongRequest.Status.QUEUED,
                    SongRequest.Status.PLAYING,
                ],
            ).exists()
            if duplicate:
                return Response(
                    {"error": "Esta cancion ya esta en la cola o reproduciendose."},
                    status=status.HTTP_409_CONFLICT,
                )

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        song_request = serializer.save()

        # Intentar agregar a la cola de Spotify del piso automáticamente
        if song_request.spotify_track_uri:
            try:
                add_to_queue(song_request.spotify_track_uri, floor=song_request.floor)
                song_request.status = SongRequest.Status.QUEUED
                song_request.save(update_fields=["status", "updated_at"])
            except SpotifyNotConnectedError:
                logger.warning(
                    f"Spotify no conectado en piso {song_request.floor}, "
                    "la solicitud queda pendiente"
                )
            except SpotifyRateLimitedError:
                # Queda en PENDING para que se encole luego cuando pase el cooldown.
                logger.warning("Spotify rate-limited, solicitud queda pendiente")
            except Exception as e:
                logger.error(f"Error al encolar en Spotify: {e}")
                song_request.status = SongRequest.Status.FAILED
                song_request.save(update_fields=["status", "updated_at"])

        broadcast_music_update(floor=song_request.floor)

    def perform_destroy(self, instance):
        floor = instance.floor
        instance.delete()
        broadcast_music_update(floor=floor)

    def get_queryset(self):
        from django.db.models import Case, When, Value, IntegerField

        queryset = super().get_queryset()

        # Filtro opcional por piso (clientes y staff lo envían; sin él se ven todos)
        floor_param = self.request.query_params.get("floor")
        if floor_param is not None:
            queryset = queryset.filter(floor=_parse_floor(floor_param))

        if not self.request.user.is_authenticated:
            queryset = queryset.filter(
                status__in=[
                    SongRequest.Status.PENDING,
                    SongRequest.Status.QUEUED,
                    SongRequest.Status.PLAYING,
                ]
            )
        else:
            status_filter = self.request.query_params.get("status")
            if status_filter:
                queryset = queryset.filter(status=status_filter)

        # Ordenar: playing primero, luego queued/pending por orden de llegada, completadas/canceladas al final
        return queryset.annotate(
            status_order=Case(
                When(status=SongRequest.Status.PLAYING, then=Value(0)),
                When(status=SongRequest.Status.QUEUED, then=Value(1)),
                When(status=SongRequest.Status.PENDING, then=Value(2)),
                When(status=SongRequest.Status.FAILED, then=Value(3)),
                When(status=SongRequest.Status.CANCELLED, then=Value(4)),
                When(status=SongRequest.Status.COMPLETED, then=Value(5)),
                default=Value(6),
                output_field=IntegerField(),
            )
        ).order_by("status_order", "created_at")

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def update_status(self, request, pk=None):
        song_request = self.get_object()
        serializer = SongRequestStatusUpdateSerializer(data=request.data)

        if serializer.is_valid():
            new_status = serializer.validated_data["status"]

            if new_status == SongRequest.Status.PLAYING:
                song_request.mark_as_playing()
            elif new_status == SongRequest.Status.COMPLETED:
                song_request.mark_as_completed()
            elif new_status == SongRequest.Status.CANCELLED:
                song_request.mark_as_cancelled()
            else:
                song_request.status = new_status
                song_request.save()

            broadcast_music_update(floor=song_request.floor)
            return Response(
                SongRequestSerializer(song_request).data, status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        """Buscar canciones en Spotify"""
        query = request.query_params.get("q", "").strip()
        if not query:
            return Response(
                {"error": "El parámetro 'q' es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            tracks = search_tracks(query, floor=_request_floor(request), limit=10)
            serializer = SpotifyTrackSerializer(tracks, many=True)
            return Response(serializer.data)
        except SpotifyNotConnectedError:
            return Response(
                {"error": "Spotify no está conectado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except SpotifyRateLimitedError:
            return Response(
                {"error": "Spotify ocupado, intenta de nuevo en unos segundos"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(f"Error buscando en Spotify: {e}")
            return Response(
                {"error": "Error al buscar en Spotify"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="now-playing")
    def now_playing(self, request):
        """Obtener la canción que está sonando en Spotify"""
        try:
            current = get_currently_playing(floor=_request_floor(request))
            if not current:
                return Response({"message": "No hay nada reproduciéndose"}, status=status.HTTP_204_NO_CONTENT)
            return Response(current)
        except SpotifyNotConnectedError:
            return Response(
                {"error": "Spotify no está conectado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(f"Error obteniendo canción actual: {e}")
            return Response(
                {"error": "Error al obtener canción actual"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="queue-status")
    def queue_status(self, request):
        """Obtener la cola de reproducción de Spotify"""
        try:
            queue = get_queue(floor=_request_floor(request))
            return Response({"queue": queue})
        except SpotifyNotConnectedError:
            return Response(
                {"error": "Spotify no está conectado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except SpotifyRateLimitedError:
            # Durante un rate-limit, devolver cola vacia para que el UI no rompa.
            return Response({"queue": []}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error obteniendo cola: {e}")
            return Response(
                {"error": "Error al obtener la cola"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="spotify-status")
    def spotify_status(self, request):
        """Verificar si Spotify está conectado en el piso"""
        connected = is_connected(floor=_request_floor(request))
        return Response({"connected": connected})

    @action(detail=False, methods=["post"], url_path="player/pause", permission_classes=[IsAuthenticated])
    def player_pause(self, request):
        """Pausar reproducción"""
        try:
            pause_playback(floor=_request_floor(request))
            return Response({"message": "Reproducción pausada"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SpotifyRateLimitedError:
            return Response({"error": "Spotify ocupado, intenta en unos segundos"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al pausar: {e}")
            return Response({"error": "Error al pausar"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/resume", permission_classes=[IsAuthenticated])
    def player_resume(self, request):
        """Reanudar reproducción"""
        try:
            resume_playback(floor=_request_floor(request))
            return Response({"message": "Reproducción reanudada"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SpotifyRateLimitedError:
            return Response({"error": "Spotify ocupado, intenta en unos segundos"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al reanudar: {e}")
            return Response({"error": "Error al reanudar"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/next", permission_classes=[IsAuthenticated])
    def player_next(self, request):
        """Saltar a la siguiente canción"""
        floor = _request_floor(request)
        try:
            skip_to_next(floor=floor)
            broadcast_music_update(floor=floor)
            return Response({"message": "Siguiente canción"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SpotifyRateLimitedError:
            return Response({"error": "Spotify ocupado, intenta en unos segundos"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al saltar: {e}")
            return Response({"error": "Error al saltar"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/previous", permission_classes=[IsAuthenticated])
    def player_previous(self, request):
        """Volver a la canción anterior"""
        floor = _request_floor(request)
        try:
            skip_to_previous(floor=floor)
            broadcast_music_update(floor=floor)
            return Response({"message": "Canción anterior"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SpotifyRateLimitedError:
            return Response({"error": "Spotify ocupado, intenta en unos segundos"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al retroceder: {e}")
            return Response({"error": "Error al retroceder"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/play-track", permission_classes=[IsAuthenticated])
    def player_play_track(self, request):
        """Reproducir un track específico inmediatamente"""
        track_uri = request.data.get("track_uri")
        if not track_uri:
            return Response({"error": "track_uri es requerido"}, status=status.HTTP_400_BAD_REQUEST)
        floor = _request_floor(request)
        try:
            play_track(track_uri, floor=floor)
            broadcast_music_update(floor=floor)
            return Response({"message": "Reproduciendo track"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SpotifyRateLimitedError:
            return Response({"error": "Spotify ocupado, intenta en unos segundos"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al reproducir track: {e}")
            return Response({"error": "Error al reproducir"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"], url_path="lyrics")
    def lyrics(self, request):
        """Obtener letras sincronizadas de LRCLib para la canción actual o por parámetros"""
        track_name = request.query_params.get("track_name", "")
        artist_name = request.query_params.get("artist_name", "")
        duration = request.query_params.get("duration")  # en segundos

        if not track_name or not artist_name:
            return Response(
                {"error": "track_name y artist_name son requeridos"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            import requests as http_requests

            params = {
                "track_name": track_name,
                "artist_name": artist_name,
            }
            if duration:
                params["duration"] = int(duration)

            resp = http_requests.get(
                "https://lrclib.net/api/get",
                params=params,
                headers={"User-Agent": "Frostbyte/1.0"},
                timeout=5,
            )

            if resp.status_code == 404:
                return Response({"synced_lyrics": None, "plain_lyrics": None})

            resp.raise_for_status()
            data = resp.json()

            return Response({
                "synced_lyrics": data.get("syncedLyrics"),
                "plain_lyrics": data.get("plainLyrics"),
                "track_name": data.get("trackName"),
                "artist_name": data.get("artistName"),
            })
        except Exception as e:
            logger.debug(f"Error obteniendo letras: {e}")
            return Response({"synced_lyrics": None, "plain_lyrics": None})

    @action(detail=False, methods=["post"], url_path="player/volume", permission_classes=[IsAuthenticated])
    def player_volume(self, request):
        """Ajustar volumen (0-100)"""
        volume = request.data.get("volume")
        if volume is None or not (0 <= int(volume) <= 100):
            return Response({"error": "volume debe ser entre 0 y 100"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            set_volume(int(volume), floor=_request_floor(request))
            return Response({"message": f"Volumen ajustado a {volume}"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SpotifyRateLimitedError:
            return Response({"error": "Spotify ocupado, intenta en unos segundos"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al ajustar volumen: {e}")
            return Response({"error": "Error al ajustar volumen"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SpotifyAuthView(APIView):
    """Inicia el flujo OAuth de Spotify redirigiendo al usuario"""

    permission_classes = [AllowAny]

    def get(self, request):
        """Redirige directamente a Spotify para autorizar.

        Recibe ?floor=N y lo pasa en el state de OAuth para que el callback
        sepa a qué piso pertenece la cuenta autorizada. El staff debe iniciar
        sesión en Spotify con la cuenta del piso correspondiente.
        """
        from django.shortcuts import redirect
        floor = _request_floor(request)
        url = get_authorize_url(state=str(floor))
        return redirect(url)


class SpotifyCallbackView(APIView):
    """Callback de Spotify después de autorización"""

    permission_classes = [AllowAny]

    def get(self, request):
        """Procesar callback de Spotify con el código de autorización"""
        code = request.query_params.get("code")
        error = request.query_params.get("error")

        if error:
            return Response(
                {"error": f"Autorización denegada: {error}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not code:
            return Response(
                {"error": "Código de autorización no recibido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # El state trae el piso que inició el flujo (default 2 para flujos viejos)
        floor = _parse_floor(request.query_params.get("state"))

        try:
            token_data = exchange_code_for_tokens(code)
            save_tokens(token_data, floor=floor)
            # Redirigir al frontend con éxito
            frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
            from django.shortcuts import redirect
            return redirect(f"{frontend_url}/musica?spotify=connected&floor={floor}")
        except Exception as e:
            logger.error(f"Error en callback de Spotify: {e}")
            return Response(
                {"error": "Error al conectar con Spotify"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SpotifyDisconnectView(APIView):
    """Desconectar la cuenta de Spotify de un piso"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import SpotifyToken
        floor = _request_floor(request)
        SpotifyToken.objects.filter(floor=floor).delete()
        return Response({"message": f"Spotify desconectado (piso {floor})"})


class MusicSettingsView(APIView):
    """Configuracion del modulo de musica (fuente activa)"""

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request):
        settings = MusicSettings.get_settings()
        return Response(MusicSettingsSerializer(settings).data)

    def patch(self, request):
        settings = MusicSettings.get_settings()
        serializer = MusicSettingsSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        broadcast_music_update()
        return Response(serializer.data)



class MusicStatsView(APIView):
    """Estadísticas de lo que la gente pide: géneros, pisos, horas y tops.

    Solo para el equipo. El rango llega como `days` (los últimos N días de
    operación) o como `start`/`end` en formato ISO; sin parámetros devuelve la
    historia completa, que es lo interesante cuando se compara un piso con el
    otro. `floor` acota a un piso.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, end = self._range(request)
        floor = _parse_floor(request.query_params.get("floor"), default=None)
        return Response(build_stats(start=start, end=end, floor=floor))

    def _range(self, request):
        params = request.query_params
        if params.get("start") or params.get("end"):
            return self._parse_date(params.get("start")), self._parse_date(params.get("end"))

        days = params.get("days")
        if not days or days == "all":
            return None, None
        try:
            days = int(days)
        except (TypeError, ValueError):
            return None, None
        hoy = timezone.localdate()
        return hoy - timedelta(days=max(days, 1) - 1), hoy

    @staticmethod
    def _parse_date(value):
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
