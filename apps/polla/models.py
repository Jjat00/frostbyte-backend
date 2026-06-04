"""Modelos de la Polla Mundialista 2026.

Juego de pronosticos del Mundial: el cliente (login con Google) pronostica el
marcador de cada partido, elige "menciones" de torneo (campeon, goleador, ...)
y compite en una tabla de posiciones. El puntaje se recalcula automaticamente
cuando llegan los resultados (comando ``polla_sync``).

Sistema de puntos (definido en el landing):
  - Marcador exacto ............ 3 pts
  - Resultado correcto ......... 1 pt
  - Incorrecto ................. 0 pts
"""
from django.conf import settings
from django.db import IntegrityError, models
from django.utils import timezone
from django.utils.crypto import get_random_string

# ── Constantes de puntaje ────────────────────────────────────────────────
POINTS_EXACT = 3
POINTS_OUTCOME = 1
POINTS_NONE = 0

# ── Referidos (invitar amigos) ───────────────────────────────────────────
# 1 punto por cada amigo que se une y juega, hasta un tope de 5 amigos.
REFERRAL_POINTS_PER = 1
REFERRAL_CAP = 5
# Alfabeto sin caracteres ambiguos (sin 0/O/1/I/L) para códigos legibles.
REFERRAL_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REFERRAL_CODE_LEN = 6


def generate_referral_code():
    """Genera un código de invitación corto y legible (no garantiza unicidad).

    La unicidad la resuelve la constraint ``unique`` de la BD: quien guarda debe
    reintentar ante ``IntegrityError`` (ver ``UserScore.save``). Función pura y
    reutilizable: la usan ``UserScore.save``, el endpoint perezoso y la data
    migration de backfill (que corre con el modelo histórico, sin ``save``).
    """
    return get_random_string(REFERRAL_CODE_LEN, allowed_chars=REFERRAL_ALPHABET)


def outcome_of(home, away):
    """'home' | 'away' | 'draw' a partir de un marcador. None si falta dato."""
    if home is None or away is None:
        return None
    if home > away:
        return "home"
    if away > home:
        return "away"
    return "draw"


class Confederation(models.TextChoices):
    UEFA = "UEFA", "UEFA"
    CONMEBOL = "CONMEBOL", "CONMEBOL"
    CONCACAF = "CONCACAF", "CONCACAF"
    CAF = "CAF", "CAF"
    AFC = "AFC", "AFC"
    OFC = "OFC", "OFC"


class Tournament(models.Model):
    """Metadatos del torneo activo (pitazo inicial, premio, ids de API)."""

    name = models.CharField(max_length=120, default="Mundial 2026", verbose_name="Nombre")
    slug = models.SlugField(max_length=120, unique=True, default="mundial-2026")
    kickoff = models.DateTimeField(verbose_name="Pitazo inicial")
    prize = models.CharField(max_length=120, default="$500.000", verbose_name="Premio")
    prize_note = models.CharField(
        max_length=200, blank=True, default="En efectivo · un solo ganador"
    )
    api_league_id = models.PositiveIntegerField(default=1, verbose_name="API-Football league id")
    api_season = models.PositiveIntegerField(default=2026, verbose_name="API-Football season")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Torneo"
        verbose_name_plural = "Torneos"
        ordering = ["-is_active", "-kickoff"]

    def __str__(self):
        return self.name

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).order_by("-kickoff").first()


class Group(models.Model):
    """Grupo del Mundial (A-L)."""

    letter = models.CharField(max_length=1, unique=True, verbose_name="Letra")
    name = models.CharField(max_length=40, verbose_name="Nombre")
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Grupo"
        verbose_name_plural = "Grupos"
        ordering = ["display_order", "letter"]

    def __str__(self):
        return self.name or f"Grupo {self.letter}"


class Team(models.Model):
    """Seleccion nacional."""

    code = models.CharField(max_length=8, unique=True, verbose_name="Código")
    name = models.CharField(max_length=60, verbose_name="Nombre")
    iso2 = models.CharField(max_length=8, blank=True, verbose_name="ISO-2 (bandera)")
    confederation = models.CharField(
        max_length=10, choices=Confederation.choices, blank=True
    )
    group = models.ForeignKey(
        Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="teams"
    )
    is_host = models.BooleanField(default=False, verbose_name="Anfitrión")
    recent_form = models.JSONField(
        default=list, blank=True,
        help_text='Forma reciente: lista como ["w","w","d","l","w"]',
    )
    api_team_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "Selección"
        verbose_name_plural = "Selecciones"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Player(models.Model):
    """Jugador (opciones para Goleador / MVP / Guante de Oro)."""

    name = models.CharField(max_length=80, verbose_name="Nombre")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    is_keeper = models.BooleanField(default=False, verbose_name="Arquero")
    api_player_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "Jugador"
        verbose_name_plural = "Jugadores"
        ordering = ["name"]
        unique_together = [("name", "team")]

    def __str__(self):
        return f"{self.name} — {self.team.code}"


class Match(models.Model):
    """Partido del torneo."""

    class Stage(models.TextChoices):
        GROUP = "group", "Fase de grupos"
        R32 = "r32", "Dieciseisavos"
        R16 = "r16", "Octavos"
        QF = "qf", "Cuartos"
        SF = "sf", "Semifinal"
        THIRD = "third", "Tercer puesto"
        FINAL = "final", "Final"

    class Status(models.TextChoices):
        UPCOMING = "upcoming", "Próximo"
        LIVE = "live", "En vivo"
        FINISHED = "finished", "Finalizado"

    number = models.PositiveIntegerField(unique=True, verbose_name="N.º de partido")
    slug = models.SlugField(max_length=20, unique=True, help_text="ID estable, p.ej. m1")
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.GROUP)
    group = models.ForeignKey(
        Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="matches"
    )
    round_label = models.CharField(max_length=40, blank=True, verbose_name="Jornada/Ronda")

    home_team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="home_matches"
    )
    away_team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="away_matches"
    )
    home_placeholder = models.CharField(max_length=40, blank=True, help_text='p.ej. "1A"')
    away_placeholder = models.CharField(max_length=40, blank=True)

    kickoff = models.DateTimeField(verbose_name="Hora (UTC)")
    venue_city = models.CharField(max_length=80, blank=True)
    venue_stadium = models.CharField(max_length=120, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPCOMING)
    minute = models.PositiveIntegerField(null=True, blank=True, verbose_name="Minuto")
    home_score = models.PositiveIntegerField(null=True, blank=True)
    away_score = models.PositiveIntegerField(null=True, blank=True)
    # Quién avanzó realmente. En eliminatorias define el ganador aunque el
    # marcador quede empatado (penales); lo puebla el admin o polla_sync.
    winner_team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="won_matches",
        verbose_name="Ganador (avanza)",
    )

    featured = models.BooleanField(default=False, verbose_name="Destacado")
    api_fixture_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Partido"
        verbose_name_plural = "Partidos"
        ordering = ["kickoff", "number"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["stage"]),
            models.Index(fields=["kickoff"]),
        ]

    def __str__(self):
        h = self.home_team.code if self.home_team else (self.home_placeholder or "?")
        a = self.away_team.code if self.away_team else (self.away_placeholder or "?")
        return f"#{self.number} {h} vs {a}"

    @property
    def is_finished(self):
        return self.status == self.Status.FINISHED

    @property
    def is_locked(self):
        """True si ya no se puede pronosticar (empezo o paso el pitazo)."""
        if self.status != self.Status.UPCOMING:
            return True
        return timezone.now() >= self.kickoff

    @property
    def outcome(self):
        return outcome_of(self.home_score, self.away_score)


class Prediction(models.Model):
    """Pronostico de un usuario para un partido."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="polla_predictions"
    )
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="predictions")
    home_score = models.PositiveIntegerField()
    away_score = models.PositiveIntegerField()

    points_earned = models.PositiveIntegerField(default=0)
    is_exact = models.BooleanField(default=False)
    is_correct_outcome = models.BooleanField(default=False)
    scored = models.BooleanField(default=False, help_text="Ya se calculo el puntaje")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pronóstico"
        verbose_name_plural = "Pronósticos"
        ordering = ["-updated_at"]
        unique_together = [("user", "match")]

    def __str__(self):
        return f"{self.user} · #{self.match.number}: {self.home_score}-{self.away_score}"

    @property
    def outcome(self):
        return outcome_of(self.home_score, self.away_score)


class Award(models.Model):
    """Mención de torneo (campeón, goleador, etc.)."""

    class AwardType(models.TextChoices):
        TEAM = "team", "Selección"
        PLAYER = "player", "Jugador"
        KEEPER = "keeper", "Arquero"

    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=40)
    hint = models.CharField(max_length=120, blank=True)
    points = models.PositiveIntegerField(default=0)
    award_type = models.CharField(max_length=10, choices=AwardType.choices)
    display_order = models.PositiveIntegerField(default=0)

    resolved = models.BooleanField(default=False, verbose_name="Resuelto")
    resolved_team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_player = models.ForeignKey(
        Player, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "Mención"
        verbose_name_plural = "Menciones"
        ordering = ["display_order"]

    def __str__(self):
        return self.title


class AwardPick(models.Model):
    """Elección de un usuario para una mención."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="polla_award_picks"
    )
    award = models.ForeignKey(Award, on_delete=models.CASCADE, related_name="picks")
    team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    player = models.ForeignKey(
        Player, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    points_earned = models.PositiveIntegerField(default=0)
    scored = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Elección de mención"
        verbose_name_plural = "Elecciones de menciones"
        ordering = ["-updated_at"]
        unique_together = [("user", "award")]

    def __str__(self):
        choice = self.team.code if self.team else (self.player.name if self.player else "?")
        return f"{self.user} · {self.award.code}: {choice}"


class BracketPick(models.Model):
    """Pick de avance del usuario en un cruce de eliminación.

    El bracket es ENCADENADO: los dieciseisavos se siembran con los equipos
    que el usuario clasificó según sus marcadores de grupo, y de ahí en
    adelante cada cruce toma los ganadores que el propio usuario fue eligiendo.
    ``winner_team`` es el equipo que el usuario cree que avanza del ``match``.
    Puntúa comparándose con el ganador real del cruce (ver ``bracket.py``).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="polla_bracket_picks"
    )
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="bracket_picks")
    winner_team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    points_earned = models.PositiveIntegerField(default=0)
    scored = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pick de bracket"
        verbose_name_plural = "Picks de bracket"
        ordering = ["match__number"]
        unique_together = [("user", "match")]

    def __str__(self):
        w = self.winner_team.code if self.winner_team else "?"
        return f"{self.user} · #{self.match.number} → {w}"


class Mission(models.Model):
    """Reto que otorga puntos extra o insignias."""

    class Kind(models.TextChoices):
        FIRST_PREDICTION = "first_prediction", "Primer pronóstico"
        EXACT_SCORES = "exact_scores", "Marcadores exactos"
        TEAM_MATCHES = "team_matches", "Partidos de un equipo"
        STREAK = "streak", "Racha de aciertos"
        GROUP_STAGE_COMPLETE = "group_stage_complete", "Fase de grupos completa"
        INVITE = "invite", "Invitar amigos"

    code = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=60)
    desc = models.CharField(max_length=200)
    reward = models.CharField(max_length=40, help_text='Texto, p.ej. "+25 pts"')
    kind = models.CharField(max_length=30, choices=Kind.choices)
    target = models.PositiveIntegerField(default=1, verbose_name="Meta")
    bonus_points = models.PositiveIntegerField(
        default=0, help_text="Puntos que suma al ranking al completarse"
    )
    param = models.CharField(max_length=20, blank=True, help_text='p.ej. código de equipo "COL"')
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Misión"
        verbose_name_plural = "Misiones"
        ordering = ["display_order"]

    def __str__(self):
        return self.title


class UserMission(models.Model):
    """Progreso de un usuario en una misión."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="polla_missions"
    )
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="user_progress")
    progress = models.PositiveIntegerField(default=0)
    done = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Progreso de misión"
        verbose_name_plural = "Progreso de misiones"
        unique_together = [("user", "mission")]

    def __str__(self):
        return f"{self.user} · {self.mission.code}: {self.progress}/{self.mission.target}"


class UserScore(models.Model):
    """Puntaje, posición e identidad de referido cacheados por usuario.

    Se recalcula en cada sync. Además del puntaje, guarda el ``referral_code``
    personal del cliente (su enlace de invitación) y los puntos ganados por
    invitar amigos.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="polla_score"
    )
    points = models.PositiveIntegerField(default=0, verbose_name="Puntos totales")
    match_points = models.PositiveIntegerField(default=0)
    award_points = models.PositiveIntegerField(default=0)
    mission_points = models.PositiveIntegerField(default=0)
    bracket_points = models.PositiveIntegerField(default=0)
    referral_points = models.PositiveIntegerField(default=0)
    exact_hits = models.PositiveIntegerField(default=0)
    correct_hits = models.PositiveIntegerField(default=0)
    predicted = models.PositiveIntegerField(default=0)
    position = models.PositiveIntegerField(default=0)
    # Código personal de invitación. null (no "") para que ``unique`` admita
    # varias filas "sin código aún" (p.ej. creadas por bulk_create).
    referral_code = models.CharField(
        max_length=8, unique=True, null=True, blank=True, db_index=True,
        verbose_name="Código de invitación",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Puntaje de usuario"
        verbose_name_plural = "Puntajes de usuarios"
        ordering = ["-points", "-exact_hits"]

    def __str__(self):
        return f"{self.user} · {self.points} pts (#{self.position})"

    @property
    def referral_qualified_count(self):
        """Cantidad de amigos que ya calificaron (hicieron su 1er pronóstico)."""
        return self.user.referrals_made.filter(qualified=True).count()

    def save(self, *args, **kwargs):
        """Autogenera ``referral_code`` único si falta, reintentando colisiones.

        La constraint ``unique`` de la BD es el árbitro final de la unicidad
        (un ``exists()`` previo tendría una race). Ojo: ``bulk_create`` NO pasa
        por aquí, por eso el endpoint de referidos también lo genera de forma
        perezosa.
        """
        if self.referral_code:
            return super().save(*args, **kwargs)

        for _ in range(5):
            self.referral_code = generate_referral_code()
            try:
                return super().save(*args, **kwargs)
            except IntegrityError:
                # Colisión de código (rarísima): reintentar con otro.
                self.referral_code = None
                # Si el update_fields limitaba a otros campos, no forzar nada:
                # en el reintento volvemos a entrar al bucle con un código nuevo.
                continue
        # Último intento sin capturar: que propague si de verdad no hay cupo.
        self.referral_code = generate_referral_code()
        return super().save(*args, **kwargs)


class Referral(models.Model):
    """Vínculo de invitación: ``inviter`` invitó a ``invitee`` a la Polla.

    Se crea cuando el invitado inicia sesión por primera vez usando un código
    de invitación. ``qualified`` pasa a ``True`` (de forma permanente) cuando el
    invitado hace su primer pronóstico; recién ahí el invitador suma su punto.
    El ``OneToOne`` sobre ``invitee`` garantiza que cada cuenta solo puede ser
    invitada una vez.
    """

    inviter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrals_made"
    )
    invitee = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_source"
    )
    qualified = models.BooleanField(
        default=False, help_text="True cuando el invitado ya hizo su primer pronóstico"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    qualified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Invitación"
        verbose_name_plural = "Invitaciones"
        ordering = ["-created_at"]

    def __str__(self):
        estado = "calificada" if self.qualified else "pendiente"
        return f"{self.inviter} → {self.invitee} ({estado})"
