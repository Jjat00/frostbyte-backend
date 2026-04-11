from rest_framework import viewsets, filters, status
from rest_framework.response import Response

from apps.accounts.permissions import IsEmployeeOrAdminWithAdminWrite
from .models import RecipeCategory, RecipeBook
from .serializers import (
    RecipeCategorySerializer,
    RecipeCategoryCreateUpdateSerializer,
    RecipeBookListSerializer,
    RecipeBookDetailSerializer,
    RecipeBookCreateUpdateSerializer,
)


class RecipeCategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet para categorías de recetarios.

    list: Listar todas las categorías
    retrieve: Obtener detalle de una categoría
    create: Crear nueva categoría (admin)
    update: Actualizar categoría (admin)
    destroy: Eliminar categoría - soft delete (admin)
    """

    queryset = RecipeCategory.objects.all()
    permission_classes = [IsEmployeeOrAdminWithAdminWrite]
    lookup_field = "slug"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["display_order", "name"]
    ordering = ["display_order"]

    def get_queryset(self):
        queryset = super().get_queryset()
        active_only = self.request.query_params.get("active_only", "false")
        if active_only.lower() == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return RecipeCategoryCreateUpdateSerializer
        return RecipeCategorySerializer

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecipeBookViewSet(viewsets.ModelViewSet):
    """
    ViewSet para recetarios (guías de preparación).

    list: Listar todos los recetarios
    retrieve: Obtener detalle con pasos, ingredientes e imágenes
    create: Crear nuevo recetario (admin)
    update: Actualizar recetario (admin)
    destroy: Eliminar recetario - soft delete (admin)
    """

    queryset = RecipeBook.objects.select_related(
        "category", "product", "product_variant"
    ).prefetch_related("steps", "ingredients", "images")
    permission_classes = [IsEmployeeOrAdminWithAdminWrite]
    lookup_field = "slug"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description", "ingredients__name"]
    ordering_fields = ["name", "created_at", "difficulty"]
    ordering = ["category__display_order", "name"]

    def get_serializer_class(self):
        if self.action == "list":
            return RecipeBookListSerializer
        elif self.action in ["create", "update", "partial_update"]:
            return RecipeBookCreateUpdateSerializer
        return RecipeBookDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        active_only = self.request.query_params.get("active_only", "false")
        if active_only.lower() == "true":
            queryset = queryset.filter(is_active=True)

        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category__slug=category)

        difficulty = self.request.query_params.get("difficulty")
        if difficulty:
            queryset = queryset.filter(difficulty=difficulty)

        product = self.request.query_params.get("product")
        if product:
            queryset = queryset.filter(product_id=product)

        return queryset

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
