from rest_framework import serializers
from .models import AIImageGeneration


class AIImageGenerationSerializer(serializers.ModelSerializer):
    """Serializer para lectura de generaciones"""
    original_image_url = serializers.SerializerMethodField()
    reference_image_url = serializers.SerializerMethodField()
    generated_image_url = serializers.SerializerMethodField()

    class Meta:
        model = AIImageGeneration
        fields = [
            'id',
            'original_image_url', 'reference_image_url', 'generated_image_url',
            'user_prompt', 'transparent_background', 'ai_model',
            'status', 'error_message', 'product',
            'is_saved_to_r2', 'created_at', 'saved_at',
        ]
        read_only_fields = [
            'id', 'status', 'error_message', 'created_at',
            'is_saved_to_r2', 'saved_at',
        ]

    def get_original_image_url(self, obj):
        url = obj.get_image_url('original')
        if url and not url.startswith('http'):
            req = self.context.get('request')
            return req.build_absolute_uri(url) if req else url
        return url

    def get_reference_image_url(self, obj):
        url = obj.get_image_url('reference')
        if url and not url.startswith('http'):
            req = self.context.get('request')
            return req.build_absolute_uri(url) if req else url
        return url

    def get_generated_image_url(self, obj):
        url = obj.get_image_url('generated')
        if url and not url.startswith('http'):
            req = self.context.get('request')
            return req.build_absolute_uri(url) if req else url
        return url


class AIImageGenerationCreateSerializer(serializers.Serializer):
    """Serializer para creacion de generaciones"""
    original_image = serializers.ImageField(write_only=True)
    reference_image = serializers.ImageField(write_only=True, required=False)
    user_prompt = serializers.CharField(required=False, allow_blank=True, default='')
    transparent_background = serializers.BooleanField(default=True)
    ai_model = serializers.ChoiceField(
        choices=[
            ('gemini-3-pro-image-preview', 'Gemini 3 Pro Image'),
            ('gemini-3.1-flash-image-preview', 'Gemini 3.1 Flash Image'),
            ('gpt-image-1.5', 'GPT Image 1.5'),
            ('gpt-image-2', 'GPT Image 2'),
        ],
        default='gemini-3-pro-image-preview',
        required=False,
    )


class SuggestDescriptionSerializer(serializers.Serializer):
    """Serializer para sugerencia de descripción con IA"""
    product_name = serializers.CharField(max_length=255)


class SuggestHistorySerializer(serializers.Serializer):
    """Serializer para sugerencia de historia de cóctel con IA"""
    product_name = serializers.CharField(max_length=255)


class SaveToProductSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()

    def validate_product_id(self, value):
        from apps.products.models import Product
        if not Product.objects.filter(id=value).exists():
            raise serializers.ValidationError("Producto no existe")
        return value
