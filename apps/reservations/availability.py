"""Cálculo de disponibilidad de mesas y de la Sala VIP.

Reglas:
- El cliente nunca reserva una mesa concreta: reserva "una mesa para N en el
  piso X". El staff asigna la mesa física al sentar al grupo.
- Por piso y franja solo se puede reservar un tope de mesas
  (`max_reserved_tables_per_slot`); el resto queda libre para walk-ins.
- Si la Sala VIP está reservada y contiene mesas del pool del piso 3
  (`vip_room_table_count`), esas mesas se descuentan durante su turno.
- Una solicitud de sala que no cabe junto a las reservas de mesa ya
  confirmadas no se rechaza: entra en PENDING_REVIEW para que el staff
  reubique voluntariamente (nunca se cancela una reserva confirmada).
"""
from datetime import datetime, timedelta
from math import ceil

from django.db.models import Sum
from django.utils import timezone

from apps.orders.models import Table

from .models import Reservation, ReservationSettings, VIP_FLOOR

# Cada cuántos minutos se ofrece una hora de llegada
SLOT_STEP_MINUTES = 30
# Antelación mínima para reservar hoy (no aceptar "llego en 5 minutos")
MIN_LEAD_MINUTES = 30


def physical_tables(floor):
    """Mesas físicas reservables del piso (activas, sin contar la barra)."""
    return Table.objects.filter(
        floor=floor, is_active=True, table_number__gt=0,
    ).count()


def tables_needed_for(party_size, settings=None):
    """Cuántas mesas ocupa un grupo de N personas."""
    settings = settings or ReservationSettings.load()
    return max(1, ceil(party_size / settings.seats_per_table))


def release_expired_holds(settings=None):
    """Libera (marca no_show) reservas de mesa cuya tolerancia ya venció.

    Solo aplica a reservas SIN anticipo (tipo mesa): con anticipo pagado la
    decisión es del staff. Se llama de forma perezosa desde los endpoints de
    disponibilidad y del dashboard: no hace falta un cron para esto.
    """
    settings = settings or ReservationSettings.load()
    now = timezone.localtime()
    today = now.date()
    grace_ago = now - timedelta(minutes=settings.table_hold_minutes)
    if grace_ago.date() < today:
        # Recién pasada la medianoche la resta cae en ayer: comparar su .time()
        # (~23:5x) contra las llegadas de hoy marcaría no_show reservas que
        # aún no ocurren. A esa hora ninguna tolerancia de hoy ha vencido.
        return 0
    cutoff = grace_ago.time()

    expired = Reservation.objects.filter(
        reservation_type=Reservation.Type.TABLE,
        status=Reservation.Status.CONFIRMED,
        date=today,
        start_time__lt=cutoff,
    )
    return expired.update(
        status=Reservation.Status.NO_SHOW,
        status_changed_at=now,
    )


def _blocking_qs(date):
    return Reservation.objects.filter(
        date=date, status__in=Reservation.BLOCKING_STATUSES)


def vip_reservation_overlapping(date, start, end, exclude_pk=None):
    """Reserva de la sala (bloqueante) que se cruza con la ventana dada."""
    qs = _blocking_qs(date).filter(
        reservation_type=Reservation.Type.VIP_ROOM,
        start_time__lt=end,
        end_time__gt=start,
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def reserved_tables_overlapping(date, floor, start, end, exclude_pk=None):
    """Cuántas mesas están ya reservadas (mesa+grupo) en la ventana dada."""
    qs = _blocking_qs(date).filter(
        reservation_type__in=(
            Reservation.Type.TABLE, Reservation.Type.GROUP),
        floor=floor,
        start_time__lt=end,
        end_time__gt=start,
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.aggregate(total=Sum("tables_needed"))["total"] or 0


def tables_available(date, floor, start, end, settings=None, exclude_pk=None):
    """Mesas que aún se pueden reservar en el piso para esa ventana."""
    settings = settings or ReservationSettings.load()
    total = physical_tables(floor)

    # La sala reservada bloquea las mesas del pool que viven dentro de ella
    if floor == VIP_FLOOR and vip_reservation_overlapping(date, start, end):
        total -= settings.vip_room_table_count

    reserved = reserved_tables_overlapping(
        date, floor, start, end, exclude_pk=exclude_pk)
    cap = settings.max_reserved_tables_per_slot
    return max(0, min(total, cap) - reserved)


def _add_minutes(t, minutes):
    """Suma minutos a un time, sin pasar de las 23:59 (no cruzamos medianoche)."""
    base = datetime(2000, 1, 1, t.hour, t.minute)
    result = base + timedelta(minutes=minutes)
    if result.date() != base.date():
        return result.time().replace(hour=23, minute=59)
    return result.time()


def table_arrival_slots(date, party_size, floor, settings=None):
    """Horas de llegada ofrecibles para una reserva de mesa/grupo.

    Devuelve [{"time": "15:00", "available": bool}, ...] cada 30 minutos
    dentro del horario reservable.
    """
    settings = settings or ReservationSettings.load()
    needed = tables_needed_for(party_size, settings)
    now = timezone.localtime()
    is_today = date == now.date()

    slots = []
    t = settings.reservable_from
    while t <= settings.reservable_until:
        if is_today and (
            datetime.combine(date, t)
            <= (now + timedelta(minutes=MIN_LEAD_MINUTES)).replace(tzinfo=None)
        ):
            available = False
        else:
            end = _add_minutes(t, settings.table_window_minutes)
            available = tables_available(
                date, floor, t, end, settings) >= needed
        slots.append({"time": t.strftime("%H:%M"), "available": available})
        t = _add_minutes(t, SLOT_STEP_MINUTES)
    return slots


def vip_slot_status(date, settings=None):
    """Estado de los dos turnos de la Sala VIP para una fecha.

    Cada turno indica si está libre y si tomar la sala chocaría con reservas
    de mesa existentes en el piso 3 (→ la solicitud entraría a revisión del
    staff en lugar de confirmarse de una).
    """
    settings = settings or ReservationSettings.load()
    now = timezone.localtime()
    is_today = date == now.date()
    outside_tables = physical_tables(VIP_FLOOR) - settings.vip_room_table_count

    result = []
    for slot, label in ((1, "Tarde"), (2, "Noche")):
        start, end = settings.vip_slot_bounds(slot)
        taken = vip_reservation_overlapping(date, start, end) is not None
        past = is_today and datetime.combine(date, start) <= (
            now + timedelta(minutes=MIN_LEAD_MINUTES)).replace(tzinfo=None)
        reserved = reserved_tables_overlapping(date, VIP_FLOOR, start, end)
        needs_review = reserved > max(0, outside_tables)
        result.append({
            "slot": slot,
            "label": label,
            "start": start.strftime("%H:%M"),
            "end": end.strftime("%H:%M"),
            "available": not taken and not past,
            "needs_review": needs_review,
            "deposit": str(settings.vip_deposit),
            "min_consumption": str(settings.vip_min_consumption_for(date)),
        })
    return result
