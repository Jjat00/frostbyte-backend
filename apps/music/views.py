from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Q

from .models import SongRequest
from .serializers import (
    SongRequestSerializer,
    SongRequestCreateSerializer,
    SongRequestStatusUpdateSerializer,
)


class SongRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gestionar solicitudes de canciones.

    list: Listar solicitudes (público para ver, autenticado para gestionar)
    retrieve: Detalle de una solicitud
    create: Crear nueva solicitud (público)
    update: Actualizar solicitud (solo autenticado)
    destroy: Eliminar solicitud (solo autenticado)
    """

    queryset = SongRequest.objects.all()
    permission_classes = [AllowAny]  # Permitir crear sin autenticación

    def get_serializer_class(self):
        if self.action == "create":
            return SongRequestCreateSerializer
        elif self.action == "update_status":
            return SongRequestStatusUpdateSerializer
        return SongRequestSerializer

    def get_permissions(self):
        """
        Permisos personalizados:
        - Cualquiera puede crear y listar
        - Solo autenticados pueden actualizar/eliminar/cambiar estado
        """
        if self.action in ["create", "list", "retrieve"]:
            return [AllowAny()]
        # Todas las demás acciones (update, destroy, update_status) requieren autenticación
        return [IsAuthenticated()]

    def get_queryset(self):
        """Filtrar queryset según el usuario"""
        queryset = super().get_queryset()

        # Para usuarios no autenticados, solo mostrar las pendientes y reproduciendo
        if not self.request.user.is_authenticated:
            queryset = queryset.filter(
                status__in=[SongRequest.Status.PENDING, SongRequest.Status.PLAYING]
            )
        else:
            # Usuarios autenticados pueden ver todas, con filtro opcional por estado
            status_filter = self.request.query_params.get('status')
            if status_filter:
                queryset = queryset.filter(status=status_filter)
            # Si no hay filtro, mostrar todas (incluyendo completadas)

        # Ordenar por fecha de creación (más recientes primero)
        return queryset.order_by("-created_at")

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def update_status(self, request, pk=None):
        """Actualizar el estado de una solicitud (solo usuarios autenticados)"""
        song_request = self.get_object()
        serializer = SongRequestStatusUpdateSerializer(data=request.data)

        if serializer.is_valid():
            new_status = serializer.validated_data["status"]

            # Actualizar estado según la acción
            if new_status == SongRequest.Status.PLAYING:
                song_request.mark_as_playing()
            elif new_status == SongRequest.Status.COMPLETED:
                song_request.mark_as_completed()
            elif new_status == SongRequest.Status.CANCELLED:
                song_request.mark_as_cancelled()
            else:
                song_request.status = new_status
                song_request.save()

            return Response(
                SongRequestSerializer(song_request).data, status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

