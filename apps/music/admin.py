from django.contrib import admin
from .models import ArtistGenre, SongRequest, SpotifyToken, MusicSettings
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



@admin.register(ArtistGenre)
class ArtistGenreAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    """Los géneros los pone la IA; aquí se corrigen.

    Editar el género desde el admin marca la fila como `manual`, y a partir de
    ahí ninguna reclasificación la vuelve a tocar.
    """

    list_display = ["artist_name", "genre", "source", "model_used", "updated_at"]
    list_filter = ["genre", "source"]
    list_editable = ["genre"]
    search_fields = ["artist_name", "artist_key"]
    readonly_fields = ["artist_key", "created_at", "updated_at"]
    ordering = ["artist_name"]

    def save_model(self, request, obj, form, change):
        if change and "genre" in form.changed_data:
            obj.source = ArtistGenre.Source.MANUAL
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        """El mismo criterio para la edición en lote desde el listado."""
        instancias = formset.save(commit=False)
        for instancia in instancias:
            instancia.source = ArtistGenre.Source.MANUAL
            instancia.save()
        formset.save_m2m()
