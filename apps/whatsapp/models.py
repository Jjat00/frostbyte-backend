import re

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from .tones import DEFAULT_TONE, SEED_TONES, seed_persona, seed_tone

# Cuántos stickers recuerda cada contacto. Solo hacen falta los del día, y un
# día no da para más de un puñado.
STICKER_MEMORY = 5


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
    sticker_log = models.JSONField(
        default=list,
        blank=True,
        editable=False,
        verbose_name="Stickers recientes",
        help_text=(
            "Los últimos que el agente le mandó a este contacto, del más nuevo al más "
            "viejo. Es su memoria corta: sin ella repetiría el mismo sticker y lo "
            "mandaría en cada mensaje (ver mood.sticker_urge)"
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

    def remember_sticker(self, label):
        """Anota el sticker que se le acaba de mandar.

        Se guardan unos pocos y no todos a propósito: lo único que hay que
        saber después es cuántos van hoy y cuál fue el último, y un historial
        completo por contacto solo engordaría la fila.
        """
        entry = {"label": label, "at": timezone.now().isoformat()}
        previous = [e for e in (self.sticker_log or []) if isinstance(e, dict)]
        self.sticker_log = [entry] + previous[: STICKER_MEMORY - 1]
        self.save(update_fields=["sticker_log", "updated_at"])

    def stickers_today(self):
        """Los de hoy, del más reciente al más viejo: [(nombre, cuándo)].

        Hoy, y no las últimas horas, porque el hilo del agente también se
        renueva cada día: la conversación de ayer no cuenta para el pulso de
        la de hoy.
        """
        today = timezone.localdate()
        recent = []
        for entry in self.sticker_log or []:
            if not isinstance(entry, dict):
                continue
            moment = parse_datetime(entry.get("at") or "")
            if moment is None or timezone.localtime(moment).date() != today:
                continue
            recent.append((entry.get("label") or "", moment))
        return recent


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


class AgentTone(models.Model):
    """Una de las personalidades con las que puede hablar el agente.

    El texto de `persona` entra tal cual en el prompt, en el bloque QUIÉN ERES,
    y REEMPLAZA al de fábrica en vez de sumarse. Vive en la base y no en el
    código porque cómo habla el negocio es del negocio: el dueño afina el suyo
    o se inventa uno nuevo sin esperar un despliegue.

    Los cuatro de fábrica llegan con la siembra marcados `is_builtin`: se
    editan igual que cualquier otro, pero su texto original sigue en el código
    (`tones.SEED_TONES`) y por eso se pueden devolver a como estaban.
    """

    key = models.SlugField(
        max_length=30,
        unique=True,
        verbose_name="Clave",
        help_text="Identificador estable; se genera del nombre y no cambia después.",
    )
    name = models.CharField(
        max_length=40,
        verbose_name="Nombre",
        help_text="Como se ve en el panel al elegirlo: 'Parcero', 'Serio'.",
    )
    description = models.CharField(
        max_length=200,
        verbose_name="De qué va",
        help_text="Una línea que resuma el tono para quien elige. No la lee el agente.",
    )
    sample = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Frase de ejemplo",
        help_text=(
            "Cómo sonaría un saludo suyo. Se ve en el panel y además se la damos al "
            "agente: una frase de muestra le calibra el registro mejor que un párrafo "
            "describiéndoselo."
        ),
    )
    persona = models.TextField(
        verbose_name="Personalidad",
        help_text=(
            "Esto SÍ lo lee el agente: es el bloque QUIÉN ERES del prompt. Escríbelo "
            "en segunda persona ('eres…', 'tuteas…') y di también qué hacer cuando el "
            "cliente está molesto."
        ),
    )
    is_builtin = models.BooleanField(
        default=False,
        editable=False,
        verbose_name="De fábrica",
        help_text="Los que vinieron con el sistema; se pueden devolver a su texto original.",
    )
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Creado")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado")

    class Meta:
        verbose_name = "Tono del agente"
        verbose_name_plural = "Tonos del agente"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    @classmethod
    def make_key(cls, name, exclude_pk=None):
        """Una clave estable a partir del nombre, sin chocar con las que ya hay."""
        base = slugify(name or "")[:24] or "tono"
        taken = cls.objects.exclude(pk=exclude_pk) if exclude_pk else cls.objects.all()
        taken = set(taken.values_list("key", flat=True))
        key, sufijo = base, 2
        while key in taken:
            key = f"{base}-{sufijo}"[:30]
            sufijo += 1
        return key

    @classmethod
    def persona_for(cls, key):
        """El bloque QUIÉN ERES del tono elegido.

        Cae en cascada a propósito: el tono elegido, cualquier otro del
        catálogo, y de último el texto de fábrica. Un agente sin personalidad
        no puede ser el resultado de que alguien borrara un tono.
        """
        tone = cls.objects.filter(key=key).first() or cls.objects.first()
        if tone and tone.persona.strip():
            return tone.persona.strip()
        return seed_persona(key)

    @classmethod
    def sample_for(cls, key):
        """La frase de muestra del tono elegido, o vacío si no tiene.

        Cae en cascada como persona_for, pero sin suelo: un tono puede no
        tener muestra y el prompt se apaña sin ella.
        """
        tone = cls.objects.filter(key=key).first() or cls.objects.first()
        if tone and tone.sample.strip():
            return tone.sample.strip()
        return (seed_tone(key) or {}).get("sample", "")

    @property
    def seed(self):
        """El texto de fábrica de este tono, si es uno de los que vinieron."""
        return seed_tone(self.key) if self.is_builtin else None

    @property
    def is_modified(self):
        """True si es de fábrica y alguien le cambió algo (habilita 'restaurar')."""
        original = self.seed
        if not original:
            return False
        return any(getattr(self, field) != original[field] for field in ("name", "description", "sample", "persona"))

    def restore(self):
        """Devuelve un tono de fábrica a su texto original."""
        original = self.seed
        if not original:
            return False
        for field in ("name", "description", "sample", "persona"):
            setattr(self, field, original[field])
        self.save(update_fields=["name", "description", "sample", "persona", "updated_at"])
        return True

    @classmethod
    def seed_catalog(cls):
        """Siembra los tonos de fábrica que falten. Idempotente."""
        for orden, tone in enumerate(SEED_TONES):
            cls.objects.get_or_create(
                key=tone["key"],
                defaults={**tone, "is_builtin": True, "display_order": orden},
            )


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
    owner_phones = models.CharField(
        max_length=200,
        default="573164277879",
        blank=True,
        verbose_name="Números del dueño",
        help_text=(
            "Separados por coma, con indicativo (573001234567). Desde estos números el "
            "agente reconoce al dueño: lo trata distinto y le deja gestionar sus stickers "
            "y su tono por chat. Sigue tomándole pedidos de verdad."
        ),
    )
    tone_preset = models.CharField(
        max_length=30,
        default=DEFAULT_TONE,
        verbose_name="Tono",
        help_text=(
            "Clave del tono con el que habla (ver Tonos del agente). Reemplaza el bloque "
            "de QUIÉN ERES del prompt: no se suma al de por defecto, lo sustituye."
        ),
    )
    tone = models.TextField(
        blank=True,
        verbose_name="Ajustes de tono",
        help_text=(
            "Instrucciones extra sobre CÓMO habla (no sobre qué hace). Se añaden al "
            "final del prompt, así que mandan sobre el tono elegido. "
            "Ej.: 'Trata al cliente de usted' o 'sin emojis'. Vacío = solo el tono elegido."
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
        help_text=(
            "Botones de respuesta rápida (máx. 3) para confirmar el pedido. El método "
            "de pago NUNCA se pregunta con botones."
        ),
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

    def persona(self):
        """El bloque QUIEN ERES que le toca segun el tono elegido."""
        return AgentTone.persona_for(self.tone_preset)

    def sample(self):
        """Una frase de muestra del tono elegido, para el prompt."""
        return AgentTone.sample_for(self.tone_preset)

    @property
    def tone_catalog(self):
        """Los tonos entre los que se puede elegir, para la pantalla."""
        return AgentTone.objects.all()

    def owner_numbers(self):
        """Los números del dueño, ya en dígitos."""
        return {
            re.sub(r"\D", "", part)
            for part in (self.owner_phones or "").split(",")
            if re.sub(r"\D", "", part)
        }

    def is_owner(self, phone):
        """True si quien escribe es el dueño.

        Compara por los últimos 10 dígitos: el mismo celular llega unas veces
        con indicativo y otras sin él, y un número colombiano queda
        identificado por su celular. Un BSUID nunca es dueño —no tiene dígitos
        que comparar—, así que quien oculta su número no hereda el permiso.
        """
        from . import kapso

        digits = re.sub(r"\D", "", str(phone or ""))
        if not digits or len(digits) < 10 or kapso.is_bsuid(phone):
            return False
        return any(number[-10:] == digits[-10:] for number in self.owner_numbers())


class Sticker(models.Model):
    """Un sticker del banco que el agente puede mandar.

    Los bytes viven en la base de datos y no en el disco a propósito: el
    sistema de archivos de Railway se borra en cada despliegue, así que un
    sticker guardado como archivo desaparecería sin avisar. El agente no puede
    "ver" el banco cuando responde, así que elige por la descripción: ahí está
    escrito PARA QUÉ sirve cada uno, no qué se dibuja en él.

    La descripción orienta, no reparte: dos stickers pueden servir para el
    mismo momento y el agente elige entre ellos (ver mood.py). Un banco con un
    solo sticker por situación siempre va a sonar igual, por mucho que el
    prompt le pida variar.
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
            "Lo único que el agente lee para elegirlo. Describe el MOMENTO o el ánimo, no "
            "el dibujo: 'para celebrar que el pedido quedó listo', no 'un vaso azul con "
            "ojos'. No es exclusivo: varios pueden servir para lo mismo, y cuantos más "
            "haya para un mismo ánimo menos se repite el agente."
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


class StickerDraft(models.Model):
    """El último archivo que el dueño mandó por chat, esperando nombre.

    Convertirlo en sticker necesita dos cosas que llegan por separado: el
    archivo y el momento en que hay que usarlo. Aquí se guarda el archivo tal
    como llegó (sin normalizar todavía, porque puede acabar descartado)
    mientras el agente pregunta lo demás.

    Uno por contacto: mandar otro archivo reemplaza el anterior, que es lo que
    espera cualquiera que se equivocó de foto y manda la buena.
    """

    class Kind(models.TextChoices):
        IMAGE = "image", "Imagen"
        STICKER = "sticker", "Sticker"
        VIDEO = "video", "Video"

    contact = models.OneToOneField(
        "WhatsAppContact",
        on_delete=models.CASCADE,
        related_name="sticker_draft",
        verbose_name="Contacto",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, verbose_name="Tipo")
    data = models.BinaryField(verbose_name="Archivo original", editable=False)
    mime = models.CharField(max_length=80, blank=True, verbose_name="Tipo MIME")
    created_at = models.DateTimeField(auto_now=True, verbose_name="Recibido")

    class Meta:
        verbose_name = "Archivo pendiente de volverse sticker"
        verbose_name_plural = "Archivos pendientes de volverse sticker"

    def __str__(self):
        return f"{self.contact.phone} · {self.get_kind_display()}"

    @classmethod
    def keep(cls, contact, kind, data, mime=""):
        """Guarda (o reemplaza) el archivo pendiente de este contacto."""
        return cls.objects.update_or_create(
            contact=contact, defaults={"kind": kind, "data": data, "mime": mime}
        )[0]
