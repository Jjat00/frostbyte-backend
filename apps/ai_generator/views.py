from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import AIImageGeneration, AIGenerationUsageQuota
from .serializers import (
    AIImageGenerationSerializer,
    AIGenerationHistorySerializer,
    SaveToProductSerializer,
    AIQuotaSerializer,
    RegenerateSerializer
)
from .tasks import generate_image_task, generate_image_sync
from apps.products.models import Product
import logging

logger = logging.getLogger(__name__)


class AIImageGenerationViewSet(viewsets.ModelViewSet):
    """ViewSet para generación de imágenes con IA"""

    permission_classes = [IsAuthenticated]
    serializer_class = AIImageGenerationSerializer

    def get_queryset(self):
        """Filtrar por usuario actual"""
        return AIImageGeneration.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        """Usar serializer diferente para list"""
        if self.action == 'list':
            return AIGenerationHistorySerializer
        return AIImageGenerationSerializer

    def create(self, request, *args, **kwargs):
        """
        POST /api/ai/generations/

        Crea una nueva generación de imagen
        """
        # Verificar cuota del usuario
        quota, created = AIGenerationUsageQuota.objects.get_or_create(
            user=request.user,
            defaults={'monthly_limit': 10}
        )

        if not quota.can_generate():
            return Response(
                {
                    'error': 'Cuota mensual excedida',
                    'detail': f'Has usado {quota.used_this_month}/{quota.monthly_limit} generaciones este mes.',
                    'used': quota.used_this_month,
                    'limit': quota.monthly_limit
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Validar datos
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Crear objeto (status=pending)
        generation = serializer.save(user=request.user)

        logger.info(
            f"Created AI generation {generation.id} for user {request.user.email}")

        # Encolar tarea asíncrona (o ejecutar síncronamente si Celery no está disponible)
        try:
            # Intentar encolar con Celery
            from .tasks import CELERY_AVAILABLE
            if CELERY_AVAILABLE:
                generate_image_task.delay(str(generation.id))
                logger.info(f"Enqueued generation task for {generation.id}")
            else:
                # Ejecutar síncronamente en desarrollo
                logger.warning(
                    "Executing generation synchronously (Celery not available)")
                generate_image_sync(str(generation.id))
                # Refrescar para que la respuesta incluya status completed y generated_image_url
                generation.refresh_from_db()

        except Exception as e:
            logger.error(f"Error starting generation task: {e}")
            generation.status = 'failed'
            generation.error_message = "Error al iniciar la generación"
            generation.save()
            return Response(
                {'error': 'Error al iniciar la generación'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Retornar respuesta
        response_serializer = AIImageGenerationSerializer(
            generation, context={'request': request})
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=['post'])
    def save_to_product(self, request, pk=None):
        """
        POST /api/ai/generations/{id}/save_to_product/

        Guarda la imagen generada en un producto
        """
        generation = self.get_object()

        if generation.status != 'completed':
            return Response(
                {'error': 'La generación de imagen no está completada aún'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not generation.generated_image:
            return Response(
                {'error': 'No hay imagen generada disponible'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = SaveToProductSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data['product_id']
        product = get_object_or_404(Product, id=product_id)

        # Actualizar imagen del producto
        product.image_url = generation.generated_image.url
        product.save()

        # Asociar generación con producto
        generation.product = product
        generation.save()

        logger.info(
            f"Saved generated image {generation.id} to product {product.id}")

        return Response({
            'success': True,
            'product_id': product.id,
            'product_name': product.name,
            'image_url': generation.generated_image.url,
            'message': f'Imagen guardada en el producto "{product.name}"'
        })

    @action(detail=True, methods=['post'])
    def regenerate(self, request, pk=None):
        """
        POST /api/ai/generations/{id}/regenerate/

        Regenera una imagen con nuevos parámetros
        """
        original_generation = self.get_object()

        # Verificar cuota
        quota = AIGenerationUsageQuota.objects.get(user=request.user)
        if not quota.can_generate():
            return Response(
                {'error': 'Cuota mensual excedida'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Validar nuevos parámetros
        serializer = RegenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Crear nueva generación basada en la original
        new_generation = AIImageGeneration.objects.create(
            user=request.user,
            original_image=original_generation.original_image,
            reference_image=original_generation.reference_image,
            user_prompt=serializer.validated_data.get(
                'user_prompt', original_generation.user_prompt),
            transparent_background=serializer.validated_data.get(
                'transparent_background', original_generation.transparent_background),
            product=original_generation.product
        )

        # Encolar tarea
        try:
            from .tasks import CELERY_AVAILABLE
            if CELERY_AVAILABLE:
                generate_image_task.delay(str(new_generation.id))
            else:
                generate_image_sync(str(new_generation.id))

            logger.info(
                f"Regeneration {new_generation.id} started from {original_generation.id}")

        except Exception as e:
            logger.error(f"Error starting regeneration: {e}")
            new_generation.status = 'failed'
            new_generation.error_message = "Error al iniciar la regeneración"
            new_generation.save()
            return Response(
                {'error': 'Error al iniciar la regeneración'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        response_serializer = AIImageGenerationSerializer(
            new_generation, context={'request': request})
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=['get'])
    def quota_status(self, request):
        """
        GET /api/ai/generations/quota_status/

        Obtiene el estado de la cuota del usuario
        """
        quota, created = AIGenerationUsageQuota.objects.get_or_create(
            user=request.user,
            defaults={'monthly_limit': 10}
        )

        serializer = AIQuotaSerializer(quota)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """
        GET /api/ai/generations/stats/

        Obtiene estadísticas de generaciones del usuario
        """
        total_generations = self.get_queryset().count()
        completed = self.get_queryset().filter(status='completed').count()
        failed = self.get_queryset().filter(status='failed').count()
        processing = self.get_queryset().filter(
            status__in=['pending', 'processing']).count()

        total_cost = sum(
            float(gen.cost_usd or 0)
            for gen in self.get_queryset().filter(status='completed')
        )

        avg_time = None
        completed_gens = self.get_queryset().filter(
            status='completed',
            generation_time_seconds__isnull=False
        )
        if completed_gens.exists():
            avg_time = sum(
                float(gen.generation_time_seconds)
                for gen in completed_gens
            ) / completed_gens.count()

        return Response({
            'total_generations': total_generations,
            'completed': completed,
            'failed': failed,
            'processing': processing,
            'total_cost_usd': round(total_cost, 2),
            'avg_generation_time_seconds': round(avg_time, 2) if avg_time else None,
            'success_rate': round((completed / total_generations * 100), 1) if total_generations > 0 else 0
        })
