from rest_framework import serializers
from .models import AIImageGeneration


class AIImageGenerationSerializer(serializers.ModelSerializer):
    original_image_url = serializers.SerializerMethodField()
    reference_image_url = serializers.SerializerMethodField()
    generated_image_url = serializers.SerializerMethodField()

    class Meta:
        model = AIImageGeneration
        fields = [
            'id', 'original_image', 'reference_image',
            'original_image_url', 'reference_image_url', 'generated_image_url',
            'user_prompt', 'transparent_background',
            'status', 'error_message', 'product', 'created_at',
        ]
        read_only_fields = ['id', 'status', 'error_message', 'created_at']
        extra_kwargs = {
            'original_image': {'write_only': True},
            'reference_image': {'write_only': True, 'required': False},
        }

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


class SaveToProductSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()

    def validate_product_id(self, value):
        from apps.products.models import Product
        if not Product.objects.filter(id=value).exists():
            raise serializers.ValidationError("Producto no existe")
        return value
