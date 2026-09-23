"""Concursos con inscripción paga (el primero: disfraces de Halloween 2026).

Flujo decidido el 2026-09-23:
- El cliente se inscribe en la app con su cuenta de Google. Declara ser mayor
  de edad y deja su usuario de Instagram.
- La inscripción cuesta `entry_fee` y se paga en la barra del local. Ahí el
  staff pide la cédula (la edad solo se verifica en persona) y marca el pago.
- El staff también revisa que la cuenta siga a Frostbyte en Instagram: desde
  la web no hay forma de comprobarlo, así que es una marca manual.
- Con las tres cosas (cuenta, pago y seguimiento) la participación queda
  confirmada. Nada se confirma solo.

El modelo es genérico a propósito: el próximo concurso es otra fila de
Contest, no otra app.
"""
import re
from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone

# Formato literal de un usuario de Instagram: letras, números, punto y guion
# bajo, hasta 30 caracteres.
INSTAGRAM_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def normalize_instagram_handle(value):
    """'@Frostbyte.Col ' -> 'frostbyte.col'. Acepta también la URL del perfil."""
    handle = (value or "").strip()
    handle = re.sub(r"^(https?://)?(www\.)?instagram\.com/", "", handle,
                    flags=re.IGNORECASE)
    return handle.strip("/").lstrip("@").strip().lower()


class Contest(models.Model):
    """Un concurso con inscripción previa (disfraces, karaoke, etc.)."""

    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=120, verbose_name="Nombre")
    description = models.TextField(
        blank=True, default="", verbose_name="Descripción",
        help_text="Texto de la página pública del concurso",
    )
    event_date = models.DateField(
        null=True, blank=True, verbose_name="Fecha del concurso")
    event_time = models.TimeField(
        null=True, blank=True, verbose_name="Hora del concurso")
    prize = models.CharField(
        max_length=200, blank=True, default="", verbose_name="Premio",
        help_text="Vacío mientras no esté definido: la página no lo menciona",
    )
    entry_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("10000.00"),
        verbose_name="Valor de la inscripción",
    )
    min_age = models.PositiveSmallIntegerField(
        default=18, verbose_name="Edad mínima")
    requires_instagram_follow = models.BooleanField(
        default=True, verbose_name="Exige seguir a Frostbyte en Instagram")
    payment_instructions = models.TextField(
        default=(
            "Paga la inscripción en la barra de Frostbyte y lleva tu cédula: "
            "ahí confirmamos tu pago y tu edad."
        ),
        verbose_name="Instrucciones de pago",
    )

    is_published = models.BooleanField(
        default=False, verbose_name="Visible en la app",
        help_text="Apagado, la página pública y el anuncio de la carta no existen",
    )
    registrations_open = models.BooleanField(
        default=False, verbose_name="Inscripciones abiertas")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Concurso"
        verbose_name_plural = "Concursos"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @classmethod
    def current_public(cls):
        """El concurso que la app muestra: el más reciente publicado."""
        return cls.objects.filter(is_published=True).first()

    @classmethod
    def current_for_staff(cls):
        """El concurso que ve el staff: el más reciente, publicado o no."""
        return cls.objects.first()


class ContestEntry(models.Model):
    """Inscripción de un cliente (cuenta de Google) a un concurso."""

    class Status(models.TextChoices):
        # Falta el pago en barra, el seguimiento en Instagram o ambos
        PENDING = "pending", "Pendiente"
        CONFIRMED = "confirmed", "Confirmada"
        CANCELLED = "cancelled", "Cancelada"

    contest = models.ForeignKey(
        Contest, on_delete=models.CASCADE, related_name="entries",
        verbose_name="Concurso",
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE,
        related_name="contest_entries", verbose_name="Cuenta",
    )
    number = models.PositiveIntegerField(
        verbose_name="Número de inscripción",
        help_text="Consecutivo dentro del concurso; lo dice el cliente en la barra",
    )
    full_name = models.CharField(max_length=100, verbose_name="Nombre")
    phone = models.CharField(max_length=20, verbose_name="Celular")
    instagram_handle = models.CharField(
        max_length=30, verbose_name="Usuario de Instagram")
    costume = models.CharField(
        max_length=120, blank=True, default="", verbose_name="Disfraz",
        help_text="Opcional: de qué piensa venir",
    )
    declared_adult = models.BooleanField(
        default=False, verbose_name="Declaró ser mayor de edad")

    paid = models.BooleanField(default=False, verbose_name="Pagó")
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name="Pago registrado por",
    )
    follows_instagram = models.BooleanField(
        default=False, verbose_name="Sigue en Instagram")
    instagram_checked_at = models.DateTimeField(null=True, blank=True)
    instagram_checked_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name="Instagram revisado por",
    )
    cancelled = models.BooleanField(default=False, verbose_name="Cancelada")

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
        editable=False, verbose_name="Estado",
        help_text="Se calcula solo a partir del pago, Instagram y la cancelación",
    )
    staff_notes = models.TextField(
        blank=True, default="", verbose_name="Notas del staff")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Inscripción"
        verbose_name_plural = "Inscripciones"
        ordering = ["contest", "number"]
        constraints = [
            # Una inscripción viva por cuenta; tras cancelar puede volver
            models.UniqueConstraint(
                fields=["contest", "user"],
                condition=Q(cancelled=False),
                name="contest_entry_one_active_per_user",
            ),
            models.UniqueConstraint(
                fields=["contest", "number"],
                name="contest_entry_unique_number",
            ),
        ]

    def __str__(self):
        return f"#{self.number} {self.full_name} · {self.contest} ({self.get_status_display()})"

    def compute_status(self):
        if self.cancelled:
            return self.Status.CANCELLED
        follows_ok = self.follows_instagram or not self.contest.requires_instagram_follow
        if self.paid and follows_ok:
            return self.Status.CONFIRMED
        return self.Status.PENDING

    def save(self, *args, **kwargs):
        self.status = self.compute_status()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "status", "updated_at"}
        super().save(*args, **kwargs)

    def set_paid(self, value, by):
        self.paid = value
        self.paid_at = timezone.now() if value else None
        self.paid_by = by if value else None

    def set_follows_instagram(self, value, by):
        self.follows_instagram = value
        self.instagram_checked_at = timezone.now() if value else None
        self.instagram_checked_by = by if value else None
