from django.db import models


class WhatsAppContact(models.Model):
    """Contacto de WhatsApp que interactúa con el agente de pedidos.

    Guarda la identidad y las preferencias de largo plazo del cliente; la
    memoria de la conversación en sí vive en el checkpointer de LangGraph.
    """

    phone = models.CharField(
        max_length=160,
        unique=True,
        verbose_name="Teléfono",
        help_text=(
            "Solo dígitos con indicativo de país (ej. 573001234567) o, si el "
            "cliente oculta su número, su BSUID de Meta (ej. CO.2430294670795328)"
        ),
    )
    wa_user_id = models.CharField(
        max_length=160,
        blank=True,
        db_index=True,
        verbose_name="ID de usuario de WhatsApp",
        help_text="business_scoped_user_id de Meta; estable aunque el número no venga",
    )
    username = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Usuario de WhatsApp",
        help_text="Nombre de usuario de WhatsApp, si el cliente tiene; puede cambiar",
    )
    contact_phone = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="Celular de contacto",
        help_text=(
            "Número que dio el cliente para llamarlo cuando WhatsApp no muestra el suyo "
            "(contactos identificados por BSUID)"
        ),
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
    to_phone = models.CharField(max_length=160, blank=True, verbose_name="Destinatario")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Enviado")

    class Meta:
        verbose_name = "Mensaje enviado por el sistema"
        verbose_name_plural = "Mensajes enviados por el sistema"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.to_phone} · {self.wamid[:40]}"


class ChatMessage(models.Model):
    """Archivo de la conversación: lo que dijo cada lado, indexado por wamid.

    Nace para resolver las citas (cuando el cliente responde deslizando un
    mensaje, WhatsApp solo manda el id del citado en `context.id`), pero se
    conserva porque es el único registro de qué se habló: muchos pedidos los
    cierra a mano el equipo durante una pausa humana y sin este historial no
    hay forma de saber después si la conversación terminó en venta.
    """

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Del cliente"
        OUTBOUND = "outbound", "Del negocio"

    class Author(models.TextChoices):
        CUSTOMER = "customer", "Cliente"
        AGENT = "agent", "Agente IA"
        HUMAN = "human", "Persona del equipo"

    wamid = models.CharField(max_length=128, unique=True, verbose_name="ID de mensaje")
    phone = models.CharField(max_length=30, blank=True, verbose_name="Teléfono del cliente")
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.INBOUND,
        verbose_name="Dirección",
    )
    author = models.CharField(
        max_length=10,
        choices=Author.choices,
        default=Author.CUSTOMER,
        db_index=True,
        verbose_name="Quién lo escribió",
        help_text="Distingue lo que respondió el agente de lo que escribió una persona del equipo.",
    )
    body = models.TextField(blank=True, verbose_name="Texto")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Fecha")

    class Meta:
        verbose_name = "Mensaje de la conversación"
        verbose_name_plural = "Mensajes de las conversaciones"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["phone", "created_at"])]

    def __str__(self):
        return f"{self.phone} · {self.get_author_display()} · {self.body[:40]}"

    @classmethod
    def remember(cls, wamid, phone, direction, body, author=None):
        """Guarda el texto de un mensaje si trae id y contenido."""
        if not wamid or not (body or "").strip():
            return None
        if author is None:
            author = (
                cls.Author.CUSTOMER
                if direction == cls.Direction.INBOUND
                else cls.Author.AGENT
            )
        return cls.objects.get_or_create(
            wamid=str(wamid)[:128],
            defaults={
                "phone": str(phone or "")[:30],
                "direction": direction,
                "author": author,
                "body": body.strip(),
            },
        )[0]

    @classmethod
    def enrich(cls, wamid, body):
        """Reemplaza el texto por lo que el agente realmente leyó.

        Las notas de voz y las imágenes se guardan primero con el texto de
        respaldo y solo después se transcriben o describen: sin esto el archivo
        diría "imagen que no se pudo procesar" donde había un comprobante.
        """
        if not wamid or not (body or "").strip():
            return
        cls.objects.filter(wamid=str(wamid)[:128]).exclude(body=body.strip()).update(
            body=body.strip()
        )


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
    contact_phone = models.CharField(max_length=160, blank=True, verbose_name="Teléfono del cliente")
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


class AgentSettings(models.Model):
    """Configuración de Frosty, el agente de WhatsApp (singleton).

    La casa de todo lo que se pueda cambiar del agente sin redeploy: cómo se
    llama, cómo habla y qué cosas puede mandar además de texto. El prompt base
    (reglas del pedido, cobertura, pagos) NO vive aquí a propósito: eso es
    lógica de negocio probada por tests, no una preferencia.
    """

    agent_name = models.CharField(
        max_length=40,
        default="Frosty",
        verbose_name="Nombre del agente",
        help_text="Con este nombre se presenta ante el cliente.",
    )
    tone = models.TextField(
        blank=True,
        verbose_name="Tono y personalidad",
        help_text=(
            "Instrucciones extra sobre CÓMO habla (no sobre qué hace). Se añaden al "
            "final del prompt, así que mandan sobre el estilo por defecto. "
            "Ej.: 'Trata al cliente de usted' o 'sin emojis'. Vacío = el tono por defecto."
        ),
    )
    stickers_enabled = models.BooleanField(
        default=True,
        verbose_name="Puede mandar stickers",
        help_text="Si se apaga, el agente no ve el banco de stickers y nunca manda uno.",
    )
    reactions_enabled = models.BooleanField(
        default=True,
        verbose_name="Puede reaccionar con emoji",
        help_text="Reacciones de WhatsApp sobre el mensaje del cliente (❤️, 😂, 👀).",
    )
    product_photos_enabled = models.BooleanField(
        default=True,
        verbose_name="Puede mandar fotos de productos",
        help_text="Manda la foto real del producto cuando el cliente pregunta cómo es.",
    )
    quick_replies_enabled = models.BooleanField(
        default=True,
        verbose_name="Puede mandar botones",
        help_text="Botones de respuesta rápida (máx. 3) para confirmar el pedido o elegir pago.",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado")

    class Meta:
        verbose_name = "Configuración del agente"
        verbose_name_plural = "Configuración del agente"

    def __str__(self):
        return f"Configuración de {self.agent_name}"

    @classmethod
    def load(cls):
        """Devuelve la única instancia de configuración, creándola si no existe."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Sticker(models.Model):
    """Un sticker del banco que el agente puede mandar.

    Los bytes viven en la base de datos y no en el disco a propósito: el
    sistema de archivos de Railway se borra en cada despliegue, así que un
    sticker guardado como archivo desaparecería sin avisar. El agente no puede
    "ver" el banco cuando responde, así que elige por la descripción: ahí está
    escrito PARA QUÉ sirve cada uno, no qué se dibuja en él.
    """

    label = models.CharField(
        max_length=60,
        unique=True,
        verbose_name="Nombre",
        help_text="Corto y en minúsculas, como lo pediría alguien: 'granizado feliz', 'pulgar arriba'.",
    )
    description = models.CharField(
        max_length=200,
        verbose_name="Cuándo usarlo",
        help_text=(
            "Lo único que el agente lee para elegirlo. Describe el MOMENTO, no el dibujo: "
            "'para celebrar que el pedido quedó listo', no 'un vaso azul con ojos'."
        ),
    )
    data = models.BinaryField(verbose_name="Archivo WebP", editable=False)
    byte_size = models.PositiveIntegerField(default=0, verbose_name="Tamaño (bytes)")
    is_animated = models.BooleanField(default=False, verbose_name="Animado")
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activo",
        help_text="Los inactivos no se le muestran al agente.",
    )
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    sent_count = models.PositiveIntegerField(default=0, verbose_name="Veces enviado")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Creado")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado")

    class Meta:
        verbose_name = "Sticker"
        verbose_name_plural = "Stickers"
        ordering = ["display_order", "label"]

    def __str__(self):
        return self.label

    @property
    def url(self):
        """URL pública que WhatsApp usa para descargarlo (ver views.sticker_file)."""
        from django.conf import settings

        return f"{settings.BACKEND_PUBLIC_URL.rstrip('/')}/api/v1/whatsapp/stickers/{self.pk}.webp"

    @classmethod
    def catalog(cls):
        """Los stickers que el agente puede usar ahora mismo."""
        return list(cls.objects.filter(is_active=True))

    @classmethod
    def render(cls, stickers):
        """El banco tal como lo lee el agente dentro del prompt."""
        if not stickers:
            return ""
        return "\n".join(f"- {s.label}: {s.description}" for s in stickers)
