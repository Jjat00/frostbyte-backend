from django.contrib import admin
from .models import RecommenderLog, MotherDedication, MothersDayCardEvent


@admin.register(RecommenderLog)
class RecommenderLogAdmin(admin.ModelAdmin):
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


@admin.register(MotherDedication)
class MotherDedicationAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "author_name",
        "mother_name",
        "message_preview",
        "is_approved",
    )
    list_filter = ("is_approved", "created_at")
    search_fields = ("author_name", "mother_name", "message")
    list_editable = ("is_approved",)
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    def message_preview(self, obj):
        return obj.message[:80] + "..." if len(obj.message) > 80 else obj.message

    message_preview.short_description = "Mensaje"


@admin.register(MothersDayCardEvent)
class MothersDayCardEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "user_agent_short")
    list_filter = ("event_type", "created_at")
    search_fields = ("user_agent",)
    readonly_fields = ("event_type", "user_agent", "created_at")
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    def user_agent_short(self, obj):
        return obj.user_agent[:60] + "..." if len(obj.user_agent) > 60 else obj.user_agent

    user_agent_short.short_description = "User-Agent"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
