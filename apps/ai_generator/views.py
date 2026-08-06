from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404
from django.conf import settings
from openai import OpenAI
from .models import AIImageGeneration
from .serializers import AIImageGenerationSerializer, AIImageGenerationCreateSerializer, SaveToProductSerializer, SuggestDescriptionSerializer, SuggestHistorySerializer
from .tasks import generate_image_sync
from .services.temp_storage import TempImageStorage
from .services.r2_persistence import AIGenerationR2Service
from apps.products.models import Product
import logging
import os

logger = logging.getLogger(__name__)


def friendly_generation_error(error_message: str) -> str:
    """Traduce el error crudo del proveedor a algo accionable para el equipo."""
    raw = (error_message or '').lower()

    if 'api key not valid' in raw or 'api_key_invalid' in raw or 'invalid_api_key' in raw:
        return (
            'El proveedor de IA rechazo la API key (invalida o vencida). '
            'Avisa al administrador para renovarla; mientras tanto prueba con '
            'otro modelo.'
        )
    if 'no esta configurado' in raw or 'not configured' in raw:
        return (
            'Falta configurar la API key de este proveedor de IA. '
            'Avisa al administrador o prueba con otro modelo.'
        )
    if 'quota' in raw or 'billing' in raw or 'insufficient_quota' in raw:
        return (
            'Se agoto el saldo o la cuota del proveedor de IA. '
            'Avisa al administrador o prueba con otro modelo.'
        )
    if 'rate limit' in raw or 'rate_limit' in raw or '429' in raw:
        return (
            'El proveedor de IA esta saturado en este momento. '
            'Espera un momento y vuelve a intentar.'
        )
    if 'timeout' in raw or 'timed out' in raw:
        return (
            'El proveedor de IA tardo demasiado en responder. '
            'Vuelve a intentar o prueba con otro modelo.'
        )

    return (
        'No se pudo generar la imagen. Prueba con otro modelo. '
        f'Detalle: {error_message}'
    )


class AIImageGenerationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AIImageGenerationSerializer

    def get_queryset(self):
        # La galeria es compartida: cualquier usuario con acceso ve todas las
        # generaciones, sin importar quien las creo.
        queryset = AIImageGeneration.objects.all()

        # En el listado no tiene sentido mostrar generaciones fallidas (no
        # tienen imagen); ahora que la galeria es compartida serian ruido
        # para todo el equipo.
        if self.action == 'list':
            queryset = queryset.exclude(status='failed')

        return queryset

    def get_serializer_class(self):
        if self.action == 'create':
            return AIImageGenerationCreateSerializer
        return AIImageGenerationSerializer

    def create(self, request, *args, **kwargs):
        """Crea generación y guarda imágenes temporalmente"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Guardar imágenes en storage temporal
        temp_storage = TempImageStorage()

        original_file = request.FILES.get('original_image')
        reference_file = request.FILES.get('reference_image')

        if not original_file:
            return Response(
                {'error': 'original_image es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        temp_original = temp_storage.save_temp_file(original_file, 'original')
        temp_reference = temp_storage.save_temp_file(reference_file, 'reference') if reference_file else ''

        # Crear generación con rutas temporales
        generation = AIImageGeneration.objects.create(
            user=request.user,
            temp_original_path=temp_original,
            temp_reference_path=temp_reference,
            user_prompt=serializer.validated_data.get('user_prompt', ''),
            transparent_background=serializer.validated_data.get('transparent_background', True),
            ai_model=serializer.validated_data.get('ai_model', 'gpt-image-1.5'),
        )

        logger.info(f"AI generation {generation.id} created with temp storage")

        # Procesar con el proveedor de IA seleccionado
        try:
            generate_image_sync(str(generation.id))
            generation.refresh_from_db()
        except Exception as e:
            logger.error(f"Generation error: {e}")
            generation.status = 'failed'
            generation.error_message = str(e)
            generation.save()

        data = AIImageGenerationSerializer(generation, context={'request': request}).data

        # Una generacion fallida no es un 201: el front mostraba "generada
        # exitosamente" con la imagen vacia y el usuario se quedaba sin saber
        # que paso.
        if generation.status == 'failed':
            return Response(
                {**data, 'error': friendly_generation_error(generation.error_message)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def save_to_r2(self, request, pk=None):
        """Guarda la generación permanentemente en R2"""
        generation = self.get_object()

        if generation.status not in ['completed', 'saved']:
            return Response(
                {'error': 'Generación no completada'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if generation.is_saved_to_r2:
            return Response({
                'message': 'Ya está guardado en R2',
                'urls': {
                    'original': generation.original_image_url,
                    'reference': generation.reference_image_url,
                    'generated': generation.generated_image_url,
                }
            })

        # Persistir a R2
        r2_service = AIGenerationR2Service()
        success = r2_service.save_generation_to_r2(generation)

        if success:
            generation.refresh_from_db()
            return Response({
                'success': True,
                'message': 'Guardado en R2 exitosamente',
                'urls': {
                    'original': generation.original_image_url,
                    'reference': generation.reference_image_url,
                    'generated': generation.generated_image_url,
                }
            })
        else:
            return Response(
                {'error': 'Error al guardar en R2'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'])
    def save_to_product(self, request, pk=None):
        """Guarda resultado a producto (automáticamente guarda a R2 primero)"""
        generation = self.get_object()

        if generation.status not in ['completed', 'saved']:
            return Response(
                {'error': 'Generación no completada'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = SaveToProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Asegurar que está guardado en R2
        if not generation.is_saved_to_r2:
            r2_service = AIGenerationR2Service()
            if not r2_service.save_generation_to_r2(generation):
                return Response(
                    {'error': 'Error al guardar en R2'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            generation.refresh_from_db()

        # Asociar con producto
        product = get_object_or_404(Product, id=serializer.validated_data['product_id'])
        product.image_url = generation.generated_image_url
        product.save()

        generation.product = product
        generation.save()

        return Response({
            'success': True,
            'product_id': product.id,
            'product_name': product.name,
            'image_url': product.image_url,
        })

    @action(detail=True, methods=['get'], url_path='temp-image/(?P<image_type>[^/.]+)', permission_classes=[])
    def serve_temp_image(self, request, pk=None, image_type=None):
        """Sirve imagen temporal (público - las imágenes se eliminan en 24h)"""
        generation = get_object_or_404(AIImageGeneration, pk=pk)

        if generation.is_saved_to_r2:
            # Redirigir a URL de R2
            urls = {
                'original': generation.original_image_url,
                'reference': generation.reference_image_url,
                'generated': generation.generated_image_url,
            }
            url = urls.get(image_type)
            if url:
                return Response({'redirect': url})
            raise Http404("Imagen no encontrada")

        # Obtener ruta según tipo
        path_map = {
            'original': generation.temp_original_path,
            'reference': generation.temp_reference_path,
            'generated': generation.temp_generated_path,
        }

        filepath = path_map.get(image_type)

        if not filepath or not os.path.exists(filepath):
            raise Http404("Imagen temporal no encontrada")

        return FileResponse(open(filepath, 'rb'), content_type='image/png')

    @action(detail=True, methods=['delete'])
    def discard(self, request, pk=None):
        """Descarta una generación y limpia archivos temporales"""
        generation = self.get_object()

        if generation.is_saved_to_r2:
            return Response(
                {'error': 'No se puede descartar una generación ya guardada en R2'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Limpiar archivos temporales
        generation.cleanup_temp_files()
        generation.delete()

        return Response({'success': True, 'message': 'Generación descartada'})


class SuggestDescriptionView(APIView):
    """Sugiere una descripción de producto usando OpenAI"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SuggestDescriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_name = serializer.validated_data['product_name']

        try:
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'Eres un experto en marketing gastronómico. '
                            'Genera una descripción MUY corta y atractiva para un producto de restaurante. '
                            'Máximo 1 oración de 10-15 palabras. '
                            'Responde SOLO con la descripción, sin comillas ni explicaciones.'
                        ),
                    },
                    {
                        'role': 'user',
                        'content': f'Genera una descripción para el producto: {product_name}',
                    },
                ],
                max_tokens=60,
                temperature=0.7,
            )
            description = response.choices[0].message.content.strip()
            return Response({'description': description})
        except Exception as e:
            logger.error(f'Error al sugerir descripción: {e}')
            return Response(
                {'error': 'No se pudo generar la descripción'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SuggestHistoryView(APIView):
    """Sugiere una breve historia/origen de un cóctel usando OpenAI"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SuggestHistorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_name = serializer.validated_data['product_name']

        try:
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'Eres un experto en coctelería e historia de las bebidas. '
                            'Cuenta la historia u origen de un cóctel de forma breve, amena y fiel. '
                            'Máximo 2 o 3 oraciones (35-55 palabras). '
                            'Tono cálido y de divulgación, en español. '
                            'REGLA 1: incluye SIEMPRE el año exacto de creación mejor documentado '
                            '(un número de año concreto). Si el año es disputado, usa el más aceptado '
                            'históricamente; nunca inventes un año falso. '
                            'REGLA 2: si el nombre es una variación de un cóctel clásico '
                            '(por ejemplo "Margarita de fresa", "Caipiroshka", "Blue Long"), '
                            'cuenta la historia del cóctel ORIGINAL del que deriva, no de la variación. '
                            'Si el nombre no corresponde a ningún cóctel conocido, escribe una breve '
                            'nota evocadora sobre el estilo de bebida, sin inventar fechas. '
                            'Responde SOLO con la historia, sin comillas, títulos ni explicaciones.'
                        ),
                    },
                    {
                        'role': 'user',
                        'content': f'Cuenta la breve historia, con su año exacto de creación, del cóctel: {product_name}',
                    },
                ],
                max_tokens=160,
                temperature=0.7,
            )
            history = response.choices[0].message.content.strip()
            return Response({'history': history})
        except Exception as e:
            logger.error(f'Error al sugerir historia: {e}')
            return Response(
                {'error': 'No se pudo generar la historia'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
