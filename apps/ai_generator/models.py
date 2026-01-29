from decimal import Decimal
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import FileExtensionValidator
from django.utils import timezone
import uuid

User = get_user_model()


class AIImageGeneration(models.Model):
    """Almacena cada generación de imagen con IA"""

    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('processing', 'Procesando'),
        ('completed', 'Completado'),
        ('failed', 'Fallido'),
    ]

    # Identificación
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='ai_generations',
        verbose_name='Usuario'
    )

    # Imágenes
    original_image = models.ImageField(
        upload_to='ai_generations/originals/%Y/%m/',
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
        verbose_name='Imagen Original'
    )
    reference_image = models.ImageField(
        upload_to='ai_generations/references/%Y/%m/',
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
        null=True,
        blank=True,
        verbose_name='Imagen de Referencia'
    )
    generated_image = models.ImageField(
        upload_to='ai_generations/results/%Y/%m/',
        null=True,
        blank=True,
        verbose_name='Imagen Generada'
    )

    # Parámetros de generación
    user_prompt = models.TextField(
        max_length=500,
        blank=True,
        verbose_name='Prompt del Usuario'
    )
    transparent_background = models.BooleanField(
        default=True,
        verbose_name='Fondo Transparente'
    )

    # Prompt completo enviado a OpenAI (auto-generado)
    full_prompt = models.TextField(
        verbose_name='Prompt Completo',
        blank=True
    )

    # Estado y metadata
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Estado'
    )
    error_message = models.TextField(
        blank=True,
        verbose_name='Mensaje de Error'
    )

    # Métricas
    generation_time_seconds = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Tiempo de Generación (segundos)'
    )
    cost_usd = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name='Costo (USD)'
    )
    openai_model = models.CharField(
        max_length=50,
        default='gpt-image-1.5',
        verbose_name='Modelo de OpenAI'
    )

    # Metadata adicional
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Metadata'
    )

    # Asociación con producto (opcional)
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ai_generated_images',
        verbose_name='Producto'
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Creación'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Fecha de Actualización'
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha de Completado'
    )

    class Meta:
        verbose_name = 'Generación de Imagen IA'
        verbose_name_plural = 'Generaciones de Imágenes IA'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Generación {self.id} - {self.get_status_display()}"

    @property
    def is_completed(self):
        return self.status == 'completed'

    @property
    def is_processing(self):
        return self.status in ['pending', 'processing']


class AIGenerationUsageQuota(models.Model):
    """Control de cuotas de uso por usuario"""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='ai_quota',
        verbose_name='Usuario'
    )

    # Límites
    monthly_limit = models.IntegerField(
        default=10,
        verbose_name='Límite Mensual'
    )
    used_this_month = models.IntegerField(
        default=0,
        verbose_name='Usado Este Mes'
    )

    # Costos acumulados
    total_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
        verbose_name='Costo Total (USD)'
    )

    # Reset tracking
    last_reset = models.DateField(
        auto_now_add=True,
        verbose_name='Último Reset'
    )

    class Meta:
        verbose_name = 'Cuota de Uso IA'
        verbose_name_plural = 'Cuotas de Uso IA'

    def __str__(self):
        return f"{self.user.email} - {self.used_this_month}/{self.monthly_limit}"

    def can_generate(self):
        """Verifica si el usuario puede generar más imágenes"""
        return self.used_this_month < self.monthly_limit

    def increment_usage(self, cost=0):
        """Incrementa el contador de uso. cost puede ser float o Decimal."""
        self.used_this_month += 1
        self.total_cost_usd += Decimal(str(cost))
        self.save()

    def reset_monthly_usage(self):
        """Reset mensual (llamado por cron job)"""
        self.used_this_month = 0
        self.last_reset = timezone.now().date()
        self.save()
