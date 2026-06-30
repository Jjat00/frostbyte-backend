from rest_framework import serializers
from decimal import Decimal

from apps.products.models import ProductVariant
from .models import Order, OrderItem, Table


class TableSerializer(serializers.ModelSerializer):
    """Serializer para gestionar mesas y barras (CRUD)."""

    label = serializers.CharField(read_only=True)

    class Meta:
        model = Table
        fields = [
            "id",
            "table_number",
            "floor",
            "table_name",
            "label",
            "is_active",
            "visit_count",
        ]
        read_only_fields = ["id", "visit_count"]

    def validate_table_number(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "El número de mesa no puede ser negativo (0 = Barra).")
        return value

    def validate_floor(self, value):
        if value < 1:
            raise serializers.ValidationError("El piso debe ser 1 o mayor.")
        return value


class OrderItemSerializer(serializers.ModelSerializer):
    """Serializer para items de pedido"""

    product_name = serializers.CharField(
        source="product_variant.product.name", read_only=True
    )
    variant_name = serializers.CharField(
        source="product_variant.name", read_only=True
    )
    product_variant_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariant.objects.filter(is_active=True),
        source="product_variant",
        write_only=True,
    )
    payment_method_display = serializers.CharField(
        source="get_payment_method_display", read_only=True
    )
    business_name = serializers.CharField(source="business.name", read_only=True)
    business_slug = serializers.CharField(source="business.slug", read_only=True)
    business_color = serializers.CharField(source="business.color", read_only=True)
    prep_status_display = serializers.CharField(
        source="get_prep_status_display", read_only=True
    )

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product_variant",
            "product_variant_id",
            "product_name",
            "variant_name",
            "business",
            "business_name",
            "business_slug",
            "business_color",
            "prep_status",
            "prep_status_display",
            "quantity",
            "unit_price",
            "subtotal",
            "notes",
            "is_paid",
            "payment_method",
            "payment_method_display",
            "paid_at",
            "is_delivered",
            "delivered_at",
        ]
        read_only_fields = ["subtotal", "product_variant", "business",
                            "paid_at", "delivered_at"]


class AddItemToOrderSerializer(serializers.Serializer):
    """Serializer para añadir items a una orden existente"""

    product_variant_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariant.objects.filter(is_active=True),
    )
    quantity = serializers.IntegerField(min_value=1, default=1)
    notes = serializers.CharField(
        max_length=200, required=False, allow_blank=True)


class MarkItemPaidSerializer(serializers.Serializer):
    """Serializer para marcar un item como pagado"""

    payment_method = serializers.ChoiceField(
        choices=Order.PaymentMethod.choices,
        required=False,
        allow_blank=True
    )


class OrderItemCreateSerializer(serializers.Serializer):
    """Serializer para crear items al crear un pedido"""

    product_variant_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariant.objects.filter(is_active=True),
    )
    quantity = serializers.IntegerField(min_value=1, default=1)
    notes = serializers.CharField(
        max_length=200, required=False, allow_blank=True)


def build_table_label(order):
    """Etiqueta de mesa lista para mostrar, ej: 'Mesa 1 · Piso 3'."""
    if order.table_id and order.table:
        return order.table.label
    number = order.table_number
    if number is None:
        return None
    name = "Barra" if number == 0 else f"Mesa {number}"
    floor = order.table_floor
    return f"{name} · Piso {floor}" if floor else name


class OrderListSerializer(serializers.ModelSerializer):
    """Serializer para listar pedidos"""

    status_display = serializers.CharField(
        source="get_status_display", read_only=True)
    payment_method_display = serializers.CharField(
        source="get_payment_method_display", read_only=True
    )
    items_count = serializers.IntegerField(read_only=True)
    business_breakdown = serializers.ReadOnlyField()
    floor = serializers.IntegerField(source="table_floor", read_only=True)
    table_label = serializers.SerializerMethodField()

    def get_table_label(self, obj):
        return build_table_label(obj)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "access_code",
            "customer_name",
            "customer_phone",
            "status",
            "status_display",
            "is_paid",
            "payment_method",
            "payment_method_display",
            "total",
            "items_count",
            "business_breakdown",
            "table_number",
            "table_floor",
            "floor",
            "table_label",
            "created_at",
        ]


class OrderDetailSerializer(serializers.ModelSerializer):
    """Serializer para detalle de pedido"""

    status_display = serializers.CharField(
        source="get_status_display", read_only=True)
    payment_method_display = serializers.CharField(
        source="get_payment_method_display", read_only=True
    )
    items = OrderItemSerializer(many=True, read_only=True)
    items_count = serializers.IntegerField(read_only=True)
    paid_total = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True)
    pending_total = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True)
    paid_items_count = serializers.IntegerField(read_only=True)
    unpaid_items_count = serializers.IntegerField(read_only=True)
    delivered_items_count = serializers.IntegerField(read_only=True)
    undelivered_items_count = serializers.IntegerField(read_only=True)
    business_breakdown = serializers.ReadOnlyField()
    floor = serializers.IntegerField(source="table_floor", read_only=True)
    table_label = serializers.SerializerMethodField()

    def get_table_label(self, obj):
        return build_table_label(obj)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "access_code",
            "customer_name",
            "customer_phone",
            "customer_notes",
            "status",
            "status_display",
            "payment_method",
            "payment_method_display",
            "is_paid",
            "subtotal",
            "discount",
            "total",
            "paid_total",
            "pending_total",
            "paid_items_count",
            "unpaid_items_count",
            "delivered_items_count",
            "undelivered_items_count",
            "business_breakdown",
            "items",
            "items_count",
            "table",
            "table_number",
            "table_floor",
            "floor",
            "table_label",
            "created_at",
            "updated_at",
            "completed_at",
        ]

    def to_representation(self, instance):
        """Ordena los items alfabéticamente por nombre del producto"""
        representation = super().to_representation(instance)

        # Obtener los items ordenados alfabéticamente por nombre del producto
        items = instance.items.select_related('product_variant__product', 'business').order_by(
            'product_variant__product__name',
            'product_variant__name'
        )

        # Serializar los items ordenados
        representation['items'] = OrderItemSerializer(items, many=True).data

        return representation


class OrderCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear pedidos"""

    items = OrderItemCreateSerializer(many=True, write_only=True)
    table_id = serializers.PrimaryKeyRelatedField(
        queryset=Table.objects.filter(is_active=True),
        source="table",
        write_only=True,
        required=False,
        allow_null=True,
    )
    # Compatibilidad: clientes que aún envían solo el número de mesa.
    table_number = serializers.IntegerField(min_value=0, required=False)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "access_code",
            "customer_name",
            "customer_phone",
            "customer_notes",
            "payment_method",
            "discount",
            "total",
            "table_id",
            "table_number",
            "items",
        ]
        read_only_fields = ["id", "order_number", "access_code", "total"]

    def _resolve_table(self, attrs):
        """Determina la mesa a partir de table_id o (fallback) del número."""
        table = attrs.get("table")
        if table is None:
            number = attrs.get("table_number")
            if number is None:
                raise serializers.ValidationError(
                    {"table_id": "Debe seleccionar una mesa o barra."})
            table = (
                Table.objects.filter(table_number=number, is_active=True)
                .order_by("floor")
                .first()
            )
            if table is None:
                raise serializers.ValidationError(
                    {"table_number": "Mesa no encontrada o inactiva."})
        return table

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError(
                "El pedido debe tener al menos un item.")
        return value

    def validate(self, attrs):
        table = self._resolve_table(attrs)
        attrs["table"] = table
        attrs["table_number"] = table.table_number
        attrs["table_floor"] = table.floor
        return attrs

    def create(self, validated_data):
        items_data = validated_data.pop("items")

        # Crear el pedido
        order = Order.objects.create(**validated_data)

        # Crear los items individuales (cada uno con quantity=1) para permitir pagos separados
        for item_data in items_data:
            product_variant = item_data["product_variant_id"]
            quantity = item_data.get("quantity", 1)
            notes = item_data.get("notes", "")
            unit_price = product_variant.price or Decimal("0.00")

            # Si quantity > 1, crear múltiples items independientes
            for _ in range(quantity):
                OrderItem.objects.create(
                    order=order,
                    product_variant=product_variant,
                    quantity=1,  # Siempre 1 para que cada item sea independiente
                    unit_price=unit_price,
                    subtotal=unit_price,  # Subtotal de un solo item
                    notes=notes,
                )

        # Recalcular totales
        order.calculate_totals()
        order.save()

        return order


class OrderUpdateSerializer(serializers.ModelSerializer):
    """Serializer para actualizar pedidos"""

    table_id = serializers.PrimaryKeyRelatedField(
        queryset=Table.objects.filter(is_active=True),
        source="table",
        write_only=True,
        required=False,
        allow_null=True,
    )
    # Compatibilidad: aún se acepta el número suelto.
    table_number = serializers.IntegerField(min_value=0, required=False)

    class Meta:
        model = Order
        fields = [
            "customer_name",
            "customer_phone",
            "customer_notes",
            "payment_method",
            "is_paid",
            "discount",
            "table_id",
            "table_number",
        ]

    def update(self, instance, validated_data):
        table = validated_data.get("table")
        if table is None:
            number = validated_data.get("table_number")
            if number is not None:
                table = (
                    Table.objects.filter(table_number=number, is_active=True)
                    .order_by("floor")
                    .first()
                )
                if table is None:
                    raise serializers.ValidationError(
                        {"table_number": "Mesa no encontrada o inactiva."})
        if table is not None:
            validated_data["table"] = table
            validated_data["table_number"] = table.table_number
            validated_data["table_floor"] = table.floor
        return super().update(instance, validated_data)


class OrderStatusUpdateSerializer(serializers.Serializer):
    """Serializer para actualizar estado de pedido"""

    status = serializers.ChoiceField(choices=Order.Status.choices)


class PublicOrderItemSerializer(serializers.ModelSerializer):
    """Serializer público para items de pedido (sin info de pago)"""

    product_name = serializers.CharField(
        source="product_variant.product.name", read_only=True
    )
    variant_name = serializers.CharField(
        source="product_variant.name", read_only=True
    )
    product_image = serializers.URLField(
        source="product_variant.product.image_url", read_only=True
    )
    product_category = serializers.CharField(
        source="product_variant.product.category.name", read_only=True
    )

    class Meta:
        model = OrderItem
        fields = [
            "product_name",
            "variant_name",
            "product_image",
            "product_category",
            "quantity",
            "unit_price",
            "subtotal",
            "is_delivered",
        ]


class PublicOrderDetailSerializer(serializers.ModelSerializer):
    """Serializer público para detalle de pedido (sin info sensible)"""

    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )
    items = PublicOrderItemSerializer(many=True, read_only=True)
    items_count = serializers.IntegerField(read_only=True)
    delivered_items_count = serializers.IntegerField(read_only=True)
    undelivered_items_count = serializers.IntegerField(read_only=True)
    floor = serializers.IntegerField(source="table_floor", read_only=True)
    table_label = serializers.SerializerMethodField()

    def get_table_label(self, obj):
        return build_table_label(obj)

    class Meta:
        model = Order
        fields = [
            "order_number",
            "customer_name",
            "status",
            "status_display",
            "is_paid",
            "total",
            "discount",
            "items",
            "items_count",
            "delivered_items_count",
            "undelivered_items_count",
            "table_number",
            "table_floor",
            "floor",
            "table_label",
            "created_at",
        ]
