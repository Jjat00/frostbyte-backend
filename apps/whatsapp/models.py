from django.db import models


class WhatsAppContact(models.Model):
    """Contacto de WhatsApp que interactúa con el agente de pedidos.

    Guarda la identidad y las preferencias de largo plazo del cliente; la
    memoria de la conversación en sí vive en el checkpointer de LangGraph.
    """

    phone = models.CharField(
        max_length=30,
        unique=True,
        verbose_name="Teléfono",
        help_text="Solo dígitos, con indicativo de país (ej. 573001234567)",
    )
    wa_user_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="ID de usuario de WhatsApp",
        help_text="business_scoped_user_id de Meta; estable aunque el número no venga",
    )
    profile_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Nombre de perfil",
    )
    customer_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Nombre confirmado",
        help_text="Nombre que el cliente confirmó para sus pedidos",
    )
    default_address = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Dirección habitual",
    )
    default_reference = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Referencia habitual",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Preferencias",
        help_text="Preferencias de largo plazo aprendidas por el agente",
    )
    last_phone_number_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Último número Kapso",
        help_text="phone_number_id por el que escribió la última vez; se usa para notificarle",
    )
    human_handoff = models.BooleanField(
        default=False,
        verbose_name="Atendido por humano",
        help_text="Si está activo, el agente NO responde a este contacto hasta desactivarlo",
    )
    is_blocked = models.BooleanField(
        default=False,
        verbose_name="Bloqueado",
        help_text="El agente ignora por completo los mensajes de este contacto",
    )
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Último mensaje",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Creado")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado")

    class Meta:
        verbose_name = "Contacto de WhatsApp"
        verbose_name_plural = "Contactos de WhatsApp"
        ordering = ["-last_message_at"]

    def __str__(self):
        name = self.customer_name or self.profile_name or "sin nombre"
        return f"{self.phone} ({name})"


class WebhookEvent(models.Model):
    """Webhook recibido de Kapso: idempotencia + auditoría de procesamiento."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PROCESSED = "processed", "Procesado"
        IGNORED = "ignored", "Ignorado"
        FAILED = "failed", "Fallido"

    idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        verbose_name="Clave de idempotencia",
        help_text="Header X-Idempotency-Key de Kapso (o hash del body si no vino)",
    )
    phone_number_id = models.CharField(max_length=64, blank=True, verbose_name="Número destino")
    contact_phone = models.CharField(max_length=30, blank=True, verbose_name="Teléfono del cliente")
    event_type = models.CharField(max_length=64, blank=True, verbose_name="Tipo de evento")
    payload = models.JSONField(default=dict, verbose_name="Payload")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Estado",
    )
    error = models.TextField(blank=True, verbose_name="Error")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Recibido")

    class Meta:
        verbose_name = "Evento de webhook"
        verbose_name_plural = "Eventos de webhook"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type or 'evento'} · {self.contact_phone} · {self.get_status_display()}"
