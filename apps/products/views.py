from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Category, Product, ProductVariant
from .serializers import (
    CategorySerializer,
    CategoryDetailSerializer,
    ProductSerializer,
    ProductListSerializer,
    ProductVariantSerializer,
)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para categorías de productos.

    list: Listar todas las categorías activas
    retrieve: Obtener detalle de una categoría con sus productos
    """

    queryset = Category.objects.filter(is_active=True)
    lookup_field = "slug"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["display_order", "name"]
    ordering = ["display_order"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return CategoryDetailSerializer
        return CategorySerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para productos.

    list: Listar todos los productos activos
    retrieve: Obtener detalle de un producto con sus variantes
    """

    queryset = Product.objects.filter(is_active=True).select_related(
        "category").prefetch_related("variants")
    lookup_field = "slug"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at", "category"]
    ordering = ["category__display_order", "name"]

    def get_serializer_class(self):
        if self.action == "list":
            return ProductListSerializer
        return ProductSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por categoría si se proporciona
        category_slug = self.request.query_params.get("category")
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)

        # Filtrar productos próximamente
        coming_soon = self.request.query_params.get("coming_soon")
        if coming_soon is not None:
            queryset = queryset.filter(
                is_coming_soon=coming_soon.lower() == "true")

        return queryset

    @action(detail=True, methods=["get"])
    def variants(self, request, slug=None):
        """Obtener solo las variantes de un producto específico"""
        product = self.get_object()
        variants = product.variants.filter(is_active=True)
        serializer = ProductVariantSerializer(variants, many=True)
        return Response(serializer.data)


class ProductVariantViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet para variantes de producto.
    """

    queryset = ProductVariant.objects.filter(
        is_active=True).select_related("product", "product__category")
    serializer_class = ProductVariantSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "sku", "product__name"]
