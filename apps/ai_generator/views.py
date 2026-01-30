"""
Vista para generación de imágenes con IA (uso interno).
Crear generación, listar, detalle y guardar imagen en producto del menú/carta.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import AIImageGeneration
from .serializers import AIImageGenerationSerializer, SaveToProductSerializer
from .tasks import generate_image_task, generate_image_sync
from apps.products.models import Product
import logging

logger = logging.getLogger(__name__)


class AIImageGenerationViewSet(viewsets.ModelViewSet):
    """Crear/consultar generaciones y guardar imagen en producto."""

    permission_classes = [IsAuthenticated]
    serializer_class = AIImageGenerationSerializer

    def get_queryset(self):
        return AIImageGeneration.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        generation = serializer.save(user=request.user)

        logger.info(
            f"AI generation {generation.id} for user {request.user.email}")

        try:
            from .tasks import CELERY_AVAILABLE
            if CELERY_AVAILABLE:
                generate_image_task.delay(str(generation.id))
            else:
                generate_image_sync(str(generation.id))
                generation.refresh_from_db()
        except Exception as e:
            logger.error(f"Error starting generation: {e}")
            generation.status = 'failed'
            generation.error_message = "Error al iniciar la generación"
            generation.save()
            return Response(
                {'error': 'Error al iniciar la generación'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            AIImageGenerationSerializer(
                generation, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def save_to_product(self, request, pk=None):
        """
        POST /api/v1/ai/generations/{id}/save_to_product/
        Asigna la imagen generada a un producto del menú/carta.
        Body: { "product_id": 123 }
        """
        generation = self.get_object()

        if generation.status != 'completed':
            return Response(
                {'error': 'La generación no está completada aún'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not generation.generated_image:
            return Response(
                {'error': 'No hay imagen generada disponible'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SaveToProductSerializer(
            data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data['product_id']
        product = get_object_or_404(Product, id=product_id)

        image_url = request.build_absolute_uri(generation.generated_image.url)
        product.image_url = image_url
        product.save()

        generation.product = product
        generation.save()

        logger.info(
            f"Saved generated image {generation.id} to product {product.id}")

        return Response({
            'success': True,
            'product_id': product.id,
            'product_name': product.name,
            'image_url': image_url,
            'message': f'Imagen asignada al producto "{product.name}"',
        })
