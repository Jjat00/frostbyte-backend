"""API del módulo de reservas.

Dos superficies:
- CustomerReservationViewSet: cliente Google (app). Config y disponibilidad
  públicas; crear/listar/cancelar requieren sesión.
- StaffReservationViewSet: dashboard del staff (calendario, día, transiciones
  de estado, configuración).
"""
from datetime import date as date_cls

from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.accounts.permissions import IsStaffMember
from apps.orders.models import Table

from . import availability
from .consumers import broadcast_reservations_update
from .models import Reservation, ReservationSettings, VIP_FLOOR
from .serializers import (
    CustomerReservationCreateSerializer,
    ReservationSerializer,
    ReservationSettingsSerializer,
    StaffReservationSerializer,
    StaffReservationUpdateSerializer,
)


def _parse_date(value):
    if not value:
        return None
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        raise ValidationError({"date": "Fecha inválida (usa YYYY-MM-DD)."})


class CustomerReservationThrottle(UserRateThrottle):
    """Límite anti-spam para la creación de reservas del cliente."""
    scope = "customer_reservations"
    rate = "10/hour"


class CustomerReservationViewSet(mixins.CreateModelMixin,
                                 mixins.ListModelMixin,
                                 mixins.RetrieveModelMixin,
                                 viewsets.GenericViewSet):
    """
    Reservas del cliente autenticado con Google.

    create: Crea una reserva propia (mesa, grupo o Sala VIP).
    list: 'Mis reservas' del cliente autenticado.
    retrieve: Detalle de una reserva propia.
    cancel: Cancela una reserva propia futura.
    config: Configuración pública del módulo (montos, turnos, horarios).
    availability: Disponibilidad pública por fecha (horas de mesa o turnos VIP).
    """
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        if self.action == "create":
            return [CustomerReservationThrottle()]
        return []

    def get_queryset(self):
        return (
            Reservation.objects
            .filter(user=self.request.user)
            .select_related("table")
            .order_by("-date", "-start_time")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return CustomerReservationCreateSerializer
        return ReservationSerializer

    def perform_create(self, serializer):
        cfg = ReservationSettings.load()
        if not cfg.reservations_enabled:
            raise ValidationError(
                "Las reservas en línea están deshabilitadas por el momento.")
        instance = serializer.save(
            user=self.request.user, source=Reservation.Source.APP)
        broadcast_reservations_update()
        return instance

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = self.perform_create(serializer)
        cfg = ReservationSettings.load()
        data = ReservationSerializer(instance).data
        # Referencia de pago para cuando el staff apruebe la solicitud;
        # el front deja claro que el anticipo se envía tras la confirmación
        if instance.deposit_amount:
            data["payment_instructions"] = cfg.payment_instructions
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancela una reserva propia (solo antes de sentarse)."""
        reservation = self.get_object()
        cancellable = (
            Reservation.Status.PENDING_PAYMENT,
            Reservation.Status.PENDING_REVIEW,
            Reservation.Status.CONFIRMED,
        )
        if reservation.status not in cancellable:
            raise ValidationError("Esta reserva ya no se puede cancelar.")
        reservation.set_status(Reservation.Status.CANCELLED, by=request.user)
        broadcast_reservations_update()
        return Response(ReservationSerializer(reservation).data)

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def config(self, request):
        """Configuración pública del módulo de reservas."""
        cfg = ReservationSettings.load()
        return Response({
            "reservations_enabled": cfg.reservations_enabled,
            "table_max_party": cfg.table_max_party,
            "vip_capacity": cfg.vip_capacity,
            "group_deposit": str(cfg.group_deposit),
            "vip_deposit": str(cfg.vip_deposit),
            "vip_min_consumption_weekday": str(cfg.vip_min_consumption_weekday),
            "vip_min_consumption_weekend": str(cfg.vip_min_consumption_weekend),
            "vip_slots": [
                {"slot": 1, "label": "Tarde",
                 "start": cfg.vip_slot_1_start.strftime("%H:%M"),
                 "end": cfg.vip_slot_1_end.strftime("%H:%M")},
                {"slot": 2, "label": "Noche",
                 "start": cfg.vip_slot_2_start.strftime("%H:%M"),
                 "end": cfg.vip_slot_2_end.strftime("%H:%M")},
            ],
            "reservable_from": cfg.reservable_from.strftime("%H:%M"),
            "reservable_until": cfg.reservable_until.strftime("%H:%M"),
            "table_hold_minutes": cfg.table_hold_minutes,
            "max_advance_days": cfg.max_advance_days,
            "payment_instructions": cfg.payment_instructions,
            "floors": [2, VIP_FLOOR],
        })

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def availability(self, request):
        """Disponibilidad pública por fecha.

        ?date=YYYY-MM-DD&type=table&party_size=4&floor=2
        ?date=YYYY-MM-DD&type=vip_room
        """
        cfg = ReservationSettings.load()
        availability.release_expired_holds(cfg)

        date = _parse_date(request.query_params.get("date"))
        if not date:
            raise ValidationError({"date": "Falta la fecha."})
        rtype = request.query_params.get("type", "table")

        if rtype == Reservation.Type.VIP_ROOM:
            return Response({
                "date": date.isoformat(),
                "type": rtype,
                "slots": availability.vip_slot_status(date, cfg),
            })

        try:
            party_size = int(request.query_params.get("party_size", 2))
            floor = int(request.query_params.get("floor", 2))
        except (TypeError, ValueError):
            raise ValidationError("party_size o floor inválidos.")

        return Response({
            "date": date.isoformat(),
            "type": "table",
            "floor": floor,
            "party_size": party_size,
            "tables_needed": availability.tables_needed_for(party_size, cfg),
            "slots": availability.table_arrival_slots(
                date, party_size, floor, cfg),
        })


class StaffReservationViewSet(mixins.CreateModelMixin,
                              mixins.ListModelMixin,
                              mixins.RetrieveModelMixin,
                              mixins.UpdateModelMixin,
                              mixins.DestroyModelMixin,
                              viewsets.GenericViewSet):
    """
    Gestión de reservas por el staff.

    list: Reservas de un día (?date=) o próximas.
    partial_update: Cambia estado, asigna mesa, edita notas/datos.
    destroy: Elimina la reserva definitivamente (el front confirma antes).
    day: Reservas del día + contexto de ocupación (mesas por piso).
    calendar: Resumen mensual (conteo por día) para pintar el calendario.
    settings: GET/PATCH de la configuración del módulo.
    """
    permission_classes = [IsStaffMember]

    def get_queryset(self):
        qs = (
            Reservation.objects
            .select_related("table", "status_changed_by")
            .order_by("date", "start_time")
        )
        date = _parse_date(self.request.query_params.get("date"))
        if date:
            qs = qs.filter(date=date)
        elif self.action == "list":
            qs = qs.filter(date__gte=timezone.localdate())
        return qs

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return StaffReservationUpdateSerializer
        if self.action == "create":
            return CustomerReservationCreateSerializer
        return StaffReservationSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.action == "create":
            # El staff confirma al crear: la reserva no pasa por revisión
            ctx["staff_creation"] = True
        return ctx

    def perform_create(self, serializer):
        # Reserva tomada por teléfono/en persona: sin exigir flag encendido
        instance = serializer.save(source=Reservation.Source.STAFF)
        broadcast_reservations_update()
        return instance

    def perform_update(self, serializer):
        serializer.save()
        broadcast_reservations_update()

    def perform_destroy(self, instance):
        instance.delete()
        broadcast_reservations_update()

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        # Devuelve siempre la representación completa de lectura
        return Response(StaffReservationSerializer(self.get_object()).data)

    @action(detail=False, methods=["get"])
    def day(self, request):
        """Reservas de un día + contexto para el tablero de ocupación."""
        cfg = ReservationSettings.load()
        availability.release_expired_holds(cfg)

        date = _parse_date(request.query_params.get("date")) \
            or timezone.localdate()
        reservations = (
            Reservation.objects
            .filter(date=date)
            .select_related("table", "status_changed_by")
            .order_by("start_time")
        )
        tables_by_floor = {
            str(floor): availability.physical_tables(floor)
            for floor in (2, VIP_FLOOR)
        }
        return Response({
            "date": date.isoformat(),
            "reservations": StaffReservationSerializer(
                reservations, many=True).data,
            "tables_by_floor": tables_by_floor,
            "max_reserved_tables_per_slot": cfg.max_reserved_tables_per_slot,
            "vip_room_table_count": cfg.vip_room_table_count,
        })

    @action(detail=False, methods=["get"])
    def calendar(self, request):
        """Conteo de reservas por día para un mes (?year=&month=)."""
        try:
            year = int(request.query_params.get(
                "year", timezone.localdate().year))
            month = int(request.query_params.get(
                "month", timezone.localdate().month))
        except (TypeError, ValueError):
            raise ValidationError("year o month inválidos.")

        qs = Reservation.objects.filter(date__year=year, date__month=month)
        days = {}
        for r in qs.only("date", "status", "reservation_type"):
            day = days.setdefault(r.date.isoformat(), {
                "total": 0, "pending": 0, "vip": 0,
            })
            if r.status in Reservation.BLOCKING_STATUSES:
                day["total"] += 1
                if r.status in (Reservation.Status.PENDING_PAYMENT,
                                Reservation.Status.PENDING_REVIEW):
                    day["pending"] += 1
                if r.reservation_type == Reservation.Type.VIP_ROOM:
                    day["vip"] += 1
        return Response({"year": year, "month": month, "days": days})

    @action(detail=False, methods=["get", "patch"], url_path="settings")
    def settings_view(self, request):
        """Configuración del módulo (GET para leer, PATCH para editar)."""
        cfg = ReservationSettings.load()
        if request.method == "PATCH":
            serializer = ReservationSettingsSerializer(
                cfg, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            broadcast_reservations_update()
        return Response(ReservationSettingsSerializer(cfg).data)

    @action(detail=False, methods=["get"])
    def tables(self, request):
        """Mesas activas por piso para asignar reservas (?floor=)."""
        qs = Table.objects.filter(is_active=True, table_number__gt=0)
        floor = request.query_params.get("floor")
        if floor:
            qs = qs.filter(floor=floor)
        return Response([
            {"id": t.id, "label": t.label, "floor": t.floor,
             "table_number": t.table_number}
            for t in qs.order_by("floor", "table_number")
        ])
