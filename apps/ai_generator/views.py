from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import AIImageGeneration
from .serializers import AIImageGenerationSerializer, SaveToProductSerializer
from .tasks import generate_image_sync
from apps.products.models import Product
import logging

logger = logging.getLogger(__name__)


class AIImageGenerationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AIImageGenerationSerializer

    def get_queryset(self):
        return AIImageGeneration.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        generation = serializer.save(user=request.user)

        logger.info(f"AI generation {generation.id} created")

        try:
            generate_image_sync(str(generation.id))
            generation.refresh_from_db()
        except Exception as e:
            logger.error(f"Generation error: {e}")
            generation.status = 'failed'
            generation.error_message = str(e)
            generation.save()

        return Response(
            AIImageGenerationSerializer(generation, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def save_to_product(self, request, pk=None):
        generation = self.get_object()

        if generation.status != 'completed' or not generation.generated_image:
            return Response({'error': 'Generación no completada'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = SaveToProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = get_object_or_404(Product, id=serializer.validated_data['product_id'])
        product.image_url = request.build_absolute_uri(generation.generated_image.url)
        product.save()

        generation.product = product
        generation.save()

        return Response({
            'success': True,
            'product_id': product.id,
            'product_name': product.name,
            'image_url': product.image_url,
        })
