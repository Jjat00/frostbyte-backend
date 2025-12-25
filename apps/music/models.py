from django.db import models
from django.utils import timezone


class SongRequest(models.Model):
    """Solicitud de canción de un cliente"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PLAYING = "playing", "Reproduciendo"
        COMPLETED = "completed", "Completada"
        CANCELLED = "cancelled", "Cancelada"

    song_name = models.CharField(
        max_length=200,
        verbose_name="Nombre de la canción",
    )
    artist_name = models.CharField(
        max_length=200,
        verbose_name="Nombre del artista",
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
        help_text="Información adicional sobre la solicitud",
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
        verbose_name = "Solicitud de canción"
        verbose_name_plural = "Solicitudes de canciones"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.song_name} - {self.artist_name}"

    def mark_as_playing(self):
        """Marcar solicitud como reproduciendo"""
        self.status = self.Status.PLAYING
        self.played_at = timezone.now()
        self.save(update_fields=["status", "played_at", "updated_at"])

    def mark_as_completed(self):
        """Marcar solicitud como completada"""
        self.status = self.Status.COMPLETED
        if not self.played_at:
            self.played_at = timezone.now()
        self.save(update_fields=["status", "played_at", "updated_at"])

    def mark_as_cancelled(self):
        """Cancelar solicitud"""
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "updated_at"])

