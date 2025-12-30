from rest_framework import serializers
from decimal import Decimal

from apps.products.models import ProductVariant
from .models import Order, OrderItem


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

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product_variant",
            "product_variant_id",
            "product_name",
            "variant_name",
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
        read_only_fields = ["subtotal",
                            "product_variant", "paid_at", "delivered_at"]


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


class OrderListSerializer(serializers.ModelSerializer):
    """Serializer para listar pedidos"""

    status_display = serializers.CharField(
        source="get_status_display", read_only=True)
    payment_method_display = serializers.CharField(
        source="get_payment_method_display", read_only=True
    )
    items_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer_name",
            "customer_phone",
            "status",
            "status_display",
            "is_paid",
            "payment_method",
            "payment_method_display",
            "total",
            "items_count",
            "table_number",
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

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
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
            "items",
            "items_count",
            "table_number",
            "created_at",
            "updated_at",
            "completed_at",
        ]

    def to_representation(self, instance):
        """Ordena los items alfabéticamente por nombre del producto"""
        representation = super().to_representation(instance)

        # Obtener los items ordenados alfabéticamente por nombre del producto
        items = instance.items.select_related('product_variant__product').order_by(
            'product_variant__product__name',
            'product_variant__name'
        )

        # Serializar los items ordenados
        representation['items'] = OrderItemSerializer(items, many=True).data

        return representation


class OrderCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear pedidos"""

    items = OrderItemCreateSerializer(many=True, write_only=True)
    table_number = serializers.IntegerField(min_value=0, max_value=5, required=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer_name",
            "customer_phone",
            "customer_notes",
            "payment_method",
            "discount",
            "total",
            "table_number",
            "items",
        ]
        read_only_fields = ["id", "order_number", "total"]

    def validate_table_number(self, value):
        if value is None:
            raise serializers.ValidationError(
                "Debe seleccionar una mesa o barra.")
        if value < 0 or value > 5:
            raise serializers.ValidationError(
                "El valor debe estar entre 0 (Barra) y 5.")
        return value

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError(
                "El pedido debe tener al menos un item.")
        return value

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

    table_number = serializers.IntegerField(min_value=0, max_value=5, required=False)

    class Meta:
        model = Order
        fields = [
            "customer_name",
            "customer_phone",
            "customer_notes",
            "payment_method",
            "is_paid",
            "discount",
            "table_number",
        ]

    def validate_table_number(self, value):
        if value is not None:
            if value < 0 or value > 5:
                raise serializers.ValidationError(
                    "El valor debe estar entre 0 (Barra) y 5.")
        return value


class OrderStatusUpdateSerializer(serializers.Serializer):
    """Serializer para actualizar estado de pedido"""

    status = serializers.ChoiceField(choices=Order.Status.choices)
