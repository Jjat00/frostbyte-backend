"""Serializers del módulo de reservas."""
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from . import availability
from .models import Reservation, ReservationSettings, VIP_FLOOR


class ReservationSerializer(serializers.ModelSerializer):
    """Lectura de una reserva (cliente y staff)."""

    type_display = serializers.CharField(
        source="get_reservation_type_display", read_only=True)
    status_display = serializers.CharField(
        source="get_status_display", read_only=True)
    vip_slot_display = serializers.CharField(
        source="get_vip_slot_display", read_only=True)
    table_label = serializers.CharField(
        source="table.label", read_only=True, default=None)

    class Meta:
        model = Reservation
        fields = [
            "id", "reservation_type", "type_display", "status",
            "status_display", "source", "customer_name", "customer_phone",
            "party_size", "floor", "tables_needed", "date", "start_time",
            "end_time", "vip_slot", "vip_slot_display", "deposit_amount",
            "min_consumption", "table", "table_label", "notes",
            "staff_notes", "created_at",
        ]
        read_only_fields = fields


class CustomerReservationCreateSerializer(serializers.ModelSerializer):
    """Creación de reserva por el cliente (app).

    El cliente solo elige tipo (mesa o sala), fecha, hora/turno, piso,
    personas y sus datos. Montos, estado inicial, ventana y mesas necesarias
    los decide SIEMPRE el servidor.

    Toda reserva del cliente nace como SOLICITUD (pending_review): el staff
    la aprueba (→ confirmed, o → pending_payment si lleva anticipo) o la
    rechaza. Con context["staff_creation"]=True (reserva tomada por teléfono
    o en persona) se salta la revisión: el staff confirma al crearla.
    """

    reservation_type = serializers.ChoiceField(
        choices=[Reservation.Type.TABLE, Reservation.Type.VIP_ROOM])
    vip_slot = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=2)

    class Meta:
        model = Reservation
        fields = [
            "reservation_type", "customer_name", "customer_phone",
            "party_size", "floor", "date", "start_time", "vip_slot", "notes",
        ]
        extra_kwargs = {
            "start_time": {"required": False},
            "floor": {"required": False},
        }

    def validate_customer_name(self, value):
        value = value.strip()
        if len(value) < 3:
            raise serializers.ValidationError("Escribe el nombre completo.")
        return value

    def validate_customer_phone(self, value):
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 7:
            raise serializers.ValidationError("Teléfono inválido.")
        return value.strip()

    def validate_date(self, value):
        settings = ReservationSettings.load()
        today = timezone.localdate()
        if value < today:
            raise serializers.ValidationError(
                "No se puede reservar en el pasado.")
        if value > today + timedelta(days=settings.max_advance_days):
            raise serializers.ValidationError(
                f"Solo recibimos reservas con máximo "
                f"{settings.max_advance_days} días de antelación.")
        return value

    def validate(self, attrs):
        settings = ReservationSettings.load()
        rtype = attrs["reservation_type"]
        party = attrs["party_size"]
        date = attrs["date"]

        if party < 1:
            raise serializers.ValidationError(
                {"party_size": "Indica cuántas personas vienen."})

        if rtype == Reservation.Type.VIP_ROOM:
            attrs = self._validate_vip(attrs, settings, party, date)
        else:
            attrs = self._validate_table(attrs, settings, party, date)

        attrs["settings"] = settings
        return attrs

    # ------------------------------------------------------------- Sala VIP
    def _validate_vip(self, attrs, settings, party, date):
        slot = attrs.get("vip_slot")
        if slot not in (1, 2):
            raise serializers.ValidationError(
                {"vip_slot": "Elige el turno de la sala (tarde o noche)."})
        if party > settings.vip_capacity:
            raise serializers.ValidationError({
                "party_size":
                    f"La sala tiene capacidad para {settings.vip_capacity} "
                    f"personas.",
            })

        start, end = settings.vip_slot_bounds(slot)
        if availability.vip_reservation_overlapping(date, start, end):
            raise serializers.ValidationError(
                "La sala ya está reservada en ese turno. Elige otro turno u "
                "otra fecha.")

        attrs["floor"] = VIP_FLOOR
        attrs["start_time"] = start
        attrs["end_time"] = end
        attrs["tables_needed"] = 0  # la sala no consume mesas del pool: las bloquea aparte
        attrs["deposit_amount"] = settings.vip_deposit
        attrs["min_consumption"] = settings.vip_min_consumption_for(date)
        return attrs

    # ---------------------------------------------------------- mesa / grupo
    def _validate_table(self, attrs, settings, party, date):
        start = attrs.get("start_time")
        if not start:
            raise serializers.ValidationError(
                {"start_time": "Elige la hora de llegada."})
        if not (settings.reservable_from <= start <= settings.reservable_until):
            raise serializers.ValidationError({
                "start_time":
                    f"Recibimos reservas entre "
                    f"{settings.reservable_from:%H:%M} y "
                    f"{settings.reservable_until:%H:%M}.",
            })

        floor = attrs.get("floor") or 2
        if floor not in (2, VIP_FLOOR):
            raise serializers.ValidationError({"floor": "Piso inválido."})

        # Grupo grande: mismo flujo, pero con anticipo
        if party > settings.table_max_party:
            attrs["reservation_type"] = Reservation.Type.GROUP
            attrs["deposit_amount"] = settings.group_deposit
        else:
            attrs["deposit_amount"] = 0

        needed = availability.tables_needed_for(party, settings)
        end = availability._add_minutes(start, settings.table_window_minutes)
        available = availability.tables_available(
            date, floor, start, end, settings)
        if available < needed:
            raise serializers.ValidationError(
                "Ya no hay mesas disponibles para reservar en esa hora. "
                "Prueba otra hora u otro piso.")

        attrs["floor"] = floor
        attrs["end_time"] = end
        attrs["tables_needed"] = needed
        attrs["min_consumption"] = 0
        return attrs

    def create(self, validated_data):
        validated_data.pop("settings", None)

        if self.context.get("staff_creation"):
            # El staff confirma al crearla; con anticipo queda esperando pago
            status = (Reservation.Status.PENDING_PAYMENT
                      if validated_data.get("deposit_amount")
                      else Reservation.Status.CONFIRMED)
        else:
            status = Reservation.Status.PENDING_REVIEW

        return Reservation.objects.create(status=status, **validated_data)


class StaffReservationSerializer(ReservationSerializer):
    """Lectura para el staff (incluye quién cambió el estado)."""

    status_changed_by_name = serializers.CharField(
        source="status_changed_by.get_full_name", read_only=True, default=None)

    class Meta(ReservationSerializer.Meta):
        fields = ReservationSerializer.Meta.fields + [
            "status_changed_at", "status_changed_by_name", "user",
        ]
        read_only_fields = fields


class StaffReservationUpdateSerializer(serializers.ModelSerializer):
    """Actualización por el staff: estado, mesa asignada, notas y datos."""

    class Meta:
        model = Reservation
        fields = [
            "status", "table", "staff_notes", "customer_name",
            "customer_phone", "party_size", "notes",
        ]

    def validate_table(self, value):
        if value and not value.is_active:
            raise serializers.ValidationError("Esa mesa está inactiva.")
        return value

    def update(self, instance, validated_data):
        new_status = validated_data.pop("status", None)
        instance = super().update(instance, validated_data)
        if new_status and new_status != instance.status:
            instance.set_status(
                new_status, by=self.context["request"].user)
        return instance


class ReservationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReservationSettings
        exclude = ["id"]
