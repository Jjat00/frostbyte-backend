from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, F
from decimal import Decimal

from apps.products.models import ProductVariant
from .models import UnitOfMeasure, RawMaterial, Recipe
from .serializers import (
    UnitOfMeasureSerializer,
    RawMaterialSerializer,
    RawMaterialListSerializer,
    RecipeSerializer,
    RecipeDetailSerializer,
)


class UnitOfMeasureViewSet(viewsets.ModelViewSet):
    """ViewSet para unidades de medida"""

    queryset = UnitOfMeasure.objects.all()
    serializer_class = UnitOfMeasureSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "abbreviation"]


class RawMaterialViewSet(viewsets.ModelViewSet):
    """
    ViewSet para materia prima.

    list: Listar toda la materia prima
    retrieve: Detalle de materia prima
    create: Crear nueva materia prima
    update: Actualizar materia prima
    low_stock: Listar materia prima con stock bajo
    """

    queryset = RawMaterial.objects.filter(is_active=True).select_related("unit")
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "supplier"]
    ordering_fields = ["name", "current_stock", "cost_per_unit"]
    ordering = ["name"]

    def get_serializer_class(self):
        if self.action == "list":
            return RawMaterialListSerializer
        return RawMaterialSerializer

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        """Obtener materia prima con stock bajo o sin stock"""
        low_stock_items = RawMaterial.objects.filter(
            is_active=True,
            current_stock__lte=F("minimum_stock"),
        ).select_related("unit")

        serializer = RawMaterialListSerializer(low_stock_items, many=True)
        return Response(
            {
                "count": low_stock_items.count(),
                "results": serializer.data,
            }
        )

    @action(detail=True, methods=["post"])
    def adjust_stock(self, request, pk=None):
        """Ajustar stock de materia prima"""
        material = self.get_object()
        adjustment = request.data.get("adjustment", 0)

        try:
            adjustment = Decimal(str(adjustment))
        except (ValueError, TypeError):
            return Response(
                {"error": "Valor de ajuste inválido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        material.current_stock += adjustment
        if material.current_stock < 0:
            material.current_stock = Decimal("0")
        material.save()

        serializer = RawMaterialSerializer(material)
        return Response(serializer.data)


class RecipeViewSet(viewsets.ModelViewSet):
    """ViewSet para recetas/ingredientes"""

    queryset = Recipe.objects.select_related(
        "product_variant",
        "product_variant__product",
        "raw_material",
        "raw_material__unit",
    )
    serializer_class = RecipeSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = [
        "product_variant__product__name",
        "product_variant__name",
        "raw_material__name",
    ]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por producto
        product_slug = self.request.query_params.get("product")
        if product_slug:
            queryset = queryset.filter(product_variant__product__slug=product_slug)

        # Filtrar por variante
        variant_id = self.request.query_params.get("variant")
        if variant_id:
            queryset = queryset.filter(product_variant_id=variant_id)

        return queryset

    @action(detail=False, methods=["get"], url_path="by-variant/(?P<variant_id>[^/.]+)")
    def by_variant(self, request, variant_id=None):
        """Obtener receta completa de una variante de producto"""
        try:
            variant = ProductVariant.objects.select_related("product").get(id=variant_id)
        except ProductVariant.DoesNotExist:
            return Response(
                {"error": "Variante no encontrada"},
                status=status.HTTP_404_NOT_FOUND,
            )

        recipe_items = Recipe.objects.filter(product_variant=variant).select_related(
            "raw_material",
            "raw_material__unit",
        )

        # Calcular costo total de la receta
        total_cost = sum(item.cost for item in recipe_items)

        serializer = RecipeDetailSerializer(recipe_items, many=True)

        return Response(
            {
                "product": variant.product.name,
                "variant": variant.name,
                "sale_price": str(variant.price) if variant.price else None,
                "ingredients": serializer.data,
                "total_cost": str(total_cost),
                "profit": str(variant.price - total_cost) if variant.price else None,
                "profit_margin": (
                    str(((variant.price - total_cost) / variant.price * 100))
                    if variant.price and variant.price > 0
                    else None
                ),
            }
        )
