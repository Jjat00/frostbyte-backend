from django.contrib import admin
from .models import SongRequest


@admin.register(SongRequest)
class SongRequestAdmin(admin.ModelAdmin):
    list_display = [
        "song_name",
        "artist_name",
        "status",
        "created_at",
        "played_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["song_name", "artist_name"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]

