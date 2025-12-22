from django.contrib import admin
from .models import Category, Product, ProductVariant


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ["name", "sku", "is_default", "is_active"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "display_order", "is_active", "products_count"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["display_order", "name"]

    def products_count(self, obj):
        return obj.products.count()

    products_count.short_description = "Productos"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "is_active", "is_coming_soon", "variants_count", "created_at"]
    list_filter = ["category", "is_active", "is_coming_soon"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductVariantInline]
    ordering = ["category", "name"]

    def variants_count(self, obj):
        return obj.variants.count()

    variants_count.short_description = "Variantes"


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ["__str__", "sku", "is_default", "is_active"]
    list_filter = ["product__category", "is_active", "is_default"]
    search_fields = ["name", "sku", "product__name"]
    ordering = ["product", "name"]
