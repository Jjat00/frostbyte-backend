from rest_framework import serializers
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
            "products_count",
        ]

    def get_products_count(self, obj):
        return obj.products.filter(is_active=True).count()


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
            "products",
        ]
