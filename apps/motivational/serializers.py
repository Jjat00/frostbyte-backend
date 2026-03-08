from rest_framework import serializers
from .models import Dedication


class DedicationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dedication
        fields = ["author_name", "honoree_name", "message"]

    def validate_message(self, value):
        if len(value.strip()) < 5:
            raise serializers.ValidationError(
                "El mensaje debe tener al menos 5 caracteres."
            )
        return value.strip()


class DedicationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dedication
        fields = ["id", "author_name", "honoree_name", "message", "created_at"]
