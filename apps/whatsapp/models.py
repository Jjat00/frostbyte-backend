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
    last_location_lat = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        verbose_name="Última ubicación (lat)",
        help_text=(
            "Última ubicación de WhatsApp que compartió el cliente. La lee "
            "crear_pedido: el agente nunca maneja coordenadas (no puede inventarlas)"
        ),
    )
    last_location_lng = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        verbose_name="Última ubicación (lng)",
    )
    last_location_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Ubicación compartida el",
    )
    human_handoff = models.BooleanField(
        default=False,
        verbose_name="Atendido por humano",
        help_text="Si está activo, el agente NO responde a este contacto hasta desactivarlo",
    )
    human_until = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Pausa humana hasta",
        help_text=(
            "Mientras esta hora esté en el futuro el agente no responde: un humano del "
            "equipo intervino en el chat. Se renueva con cada mensaje del humano"
        ),
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


class SentMessage(models.Model):
    """Mensaje saliente enviado por el propio backend vía la API de Kapso.

    Registra el wamid de todo lo que envía el sistema (agente y notificaciones)
    para distinguirlo de los mensajes que un humano del equipo manda desde el
    inbox de Kapso o la app de WhatsApp Business: un evento message.sent cuyo
    id no esté aquí se trata como intervención humana y pausa al agente.
    """

    wamid = models.CharField(max_length=128, unique=True, verbose_name="ID de mensaje")
    to_phone = models.CharField(max_length=30, blank=True, verbose_name="Destinatario")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Enviado")

    class Meta:
        verbose_name = "Mensaje enviado por el sistema"
        verbose_name_plural = "Mensajes enviados por el sistema"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.to_phone} · {self.wamid[:40]}"


class ChatMessage(models.Model):
    """Texto de los mensajes del chat, indexado por wamid.

    Cuando el cliente responde deslizando un mensaje, WhatsApp solo manda el id
    del mensaje citado (`context.id`): sin esto no habría forma de saber a qué
    se refiere. Guarda lo que dice cada lado (cliente, agente y humanos del
    equipo) y se limpia sola a los pocos días: se cita lo reciente.
    """

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Del cliente"
        OUTBOUND = "outbound", "Del negocio"

    wamid = models.CharField(max_length=128, unique=True, verbose_name="ID de mensaje")
    phone = models.CharField(max_length=30, blank=True, verbose_name="Teléfono del cliente")
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.INBOUND,
        verbose_name="Dirección",
    )
    body = models.TextField(blank=True, verbose_name="Texto")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Fecha")

    class Meta:
        verbose_name = "Texto de mensaje (citas)"
        verbose_name_plural = "Textos de mensajes (citas)"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone} · {self.body[:40]}"

    @classmethod
    def remember(cls, wamid, phone, direction, body):
        """Guarda el texto de un mensaje si trae id y contenido."""
        if not wamid or not (body or "").strip():
            return None
        return cls.objects.get_or_create(
            wamid=str(wamid)[:128],
            defaults={
                "phone": str(phone or "")[:30],
                "direction": direction,
                "body": body.strip(),
            },
        )[0]


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
