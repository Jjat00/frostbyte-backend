"""Reservas de mesas, grupos grandes y Sala VIP (piso 3).

Modelo de negocio (decidido en julio 2026):
- Mesa (1 a `table_max_party` personas): gratis, sin anticipo. Tolerancia de
  llegada de `table_hold_minutes`; pasada, la reserva se libera (no_show).
- Grupo grande (más de `table_max_party` personas, varias mesas juntas):
  anticipo pequeño abonable al consumo, sin consumo mínimo.
- Sala VIP (exclusividad del salón del piso 3): anticipo abonable + consumo
  mínimo (más alto viernes/sábado). Se reserva por turnos (tarde/noche), no
  por hora continua. Extensión libre si no hay reserva después.

Toda reserva creada por el cliente entra como SOLICITUD (pending_review):
el staff siempre da la confirmación final. Las que crea el staff a mano
nacen confirmadas (o esperando anticipo si el tipo lo lleva).

El layout físico de la sala es configurable: `vip_room_table_count` indica
cuántas mesas del pool del piso 3 quedan DENTRO de la sala (0 = la sala es un
espacio aparte y no compite con las mesas del piso).
"""
from datetime import time
from decimal import Decimal

from django.db import models
from django.utils import timezone

VIP_FLOOR = 3

# Días con consumo mínimo de fin de semana (weekday(): 4=viernes, 5=sábado)
WEEKEND_DAYS = (4, 5)


class ReservationSettings(models.Model):
    """Configuración del módulo de reservas (singleton, editable por el staff).

    Sigue el patrón de orders.StoreSettings: una sola fila (pk=1) para poder
    ajustar montos, franjas y cupos sin redeploy.
    """

    reservations_enabled = models.BooleanField(
        default=False,
        verbose_name="Reservas en línea activas",
        help_text="Interruptor general para habilitar/pausar las reservas desde la app",
    )

    # ------------------------------------------------------------------ mesas
    table_max_party = models.PositiveSmallIntegerField(
        default=6,
        verbose_name="Personas máx. por reserva de mesa",
        help_text="Por encima de este número la reserva pasa a ser de grupo grande (con anticipo)",
    )
    seats_per_table = models.PositiveSmallIntegerField(
        default=4,
        verbose_name="Puestos por mesa",
        help_text="Se usa para estimar cuántas mesas necesita un grupo",
    )
    max_reserved_tables_per_slot = models.PositiveSmallIntegerField(
        default=3,
        verbose_name="Mesas reservables por piso y franja",
        help_text="Tope de mesas reservadas a la vez por piso; el resto queda para walk-ins",
    )
    table_hold_minutes = models.PositiveSmallIntegerField(
        default=15,
        verbose_name="Tolerancia de llegada (min)",
        help_text="Minutos de gracia antes de liberar una mesa reservada que no llegó",
    )
    table_window_minutes = models.PositiveSmallIntegerField(
        default=120,
        verbose_name="Ventana estimada por reserva (min)",
        help_text="Tiempo estimado que ocupa una mesa reservada, para calcular choques",
    )
    reservable_from = models.TimeField(
        default=time(15, 0),
        verbose_name="Reservas desde",
        help_text="Hora de llegada más temprana que se puede reservar",
    )
    reservable_until = models.TimeField(
        default=time(21, 30),
        verbose_name="Reservas hasta",
        help_text="Hora de llegada más tardía que se puede reservar",
    )
    max_advance_days = models.PositiveSmallIntegerField(
        default=30,
        verbose_name="Antelación máxima (días)",
        help_text="Hasta cuántos días en el futuro se puede reservar",
    )

    # ----------------------------------------------------------- grupo grande
    group_deposit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("15000.00"),
        verbose_name="Anticipo grupo grande",
        help_text="Anticipo abonable al consumo para grupos que ocupan varias mesas",
    )

    # -------------------------------------------------------------- Sala VIP
    vip_capacity = models.PositiveSmallIntegerField(
        default=15,
        verbose_name="Capacidad Sala VIP",
    )
    vip_room_table_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Mesas del piso 3 dentro de la sala",
        help_text=(
            "Cuántas mesas del pool del piso 3 quedan dentro de la Sala VIP y se "
            "bloquean al reservarla. 0 = la sala es un espacio aparte."
        ),
    )
    vip_deposit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("40000.00"),
        verbose_name="Anticipo Sala VIP",
        help_text="Anticipo abonable al consumo para reservar la sala",
    )
    vip_min_consumption_weekday = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("100000.00"),
        verbose_name="Consumo mínimo entre semana",
    )
    vip_min_consumption_weekend = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("150000.00"),
        verbose_name="Consumo mínimo viernes/sábado",
    )
    vip_slot_1_start = models.TimeField(
        default=time(15, 0), verbose_name="Turno tarde: inicio")
    vip_slot_1_end = models.TimeField(
        default=time(18, 0), verbose_name="Turno tarde: fin")
    vip_slot_2_start = models.TimeField(
        default=time(19, 0), verbose_name="Turno noche: inicio")
    vip_slot_2_end = models.TimeField(
        default=time(23, 0), verbose_name="Turno noche: fin")

    payment_instructions = models.TextField(
        default=(
            "Envía el anticipo por Nequi al 316 427 7879 y conserva el "
            "comprobante. Confirmamos tu reserva apenas verifiquemos el pago."
        ),
        verbose_name="Instrucciones de pago del anticipo",
        help_text="Se muestran al cliente al crear una reserva con anticipo",
    )

    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado")

    class Meta:
        verbose_name = "Configuración de reservas"
        verbose_name_plural = "Configuración de reservas"

    def __str__(self):
        estado = "activas" if self.reservations_enabled else "inactivas"
        return f"Configuración de reservas ({estado})"

    def save(self, *args, **kwargs):
        # Fuerza singleton: siempre la misma fila
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Devuelve la única instancia de configuración, creándola si no existe."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    # ------------------------------------------------------------- helpers
    def vip_min_consumption_for(self, date):
        """Consumo mínimo de la sala según el día (vie/sáb más alto)."""
        if date.weekday() in WEEKEND_DAYS:
            return self.vip_min_consumption_weekend
        return self.vip_min_consumption_weekday

    def vip_slot_bounds(self, slot):
        """(inicio, fin) del turno 1 (tarde) o 2 (noche)."""
        if slot == 1:
            return self.vip_slot_1_start, self.vip_slot_1_end
        return self.vip_slot_2_start, self.vip_slot_2_end


class Reservation(models.Model):
    """Una reserva de mesa, grupo grande o de la Sala VIP completa."""

    class Type(models.TextChoices):
        TABLE = "table", "Mesa"
        GROUP = "group", "Grupo grande"
        VIP_ROOM = "vip_room", "Sala VIP"

    class Status(models.TextChoices):
        # Aprobada por el staff pero con anticipo por pagar y verificar
        PENDING_PAYMENT = "pending_payment", "Pendiente de anticipo"
        # Solicitud del cliente esperando la aprobación del staff
        PENDING_REVIEW = "pending_review", "En revisión"
        CONFIRMED = "confirmed", "Confirmada"
        SEATED = "seated", "En la mesa"
        COMPLETED = "completed", "Completada"
        CANCELLED = "cancelled", "Cancelada"
        NO_SHOW = "no_show", "No llegó"

    class Source(models.TextChoices):
        APP = "app", "App"
        WHATSAPP = "whatsapp", "WhatsApp"
        STAFF = "staff", "Staff"

    # Estados que bloquean disponibilidad (una cancelada o no_show libera cupo)
    BLOCKING_STATUSES = (
        Status.PENDING_PAYMENT,
        Status.PENDING_REVIEW,
        Status.CONFIRMED,
        Status.SEATED,
    )

    reservation_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        verbose_name="Tipo",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CONFIRMED,
        verbose_name="Estado",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.APP,
        verbose_name="Origen",
    )

    user = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reservations",
        verbose_name="Cliente (cuenta)",
    )
    customer_name = models.CharField(
        max_length=100, verbose_name="Nombre del cliente")
    customer_phone = models.CharField(
        max_length=20, verbose_name="Teléfono del cliente")

    party_size = models.PositiveSmallIntegerField(verbose_name="Personas")
    floor = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Piso",
        help_text="La Sala VIP siempre es piso 3",
    )
    tables_needed = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Mesas que ocupa",
        help_text="Calculado a partir del tamaño del grupo; la sala no usa este campo",
    )

    date = models.DateField(verbose_name="Fecha")
    start_time = models.TimeField(
        verbose_name="Hora de llegada / inicio de turno")
    end_time = models.TimeField(
        verbose_name="Fin estimado / fin de turno")
    vip_slot = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=((1, "Tarde"), (2, "Noche")),
        verbose_name="Turno de la sala",
    )

    deposit_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Anticipo",
    )
    min_consumption = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Consumo mínimo",
    )

    # La mesa concreta la asigna el staff al sentar al grupo, nunca el cliente
    table = models.ForeignKey(
        "orders.Table",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reservations",
        verbose_name="Mesa asignada",
    )

    notes = models.TextField(
        blank=True, default="", verbose_name="Notas del cliente")
    staff_notes = models.TextField(
        blank=True, default="", verbose_name="Notas del staff")

    status_changed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Estado cambiado el")
    status_changed_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Estado cambiado por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Reserva"
        verbose_name_plural = "Reservas"
        ordering = ["date", "start_time", "id"]
        indexes = [
            models.Index(fields=["date", "floor"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return (
            f"{self.get_reservation_type_display()} · {self.customer_name} · "
            f"{self.date} {self.start_time:%H:%M} ({self.get_status_display()})"
        )

    @property
    def is_blocking(self):
        """¿Esta reserva sigue ocupando cupo en el cálculo de disponibilidad?"""
        return self.status in self.BLOCKING_STATUSES

    def set_status(self, new_status, by=None):
        """Cambia el estado dejando rastro de quién y cuándo."""
        self.status = new_status
        self.status_changed_at = timezone.now()
        self.status_changed_by = by
        self.save(update_fields=[
            "status", "status_changed_at", "status_changed_by", "updated_at",
        ])
