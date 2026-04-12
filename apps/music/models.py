from django.db import models
from django.utils import timezone


class SpotifyToken(models.Model):
    """Almacena los tokens de autenticación de Spotify para el local.
    Solo debe existir un registro (singleton) ya que es una sola cuenta de Spotify."""

    access_token = models.TextField()
    refresh_token = models.TextField()
    token_type = models.CharField(max_length=50, default="Bearer")
    expires_at = models.DateTimeField()
    scope = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Token de Spotify"
        verbose_name_plural = "Tokens de Spotify"

    def __str__(self):
        return f"Spotify Token (expira: {self.expires_at})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @classmethod
    def get_active_token(cls):
        """Obtiene el token activo (singleton)"""
        return cls.objects.first()


class MusicSettings(models.Model):
    """Configuracion global del modulo de musica (singleton).
    Determina que fuente de musica usan los clientes (Spotify o YouTube)."""

    class Source(models.TextChoices):
        SPOTIFY = "spotify", "Spotify"
        YOUTUBE = "youtube", "YouTube"

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.YOUTUBE,
        verbose_name="Fuente de musica",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracion de musica"
        verbose_name_plural = "Configuracion de musica"

    def __str__(self):
        return f"Fuente activa: {self.get_source_display()}"

    @classmethod
    def get_settings(cls):
        """Obtiene o crea la configuracion unica (singleton)"""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SongRequest(models.Model):
    """Solicitud de canción de un cliente"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        QUEUED = "queued", "En cola"
        PLAYING = "playing", "Reproduciendo"
        COMPLETED = "completed", "Completada"
        CANCELLED = "cancelled", "Cancelada"
        FAILED = "failed", "Fallida"

    song_name = models.CharField(
        max_length=200,
        verbose_name="Nombre de la canción",
    )
    artist_name = models.CharField(
        max_length=200,
        verbose_name="Nombre del artista",
    )
    spotify_track_uri = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="URI de Spotify",
        help_text="URI del track en Spotify (spotify:track:xxx)",
    )
    spotify_track_image = models.URLField(
        blank=True,
        verbose_name="Imagen del track",
    )
    spotify_track_duration_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Duración en ms",
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

