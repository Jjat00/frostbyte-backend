from rest_framework import serializers
from .models import UnitOfMeasure, RawMaterial, Recipe


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

    unit_abbreviation = serializers.CharField(source="unit.abbreviation", read_only=True)
    stock_status = serializers.CharField(read_only=True)

    class Meta:
        model = RawMaterial
        fields = [
            "id",
            "name",
            "unit_abbreviation",
            "current_stock",
            "minimum_stock",
            "cost_per_unit",
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

