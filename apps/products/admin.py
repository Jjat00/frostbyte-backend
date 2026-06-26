from django.contrib import admin
from .models import (
    Category,
    ModifierGroup,
    ModifierOption,
    Product,
    ProductModifierGroup,
    ProductVariant,
)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ["name", "sku", "is_default", "is_active"]


class ProductModifierGroupInline(admin.TabularInline):
    model = ProductModifierGroup
    extra = 1
    fields = ["group", "display_order", "min_select_override", "max_select_override", "is_active"]
    autocomplete_fields = ["group"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "business", "slug", "display_order", "is_active", "products_count"]
    list_filter = ["business", "is_active"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["business", "display_order", "name"]

    def products_count(self, obj):
        return obj.products.count()

    products_count.short_description = "Productos"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "business", "category", "is_active", "is_coming_soon", "variants_count", "created_at"]
    list_filter = ["business", "category", "is_active", "is_coming_soon"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["business"]  # se hereda de la categoría
    inlines = [ProductVariantInline, ProductModifierGroupInline]
    ordering = ["business", "category", "name"]

    def variants_count(self, obj):
        return obj.variants.count()

    variants_count.short_description = "Variantes"


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ["__str__", "sku", "is_default", "is_active"]
    list_filter = ["product__category", "is_active", "is_default"]
    search_fields = ["name", "sku", "product__name"]
    ordering = ["product", "name"]


class ModifierOptionInline(admin.TabularInline):
    model = ModifierOption
    extra = 2
    fields = ["name", "price_delta", "is_default", "is_active", "display_order"]


@admin.register(ModifierGroup)
class ModifierGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "business", "min_select", "max_select", "is_required", "is_active"]
    list_filter = ["business", "is_active"]
    search_fields = ["name", "description"]
    ordering = ["business", "name"]
    inlines = [ModifierOptionInline]

    def is_required(self, obj):
        return obj.is_required

    is_required.boolean = True
    is_required.short_description = "Obligatorio"


@admin.register(ModifierOption)
class ModifierOptionAdmin(admin.ModelAdmin):
    list_display = ["name", "group", "price_delta", "is_default", "is_active", "display_order"]
    list_filter = ["group__business", "group", "is_active"]
    search_fields = ["name", "group__name"]
    ordering = ["group", "display_order", "name"]


@admin.register(ProductModifierGroup)
class ProductModifierGroupAdmin(admin.ModelAdmin):
    list_display = ["product", "group", "display_order", "effective_min", "effective_max", "is_active"]
    list_filter = ["group__business", "is_active"]
    search_fields = ["product__name", "group__name"]
    ordering = ["product", "display_order"]

    def effective_min(self, obj):
        return obj.effective_min

    def effective_max(self, obj):
        return obj.effective_max
