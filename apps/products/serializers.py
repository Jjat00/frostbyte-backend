from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from .models import Category, Product, ProductVariant


class ProductVariantSerializer(serializers.ModelSerializer):
    """Serializer para variantes de producto"""

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "name",
            "sku",
            "price",
            "is_default",
            "is_active",
        ]
        read_only_fields = ["sku"]


class ProductVariantCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer para crear/actualizar variantes de producto"""

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "product",
            "name",
            "sku",
            "price",
            "is_default",
            "is_active",
        ]
        read_only_fields = ["sku"]

    def validate(self, data):
        """Validar que solo haya una variante por defecto por producto"""
        is_default = data.get('is_default', False)
        product = data.get('product')
        
        if is_default and product:
            # Si estamos actualizando, excluir el objeto actual
            instance = self.instance
            existing_default = ProductVariant.objects.filter(
                product=product,
                is_default=True
            )
            if instance:
                existing_default = existing_default.exclude(id=instance.id)
            
            if existing_default.exists():
                raise ValidationError(
                    "Ya existe una variante por defecto para este producto. "
                    "Solo puede haber una variante por defecto por producto."
                )
        
        return data


class ProductSerializer(serializers.ModelSerializer):
    """Serializer para productos con sus variantes"""

    variants = ProductVariantSerializer(many=True, read_only=True)
    category_name = serializers.CharField(
        source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "image_url",
            "is_active",
            "is_coming_soon",
            "category",
            "category_name",
            "variants",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["slug", "created_at", "updated_at"]


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer para crear/actualizar productos"""

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "image_url",
            "is_active",
            "is_coming_soon",
            "category",
        ]
        read_only_fields = ["slug"]


class ProductListSerializer(serializers.ModelSerializer):
    """Serializer para listado de productos con variantes"""

    category_name = serializers.CharField(
        source="category.name", read_only=True)
    category_slug = serializers.CharField(
        source="category.slug", read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    variants_count = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "image_url",
            "is_active",
            "is_coming_soon",
            "category",
            "category_name",
            "category_slug",
            "variants",
            "variants_count",
        ]

    def get_variants_count(self, obj):
        return obj.variants.filter(is_active=True).count()


class CategorySerializer(serializers.ModelSerializer):
    """Serializer para categorías"""

    products_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "display_order",
            "is_active",
            "show_extras",
            "products_count",
        ]
        read_only_fields = ["slug", "products_count"]

    def get_products_count(self, obj):
        return obj.products.filter(is_active=True).count()


class CategoryCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer para crear/actualizar categorías"""

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "display_order",
            "is_active",
            "show_extras",
        ]
        read_only_fields = ["slug"]


class CategoryDetailSerializer(serializers.ModelSerializer):
    """Serializer para detalle de categoría con sus productos"""

    products = ProductListSerializer(many=True, read_only=True)

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "display_order",
            "is_active",
            "show_extras",
            "products",
        ]
