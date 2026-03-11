from django.db import models
from django.utils import timezone
import uuid


class WordCategory(models.Model):
    """Categoría de palabras para el juego del impostor"""

    slug = models.SlugField(
        max_length=50,
        unique=True,
        verbose_name="Slug",
        help_text="Identificador único de la categoría (ej: deportes)",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="Nombre",
        help_text="Nombre para mostrar (ej: Deportes)",
    )
    icon = models.CharField(
        max_length=50,
        verbose_name="Icono",
        help_text="Nombre del icono Lucide (ej: Trophy)",
    )
    has_images = models.BooleanField(
        default=False,
        verbose_name="Tiene imágenes",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activa",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Categoría de palabras"
        verbose_name_plural = "Categorías de palabras"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def word_count(self):
        return self.words.filter(is_active=True).count()


class Word(models.Model):
    """Palabra con su trampa para el juego del impostor"""

    category = models.ForeignKey(
        WordCategory,
        on_delete=models.CASCADE,
        related_name="words",
        verbose_name="Categoría",
    )
    word = models.CharField(
        max_length=100,
        verbose_name="Palabra",
        help_text="La palabra real que ven los jugadores normales",
    )
    trap_word = models.CharField(
        max_length=100,
        verbose_name="Palabra trampa",
        help_text="Palabra similar que ve el impostor (ej: si la real es Fútbol, la trampa es Tenis)",
    )
    image_url = models.URLField(
        null=True,
        blank=True,
        verbose_name="URL de imagen",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activa",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Palabra"
        verbose_name_plural = "Palabras"
        ordering = ["word"]
        unique_together = [["category", "word"]]

    def __str__(self):
        return f"{self.word} → {self.trap_word} ({self.category.name})"


class ImpostorGameSession(models.Model):
    """Sesión completa de una partida del impostor"""

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "En progreso"
        COMPLETED = "completed", "Completada"
        ABANDONED = "abandoned", "Abandonada"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    total_rounds = models.PositiveIntegerField(
        verbose_name="Total de rondas",
    )
    impostor_count = models.PositiveIntegerField(
        default=1,
        verbose_name="Cantidad de impostores",
    )
    hint_enabled = models.BooleanField(
        default=False,
        verbose_name="Pista habilitada",
    )
    trap_word_enabled = models.BooleanField(
        default=True,
        verbose_name="Palabra trampa habilitada",
    )
    spy_enabled = models.BooleanField(
        default=False,
        verbose_name="Rol espía habilitado",
    )
    turn_timer_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Timer por turno (segundos)",
    )
    ai_words_used = models.BooleanField(
        default=False,
        verbose_name="Palabras IA usadas",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
        verbose_name="Estado",
    )
    started_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Iniciada",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Finalizada",
    )

    class Meta:
        verbose_name = "Sesión de Impostor"
        verbose_name_plural = "Sesiones de Impostor"
        ordering = ["-started_at"]

    def __str__(self):
        return f"Sesión {str(self.id)[:8]} - {self.player_count} jugadores - {self.status}"

    @property
    def player_count(self):
        return self.players.count()

    @property
    def duration_seconds(self):
        if self.finished_at and self.started_at:
            return int((self.finished_at - self.started_at).total_seconds())
        return None


class ImpostorPlayer(models.Model):
    """Jugador en una sesión del impostor"""

    session = models.ForeignKey(
        ImpostorGameSession,
        on_delete=models.CASCADE,
        related_name="players",
        verbose_name="Sesión",
    )
    name = models.CharField(
        max_length=50,
        verbose_name="Nombre",
    )
    color = models.CharField(
        max_length=20,
        verbose_name="Color",
        help_text="Color elegido por el jugador (ej: #ff00d4)",
    )
    total_score = models.IntegerField(
        default=0,
        verbose_name="Puntuación total",
    )
    joined_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Se unió",
    )

    class Meta:
        verbose_name = "Jugador Impostor"
        verbose_name_plural = "Jugadores Impostor"
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.name} ({self.total_score} pts)"


class ImpostorRound(models.Model):
    """Ronda individual de una sesión del impostor"""

    session = models.ForeignKey(
        ImpostorGameSession,
        on_delete=models.CASCADE,
        related_name="rounds",
        verbose_name="Sesión",
    )
    round_number = models.PositiveIntegerField(
        verbose_name="Número de ronda",
    )
    category_slug = models.CharField(
        max_length=50,
        verbose_name="Categoría",
    )
    word = models.CharField(
        max_length=100,
        verbose_name="Palabra real",
    )
    trap_word = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Palabra trampa",
    )
    impostor_caught = models.BooleanField(
        default=False,
        verbose_name="Impostor descubierto",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creada",
    )

    class Meta:
        verbose_name = "Ronda Impostor"
        verbose_name_plural = "Rondas Impostor"
        unique_together = [["session", "round_number"]]
        ordering = ["round_number"]

    def __str__(self):
        caught = "atrapado" if self.impostor_caught else "libre"
        return f"Ronda {self.round_number} - {self.word} ({caught})"


class ImpostorRoundPlayer(models.Model):
    """Resultado de un jugador en una ronda específica"""

    class Role(models.TextChoices):
        NORMAL = "normal", "Normal"
        IMPOSTOR = "impostor", "Impostor"
        SPY = "spy", "Espía"

    round = models.ForeignKey(
        ImpostorRound,
        on_delete=models.CASCADE,
        related_name="player_results",
        verbose_name="Ronda",
    )
    player = models.ForeignKey(
        ImpostorPlayer,
        on_delete=models.CASCADE,
        related_name="round_results",
        verbose_name="Jugador",
    )
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.NORMAL,
        verbose_name="Rol",
    )
    voted_for = models.ForeignKey(
        ImpostorPlayer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="votes_received_set",
        verbose_name="Votó por",
    )
    votes_received = models.PositiveIntegerField(
        default=0,
        verbose_name="Votos recibidos",
    )
    points_earned = models.IntegerField(
        default=0,
        verbose_name="Puntos ganados",
    )
    guessed_word = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Palabra adivinada",
        help_text="Si el impostor intentó adivinar la palabra real",
    )

    class Meta:
        verbose_name = "Resultado jugador por ronda"
        verbose_name_plural = "Resultados jugadores por ronda"
        unique_together = [["round", "player"]]

    def __str__(self):
        return f"{self.player.name} - {self.role} - {self.points_earned} pts"
