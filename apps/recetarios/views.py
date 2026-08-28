from rest_framework import viewsets, filters, status
from rest_framework.response import Response

from apps.accounts.permissions import IsStaffMember
from .models import RecipeBook
from .serializers import (
    RecipeBookListSerializer,
    RecipeBookDetailSerializer,
    RecipeBookCreateUpdateSerializer,
)
from apps.search import PlainSearchFilter


class RecipeBookViewSet(viewsets.ModelViewSet):
    """
    ViewSet para recetarios (guías de preparación).

    list: Listar todos los recetarios (staff)
    retrieve: Obtener detalle con pasos, ingredientes e imágenes (staff)
    create: Crear nuevo recetario (staff)
    update: Actualizar recetario (staff)
    destroy: Eliminar recetario - soft delete (staff)
    """

    queryset = RecipeBook.objects.select_related(
        "category__business",
        "product__business",
        "product_variant__product__business",
    ).prefetch_related("steps", "ingredients", "images")
    permission_classes = [IsStaffMember]
    lookup_field = "slug"
    filter_backends = [PlainSearchFilter, filters.OrderingFilter]
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

        # Filtro por negocio: la receta pertenece al negocio de su producto
        # (o categoría, o variante). Una receta sin vínculos no matchea ningún
        # negocio y solo aparece en consolidado.
        business = self.request.query_params.get("business")
        if business:
            from django.db.models import Q

            queryset = queryset.filter(
                Q(product__business__slug=business)
                | Q(category__business__slug=business)
                | Q(product_variant__product__business__slug=business)
            ).distinct()

        return queryset

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
