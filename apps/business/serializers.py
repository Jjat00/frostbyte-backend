from rest_framework import serializers

from .models import Business


class BusinessSerializer(serializers.ModelSerializer):
    """Serializer para negocios (catalogo de Business)."""

    class Meta:
        model = Business
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "floor",
            "color",
            "display_order",
            "is_active",
        ]
        read_only_fields = ["slug"]
