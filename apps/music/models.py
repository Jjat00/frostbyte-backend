from django.db import models
from django.utils import timezone

from apps.search import normalize_text

from .genres import GENRE_CHOICES

# Pisos con sonido propio. Cada piso tiene su propia cuenta de Spotify.
FLOOR_CHOICES = [(2, "Piso 2"), (3, "Piso 3")]
DEFAULT_FLOOR = 2


class SpotifyToken(models.Model):
    """Almacena los tokens de autenticación de Spotify del local.
    Un registro por piso: cada piso tiene su propia cuenta de Spotify."""

    floor = models.PositiveSmallIntegerField(
        choices=FLOOR_CHOICES,
        default=DEFAULT_FLOOR,
        unique=True,
        verbose_name="Piso",
    )
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
        return f"Spotify Token piso {self.floor} (expira: {self.expires_at})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @classmethod
    def get_active_token(cls, floor=DEFAULT_FLOOR):
        """Obtiene el token de la cuenta de Spotify del piso indicado"""
        return cls.objects.filter(floor=floor).first()


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

    floor = models.PositiveSmallIntegerField(
        choices=FLOOR_CHOICES,
        default=DEFAULT_FLOOR,
        verbose_name="Piso",
        help_text="Piso donde sonará la canción (cada piso tiene su Spotify)",
    )
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



def primary_artist(artist_name):
    """El artista principal de una canción.

    Spotify entrega los colaboradores en un solo campo ("Feid, Granuja"), y el
    género de la canción es el del primero: el featuring no cambia de cajón a
    un corrido. Devuelve el nombre tal cual lo escribió Spotify, ya recortado.
    """
    if not artist_name:
        return ""
    for separador in (",", " feat.", " ft.", " x ", " & "):
        if separador in artist_name:
            artist_name = artist_name.split(separador)[0]
    return artist_name.strip()


class ArtistGenre(models.Model):
    """El género de un artista, clasificado una sola vez y cacheado.

    No viene de Spotify: la API dejó de exponer `genres` para esta aplicación
    (ver `apps/music/genres.py`). Lo pone el modelo de lenguaje con el comando
    `classify_artist_genres`, y cualquier error se corrige a mano desde el
    admin — `source` distingue una cosa de la otra para que una reclasificación
    no pise lo corregido por una persona.
    """

    class Source(models.TextChoices):
        AI = "ai", "Clasificado por IA"
        MANUAL = "manual", "Corregido a mano"

    artist_key = models.CharField(
        max_length=200,
        unique=True,
        verbose_name="Clave del artista",
        help_text="Nombre normalizado (sin tildes ni mayúsculas) usado para cruzar con las canciones",
    )
    artist_name = models.CharField(
        max_length=200,
        verbose_name="Artista",
        help_text="Nombre tal como lo devuelve Spotify",
    )
    genre = models.CharField(
        max_length=30,
        choices=GENRE_CHOICES,
        verbose_name="Género",
    )
    source = models.CharField(
        max_length=10,
        choices=Source.choices,
        default=Source.AI,
        verbose_name="Origen",
    )
    model_used = models.CharField(
        max_length=60,
        blank=True,
        verbose_name="Modelo",
        help_text="Modelo de lenguaje que hizo la clasificación",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Género de artista"
        verbose_name_plural = "Géneros de artistas"
        ordering = ["artist_name"]
        indexes = [models.Index(fields=["genre"])]

    def __str__(self):
        return f"{self.artist_name} → {self.get_genre_display()}"

    @staticmethod
    def key_for(artist_name):
        """La clave con la que se cruza un `SongRequest` con su género."""
        return normalize_text(primary_artist(artist_name))
