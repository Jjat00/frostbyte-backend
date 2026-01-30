from django.db import models
from django.contrib.auth import get_user_model
import uuid

User = get_user_model()


class AIImageGeneration(models.Model):
    """Generación de imagen con IA - modelo simplificado"""

    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('processing', 'Procesando'),
        ('completed', 'Completado'),
        ('failed', 'Fallido'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='ai_generations')

    # Imágenes
    original_image = models.ImageField(upload_to='ai_generations/originals/')
    reference_image = models.ImageField(
        upload_to='ai_generations/references/', null=True, blank=True)
    generated_image = models.ImageField(
        upload_to='ai_generations/results/', null=True, blank=True)

    # Parámetros
    user_prompt = models.TextField(blank=True)
    transparent_background = models.BooleanField(default=True)

    # Estado
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)

    # Producto asociado (opcional)
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ai_generated_images'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Gen {self.id} - {self.status}"
