from django.contrib import admin
from django.utils.html import format_html
from .models import UnitOfMeasure, RawMaterial, Recipe


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ["name", "abbreviation"]
    search_fields = ["name", "abbreviation"]
    ordering = ["name"]


class RecipeInline(admin.TabularInline):
    model = Recipe
    extra = 1
    autocomplete_fields = ["raw_material"]


@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "unit",
        "current_stock",
        "minimum_stock",
        "stock_status_display",
        "cost_per_unit",
        "supplier",
        "is_active",
    ]
    list_filter = ["is_active", "unit", "supplier"]
    search_fields = ["name", "supplier"]
    ordering = ["name"]
    list_editable = ["current_stock", "cost_per_unit"]

    fieldsets = (
        (None, {"fields": ("name", "unit", "is_active")}),
        ("Stock", {"fields": ("current_stock", "minimum_stock")}),
        ("Costos", {"fields": ("cost_per_unit",)}),
        ("Proveedor", {"fields": ("supplier",)}),
    )

    def stock_status_display(self, obj):
        status = obj.stock_status
        if status == "sin_stock":
            color = "red"
            text = "❌ Sin stock"
        elif status == "bajo":
            color = "orange"
            text = "⚠️ Stock bajo"
        else:
            color = "green"
            text = "✅ Normal"
        return format_html('<span style="color: {};">{}</span>', color, text)

    stock_status_display.short_description = "Estado stock"


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = [
        "product_variant",
        "raw_material",
        "quantity",
        "unit_display",
        "cost_display",
    ]
    list_filter = [
        "product_variant__product__category",
        "product_variant__product",
    ]
    search_fields = [
        "product_variant__product__name",
        "product_variant__name",
        "raw_material__name",
    ]
    autocomplete_fields = ["product_variant", "raw_material"]

    def unit_display(self, obj):
        return obj.raw_material.unit.abbreviation

    unit_display.short_description = "Unidad"

    def cost_display(self, obj):
        return f"${obj.cost:,.0f}"

    cost_display.short_description = "Costo"
