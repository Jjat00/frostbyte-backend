from django.db import models
from django.contrib.auth import get_user_model
import uuid
import os

User = get_user_model()


class AIImageGeneration(models.Model):
    """Generación de imagen con IA - almacenamiento temporal y R2"""

    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('processing', 'Procesando'),
        ('completed', 'Completado'),
        ('failed', 'Fallido'),
        ('saved', 'Guardado en R2'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='ai_generations')

    # URLs permanentes (R2)
    original_image_url = models.URLField(max_length=500, blank=True)
    reference_image_url = models.URLField(max_length=500, blank=True)
    generated_image_url = models.URLField(max_length=500, blank=True)

    # Rutas temporales (antes de guardar a R2)
    temp_original_path = models.CharField(max_length=500, blank=True)
    temp_reference_path = models.CharField(max_length=500, blank=True)
    temp_generated_path = models.CharField(max_length=500, blank=True)

    # Parámetros
    user_prompt = models.TextField(blank=True)
    transparent_background = models.BooleanField(default=True)

    # Estado
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    is_saved_to_r2 = models.BooleanField(default=False)

    # Producto asociado (opcional)
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ai_generated_images'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    saved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Gen {self.id} - {self.status}"

    def get_image_url(self, image_type='generated'):
        """Retorna URL según el estado (R2 o temporal)"""
        if self.is_saved_to_r2:
            urls = {
                'original': self.original_image_url,
                'reference': self.reference_image_url,
                'generated': self.generated_image_url,
            }
            return urls.get(image_type)
        else:
            paths = {
                'original': self.temp_original_path,
                'reference': self.temp_reference_path,
                'generated': self.temp_generated_path,
            }
            path = paths.get(image_type)
            if path:
                return f"/api/v1/ai/generations/{self.id}/temp-image/{image_type}/"
        return None

    def cleanup_temp_files(self):
        """Elimina archivos temporales"""
        for path in [self.temp_original_path, self.temp_reference_path, self.temp_generated_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
