from rest_framework import serializers
from .models import VideoRequest


class VideoRequestSerializer(serializers.ModelSerializer):
    """Serializer para solicitudes de video"""

    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    class Meta:
        model = VideoRequest
        fields = [
            "id",
            "title",
            "channel_name",
            "video_id",
            "thumbnail",
            "duration",
            "status",
            "status_display",
            "notes",
            "created_at",
            "updated_at",
            "played_at",
        ]
        read_only_fields = ["status", "created_at", "updated_at", "played_at"]


class VideoRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear solicitudes de video"""

    class Meta:
        model = VideoRequest
        fields = [
            "title",
            "channel_name",
            "video_id",
            "thumbnail",
            "duration",
            "notes",
        ]

    def validate_title(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("El titulo del video es requerido")
        return value.strip()

    def validate_video_id(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("El ID del video es requerido")
        return value.strip()


class VideoRequestStatusUpdateSerializer(serializers.Serializer):
    """Serializer para actualizar el estado de una solicitud"""

    status = serializers.ChoiceField(choices=VideoRequest.Status.choices)


class YouTubeVideoSerializer(serializers.Serializer):
    """Serializer para resultados de busqueda de YouTube"""

    video_id = serializers.CharField()
    title = serializers.CharField()
    channel_name = serializers.CharField()
    thumbnail = serializers.URLField(allow_blank=True)
    duration = serializers.CharField(allow_blank=True)
