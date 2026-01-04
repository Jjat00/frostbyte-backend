from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string
import uuid


class Table(models.Model):
    """Mesa física con QR fijo"""
    
    table_number = models.IntegerField(
        unique=True,
        verbose_name="Número de mesa",
        help_text="Número de mesa (0=Barra, 1-5=Mesas)",
    )
    qr_code = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Código QR",
        help_text="Código único del QR físico de la mesa",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activa",
        help_text="Si la mesa está disponible para juegos",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mesa"
        verbose_name_plural = "Mesas"
        ordering = ["table_number"]

    def __str__(self):
        if self.table_number == 0:
            return f"Barra (QR: {self.qr_code})"
        return f"Mesa {self.table_number} (QR: {self.qr_code})"

    @staticmethod
    def generate_qr_code():
        """Genera un código QR único"""
        return f"FROSTBYTE-{get_random_string(8).upper()}"


class GameRoom(models.Model):
    """
    Sala de juego temporal asociada a una mesa con pedido activo.
    
    NOTA: Actualmente solo soporta juegos CHILL (sin apuestas).
    Los campos de apuestas están preparados para futuros juegos donde
    el perdedor paga productos de Frostbyte como apuesta.
    """
    
    class GameType(models.TextChoices):
        CHILL = "chill", "Chill (sin apuestas)"
        # Futuros tipos de juego con apuestas:
        # WAGER = "wager", "Con apuestas"
    
    class Status(models.TextChoices):
        WAITING = "waiting", "Esperando jugadores"
        CONFIGURING = "configuring", "Configurando juego"
        PLAYING = "playing", "Jugando"
        FINISHED = "finished", "Finalizada"
        EXPIRED = "expired", "Expirada"
        CANCELLED = "cancelled", "Cancelada"

    # Identificadores
    room_code = models.CharField(
        max_length=8,
        unique=True,
        verbose_name="Código de sala",
        help_text="Código único para compartir la sala",
    )
    room_link = models.CharField(
        max_length=200,
        unique=True,
        verbose_name="Link de sala",
        help_text="Link único para acceder a la sala",
    )

    # Tipo de juego
    game_type = models.CharField(
        max_length=20,
        choices=GameType.choices,
        default=GameType.CHILL,
        verbose_name="Tipo de juego",
        help_text="CHILL = sin apuestas, WAGER = con apuestas (futuro)",
    )

    # Relaciones
    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name="game_rooms",
        verbose_name="Mesa",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="game_rooms",
        verbose_name="Pedido",
        help_text="Pedido activo asociado a esta sala",
    )
    
    # Campos para apuestas (NULL para juegos CHILL)
    # Estos campos se usarán en futuros juegos donde el perdedor paga productos
    wager_product = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="game_room_wagers",
        verbose_name="Producto de apuesta",
        help_text="Producto que se apuesta (solo para juegos con apuestas, NULL para CHILL)",
    )
    wager_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Cantidad de apuesta",
        help_text="Cantidad del producto apostado (solo para juegos con apuestas)",
    )

    # Configuración del juego
    total_rounds = models.PositiveIntegerField(
        default=3,
        verbose_name="Total de rondas",
        help_text="Número de rondas configuradas (1, 3 o 5)",
    )
    current_round = models.PositiveIntegerField(
        default=0,
        verbose_name="Ronda actual",
        help_text="Ronda que se está jugando actualmente (0 = no iniciado)",
    )

    # Estado
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.WAITING,
        verbose_name="Estado",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Iniciada",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Finalizada",
    )
    expires_at = models.DateTimeField(
        verbose_name="Expira",
        help_text="Momento en que la sala expira por inactividad",
    )

    class Meta:
        verbose_name = "Sala de juego"
        verbose_name_plural = "Salas de juego"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Sala {self.room_code} - Mesa {self.table.table_number}"

    def save(self, *args, **kwargs):
        if not self.room_code:
            self.room_code = self._generate_room_code()
        if not self.room_link:
            self.room_link = self._generate_room_link()
        if not self.expires_at:
            # La sala expira después de 1 hora de inactividad
            self.expires_at = timezone.now() + timezone.timedelta(hours=1)
        super().save(*args, **kwargs)

    def _generate_room_code(self):
        """Genera un código de sala único de 8 caracteres"""
        code = get_random_string(8).upper()
        while GameRoom.objects.filter(room_code=code).exists():
            code = get_random_string(8).upper()
        return code

    def _generate_room_link(self):
        """Genera un link único para la sala"""
        link_uuid = uuid.uuid4().hex[:16]
        while GameRoom.objects.filter(room_link=link_uuid).exists():
            link_uuid = uuid.uuid4().hex[:16]
        return link_uuid

    @property
    def participant_count(self):
        """Número de participantes en la sala"""
        return self.participants.count()

    @property
    def is_expired(self):
        """Verifica si la sala ha expirado"""
        return timezone.now() > self.expires_at

    @property
    def can_play(self):
        """Verifica si la sala puede iniciar el juego"""
        # Verificar que el pedido permita el juego (no entregado y pagado)
        order_allows = not (self.order.status == self.order.Status.DELIVERED and self.order.is_paid)
        order_allows = order_allows and self.order.status != self.order.Status.CANCELLED
        
        return (
            self.status in [self.Status.WAITING, self.Status.CONFIGURING]
            and self.participant_count >= 2
            and order_allows
            and not self.is_expired
        )

    def extend_expiration(self):
        """Extiende el tiempo de expiración en 30 minutos"""
        self.expires_at = timezone.now() + timezone.timedelta(minutes=30)
        self.save(update_fields=["expires_at"])


class GameParticipant(models.Model):
    """Participante en una sala de juego"""
    
    # Relaciones
    room = models.ForeignKey(
        GameRoom,
        on_delete=models.CASCADE,
        related_name="participants",
        verbose_name="Sala",
    )

    # Información del participante (sin login)
    player_name = models.CharField(
        max_length=50,
        verbose_name="Nombre del jugador",
        help_text="Nombre que el jugador elige para jugar",
    )
    player_device_id = models.CharField(
        max_length=200,
        verbose_name="ID del dispositivo",
        help_text="Identificador único del dispositivo del jugador",
    )

    # Timestamps
    joined_at = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Participante"
        verbose_name_plural = "Participantes"
        unique_together = [["room", "player_device_id"]]
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.player_name} en {self.room.room_code}"

    def update_activity(self):
        """Actualiza el timestamp de última actividad"""
        self.last_active = timezone.now()
        self.save(update_fields=["last_active"])


class GameRound(models.Model):
    """Ronda individual del juego"""
    
    # Relaciones
    room = models.ForeignKey(
        GameRoom,
        on_delete=models.CASCADE,
        related_name="rounds",
        verbose_name="Sala",
    )

    # Información de la ronda
    round_number = models.PositiveIntegerField(
        verbose_name="Número de ronda",
    )
    signal_delay_ms = models.PositiveIntegerField(
        verbose_name="Delay de señal (ms)",
        help_text="Tiempo en milisegundos desde el inicio hasta la señal visual",
    )

    # Estado
    is_finished = models.BooleanField(
        default=False,
        verbose_name="Finalizada",
    )

    # Timestamps
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
        verbose_name = "Ronda"
        verbose_name_plural = "Rondas"
        unique_together = [["room", "round_number"]]
        ordering = ["round_number"]

    def __str__(self):
        return f"Ronda {self.round_number} - {self.room.room_code}"

    def mark_finished(self):
        """Marca la ronda como finalizada"""
        self.is_finished = True
        self.finished_at = timezone.now()
        self.save(update_fields=["is_finished", "finished_at"])


class GameRoundResult(models.Model):
    """
    Resultado de un participante en una ronda.
    
    NOTA: Para futuros juegos con apuestas, este modelo puede extenderse
    para rastrear quién perdió y debe pagar la apuesta (campo is_loser).
    """
    
    # Relaciones
    round = models.ForeignKey(
        GameRound,
        on_delete=models.CASCADE,
        related_name="results",
        verbose_name="Ronda",
    )
    participant = models.ForeignKey(
        GameParticipant,
        on_delete=models.CASCADE,
        related_name="round_results",
        verbose_name="Participante",
    )

    # Tiempos
    reaction_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Tiempo de reacción (ms)",
        help_text="Tiempo desde la señal visual hasta el toque en milisegundos",
    )
    tapped_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Tocado",
        help_text="Momento exacto en que el jugador tocó la pantalla",
    )

    # Estado
    is_disqualified = models.BooleanField(
        default=False,
        verbose_name="Descalificado",
        help_text="True si tocó antes de la señal (anticipación)",
    )
    did_not_tap = models.BooleanField(
        default=False,
        verbose_name="No tocó",
        help_text="True si no tocó la pantalla en esta ronda",
    )
    # Campo para futuros juegos con apuestas
    is_loser = models.BooleanField(
        default=False,
        verbose_name="Perdedor",
        help_text="True si este participante perdió la ronda/juego (para juegos con apuestas, futuro)",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Resultado de ronda"
        verbose_name_plural = "Resultados de ronda"
        unique_together = [["round", "participant"]]
        ordering = ["reaction_time_ms"]

    def __str__(self):
        if self.is_disqualified:
            return f"{self.participant.player_name} - Descalificado"
        if self.did_not_tap:
            return f"{self.participant.player_name} - No tocó"
        return f"{self.participant.player_name} - {self.reaction_time_ms}ms"

    @property
    def display_time(self):
        """Tiempo a mostrar (o mensaje si está descalificado/no tocó)"""
        if self.is_disqualified:
            return "Anticipación"
        if self.did_not_tap:
            return "Sin respuesta"
        return f"{self.reaction_time_ms}ms"
