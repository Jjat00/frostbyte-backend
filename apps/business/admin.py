from django.contrib import admin

from .models import Business
from apps.search import PlainSearchAdminMixin


@admin.register(Business)
class BusinessAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ["name", "slug", "floor", "display_order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["display_order", "name"]
