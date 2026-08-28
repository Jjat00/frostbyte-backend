from django.contrib import admin
from .models import SongRequest, SpotifyToken, MusicSettings
from apps.search import PlainSearchAdminMixin


@admin.register(MusicSettings)
class MusicSettingsAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ["source", "updated_at"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(SongRequest)
class SongRequestAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "song_name",
        "artist_name",
        "floor",
        "status",
        "spotify_track_uri",
        "created_at",
        "played_at",
    ]
    list_filter = ["floor", "status", "created_at"]
    search_fields = ["song_name", "artist_name", "spotify_track_uri"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(SpotifyToken)
class SpotifyTokenAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "floor",
        "token_type",
        "expires_at",
        "is_expired",
        "created_at",
        "updated_at",
    ]
    list_filter = ["floor"]
    readonly_fields = ["created_at", "updated_at"]

    def is_expired(self, obj):
        return obj.is_expired
    is_expired.boolean = True
    is_expired.short_description = "Expirado"

