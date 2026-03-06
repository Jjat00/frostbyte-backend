from rest_framework import serializers
from .models import SongRequest


class SongRequestSerializer(serializers.ModelSerializer):
    """Serializer para solicitudes de canciones"""

    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    class Meta:
        model = SongRequest
        fields = [
            "id",
            "song_name",
            "artist_name",
            "spotify_track_uri",
            "spotify_track_image",
            "spotify_track_duration_ms",
            "status",
            "status_display",
            "notes",
            "created_at",
            "updated_at",
            "played_at",
        ]
        read_only_fields = ["status", "created_at", "updated_at", "played_at"]


class SongRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear solicitudes con integración Spotify"""

    class Meta:
        model = SongRequest
        fields = [
            "song_name",
            "artist_name",
            "spotify_track_uri",
            "spotify_track_image",
            "spotify_track_duration_ms",
            "notes",
        ]

    def validate_song_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("El nombre de la canción es requerido")
        return value.strip()

    def validate_artist_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("El nombre del artista es requerido")
        return value.strip()


class SongRequestStatusUpdateSerializer(serializers.Serializer):
    """Serializer para actualizar el estado de una solicitud"""

    status = serializers.ChoiceField(choices=SongRequest.Status.choices)


class SpotifyTrackSerializer(serializers.Serializer):
    """Serializer para resultados de búsqueda de Spotify"""

    uri = serializers.CharField()
    name = serializers.CharField()
    artists = serializers.CharField()
    album = serializers.CharField()
    image = serializers.URLField(allow_blank=True)
    duration_ms = serializers.IntegerField()
    preview_url = serializers.URLField(allow_null=True, allow_blank=True)

