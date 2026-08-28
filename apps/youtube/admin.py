from django.contrib import admin
from .models import VideoRequest, TVState
from apps.search import PlainSearchAdminMixin


@admin.register(TVState)
class TVStateAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ["video_id", "title", "is_mix", "updated_at"]
    readonly_fields = ["updated_at"]


@admin.register(VideoRequest)
class VideoRequestAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "title",
        "channel_name",
        "status",
        "video_id",
        "created_at",
        "played_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["title", "channel_name", "video_id"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]
