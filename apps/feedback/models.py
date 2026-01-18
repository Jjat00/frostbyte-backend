from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


class Feedback(models.Model):
    """Comentarios y feedback de clientes"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        REVIEWED = "reviewed", "Revisado"
        RESPONDED = "responded", "Respondido"
        ARCHIVED = "archived", "Archivado"

    class FeedbackType(models.TextChoices):
        SUGGESTION = "suggestion", "Sugerencia"
        COMPLAINT = "complaint", "Queja"
        COMPLIMENT = "compliment", "Felicitacion"
        QUESTION = "question", "Pregunta"
        OTHER = "other", "Otro"

    customer_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Nombre del cliente",
        help_text="Opcional - Nombre del cliente",
    )
    comment = models.TextField(
        verbose_name="Comentario",
        help_text="El mensaje o comentario del cliente",
    )
    rating = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name="Calificacion",
        help_text="Calificacion del 1 al 5 estrellas (opcional)",
    )
    feedback_type = models.CharField(
        max_length=20,
        choices=FeedbackType.choices,
        default=FeedbackType.OTHER,
        verbose_name="Tipo de feedback",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Estado",
    )
    admin_notes = models.TextField(
        blank=True,
        verbose_name="Notas del administrador",
        help_text="Notas internas sobre este feedback",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creado",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Actualizado",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Revisado",
    )

    class Meta:
        verbose_name = "Feedback"
        verbose_name_plural = "Feedbacks"
        ordering = ["-created_at"]

    def __str__(self):
        name = self.customer_name or "Anonimo"
        return f"{name} - {self.get_feedback_type_display()} ({self.created_at.strftime('%d/%m/%Y') if self.created_at else 'Sin fecha'})"

    def mark_as_reviewed(self):
        """Marcar feedback como revisado"""
        self.status = self.Status.REVIEWED
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_at", "updated_at"])

    def mark_as_responded(self):
        """Marcar feedback como respondido"""
        self.status = self.Status.RESPONDED
        if not self.reviewed_at:
            self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_at", "updated_at"])

    def mark_as_archived(self):
        """Archivar feedback"""
        self.status = self.Status.ARCHIVED
        self.save(update_fields=["status", "updated_at"])
