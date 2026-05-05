from django.db import models
from django.core.validators import MinLengthValidator


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


class MotherDedication(models.Model):
    """Dedicatoria del Día de la Madre — los hijos dejan mensajes de amor para sus mamás."""

    author_name = models.CharField(
        max_length=60,
        blank=True,
        verbose_name="De parte de",
    )
    mother_name = models.CharField(
        max_length=60,
        blank=True,
        verbose_name="Para mamá",
    )
    message = models.CharField(
        max_length=240,
        validators=[MinLengthValidator(5)],
        verbose_name="Mensaje de amor",
    )
    is_approved = models.BooleanField(default=True, verbose_name="Aprobada")
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, verbose_name="IP del cliente"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")

    class Meta:
        verbose_name = "Dedicatoria Día de la Madre"
        verbose_name_plural = "Dedicatorias Día de la Madre"
        ordering = ["-created_at"]

    def __str__(self):
        para = f" para {self.mother_name}" if self.mother_name else ""
        de = f" de {self.author_name}" if self.author_name else ""
        return f"{self.message[:50]}{para}{de}"
