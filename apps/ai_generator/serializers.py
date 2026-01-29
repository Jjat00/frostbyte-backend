from rest_framework import serializers
from .models import AIImageGeneration, AIGenerationUsageQuota
from apps.products.models import Product
from PIL import Image
import io


class AIImageGenerationSerializer(serializers.ModelSerializer):
    """Serializer para crear y listar generaciones"""

    original_image = serializers.ImageField(write_only=True)
    reference_image = serializers.ImageField(write_only=True, required=False, allow_null=True)

    # Read-only fields
    original_image_url = serializers.SerializerMethodField()
    reference_image_url = serializers.SerializerMethodField()
    generated_image_url = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = AIImageGeneration
        fields = [
            'id',
            'original_image',
            'reference_image',
            'original_image_url',
            'reference_image_url',
            'generated_image_url',
            'user_prompt',
            'transparent_background',
            'full_prompt',
            'status',
            'error_message',
            'generation_time_seconds',
            'cost_usd',
            'product',
            'product_name',
            'metadata',
            'created_at',
            'completed_at',
        ]
        read_only_fields = [
            'id',
            'status',
            'error_message',
            'full_prompt',
            'generation_time_seconds',
            'cost_usd',
            'metadata',
            'created_at',
            'completed_at',
        ]

    def get_original_image_url(self, obj):
        if obj.original_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.original_image.url)
            return obj.original_image.url
        return None

    def get_reference_image_url(self, obj):
        if obj.reference_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.reference_image.url)
            return obj.reference_image.url
        return None

    def get_generated_image_url(self, obj):
        if obj.generated_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.generated_image.url)
            return obj.generated_image.url
        return None

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def validate_original_image(self, value):
        """Valida la imagen original"""
        # Tamaño máximo: 10MB
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("El tamaño de la imagen no puede exceder 10MB")

        # Validar que es una imagen válida
        try:
            img = Image.open(value)
            img.verify()
            value.seek(0)  # Reset file pointer
        except Exception:
            raise serializers.ValidationError("Archivo de imagen inválido o corrupto")

        # Validar dimensiones (solo máximo para evitar problemas de memoria)
        img = Image.open(value)
        width, height = img.size

        # Solo validar dimensiones máximas muy grandes (8K+)
        if width > 8192 or height > 8192:
            raise serializers.ValidationError("Las dimensiones de la imagen son demasiado grandes (máx 8192x8192)")

        # No validar dimensiones mínimas - el objetivo es mejorar imágenes de baja calidad
        value.seek(0)  # Reset file pointer
        return value

    def validate_reference_image(self, value):
        """Valida la imagen de referencia (si existe)"""
        if value is None:
            return value

        # Misma validación que original
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("El tamaño de la imagen de referencia no puede exceder 10MB")

        try:
            img = Image.open(value)
            img.verify()
            value.seek(0)
        except Exception:
            raise serializers.ValidationError("Archivo de imagen de referencia inválido")

        value.seek(0)
        return value

    def validate_user_prompt(self, value):
        """Valida el prompt del usuario"""
        if len(value) > 500:
            raise serializers.ValidationError("El prompt no puede exceder 500 caracteres")
        return value


class AIGenerationHistorySerializer(serializers.ModelSerializer):
    """Serializer simplificado para listado de historial"""

    original_image_url = serializers.SerializerMethodField()
    generated_image_url = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = AIImageGeneration
        fields = [
            'id',
            'original_image_url',
            'generated_image_url',
            'user_prompt',
            'transparent_background',
            'status',
            'generation_time_seconds',
            'cost_usd',
            'product',
            'product_name',
            'created_at',
            'completed_at',
        ]

    def get_original_image_url(self, obj):
        if obj.original_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.original_image.url)
            return obj.original_image.url
        return None

    def get_generated_image_url(self, obj):
        if obj.generated_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.generated_image.url)
            return obj.generated_image.url
        return None


class SaveToProductSerializer(serializers.Serializer):
    """Serializer para guardar imagen generada en un producto"""

    product_id = serializers.IntegerField()

    def validate_product_id(self, value):
        user = self.context['request'].user

        # Verificar que el producto existe y pertenece al usuario
        if not Product.objects.filter(id=value).exists():
            raise serializers.ValidationError("El producto no existe")

        return value


class AIQuotaSerializer(serializers.ModelSerializer):
    """Serializer para cuota de usuario"""

    remaining = serializers.SerializerMethodField()
    can_generate_more = serializers.SerializerMethodField()

    class Meta:
        model = AIGenerationUsageQuota
        fields = [
            'monthly_limit',
            'used_this_month',
            'remaining',
            'can_generate_more',
            'total_cost_usd',
            'last_reset',
        ]

    def get_remaining(self, obj):
        return max(0, obj.monthly_limit - obj.used_this_month)

    def get_can_generate_more(self, obj):
        return obj.can_generate()


class RegenerateSerializer(serializers.Serializer):
    """Serializer para regenerar una imagen con nuevos parámetros"""

    user_prompt = serializers.CharField(max_length=500, required=False, allow_blank=True)
    transparent_background = serializers.BooleanField(required=False)

    def validate_user_prompt(self, value):
        if value and len(value) > 500:
            raise serializers.ValidationError("El prompt no puede exceder 500 caracteres")
        return value
