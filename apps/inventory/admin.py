from django.contrib import admin
from django.utils.html import format_html
from .models import UnitOfMeasure, RawMaterial, Recipe, PurchaseOrder, PurchaseOrderItem


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
        "business",
        "unit",
        "current_stock",
        "minimum_stock",
        "stock_status_display",
        "cost_per_unit",
        "supplier",
        "is_active",
    ]
    list_filter = ["business", "is_active", "unit", "supplier"]
    search_fields = ["name", "supplier"]
    ordering = ["name"]
    list_editable = ["current_stock", "cost_per_unit"]

    fieldsets = (
        (None, {"fields": ("name", "business", "unit", "is_active")}),
        ("Stock", {"fields": ("current_stock", "minimum_stock")}),
        ("Costos", {"fields": ("cost_per_unit",)}),
        ("Proveedor", {"fields": ("supplier",)}),
    )

    @admin.display(description="Estado stock")
    def stock_status_display(self, obj):
        status = obj.stock_status
        if status == "sin_stock":
            return "❌ Sin stock"
        elif status == "bajo":
            return "⚠️ Stock bajo"
        return "✅ Normal"


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

    @admin.display(description="Unidad")
    def unit_display(self, obj):
        return obj.raw_material.unit.abbreviation

    @admin.display(description="Costo")
    def cost_display(self, obj):
        return "${:,.0f}".format(obj.cost)


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0
    readonly_fields = ["estimated_subtotal"]
    autocomplete_fields = ["raw_material"]


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = [
        "order_number",
        "business",
        "status",
        "created_by",
        "items_count",
        "estimated_total_display",
        "actual_total_display",
        "created_at",
    ]
    list_filter = ["business", "status", "created_at"]
    search_fields = ["order_number", "created_by__username"]
    readonly_fields = ["order_number", "estimated_total", "actual_total", "created_at", "updated_at"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    inlines = [PurchaseOrderItemInline]

    fieldsets = (
        ("Orden", {"fields": ("order_number", "business", "status", "created_by")}),
        ("Totales", {"fields": ("estimated_total", "actual_total")}),
        ("Notas", {"fields": ("notes",)}),
        ("Fechas", {"fields": ("purchased_at", "created_at", "updated_at")}),
    )

    @admin.display(description="Items")
    def items_count(self, obj):
        return obj.items.count()

    @admin.display(description="Total Estimado")
    def estimated_total_display(self, obj):
        return "${:,.0f}".format(obj.estimated_total)

    @admin.display(description="Total Real")
    def actual_total_display(self, obj):
        return "${:,.0f}".format(obj.actual_total)


@admin.register(PurchaseOrderItem)
class PurchaseOrderItemAdmin(admin.ModelAdmin):
    list_display = [
        "purchase_order",
        "raw_material",
        "quantity_needed",
        "quantity_purchased",
        "estimated_unit_price",
        "actual_unit_price",
        "is_purchased",
    ]
    list_filter = ["is_purchased", "purchase_order__status"]
    search_fields = ["raw_material__name", "purchase_order__order_number"]
    autocomplete_fields = ["purchase_order", "raw_material"]
