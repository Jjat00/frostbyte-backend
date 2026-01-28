from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from apps.accounts.permissions import IsAdminOrReadOnly, IsAdminUser
from .services import R2UploadService
from .services.r2_upload import R2UploadError
from .models import Category, Product, ProductVariant
from .serializers import (
    CategorySerializer,
    CategoryDetailSerializer,
    CategoryCreateUpdateSerializer,
    ProductSerializer,
    ProductListSerializer,
    ProductCreateUpdateSerializer,
    ProductVariantSerializer,
    ProductVariantCreateUpdateSerializer,
)


class CategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet para categorías de productos.

    list: Listar todas las categorías
    retrieve: Obtener detalle de una categoría con sus productos
    create: Crear nueva categoría
    update: Actualizar categoría
    destroy: Eliminar categoría (soft delete)
    """

    queryset = Category.objects.all()
    permission_classes = [IsAdminOrReadOnly]
    lookup_field = "slug"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["display_order", "name"]
    ordering = ["display_order"]

    def get_queryset(self):
        queryset = super().get_queryset()
        # Filtrar por activos si se solicita
        active_only = self.request.query_params.get("active_only", "false")
        if active_only.lower() == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return CategoryDetailSerializer
        elif self.action in ["create", "update", "partial_update"]:
            return CategoryCreateUpdateSerializer
        return CategorySerializer

    def destroy(self, request, *args, **kwargs):
        """Soft delete: marcar como inactivo en lugar de eliminar"""
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet para productos.

    list: Listar todos los productos
    retrieve: Obtener detalle de un producto con sus variantes
    create: Crear nuevo producto
    update: Actualizar producto
    destroy: Eliminar producto (soft delete)
    """

    queryset = Product.objects.all().select_related(
        "category").prefetch_related("variants")
    permission_classes = [IsAdminOrReadOnly]
    lookup_field = "slug"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at", "category"]
    ordering = ["category__display_order", "name"]

    def get_serializer_class(self):
        if self.action == "list":
            return ProductListSerializer
        elif self.action in ["create", "update", "partial_update"]:
            return ProductCreateUpdateSerializer
        return ProductSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por activos si se solicita (por defecto en list)
        if self.action == "list":
            active_only = self.request.query_params.get("active_only", "true")
            if active_only.lower() == "true":
                queryset = queryset.filter(is_active=True)

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

    def destroy(self, request, *args, **kwargs):
        """Soft delete: marcar como inactivo en lugar de eliminar"""
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def variants(self, request, slug=None):
        """Obtener solo las variantes de un producto específico"""
        product = self.get_object()
        variants = product.variants.filter(is_active=True)
        serializer = ProductVariantSerializer(variants, many=True)
        return Response(serializer.data)


class ProductVariantViewSet(viewsets.ModelViewSet):
    """
    ViewSet para variantes de producto.

    list: Listar todas las variantes
    retrieve: Obtener detalle de una variante
    create: Crear nueva variante
    update: Actualizar variante
    destroy: Eliminar variante (soft delete)
    """

    queryset = ProductVariant.objects.all().select_related("product", "product__category")
    serializer_class = ProductVariantSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "sku", "product__name"]

    def get_queryset(self):
        queryset = super().get_queryset()
        # Filtrar por activos si se solicita
        active_only = self.request.query_params.get("active_only", "false")
        if active_only.lower() == "true":
            queryset = queryset.filter(is_active=True)

        # Filtrar por producto si se proporciona
        product_id = self.request.query_params.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)

        return queryset

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return ProductVariantCreateUpdateSerializer
        return ProductVariantSerializer

    def destroy(self, request, *args, **kwargs):
        """Soft delete: marcar como inactivo en lugar de eliminar"""
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ImageUploadView(APIView):
    """
    Vista para subir imágenes de productos a Cloudflare R2.

    POST: Subir una imagen y recibir la URL pública
    """

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAdminUser]

    def post(self, request):
        """
        Subir una imagen a R2.

        Expects:
            - image: archivo de imagen (multipart/form-data)

        Returns:
            - url: URL pública de la imagen subida
        """
        if 'image' not in request.FILES:
            return Response(
                {'error': 'No se proporcionó ninguna imagen'},
                status=status.HTTP_400_BAD_REQUEST
            )

        image_file = request.FILES['image']

        try:
            upload_service = R2UploadService()
            url = upload_service.upload(image_file)

            return Response(
                {'url': url},
                status=status.HTTP_201_CREATED
            )

        except R2UploadError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': 'Error interno al procesar la imagen'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
