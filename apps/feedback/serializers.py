from rest_framework import serializers
from .models import Feedback


class FeedbackSerializer(serializers.ModelSerializer):
    """Serializer completo para feedback (admin)"""

    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )
    feedback_type_display = serializers.CharField(
        source="get_feedback_type_display",
        read_only=True,
    )

    class Meta:
        model = Feedback
        fields = [
            "id",
            "customer_name",
            "comment",
            "rating",
            "feedback_type",
            "feedback_type_display",
            "status",
            "status_display",
            "admin_notes",
            "created_at",
            "updated_at",
            "reviewed_at",
        ]
        read_only_fields = ["created_at", "updated_at", "reviewed_at"]


class FeedbackCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear feedback (publico)"""

    class Meta:
        model = Feedback
        fields = [
            "customer_name",
            "comment",
            "rating",
            "feedback_type",
        ]

    def validate_comment(self, value):
        """Validar que el comentario no este vacio"""
        if not value or not value.strip():
            raise serializers.ValidationError("El comentario es requerido")
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                "El comentario debe tener al menos 10 caracteres"
            )
        return value.strip()

    def validate_rating(self, value):
        """Validar rating si se proporciona"""
        if value is not None and (value < 1 or value > 5):
            raise serializers.ValidationError(
                "La calificacion debe ser entre 1 y 5"
            )
        return value


class FeedbackStatusUpdateSerializer(serializers.Serializer):
    """Serializer para actualizar el estado de un feedback"""

    status = serializers.ChoiceField(choices=Feedback.Status.choices)
    admin_notes = serializers.CharField(required=False, allow_blank=True)
