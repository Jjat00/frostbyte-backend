"""
Serializer mínimo para generación de imágenes (uso interno).
"""
from rest_framework import serializers
from .models import AIImageGeneration
from PIL import Image


class AIImageGenerationSerializer(serializers.ModelSerializer):
    """Crear generación y devolver URLs de imágenes + estado."""

    original_image = serializers.ImageField(write_only=True)
    reference_image = serializers.ImageField(
        write_only=True, required=False, allow_null=True)

    original_image_url = serializers.SerializerMethodField()
    reference_image_url = serializers.SerializerMethodField()
    generated_image_url = serializers.SerializerMethodField()

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
            'status',
            'error_message',
            'created_at',
        ]
        read_only_fields = ['id', 'status', 'error_message', 'created_at']

    def get_original_image_url(self, obj):
        if obj.original_image:
            req = self.context.get('request')
            return req.build_absolute_uri(obj.original_image.url) if req else obj.original_image.url
        return None

    def get_reference_image_url(self, obj):
        if obj.reference_image:
            req = self.context.get('request')
            return req.build_absolute_uri(obj.reference_image.url) if req else obj.reference_image.url
        return None

    def get_generated_image_url(self, obj):
        if obj.generated_image:
            req = self.context.get('request')
            return req.build_absolute_uri(obj.generated_image.url) if req else obj.generated_image.url
        return None

    def validate_original_image(self, value):
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("Imagen máximo 10MB")
        try:
            img = Image.open(value)
            img.verify()
            value.seek(0)
        except Exception:
            raise serializers.ValidationError("Imagen inválida")
        value.seek(0)
        return value

    def validate_reference_image(self, value):
        if value is None:
            return value
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError(
                "Imagen de referencia máximo 10MB")
        try:
            img = Image.open(value)
            img.verify()
            value.seek(0)
        except Exception:
            raise serializers.ValidationError("Imagen de referencia inválida")
        value.seek(0)
        return value

    def validate_user_prompt(self, value):
        if value and len(value) > 500:
            raise serializers.ValidationError("Máximo 500 caracteres")
        return value or ""


class SaveToProductSerializer(serializers.Serializer):
    """Asignar imagen generada a un producto del menú/carta."""

    product_id = serializers.IntegerField(min_value=1)

    def validate_product_id(self, value):
        from apps.products.models import Product
        if not Product.objects.filter(id=value).exists():
            raise serializers.ValidationError("El producto no existe")
        return value
