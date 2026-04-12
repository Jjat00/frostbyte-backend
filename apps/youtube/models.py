from django.db import models
from django.utils import timezone


class VideoRequest(models.Model):
    """Solicitud de video de YouTube de un cliente"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        QUEUED = "queued", "En cola"
        PLAYING = "playing", "Reproduciendo"
        COMPLETED = "completed", "Completada"
        CANCELLED = "cancelled", "Cancelada"

    title = models.CharField(
        max_length=300,
        verbose_name="Titulo del video",
    )
    channel_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Nombre del canal",
    )
    video_id = models.CharField(
        max_length=20,
        verbose_name="ID del video de YouTube",
        help_text="El ID del video (ej: dQw4w9WgXcQ)",
    )
    thumbnail = models.URLField(
        blank=True,
        verbose_name="Thumbnail del video",
    )
    duration = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Duracion",
        help_text="Duracion en formato ISO 8601 (ej: PT4M30S)",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Estado",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Notas adicionales",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creado",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Actualizado",
    )
    played_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Reproducido",
    )

    class Meta:
        verbose_name = "Solicitud de video"
        verbose_name_plural = "Solicitudes de videos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} - {self.channel_name}"

    def mark_as_queued(self):
        self.status = self.Status.QUEUED
        self.save(update_fields=["status", "updated_at"])

    def mark_as_playing(self):
        self.status = self.Status.PLAYING
        self.played_at = timezone.now()
        self.save(update_fields=["status", "played_at", "updated_at"])

    def mark_as_completed(self):
        self.status = self.Status.COMPLETED
        if not self.played_at:
            self.played_at = timezone.now()
        self.save(update_fields=["status", "played_at", "updated_at"])

    def mark_as_cancelled(self):
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "updated_at"])
