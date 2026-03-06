import logging
import os

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from .models import SongRequest
from .consumers import broadcast_music_update
from .serializers import (
    SongRequestSerializer,
    SongRequestCreateSerializer,
    SongRequestStatusUpdateSerializer,
    SpotifyTrackSerializer,
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
)
from .services.spotify_auth import (
    get_authorize_url,
    exchange_code_for_tokens,
    save_tokens,
)

logger = logging.getLogger(__name__)


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

    def perform_create(self, serializer):
        song_request = serializer.save()

        # Intentar agregar a la cola de Spotify automáticamente
        if song_request.spotify_track_uri:
            try:
                add_to_queue(song_request.spotify_track_uri)
                song_request.status = SongRequest.Status.QUEUED
                song_request.save(update_fields=["status", "updated_at"])
            except SpotifyNotConnectedError:
                logger.warning("Spotify no conectado, la solicitud queda pendiente")
            except Exception as e:
                logger.error(f"Error al encolar en Spotify: {e}")
                song_request.status = SongRequest.Status.FAILED
                song_request.save(update_fields=["status", "updated_at"])

        broadcast_music_update()

    def perform_destroy(self, instance):
        instance.delete()
        broadcast_music_update()

    def get_queryset(self):
        from django.db.models import Case, When, Value, IntegerField

        queryset = super().get_queryset()

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

            broadcast_music_update()
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
            tracks = search_tracks(query, limit=10)
            serializer = SpotifyTrackSerializer(tracks, many=True)
            return Response(serializer.data)
        except SpotifyNotConnectedError:
            return Response(
                {"error": "Spotify no está conectado"},
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
            current = get_currently_playing()
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
            queue = get_queue()
            return Response({"queue": queue})
        except SpotifyNotConnectedError:
            return Response(
                {"error": "Spotify no está conectado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(f"Error obteniendo cola: {e}")
            return Response(
                {"error": "Error al obtener la cola"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="spotify-status")
    def spotify_status(self, request):
        """Verificar si Spotify está conectado"""
        connected = is_connected()
        return Response({"connected": connected})

    @action(detail=False, methods=["post"], url_path="player/pause", permission_classes=[IsAuthenticated])
    def player_pause(self, request):
        """Pausar reproducción"""
        try:
            pause_playback()
            return Response({"message": "Reproducción pausada"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al pausar: {e}")
            return Response({"error": "Error al pausar"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/resume", permission_classes=[IsAuthenticated])
    def player_resume(self, request):
        """Reanudar reproducción"""
        try:
            resume_playback()
            return Response({"message": "Reproducción reanudada"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al reanudar: {e}")
            return Response({"error": "Error al reanudar"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/next", permission_classes=[IsAuthenticated])
    def player_next(self, request):
        """Saltar a la siguiente canción"""
        try:
            skip_to_next()
            broadcast_music_update()
            return Response({"message": "Siguiente canción"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al saltar: {e}")
            return Response({"error": "Error al saltar"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/previous", permission_classes=[IsAuthenticated])
    def player_previous(self, request):
        """Volver a la canción anterior"""
        try:
            skip_to_previous()
            broadcast_music_update()
            return Response({"message": "Canción anterior"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al retroceder: {e}")
            return Response({"error": "Error al retroceder"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/play-track", permission_classes=[IsAuthenticated])
    def player_play_track(self, request):
        """Reproducir un track específico inmediatamente"""
        track_uri = request.data.get("track_uri")
        if not track_uri:
            return Response({"error": "track_uri es requerido"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            play_track(track_uri)
            broadcast_music_update()
            return Response({"message": "Reproduciendo track"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al reproducir track: {e}")
            return Response({"error": "Error al reproducir"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="player/volume", permission_classes=[IsAuthenticated])
    def player_volume(self, request):
        """Ajustar volumen (0-100)"""
        volume = request.data.get("volume")
        if volume is None or not (0 <= int(volume) <= 100):
            return Response({"error": "volume debe ser entre 0 y 100"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            set_volume(int(volume))
            return Response({"message": f"Volumen ajustado a {volume}"})
        except SpotifyNotConnectedError:
            return Response({"error": "Spotify no está conectado"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error al ajustar volumen: {e}")
            return Response({"error": "Error al ajustar volumen"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SpotifyAuthView(APIView):
    """Inicia el flujo OAuth de Spotify redirigiendo al usuario"""

    permission_classes = [AllowAny]

    def get(self, request):
        """Redirige directamente a Spotify para autorizar"""
        from django.shortcuts import redirect
        url = get_authorize_url()
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

        try:
            token_data = exchange_code_for_tokens(code)
            save_tokens(token_data)
            # Redirigir al frontend con éxito
            frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
            from django.shortcuts import redirect
            return redirect(f"{frontend_url}/musica?spotify=connected")
        except Exception as e:
            logger.error(f"Error en callback de Spotify: {e}")
            return Response(
                {"error": "Error al conectar con Spotify"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SpotifyDisconnectView(APIView):
    """Desconectar Spotify"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import SpotifyToken
        SpotifyToken.objects.all().delete()
        return Response({"message": "Spotify desconectado"})

