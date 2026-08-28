from django.contrib import admin
from .models import Feedback
from apps.search import PlainSearchAdminMixin


@admin.register(Feedback)
class FeedbackAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "id",
        "customer_name",
        "feedback_type",
        "rating",
        "status",
        "created_at",
    ]
    list_filter = ["status", "feedback_type", "rating", "created_at"]
    search_fields = ["customer_name", "comment"]
    readonly_fields = ["created_at", "updated_at", "reviewed_at"]
    ordering = ["-created_at"]
