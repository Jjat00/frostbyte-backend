from base64 import b64encode

from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .models import (
    AgentSettings,
    AgentTone,
    ChatMessage,
    SentMessage,
    Sticker,
    WebhookEvent,
    WhatsAppContact,
)
from .stickers import StickerError, has_transparency, normalize
from apps.search import PlainSearchAdminMixin


@admin.register(WhatsAppContact)
class WhatsAppContactAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = (
        "phone",
        "customer_name",
        "profile_name",
        "username",
        "contact_phone",
        "human_handoff",
        "human_until",
        "is_blocked",
        "last_message_at",
    )
    list_editable = ("human_handoff", "is_blocked")
    search_fields = (
        "phone", "wa_user_id", "username", "contact_phone", "customer_name", "profile_name"
    )
    list_filter = ("human_handoff", "is_blocked")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_message_at",
        "last_phone_number_id",
        "last_location_lat",
        "last_location_lng",
        "last_location_at",
        "pedidos_del_cliente",
        "conversacion",
    )

    @admin.display(description="Pedidos de este teléfono")
    def pedidos_del_cliente(self, obj):
        """Los pedidos que llegaron a existir, sin importar quién los creó.

        Un pedido que el equipo cerró a mano desde la app también aparece aquí
        si quedó con el teléfono del cliente; si no aparece ninguno, la
        conversación no dejó venta registrada.
        """
        from apps.orders.models import Order

        from .tools import normalize_phone

        digits = normalize_phone(obj.phone)[-10:]
        if not digits:
            return "—"
        pedidos = Order.objects.filter(customer_phone__endswith=digits).order_by("-created_at")[:20]
        if not pedidos:
            return "Sin pedidos registrados con este teléfono."
        return format_html_join(
            "", "<div>{} · {} · {} · {} · ${}</div>",
            (
                (
                    o.created_at.strftime("%d/%m/%Y %H:%M"),
                    o.order_number,
                    o.get_order_type_display(),
                    o.get_status_display(),
                    f"{o.total:,.0f}".replace(",", "."),
                )
                for o in pedidos
            ),
        )

    @admin.display(description="Conversación (últimos 60 mensajes)")
    def conversacion(self, obj):
        mensajes = ChatMessage.objects.filter(phone=obj.phone).order_by("-created_at")[:60]
        if not mensajes:
            return "Sin mensajes guardados."
        filas = format_html_join(
            "", "<div style=\"margin-bottom:4px\"><b>{}</b> [{}]: {}</div>",
            (
                (
                    m.created_at.strftime("%d/%m %H:%M"),
                    m.get_author_display(),
                    m.body[:400],
                )
                for m in reversed(list(mensajes))
            ),
        )
        return format_html('<div style="max-height:420px;overflow:auto">{}</div>', filas)


@admin.register(SentMessage)
class SentMessageAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "to_phone", "wamid")
    search_fields = ("to_phone", "wamid")
    readonly_fields = [f.name for f in SentMessage._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ChatMessage)
class ChatMessageAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    """Archivo de las conversaciones.

    Filtrar por `author` es lo que permite revisar los chats que terminó
    atendiendo una persona del equipo, que son justo los que no dejan pedido
    en el sistema porque se cierran a mano.
    """

    list_display = ("created_at", "phone", "author", "direction", "body")
    list_filter = ("author", "direction")
    search_fields = ("phone", "wamid", "body")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in ChatMessage._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "event_type", "contact_phone", "phone_number_id", "status")
    list_filter = ("status",)
    search_fields = ("contact_phone", "idempotency_key")
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(AgentTone)
class AgentToneAdmin(admin.ModelAdmin):
    """El catálogo de personalidades del agente.

    `persona` es lo único que lee el modelo: entra tal cual en el bloque QUIÉN
    ERES del prompt, reemplazando al de fábrica. Lo demás es para quien elige.
    """

    list_display = ("name", "key", "description", "is_builtin", "display_order", "updated_at")
    list_editable = ("display_order",)
    list_filter = ("is_builtin",)
    search_fields = ("name", "key", "description")
    readonly_fields = ("key", "is_builtin", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        """El tono en uso no se borra desde aquí: dejaría al agente apuntando a nada."""
        if obj is not None and AgentSettings.load().tone_preset == obj.key:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.key:
            obj.key = AgentTone.make_key(obj.name)
        super().save_model(request, obj, form, change)


@admin.register(AgentSettings)
class AgentSettingsAdmin(admin.ModelAdmin):
    """El panel de Frosty: quién es y qué puede hacer, sin tocar código.

    Aquí va lo que es preferencia del negocio. Las reglas del pedido (cobertura,
    pagos, cómo se cotiza) NO se configuran: son lógica con tests detrás, y
    dejarlas editables convertiría un descuido de redacción en un pedido mal
    tomado.
    """

    fieldsets = (
        (
            "Identidad",
            {
                "fields": ("agent_name", "tone_preset", "tone", "banned_words"),
                "description": (
                    "El tono elegido reemplaza la personalidad por defecto; los ajustes "
                    "se suman encima y mandan sobre ella."
                ),
            },
        ),
        (
            "El dueño",
            {
                "fields": ("owner_phones",),
                "description": (
                    "Desde estos números el agente reconoce al dueño: lo trata en confianza "
                    "y le deja gestionar sus stickers y su tono por chat (mandándole una "
                    "imagen, un sticker o un video y diciéndole cuándo usarlo). Le sigue "
                    "tomando pedidos de verdad, con las mismas reglas que a cualquiera."
                ),
            },
        ),
        (
            "Qué puede mandar",
            {
                "fields": (
                    "stickers_enabled",
                    "reactions_enabled",
                    "product_photos_enabled",
                    "quick_replies_enabled",
                ),
                "description": (
                    "Cada interruptor quita a la vez la herramienta y la parte del prompt "
                    "que la explica, así que apagarlo no deja al agente prometiendo algo "
                    "que ya no puede hacer."
                ),
            },
        ),
        ("Banco de stickers", {"fields": ("stickers_link",)}),
    )
    readonly_fields = ("stickers_link",)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """El tono se elige de una lista, no se teclea.

        Las opciones se arman al abrir el formulario porque el catálogo se
        edita: un `choices` fijo en el modelo se quedaría atrás en cuanto
        alguien cree un tono nuevo.
        """
        if db_field.name == "tone_preset":
            tonos = [(t.key, t.name) for t in AgentTone.objects.all()]
            return forms.ChoiceField(
                choices=tonos or [(db_field.default, db_field.default)],
                label=db_field.verbose_name,
                help_text=db_field.help_text,
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description="Stickers")
    def stickers_link(self, obj):
        total = Sticker.objects.filter(is_active=True).count()
        return format_html(
            '<a href="{}">Gestionar los stickers</a> — {} activo(s) ahora mismo.',
            reverse("admin:whatsapp_sticker_changelist"),
            total,
        )

    def has_add_permission(self, request):
        # Fila única: se entra por "Cambiar", nunca por "Añadir"
        return not AgentSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Entra directo a la configuración: una lista de un solo elemento es un clic de más."""
        from django.shortcuts import redirect

        config = AgentSettings.load()
        return redirect("admin:whatsapp_agentsettings_change", config.pk)


class StickerForm(forms.ModelForm):
    """Sube cualquier imagen y la convierte en un sticker válido.

    WhatsApp rechaza el mensaje entero si el WebP no cumple sus medidas, y la
    persona que llena el banco no tiene por qué saber eso: sube el PNG que
    tenga y la conversión pasa aquí.
    """

    archivo = forms.FileField(
        required=False,
        label="Imagen",
        help_text=(
            "PNG, JPG, WebP o GIF. Se convierte sola a 512x512 WebP. "
            "Para que se vea como un sticker de verdad y no como una foto pegada, "
            "usa una imagen con FONDO TRANSPARENTE (PNG o WebP)."
        ),
    )

    class Meta:
        model = Sticker
        fields = ("label", "description", "is_active", "display_order")

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("archivo")
        if not upload:
            if not self.instance.pk:
                raise forms.ValidationError("Sube la imagen del sticker.")
            return cleaned
        raw = upload.read()
        try:
            data, animated = normalize(raw)
        except StickerError as exc:
            raise forms.ValidationError(str(exc)) from exc
        cleaned["_data"] = data
        cleaned["_animated"] = animated
        # Aviso, no error: bloquear la subida por esto sería peor que dejarla
        # pasar diciendo cómo va a verse. Lo emite el admin al guardar.
        self.flat_background = not has_transparency(raw)
        return cleaned

    def _post_clean(self):
        super()._post_clean()
        data = self.cleaned_data.get("_data")
        if data:
            self.instance.data = data
            self.instance.byte_size = len(data)
            self.instance.is_animated = self.cleaned_data.get("_animated", False)


@admin.register(Sticker)
class StickerAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    form = StickerForm
    list_display = ("vista", "label", "description", "peso", "is_active", "sent_count", "display_order")
    list_editable = ("is_active", "display_order")
    search_fields = ("label", "description")
    list_filter = ("is_active", "is_animated")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if getattr(form, "flat_background", False):
            messages.warning(
                request,
                f"«{obj.label}» se guardó, pero la imagen no tiene fondo transparente: en el "
                "chat se verá como un cuadro sobre el fondo, no como un sticker. Si quieres "
                "arreglarlo, súbela otra vez en PNG con transparencia.",
            )

    @admin.display(description="Sticker")
    def vista(self, obj):
        """Previsualización desde los propios bytes.

        No se enlaza la URL pública porque en local apunta al backend de
        producción, donde este sticker no existe, y porque un sticker
        desactivado no se sirve —justo el que hay que poder mirar aquí—.
        """
        if not obj.pk or not obj.data:
            return "—"
        source = f"data:image/webp;base64,{b64encode(bytes(obj.data)).decode()}"
        # Fondo a cuadros: sin él no se distingue un sticker con transparencia
        # de uno con fondo blanco, que es justo lo que hay que poder ver aquí
        return format_html(
            '<div style="width:64px;height:64px;background-image:linear-gradient(45deg,#ccc 25%,'
            "transparent 25%),linear-gradient(-45deg,#ccc 25%,transparent 25%),linear-gradient("
            "45deg,transparent 75%,#ccc 75%),linear-gradient(-45deg,transparent 75%,#ccc 75%);"
            'background-size:12px 12px;background-position:0 0,0 6px,6px -6px,-6px 0">'
            '<img src="{}" style="width:64px;height:64px;object-fit:contain"></div>',
            source,
        )

    @admin.display(description="Peso")
    def peso(self, obj):
        return f"{obj.byte_size / 1024:.0f} KB" if obj.byte_size else "—"
