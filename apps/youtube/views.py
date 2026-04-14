import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import VideoRequest, TVState
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
from .services.youtube_client import (
    search_videos,
    get_trending_music,
    get_quota_usage,
    YouTubeAPIError,
    YouTubeQuotaExceededError,
)

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
        if self.action in [
            "create",
            "list",
            "retrieve",
            "search",
            "now_playing",
            "queue",
            "last_played",
            "recommendations",
        ]:
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
        except YouTubeQuotaExceededError:
            return Response(
                {"error": "La busqueda esta temporalmente no disponible. Intenta mas tarde.", "code": "quota_exceeded"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
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
        """Obtener el video que se esta reproduciendo actualmente.
        Si no hay un VideoRequest en estado PLAYING, devuelve el estado
        reportado por la pantalla TV (que puede ser un video del Mix)."""
        current = VideoRequest.objects.filter(
            status=VideoRequest.Status.PLAYING
        ).first()

        if current:
            return Response(VideoRequestSerializer(current).data)

        # Fallback: estado reportado por la TV (incluye videos del Mix)
        state = TVState.get_state()
        if state.video_id:
            return Response({
                "id": None,
                "title": state.title,
                "channel_name": state.channel_name,
                "video_id": state.video_id,
                "thumbnail": state.thumbnail,
                "duration": "",
                "status": "playing",
                "status_display": "Reproduciendo",
                "is_mix": state.is_mix,
            })

        return Response(
            {"message": "No hay ningun video reproduciendose"},
            status=status.HTTP_204_NO_CONTENT,
        )

    @action(detail=False, methods=["get"], url_path="quota-status", permission_classes=[IsAuthenticated])
    def quota_status(self, request):
        """Estado estimado de uso de cuota del dia (solo admin/staff)"""
        return Response(get_quota_usage())

    @action(detail=False, methods=["get"], url_path="recommendations")
    def recommendations(self, request):
        """Recomendaciones de videos basadas en lo que esta sonando actualmente
        (incluye Mix automatico via TVState) o en el historial reciente.
        Si no hay nada, muestra videos musicales populares (trending)."""
        try:
            # Prioridad de semilla:
            # 1. Video actualmente reproduciendose (VideoRequest PLAYING)
            # 2. Estado reportado por la TV (puede ser del Mix)
            # 3. Ultimo video completado
            seed_title = None
            seed_channel = None
            seed_video_id = None

            current = VideoRequest.objects.filter(
                status=VideoRequest.Status.PLAYING
            ).first()
            if current and current.title:
                seed_title = current.title
                seed_channel = current.channel_name
                seed_video_id = current.video_id
            else:
                state = TVState.get_state()
                if state.video_id and state.title:
                    seed_title = state.title
                    seed_channel = state.channel_name
                    seed_video_id = state.video_id
                else:
                    last = VideoRequest.objects.filter(
                        status=VideoRequest.Status.COMPLETED,
                        played_at__isnull=False,
                    ).order_by("-played_at").first()
                    if last and last.title:
                        seed_title = last.title
                        seed_channel = last.channel_name
                        seed_video_id = last.video_id

            if seed_title:
                # Usar solo el canal como query si existe, es mas efectivo
                # para mantenerse en el mismo genero/artista
                query = seed_channel if seed_channel else seed_title
                videos = search_videos(query, limit=15)
                # Filtrar el video semilla del resultado
                if seed_video_id:
                    videos = [v for v in videos if v["video_id"] != seed_video_id]
            else:
                # Sin historial: videos musicales populares
                videos = get_trending_music(limit=15)

            serializer = YouTubeVideoSerializer(videos, many=True)
            return Response(serializer.data)
        except YouTubeQuotaExceededError:
            # Recomendaciones sin API: devolver lista vacia en vez de error
            return Response([])
        except YouTubeAPIError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(f"Error obteniendo recomendaciones: {e}")
            return Response(
                {"error": "Error al obtener recomendaciones"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="last-played")
    def last_played(self, request):
        """Ultimo video reproducido (para iniciar Mix cuando la cola esta vacia)"""
        last = VideoRequest.objects.filter(
            status=VideoRequest.Status.COMPLETED,
            played_at__isnull=False,
        ).order_by("-played_at").first()

        if not last:
            return Response(
                {"message": "No hay videos previos"},
                status=status.HTTP_204_NO_CONTENT,
            )

        return Response(VideoRequestSerializer(last).data)

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
        """Saltar al siguiente video en la cola.
        Si no hay siguiente, limpiar TVState para que la pantalla transicione
        a modo Mix automatico."""
        # Marcar el actual como completado
        from django.utils import timezone

        playing = list(VideoRequest.objects.filter(status=VideoRequest.Status.PLAYING))
        for vr in playing:
            vr.status = VideoRequest.Status.COMPLETED
            if not vr.played_at:
                vr.played_at = timezone.now()
            vr.save(update_fields=["status", "played_at", "updated_at"])

        # Obtener el siguiente en cola
        next_video = VideoRequest.objects.filter(
            status__in=[VideoRequest.Status.QUEUED, VideoRequest.Status.PENDING]
        ).order_by("created_at").first()

        if next_video:
            next_video.mark_as_playing()
            broadcast_youtube_play(next_video.video_id, next_video.title)
        else:
            # Sin proximos en cola: limpiar TVState para que la pantalla
            # detecte que no hay nada sonando y entre en modo Mix
            state = TVState.get_state()
            state.video_id = ""
            state.title = ""
            state.channel_name = ""
            state.thumbnail = ""
            state.is_mix = False
            state.save()

        broadcast_youtube_update()
        return Response({"message": "Siguiente video", "has_next": bool(next_video)})

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
