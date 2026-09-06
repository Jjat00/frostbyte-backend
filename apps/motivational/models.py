from django.db import models


class RecommenderLog(models.Model):
    SESSION_TYPE_CHOICES = [
        ("mood", "Estado de ánimo"),
        ("quiz", "Quiz rápido"),
    ]

    session_type = models.CharField(
        max_length=10,
        choices=SESSION_TYPE_CHOICES,
        verbose_name="Tipo de consulta",
    )
    # Mood: texto libre | Quiz: temperatura/taste/alcohol
    input_data = models.JSONField(verbose_name="Datos ingresados")
    recommended_product_name = models.CharField(
        max_length=200,
        verbose_name="Producto recomendado",
    )
    recommended_product_slug = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Slug del producto",
    )
    ai_reason = models.TextField(blank=True, verbose_name="Razón de la IA")
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name="IP del cliente",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y hora")

    class Meta:
        verbose_name = "Registro de recomendación"
        verbose_name_plural = "Registros de recomendaciones"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_session_type_display()}] {self.recommended_product_name} — {self.created_at:%Y-%m-%d %H:%M}"


class CardGeneration(models.Model):
    """Un intento de tarjeta de campaña, sin nada que identifique a nadie.

    Existe para responder una pregunta operativa: cuántas tarjetas se han
    generado y cuánto trabajo se le está yendo a cada proveedor. Por eso no
    guarda la foto, ni los nombres, ni la dedicatoria — solo el resultado.
    """

    GEMINI = "gemini"
    OPENAI = "openai"
    PROVIDER_CHOICES = [(GEMINI, "Gemini"), (OPENAI, "OpenAI")]

    OK = "ok"
    FAILED = "failed"
    STATUS_CHOICES = [(OK, "Generada"), (FAILED, "Fallida")]

    provider = models.CharField(
        max_length=20,
        choices=PROVIDER_CHOICES,
        verbose_name="Proveedor",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        verbose_name="Resultado",
    )
    model_name = models.CharField(
        max_length=80,
        blank=True,
        verbose_name="Modelo",
    )
    was_fallback = models.BooleanField(
        default=False,
        verbose_name="Entró como respaldo",
    )
    duration_ms = models.PositiveIntegerField(
        default=0,
        verbose_name="Duración (ms)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Fecha y hora",
    )

    class Meta:
        verbose_name = "Tarjeta generada"
        verbose_name_plural = "Tarjetas generadas"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_provider_display()}] {self.get_status_display()} — {self.created_at:%Y-%m-%d %H:%M}"

    @classmethod
    def record(cls, **fields):
        """Contar nunca puede tumbar la generación de una tarjeta."""
        try:
            return cls.objects.create(**fields)
        except Exception:
            return None
