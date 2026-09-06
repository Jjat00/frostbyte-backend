from django.contrib import admin
from .models import CardGeneration, RecommenderLog
from apps.search import PlainSearchAdminMixin


@admin.register(RecommenderLog)
class RecommenderLogAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = (
        "created_at",
        "session_type",
        "recommended_product_name",
        "get_input_preview",
        "ip_address",
    )
    list_filter = ("session_type", "created_at")
    search_fields = ("recommended_product_name", "ai_reason", "ip_address")
    readonly_fields = (
        "session_type",
        "input_data",
        "recommended_product_name",
        "recommended_product_slug",
        "ai_reason",
        "ip_address",
        "created_at",
    )
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    def get_input_preview(self, obj):
        if obj.session_type == "mood":
            text = obj.input_data.get("mood", "")
            return text[:80] + "..." if len(text) > 80 else text
        # Quiz
        d = obj.input_data
        return f"temp={d.get('temperature')} | sabor={d.get('taste')} | alcohol={d.get('alcohol')}"

    get_input_preview.short_description = "Consulta"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CardGeneration)
class CardGenerationAdmin(admin.ModelAdmin):
    """El contador de tarjetas de campaña: cuántas, con qué proveedor y cuándo.

    Es un registro anónimo (no guarda foto, nombres ni dedicatoria), así que
    aquí solo se lee: las filas con resultado "Generada" son las tarjetas que
    de verdad se entregaron.
    """

    list_display = ("created_at", "provider", "status", "model_name", "was_fallback", "duration_ms")
    list_filter = ("provider", "status", "was_fallback", "created_at")
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    readonly_fields = ("provider", "status", "model_name", "was_fallback", "duration_ms", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
