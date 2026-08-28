from rest_framework import viewsets, filters, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.db.models import Sum, F, Count, Prefetch
from django.utils import timezone
from decimal import Decimal

from apps.products.models import ProductVariant
from apps.accounts.permissions import IsAdminUser
from apps.business.models import Business
from .models import UnitOfMeasure, RawMaterial, Recipe, PurchaseOrder, PurchaseOrderItem
from apps.search import PlainSearchFilter


def apply_business_filter(queryset, request, lookup="business__slug"):
    """Filtra un queryset por ?business=<slug> si viene en la peticion."""
    business_slug = request.query_params.get("business")
    if business_slug:
        queryset = queryset.filter(**{lookup: business_slug})
    return queryset
from .serializers import (
    UnitOfMeasureSerializer,
    RawMaterialSerializer,
    RawMaterialListSerializer,
    RecipeSerializer,
    RecipeDetailSerializer,
    PurchaseOrderSerializer,
    PurchaseOrderCreateSerializer,
    PurchaseOrderItemSerializer,
    PurchaseOrderItemUpdateSerializer,
)


class UnitOfMeasureViewSet(viewsets.ModelViewSet):
    """ViewSet para unidades de medida"""

    queryset = UnitOfMeasure.objects.all()
    serializer_class = UnitOfMeasureSerializer
    permission_classes = [IsAdminUser]
    pagination_class = None  # Deshabilitamos paginación para obtener todas las unidades
    filter_backends = [PlainSearchFilter]
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

    queryset = RawMaterial.objects.filter(
        is_active=True).select_related("unit", "business")
    permission_classes = [IsAdminUser]
    pagination_class = None  # Deshabilitamos paginación para obtener todos los materiales
    filter_backends = [PlainSearchFilter, filters.OrderingFilter]
    search_fields = ["name", "supplier"]
    ordering_fields = ["name", "current_stock",
                       "cost_per_unit", "minimum_stock"]
    ordering = ["name"]

    def get_serializer_class(self):
        if self.action == "list":
            return RawMaterialListSerializer
        return RawMaterialSerializer

    def get_queryset(self):
        return apply_business_filter(super().get_queryset(), self.request)

    def perform_create(self, serializer):
        # Compatibilidad: si no se especifica negocio, usar el principal.
        business = serializer.validated_data.get("business") or Business.get_main()
        if business is None:
            from rest_framework.exceptions import ValidationError as DRFValidationError
            raise DRFValidationError(
                {"business": "No hay negocio principal configurado; especifica business."}
            )
        serializer.save(business=business)

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        """Obtener materia prima con stock bajo o sin stock"""
        low_stock_items = apply_business_filter(
            RawMaterial.objects.filter(
                is_active=True,
                current_stock__lt=F("minimum_stock"),
            ).select_related("unit").order_by("current_stock"),
            request,
        )

        serializer = RawMaterialListSerializer(low_stock_items, many=True)

        # Obtener materiales que tienen órdenes de compra pendientes
        pending_order_material_ids = set(
            PurchaseOrderItem.objects.filter(
                purchase_order__status=PurchaseOrder.Status.PENDING,
                raw_material__in=low_stock_items
            ).values_list("raw_material_id", flat=True)
        )

        # Agregar info de órdenes pendientes a cada material
        results = serializer.data
        for item in results:
            item["has_pending_order"] = item["id"] in pending_order_material_ids

        # Calcular total estimado para reabastecer (lo que falta para llegar al mínimo)
        total_estimated = sum(
            max(0, item.minimum_stock - item.current_stock) * item.cost_per_unit
            for item in low_stock_items
        )

        return Response(
            {
                "count": low_stock_items.count(),
                "estimated_restock_cost": str(total_estimated),
                "results": results,
            }
        )

    @action(detail=False, methods=["get"])
    def all_materials(self, request):
        """Obtener todos los materiales incluyendo inactivos (para admin)"""
        materials = apply_business_filter(
            RawMaterial.objects.all().select_related("unit", "business").order_by("name"),
            request,
        )
        serializer = RawMaterialListSerializer(materials, many=True)
        return Response(serializer.data)

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

    @action(detail=True, methods=["patch"])
    def update_price(self, request, pk=None):
        """Actualizar precio de materia prima"""
        material = self.get_object()
        new_price = request.data.get("cost_per_unit")

        if new_price is None:
            return Response(
                {"error": "Se requiere cost_per_unit"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            material.cost_per_unit = Decimal(str(new_price))
            material.save()
        except (ValueError, TypeError):
            return Response(
                {"error": "Precio inválido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = RawMaterialSerializer(material)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """Estadísticas del inventario"""
        base = apply_business_filter(
            RawMaterial.objects.filter(is_active=True), request
        )
        total_materials = base.count()
        low_stock = base.filter(
            current_stock__lt=F("minimum_stock"),
        ).count()
        zero_stock = base.filter(
            current_stock__lte=0,
        ).count()

        # Valor total del inventario
        total_value = sum(m.current_stock * m.cost_per_unit for m in base)

        return Response({
            "total_materials": total_materials,
            "low_stock_count": low_stock,
            "zero_stock_count": zero_stock,
            "total_inventory_value": str(total_value),
        })


class RecipeViewSet(viewsets.ModelViewSet):
    """ViewSet para recetas/ingredientes"""

    queryset = Recipe.objects.select_related(
        "product_variant",
        "product_variant__product",
        "raw_material",
        "raw_material__unit",
    )
    serializer_class = RecipeSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [PlainSearchFilter]
    search_fields = [
        "product_variant__product__name",
        "product_variant__name",
        "raw_material__name",
    ]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por negocio (recipe hereda el negocio via product_variant->product)
        queryset = apply_business_filter(
            queryset, self.request, lookup="product_variant__product__business__slug"
        )

        # Filtrar por producto
        product_slug = self.request.query_params.get("product")
        if product_slug:
            queryset = queryset.filter(
                product_variant__product__slug=product_slug)

        # Filtrar por variante
        variant_id = self.request.query_params.get("variant")
        if variant_id:
            queryset = queryset.filter(product_variant_id=variant_id)

        return queryset

    @action(detail=False, methods=["get"], url_path="by-variant/(?P<variant_id>[^/.]+)")
    def by_variant(self, request, variant_id=None):
        """Obtener receta completa de una variante de producto"""
        try:
            variant = ProductVariant.objects.select_related(
                "product").get(id=variant_id)
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


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """ViewSet para órdenes de compra"""

    queryset = PurchaseOrder.objects.prefetch_related(
        Prefetch(
            "items",
            queryset=PurchaseOrderItem.objects.select_related(
                "raw_material", "raw_material__unit"
            )
        )
    ).select_related("created_by", "business").annotate(
        _items_count=Count("items")
    )
    permission_classes = [IsAdminUser]
    pagination_class = None  # Sin paginación, filtrado por mes
    filter_backends = [PlainSearchFilter, filters.OrderingFilter]
    search_fields = ["order_number", "notes"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return PurchaseOrderCreateSerializer
        return PurchaseOrderSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por negocio (Frostbyte / Frostbyte Food)
        queryset = apply_business_filter(queryset, self.request)

        # Filtrar por estado
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Solo aplicar filtro de mes en la acción 'list'
        # Para otras acciones (retrieve, update, destroy, etc.) no filtrar por mes
        if self.action == 'list':
            # Filtrar por mes y año (basado en created_at)
            month = self.request.query_params.get("month")
            year = self.request.query_params.get("year")

            if month and year:
                try:
                    month = int(month)
                    year = int(year)
                    queryset = queryset.filter(
                        created_at__year=year,
                        created_at__month=month
                    )
                except (ValueError, TypeError):
                    pass
            else:
                # Por defecto, filtrar por el mes actual
                now = timezone.localtime()
                queryset = queryset.filter(
                    created_at__year=now.year,
                    created_at__month=now.month
                )

        return queryset

    def destroy(self, request, *args, **kwargs):
        """Eliminar orden y revertir stock de items comprados"""
        order = self.get_object()

        # Revertir el stock de todos los items comprados antes de eliminar
        for item in order.items.filter(is_purchased=True):
            if item.quantity_purchased:
                item.raw_material.current_stock -= item.quantity_purchased
                if item.raw_material.current_stock < 0:
                    item.raw_material.current_stock = Decimal("0")
                item.raw_material.save()

        # Eliminar la orden (esto también elimina los items por CASCADE)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"])
    def generate_from_low_stock(self, request):
        """Generar orden de compra automáticamente desde items con stock bajo"""
        # Negocio objetivo: ?business=<slug> o body, default al principal.
        business_slug = request.query_params.get("business") or request.data.get("business")
        if business_slug:
            business = Business.objects.filter(slug=business_slug).first()
            if business is None:
                return Response(
                    {"error": f'Negocio "{business_slug}" no encontrado.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            business = Business.get_main()
        if business is None:
            return Response(
                {"error": "No hay negocio principal configurado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        low_stock_items = RawMaterial.objects.filter(
            is_active=True,
            current_stock__lt=F("minimum_stock"),
            business=business,
        )

        if not low_stock_items.exists():
            return Response(
                {"message": "No hay items con stock bajo"},
                status=status.HTTP_200_OK,
            )

        # Crear la orden
        order = PurchaseOrder.objects.create(
            created_by=request.user,
            business=business,
            notes="Orden generada automáticamente desde stock bajo",
        )

        # Agregar items
        for material in low_stock_items:
            # Calcular cantidad a comprar (llevar al doble del mínimo)
            quantity_needed = (material.minimum_stock * 2) - \
                material.current_stock
            if quantity_needed <= 0:
                quantity_needed = material.minimum_stock

            PurchaseOrderItem.objects.create(
                purchase_order=order,
                raw_material=material,
                quantity_needed=quantity_needed,
                estimated_unit_price=material.cost_per_unit,
                supplier=material.supplier,
            )

        order.calculate_totals()

        serializer = PurchaseOrderSerializer(order)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def mark_purchased(self, request, pk=None):
        """Marcar la orden como comprada y actualizar stock de todos los items"""
        order = self.get_object()

        if order.status == PurchaseOrder.Status.PURCHASED:
            return Response(
                {"error": "Esta orden ya fue marcada como comprada"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Marcar todos los items como comprados
        for item in order.items.all():
            if not item.is_purchased:
                # Usar cantidad y precio estimados si no hay valores reales
                quantity = item.quantity_purchased or item.quantity_needed
                price = item.actual_unit_price or item.estimated_unit_price
                supplier = item.supplier or item.raw_material.supplier or ""

                item.mark_as_purchased(
                    quantity=quantity,
                    price=price,
                    supplier=supplier
                )

        order.status = PurchaseOrder.Status.PURCHASED
        order.purchased_at = timezone.now()
        order.save()
        order.calculate_totals()

        serializer = PurchaseOrderSerializer(order)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancelar orden de compra"""
        order = self.get_object()

        if order.status == PurchaseOrder.Status.PURCHASED:
            return Response(
                {"error": "No se puede cancelar una orden ya comprada"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = PurchaseOrder.Status.CANCELLED
        order.save()

        return Response({"message": "Orden cancelada"})

    @action(detail=True, methods=["post"])
    def revert_to_pending(self, request, pk=None):
        """Revertir orden a pendiente y desmarcar todos los items como comprados"""
        order = self.get_object()

        if order.status != PurchaseOrder.Status.PURCHASED:
            return Response(
                {"error": "Solo se pueden revertir órdenes completadas"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Revertir cada item comprado
        for item in order.items.filter(is_purchased=True):
            # Revertir el stock
            if item.quantity_purchased:
                item.raw_material.current_stock -= item.quantity_purchased
                if item.raw_material.current_stock < 0:
                    item.raw_material.current_stock = Decimal("0")
                item.raw_material.save()

            # Marcar item como no comprado (mantener valores para referencia)
            item.is_purchased = False
            item.save()

        # Cambiar estado de la orden
        order.status = PurchaseOrder.Status.PENDING
        order.purchased_at = None
        order.save()
        order.calculate_totals()

        serializer = PurchaseOrderSerializer(order)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="items/(?P<item_id>[^/.]+)/purchase")
    def purchase_item(self, request, pk=None, item_id=None):
        """Marcar un item individual como comprado y actualizar stock"""
        order = self.get_object()

        try:
            item = order.items.get(id=item_id)
        except PurchaseOrderItem.DoesNotExist:
            return Response(
                {"error": "Item no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PurchaseOrderItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        item.mark_as_purchased(
            quantity=serializer.validated_data["quantity_purchased"],
            price=serializer.validated_data["actual_unit_price"],
            supplier=serializer.validated_data.get("supplier", ""),
        )

        return Response(PurchaseOrderItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def add_item(self, request, pk=None):
        """Agregar un item a la orden"""
        order = self.get_object()

        if order.status != PurchaseOrder.Status.PENDING:
            return Response(
                {"error": "Solo se pueden agregar items a órdenes pendientes"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_material_id = request.data.get("raw_material")
        quantity_needed = request.data.get("quantity_needed")
        estimated_price = request.data.get("estimated_unit_price")

        try:
            material = RawMaterial.objects.get(id=raw_material_id)
        except RawMaterial.DoesNotExist:
            return Response(
                {"error": "Material no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        item = PurchaseOrderItem.objects.create(
            purchase_order=order,
            raw_material=material,
            quantity_needed=Decimal(str(quantity_needed)),
            estimated_unit_price=Decimal(
                str(estimated_price or material.cost_per_unit)),
            supplier=material.supplier,
        )

        order.calculate_totals()

        return Response(PurchaseOrderItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="items/(?P<item_id>[^/.]+)/update")
    def update_item(self, request, pk=None, item_id=None):
        """Actualizar un item de la orden (permite editar incluso si la orden está completada)"""
        order = self.get_object()

        try:
            item = order.items.get(id=item_id)
        except PurchaseOrderItem.DoesNotExist:
            return Response(
                {"error": "Item no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Si el item ya fue comprado, necesitamos revertir el stock antes de actualizar
        old_quantity_purchased = item.quantity_purchased if item.is_purchased else None
        old_actual_price = item.actual_unit_price if item.is_purchased else None

        # Actualizar campos del item
        if "quantity_needed" in request.data:
            item.quantity_needed = Decimal(
                str(request.data["quantity_needed"]))

        if "estimated_unit_price" in request.data:
            item.estimated_unit_price = Decimal(
                str(request.data["estimated_unit_price"]))

        if "quantity_purchased" in request.data:
            new_qty = Decimal(str(request.data["quantity_purchased"]))
            # Solo ajustar stock si el item ya está comprado
            if item.is_purchased and old_quantity_purchased is not None:
                # Revertir el stock anterior y agregar el nuevo
                item.raw_material.current_stock -= old_quantity_purchased
                item.raw_material.current_stock += new_qty
                if item.raw_material.current_stock < 0:
                    item.raw_material.current_stock = Decimal("0")
                item.raw_material.save()
            # Ya no marcamos como comprado automáticamente al establecer cantidad
            item.quantity_purchased = new_qty

        if "actual_unit_price" in request.data:
            new_price = Decimal(str(request.data["actual_unit_price"]))
            item.actual_unit_price = new_price
            # Solo actualizar precio del material si el item ya está comprado
            if item.is_purchased:
                item.raw_material.cost_per_unit = new_price
                item.raw_material.save()

        if "supplier" in request.data:
            item.supplier = request.data["supplier"]
            if item.is_purchased:
                item.raw_material.supplier = request.data["supplier"]
                item.raw_material.save()

        item.save()
        order.calculate_totals()

        return Response(PurchaseOrderItemSerializer(item).data)

    @action(detail=True, methods=["delete"], url_path="items/(?P<item_id>[^/.]+)")
    def remove_item(self, request, pk=None, item_id=None):
        """Eliminar un item de la orden (permite eliminar incluso si la orden está completada)"""
        order = self.get_object()

        try:
            item = order.items.get(id=item_id)

            # Si el item ya fue comprado, revertir el stock
            if item.is_purchased and item.quantity_purchased:
                item.raw_material.current_stock -= item.quantity_purchased
                if item.raw_material.current_stock < 0:
                    item.raw_material.current_stock = Decimal("0")
                item.raw_material.save()

            item.delete()

            # Refrescar la orden para limpiar el cache de prefetch
            order.refresh_from_db()
            order.calculate_totals()
        except PurchaseOrderItem.DoesNotExist:
            return Response(
                {"error": "Item no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({"message": "Item eliminado"})
