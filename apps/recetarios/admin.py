from django.contrib import admin
from .models import (
    RecipeBook,
    RecipeBookStep,
    RecipeBookIngredient,
    RecipeBookImage,
)
from apps.search import PlainSearchAdminMixin


class RecipeBookStepInline(admin.TabularInline):
    model = RecipeBookStep
    extra = 1
    fields = ["step_number", "instruction", "image_url", "tip"]


class RecipeBookIngredientInline(admin.TabularInline):
    model = RecipeBookIngredient
    extra = 1
    fields = ["name", "quantity", "unit", "is_optional"]


class RecipeBookImageInline(admin.TabularInline):
    model = RecipeBookImage
    extra = 1
    fields = ["image_url", "caption", "display_order"]


@admin.register(RecipeBook)
class RecipeBookAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ["name", "category", "difficulty", "product", "is_active", "created_at"]
    list_filter = ["category", "difficulty", "is_active"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [RecipeBookIngredientInline, RecipeBookStepInline, RecipeBookImageInline]
    ordering = ["category", "name"]
