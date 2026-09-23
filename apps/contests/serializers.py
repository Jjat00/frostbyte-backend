from rest_framework import serializers

from .models import (
    INSTAGRAM_HANDLE_RE,
    Contest,
    ContestEntry,
    normalize_instagram_handle,
)


class PublicContestSerializer(serializers.ModelSerializer):
    """Lo que ve cualquiera en la página del concurso."""

    confirmed_count = serializers.SerializerMethodField()

    class Meta:
        model = Contest
        fields = (
            "slug", "title", "description", "event_date", "event_time",
            "prize", "entry_fee", "min_age", "requires_instagram_follow",
            "payment_instructions", "registrations_open", "confirmed_count",
        )

    def get_confirmed_count(self, obj):
        return obj.entries.filter(status=ContestEntry.Status.CONFIRMED).count()


class StaffContestSerializer(serializers.ModelSerializer):
    """Configuración editable del concurso desde el panel del staff."""

    class Meta:
        model = Contest
        fields = (
            "id", "slug", "title", "description", "event_date", "event_time",
            "prize", "entry_fee", "min_age", "requires_instagram_follow",
            "payment_instructions", "is_published", "registrations_open",
        )
        read_only_fields = ("id", "slug")


class MyEntrySerializer(serializers.ModelSerializer):
    """La inscripción propia, tal como la ve el cliente."""

    class Meta:
        model = ContestEntry
        fields = (
            "id", "number", "full_name", "phone", "instagram_handle",
            "costume", "paid", "follows_instagram", "status", "created_at",
        )
        read_only_fields = fields


class EntryCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContestEntry
        fields = (
            "full_name", "phone", "instagram_handle", "costume",
            "declared_adult",
        )

    def validate_full_name(self, value):
        value = " ".join(value.split())
        if len(value) < 3:
            raise serializers.ValidationError("Escribe tu nombre completo.")
        return value

    def validate_phone(self, value):
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 7:
            raise serializers.ValidationError("Ingresa un celular válido.")
        return value.strip()

    def validate_instagram_handle(self, value):
        handle = normalize_instagram_handle(value)
        if not INSTAGRAM_HANDLE_RE.match(handle):
            raise serializers.ValidationError(
                "Escribe tu usuario de Instagram, por ejemplo @frostbyte.col.")
        return handle

    def validate_costume(self, value):
        return " ".join(value.split())

    def validate_declared_adult(self, value):
        if not value:
            raise serializers.ValidationError(
                "El concurso es solo para mayores de edad.")
        return value


class StaffEntrySerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    paid_by_name = serializers.SerializerMethodField()
    instagram_checked_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ContestEntry
        fields = (
            "id", "number", "full_name", "phone", "email",
            "instagram_handle", "costume", "paid", "paid_at", "paid_by_name",
            "follows_instagram", "instagram_checked_at",
            "instagram_checked_by_name", "cancelled", "status",
            "staff_notes", "created_at",
        )
        read_only_fields = fields

    @staticmethod
    def _name(user):
        if not user:
            return None
        return user.get_full_name() or user.username

    def get_paid_by_name(self, obj):
        return self._name(obj.paid_by)

    def get_instagram_checked_by_name(self, obj):
        return self._name(obj.instagram_checked_by)


class StaffEntryUpdateSerializer(serializers.Serializer):
    """Marcas que pone el staff en la barra. Todas opcionales."""

    paid = serializers.BooleanField(required=False)
    follows_instagram = serializers.BooleanField(required=False)
    cancelled = serializers.BooleanField(required=False)
    staff_notes = serializers.CharField(
        required=False, allow_blank=True, max_length=1000)
