import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import VideoRequest
from .consumers import (
    broadcast_youtube_update,
    broadcast_youtube_play,
    broadcast_youtube_control,
)
from .serializers import (
    VideoRequestSerializer,
    VideoRequestCreateSerializer,
    VideoRequestStatusUpdateSerializer,
    YouTubeVideoSerializer,
)
from .services.youtube_client import search_videos, YouTubeAPIError

logger = logging.getLogger(__name__)


class VideoRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gestionar solicitudes de videos de YouTube.

    list: Listar solicitudes
    create: Crear nueva solicitud y encolar
    update_status: Cambiar estado de una solicitud
    search: Buscar videos en YouTube
    now_playing: Video que se esta reproduciendo actualmente
    queue: Cola de videos pendientes
    player controls: pause, resume, skip (enviados via WebSocket a la pantalla TV)
    """

    queryset = VideoRequest.objects.all()
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "create":
            return VideoRequestCreateSerializer
        elif self.action == "update_status":
            return VideoRequestStatusUpdateSerializer
        return VideoRequestSerializer

    def get_permissions(self):
        if self.action in ["create", "list", "retrieve", "search", "now_playing", "queue"]:
            return [AllowAny()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Rechazar si el mismo video ya esta en cola o reproduciendose
        video_id = serializer.validated_data.get("video_id")
        if video_id:
            duplicate = VideoRequest.objects.filter(
                video_id=video_id,
                status__in=[
                    VideoRequest.Status.PENDING,
                    VideoRequest.Status.QUEUED,
                    VideoRequest.Status.PLAYING,
                ],
            ).exists()
            if duplicate:
                return Response(
                    {"error": "Este video ya esta en la cola o reproduciendose."},
                    status=status.HTTP_409_CONFLICT,
                )

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        video_request = serializer.save()
        video_request.mark_as_queued()
        broadcast_youtube_update()

    def perform_destroy(self, instance):
        instance.delete()
        broadcast_youtube_update()

    def get_queryset(self):
        from django.db.models import Case, When, Value, IntegerField

        queryset = super().get_queryset()

        if not self.request.user.is_authenticated:
            queryset = queryset.filter(
                status__in=[
                    VideoRequest.Status.PENDING,
                    VideoRequest.Status.QUEUED,
                    VideoRequest.Status.PLAYING,
                ]
            )
        else:
            status_filter = self.request.query_params.get("status")
            if status_filter:
                queryset = queryset.filter(status=status_filter)

        return queryset.annotate(
            status_order=Case(
                When(status=VideoRequest.Status.PLAYING, then=Value(0)),
                When(status=VideoRequest.Status.QUEUED, then=Value(1)),
                When(status=VideoRequest.Status.PENDING, then=Value(2)),
                When(status=VideoRequest.Status.CANCELLED, then=Value(3)),
                When(status=VideoRequest.Status.COMPLETED, then=Value(4)),
                default=Value(5),
                output_field=IntegerField(),
            )
        ).order_by("status_order", "created_at")

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def update_status(self, request, pk=None):
        video_request = self.get_object()
        serializer = VideoRequestStatusUpdateSerializer(data=request.data)

        if serializer.is_valid():
            new_status = serializer.validated_data["status"]

            if new_status == VideoRequest.Status.PLAYING:
                video_request.mark_as_playing()
            elif new_status == VideoRequest.Status.COMPLETED:
                video_request.mark_as_completed()
            elif new_status == VideoRequest.Status.CANCELLED:
                video_request.mark_as_cancelled()
            else:
                video_request.status = new_status
                video_request.save()

            broadcast_youtube_update()
            return Response(
                VideoRequestSerializer(video_request).data, status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        """Buscar videos en YouTube"""
        query = request.query_params.get("q", "").strip()
        if not query:
            return Response(
                {"error": "El parametro 'q' es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            videos = search_videos(query, limit=10)
            serializer = YouTubeVideoSerializer(videos, many=True)
            return Response(serializer.data)
        except YouTubeAPIError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(f"Error buscando en YouTube: {e}")
            return Response(
                {"error": "Error al buscar en YouTube"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="now-playing")
    def now_playing(self, request):
        """Obtener el video que se esta reproduciendo actualmente"""
        current = VideoRequest.objects.filter(
            status=VideoRequest.Status.PLAYING
        ).first()

        if not current:
            return Response(
                {"message": "No hay ningun video reproduciendose"},
                status=status.HTTP_204_NO_CONTENT,
            )

        return Response(VideoRequestSerializer(current).data)

    @action(detail=False, methods=["get"], url_path="queue")
    def queue(self, request):
        """Obtener la cola de videos pendientes"""
        queued = VideoRequest.objects.filter(
            status__in=[VideoRequest.Status.QUEUED, VideoRequest.Status.PENDING]
        ).order_by("created_at")

        return Response({
            "queue": VideoRequestSerializer(queued, many=True).data
        })

    @action(detail=False, methods=["post"], url_path="player/play", permission_classes=[IsAuthenticated])
    def player_play(self, request):
        """Reproducir un video especifico inmediatamente"""
        video_request_id = request.data.get("id")
        if not video_request_id:
            return Response(
                {"error": "id es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            vr = VideoRequest.objects.get(id=video_request_id)
        except VideoRequest.DoesNotExist:
            return Response(
                {"error": "Solicitud no encontrada"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Marcar el actual como completado si hay uno
        VideoRequest.objects.filter(
            status=VideoRequest.Status.PLAYING
        ).update(status=VideoRequest.Status.COMPLETED)

        vr.mark_as_playing()
        broadcast_youtube_play(vr.video_id, vr.title)
        broadcast_youtube_update()
        return Response({"message": "Reproduciendo video"})

    @action(detail=False, methods=["post"], url_path="player/next", permission_classes=[IsAuthenticated])
    def player_next(self, request):
        """Saltar al siguiente video en la cola"""
        # Marcar el actual como completado
        VideoRequest.objects.filter(
            status=VideoRequest.Status.PLAYING
        ).update(status=VideoRequest.Status.COMPLETED)

        # Obtener el siguiente en cola
        next_video = VideoRequest.objects.filter(
            status__in=[VideoRequest.Status.QUEUED, VideoRequest.Status.PENDING]
        ).order_by("created_at").first()

        if next_video:
            next_video.mark_as_playing()
            broadcast_youtube_play(next_video.video_id, next_video.title)

        broadcast_youtube_update()
        return Response({"message": "Siguiente video"})

    @action(detail=False, methods=["post"], url_path="player/pause", permission_classes=[IsAuthenticated])
    def player_pause(self, request):
        """Pausar reproduccion en la pantalla TV"""
        broadcast_youtube_control("pause")
        return Response({"message": "Video pausado"})

    @action(detail=False, methods=["post"], url_path="player/resume", permission_classes=[IsAuthenticated])
    def player_resume(self, request):
        """Reanudar reproduccion en la pantalla TV"""
        broadcast_youtube_control("resume")
        return Response({"message": "Video reanudado"})
