from django.contrib import admin

from .models import WebhookEvent, WhatsAppContact


@admin.register(WhatsAppContact)
class WhatsAppContactAdmin(admin.ModelAdmin):
    list_display = (
        "phone",
        "customer_name",
        "profile_name",
        "human_handoff",
        "is_blocked",
        "last_message_at",
    )
    list_editable = ("human_handoff", "is_blocked")
    search_fields = ("phone", "customer_name", "profile_name")
    list_filter = ("human_handoff", "is_blocked")
    readonly_fields = ("created_at", "updated_at", "last_message_at", "last_phone_number_id")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "contact_phone", "phone_number_id", "status")
    list_filter = ("status",)
    search_fields = ("contact_phone", "idempotency_key")
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False
