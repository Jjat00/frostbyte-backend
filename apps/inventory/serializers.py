from rest_framework import serializers
from .models import UnitOfMeasure, RawMaterial, Recipe, PurchaseOrder, PurchaseOrderItem


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    """Serializer para unidades de medida"""

    class Meta:
        model = UnitOfMeasure
        fields = ["id", "name", "abbreviation"]


class RawMaterialSerializer(serializers.ModelSerializer):
    """Serializer para materia prima"""

    unit = UnitOfMeasureSerializer(read_only=True)
    unit_id = serializers.PrimaryKeyRelatedField(
        queryset=UnitOfMeasure.objects.all(),
        source="unit",
        write_only=True,
    )
    stock_status = serializers.CharField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = RawMaterial
        fields = [
            "id",
            "name",
            "unit",
            "unit_id",
            "current_stock",
            "minimum_stock",
            "cost_per_unit",
            "supplier",
            "is_active",
            "stock_status",
            "is_low_stock",
            "created_at",
            "updated_at",
        ]


class RawMaterialListSerializer(serializers.ModelSerializer):
    """Serializer simplificado para listados"""

    unit_id = serializers.IntegerField(source="unit.id", read_only=True)
    unit_abbreviation = serializers.CharField(source="unit.abbreviation", read_only=True)
    stock_status = serializers.CharField(read_only=True)

    class Meta:
        model = RawMaterial
        fields = [
            "id",
            "name",
            "unit_id",
            "unit_abbreviation",
            "current_stock",
            "minimum_stock",
            "cost_per_unit",
            "supplier",
            "stock_status",
            "is_active",
        ]


class RecipeSerializer(serializers.ModelSerializer):
    """Serializer para ingredientes de receta"""

    raw_material = RawMaterialListSerializer(read_only=True)
    raw_material_id = serializers.PrimaryKeyRelatedField(
        queryset=RawMaterial.objects.all(),
        source="raw_material",
        write_only=True,
    )
    cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Recipe
        fields = [
            "id",
            "product_variant",
            "raw_material",
            "raw_material_id",
            "quantity",
            "notes",
            "cost",
        ]


class RecipeDetailSerializer(serializers.ModelSerializer):
    """Serializer detallado para recetas"""

    raw_material_name = serializers.CharField(source="raw_material.name", read_only=True)
    unit = serializers.CharField(source="raw_material.unit.abbreviation", read_only=True)
    cost_per_unit = serializers.DecimalField(
        source="raw_material.cost_per_unit",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )
    cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Recipe
        fields = [
            "id",
            "raw_material_name",
            "quantity",
            "unit",
            "cost_per_unit",
            "cost",
            "notes",
        ]


# ============= PURCHASE ORDER SERIALIZERS =============


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    """Serializer para items de orden de compra"""

    raw_material_name = serializers.CharField(source="raw_material.name", read_only=True)
    unit_abbreviation = serializers.CharField(source="raw_material.unit.abbreviation", read_only=True)
    estimated_subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    actual_subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "id",
            "raw_material",
            "raw_material_name",
            "unit_abbreviation",
            "quantity_needed",
            "quantity_purchased",
            "estimated_unit_price",
            "actual_unit_price",
            "estimated_subtotal",
            "actual_subtotal",
            "supplier",
            "notes",
            "is_purchased",
        ]
        read_only_fields = ["id"]


class PurchaseOrderItemCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear items de orden de compra"""

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "raw_material",
            "quantity_needed",
            "estimated_unit_price",
            "supplier",
            "notes",
        ]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Serializer para órdenes de compra"""

    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source="created_by.get_full_name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "order_number",
            "status",
            "status_display",
            "created_by",
            "created_by_name",
            "notes",
            "estimated_total",
            "actual_total",
            "purchased_at",
            "created_at",
            "items",
            "items_count",
        ]
        read_only_fields = ["id", "order_number", "estimated_total", "actual_total", "created_at"]

    def get_items_count(self, obj):
        return obj.items.count()


class PurchaseOrderCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear órdenes de compra"""

    items = PurchaseOrderItemCreateSerializer(many=True, write_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["notes", "items"]

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        user = self.context["request"].user
        
        order = PurchaseOrder.objects.create(
            created_by=user,
            **validated_data
        )

        for item_data in items_data:
            PurchaseOrderItem.objects.create(purchase_order=order, **item_data)

        order.calculate_totals()
        return order


class PurchaseOrderItemUpdateSerializer(serializers.Serializer):
    """Serializer para marcar un item como comprado"""

    quantity_purchased = serializers.DecimalField(max_digits=10, decimal_places=2)
    actual_unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    supplier = serializers.CharField(required=False, allow_blank=True)


class GenerateShoppingListSerializer(serializers.Serializer):
    """Serializer para generar lista de compras desde stock bajo"""

    include_zero_stock = serializers.BooleanField(default=True)
    multiplier = serializers.DecimalField(max_digits=5, decimal_places=2, default=1)
