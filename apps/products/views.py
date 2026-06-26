from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import ValidationError as DRFValidationError

from django.db.models import Exists, OuterRef, Prefetch

from apps.accounts.permissions import IsAdminOrReadOnly, IsAdminUser
from apps.business.models import Business
from .services import R2UploadService
from .services.r2_upload import R2UploadError
from .models import (
    Category,
    ModifierGroup,
    ModifierOption,
    Product,
    ProductModifierGroup,
    ProductVariant,
)
from .serializers import (
    CategorySerializer,
    CategoryDetailSerializer,
    CategoryCreateUpdateSerializer,
    ModifierGroupSerializer,
    ModifierOptionSerializer,
    ProductModifierGroupSerializer,
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

    queryset = Category.objects.all().select_related("business")
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
        # Filtrar por negocio (Frostbyte / Frostbyte Food) si se solicita
        business_slug = self.request.query_params.get("business")
        if business_slug:
            queryset = queryset.filter(business__slug=business_slug)
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return CategoryDetailSerializer
        elif self.action in ["create", "update", "partial_update"]:
            return CategoryCreateUpdateSerializer
        return CategorySerializer

    def perform_create(self, serializer):
        # Compatibilidad: si no se especifica negocio, usar el principal.
        business = serializer.validated_data.get("business") or Business.get_main()
        if business is None:
            raise DRFValidationError(
                {"business": "No hay negocio principal configurado; especifica business."}
            )
        serializer.save(business=business)

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
        "category", "business").prefetch_related("variants").annotate(
        _is_configurable=Exists(
            ProductModifierGroup.objects.filter(product=OuterRef("pk"), is_active=True)
        ))
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

        # Filtrar por negocio (Frostbyte / Frostbyte Food) si se proporciona
        business_slug = self.request.query_params.get("business")
        if business_slug:
            queryset = queryset.filter(business__slug=business_slug)

        # Filtrar por categoría si se proporciona
        category_slug = self.request.query_params.get("category")
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)

        # Filtrar productos próximamente
        coming_soon = self.request.query_params.get("coming_soon")
        if coming_soon is not None:
            queryset = queryset.filter(
                is_coming_soon=coming_soon.lower() == "true")

        # En el detalle, precargar los grupos de opciones activos (con sus
        # opciones activas) en una sola pasada para evitar N+1 en el menú.
        if self.action == "retrieve":
            active_options = Prefetch(
                "group__options",
                queryset=ModifierOption.objects.filter(is_active=True).order_by(
                    "display_order", "name"),
            )
            active_links = Prefetch(
                "modifier_links",
                queryset=ProductModifierGroup.objects.filter(is_active=True)
                .select_related("group")
                .prefetch_related(active_options)
                .order_by("display_order"),
                to_attr="active_modifier_links",
            )
            queryset = queryset.prefetch_related(active_links)

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


class ModifierGroupViewSet(viewsets.ModelViewSet):
    """
    ViewSet para grupos de opciones (Carnes, Salsas, Bebida, etc.).

    Lectura pública (la usa el menú), escritura solo admin.
    Filtros: ?business=<slug>, ?active_only=true
    """

    queryset = ModifierGroup.objects.all().select_related(
        "business").prefetch_related("options")
    serializer_class = ModifierGroupSerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = None
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["name"]
    ordering = ["name"]

    def get_queryset(self):
        queryset = super().get_queryset()
        business_slug = self.request.query_params.get("business")
        if business_slug:
            queryset = queryset.filter(business__slug=business_slug)
        # Por defecto solo activos (lectura publica del menu); el admin puede
        # pedir todos con ?active_only=false.
        active_only = self.request.query_params.get("active_only", "true")
        if active_only.lower() == "true":
            queryset = queryset.filter(is_active=True)
        return queryset


class ModifierOptionViewSet(viewsets.ModelViewSet):
    """
    ViewSet para opciones individuales de un grupo (Pollo, Mayonesa, etc.).

    Filtros: ?group=<id>, ?business=<slug>, ?active_only=false
    """

    queryset = ModifierOption.objects.all().select_related("group", "group__business")
    serializer_class = ModifierOptionSerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = None
    filter_backends = [filters.SearchFilter]
    search_fields = ["name"]

    def get_queryset(self):
        queryset = super().get_queryset()
        group_id = self.request.query_params.get("group")
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        business_slug = self.request.query_params.get("business")
        if business_slug:
            queryset = queryset.filter(group__business__slug=business_slug)
        # Por defecto solo activas (lectura publica); admin pide todas con active_only=false.
        active_only = self.request.query_params.get("active_only", "true")
        if active_only.lower() == "true":
            queryset = queryset.filter(is_active=True)
        return queryset


class ProductModifierGroupViewSet(viewsets.ModelViewSet):
    """
    ViewSet para asociar grupos de opciones a productos (con overrides).

    Filtros: ?product=<slug>, ?product_id=<id>
    """

    queryset = ProductModifierGroup.objects.all().select_related("product", "group")
    serializer_class = ProductModifierGroupSerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = None

    def get_queryset(self):
        queryset = super().get_queryset()
        product_slug = self.request.query_params.get("product")
        if product_slug:
            queryset = queryset.filter(product__slug=product_slug)
        product_id = self.request.query_params.get("product_id")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        return queryset


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
