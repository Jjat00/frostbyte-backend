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

        return queryset.order_by("-created_at")

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

