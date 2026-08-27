from django.contrib import admin
from django.utils.html import format_html, format_html_join

from .models import ChatMessage, SentMessage, WebhookEvent, WhatsAppContact


@admin.register(WhatsAppContact)
class WhatsAppContactAdmin(admin.ModelAdmin):
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
class SentMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "to_phone", "wamid")
    search_fields = ("to_phone", "wamid")
    readonly_fields = [f.name for f in SentMessage._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
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
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "contact_phone", "phone_number_id", "status")
    list_filter = ("status",)
    search_fields = ("contact_phone", "idempotency_key")
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False
