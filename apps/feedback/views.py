from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.accounts.permissions import IsAdminUser
from django.db.models import Avg

from .models import Feedback
from .serializers import (
    FeedbackSerializer,
    FeedbackCreateSerializer,
    FeedbackStatusUpdateSerializer,
)


class FeedbackViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gestionar feedback de clientes.

    list: Listar feedback (solo autenticado)
    retrieve: Detalle de un feedback (solo autenticado)
    create: Crear nuevo feedback (publico)
    update: Actualizar feedback (solo autenticado)
    destroy: Eliminar feedback (solo autenticado)
    """

    queryset = Feedback.objects.all()
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "create":
            return FeedbackCreateSerializer
        elif self.action == "update_status":
            return FeedbackStatusUpdateSerializer
        return FeedbackSerializer

    def get_permissions(self):
        """
        Permisos personalizados:
        - Cualquiera puede crear feedback
        - Solo autenticados pueden listar/ver/actualizar
        - Solo admin puede eliminar
        """
        if self.action == "create":
            return [AllowAny()]
        if self.action == "destroy":
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def get_queryset(self):
        """Filtrar queryset segun parametros"""
        queryset = super().get_queryset()

        # Solo usuarios autenticados pueden ver la lista
        if not self.request.user.is_authenticated:
            return queryset.none()

        # Filtros opcionales
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        feedback_type = self.request.query_params.get("type")
        if feedback_type:
            queryset = queryset.filter(feedback_type=feedback_type)

        rating = self.request.query_params.get("rating")
        if rating:
            queryset = queryset.filter(rating=rating)

        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        """Crear feedback - Respuesta personalizada"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        return Response(
            {
                "message": "Gracias por tu feedback! Tu opinion es muy importante para nosotros.",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def update_status(self, request, pk=None):
        """Actualizar el estado de un feedback (solo usuarios autenticados)"""
        feedback = self.get_object()
        serializer = FeedbackStatusUpdateSerializer(data=request.data)

        if serializer.is_valid():
            new_status = serializer.validated_data["status"]
            admin_notes = serializer.validated_data.get("admin_notes", "")

            # Actualizar notas si se proporcionan
            if admin_notes:
                feedback.admin_notes = admin_notes

            # Actualizar estado segun la accion
            if new_status == Feedback.Status.REVIEWED:
                feedback.mark_as_reviewed()
            elif new_status == Feedback.Status.RESPONDED:
                feedback.mark_as_responded()
            elif new_status == Feedback.Status.ARCHIVED:
                feedback.mark_as_archived()
            else:
                feedback.status = new_status
                feedback.save()

            return Response(
                FeedbackSerializer(feedback).data, status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def stats(self, request):
        """Obtener estadisticas de feedback"""
        queryset = Feedback.objects.all()

        stats = {
            "total": queryset.count(),
            "by_status": {
                s: queryset.filter(status=s).count()
                for s, _ in Feedback.Status.choices
            },
            "by_type": {
                ftype: queryset.filter(feedback_type=ftype).count()
                for ftype, _ in Feedback.FeedbackType.choices
            },
            "average_rating": queryset.filter(rating__isnull=False).aggregate(
                avg=Avg("rating")
            )["avg"],
            "rating_distribution": {
                i: queryset.filter(rating=i).count() for i in range(1, 6)
            },
        }

        return Response(stats)
