from rest_framework import serializers
from .models import MotherDedication


class MotherDedicationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MotherDedication
        fields = ["author_name", "mother_name", "message"]

    def validate_message(self, value):
        if len(value.strip()) < 5:
            raise serializers.ValidationError(
                "El mensaje debe tener al menos 5 caracteres."
            )
        return value.strip()


class MotherDedicationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = MotherDedication
        fields = ["id", "author_name", "mother_name", "message", "created_at"]
