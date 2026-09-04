from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count
from .models import Order, OrderItem, Table, PageVisit, StoreSettings
from apps.search import PlainSearchAdminMixin


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["subtotal"]
    autocomplete_fields = ["product_variant"]


@admin.register(Order)
class OrderAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "order_number",
        "access_code",
        "customer_name",
        "order_type",
        "source",
        "status",
        "total",
        "is_paid",
        "payment_method",
        "get_items_count",
        "created_at",
    ]
    list_filter = ["status", "order_type", "source", "is_paid", "payment_method", "created_at"]
    search_fields = ["order_number", "customer_name", "customer_phone", "delivery_address"]
    readonly_fields = ["order_number", "access_code", "subtotal", "total", "user", "created_at", "updated_at", "completed_at"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    inlines = [OrderItemInline]

    fieldsets = (
        ("Pedido", {"fields": ("order_number", "access_code", "status", "order_type", "source", "user")}),
        ("Cliente", {"fields": ("customer_name", "customer_phone", "customer_notes", "table", "table_number", "table_floor")}),
        ("Domicilio", {"fields": ("delivery_address", "delivery_reference", "delivery_lat", "delivery_lng", "delivery_fee")}),
        ("Pago", {"fields": ("payment_method", "is_paid")}),
        ("Totales", {"fields": ("subtotal", "discount", "total")}),
        ("Fechas", {"fields": ("created_at", "updated_at", "completed_at")}),
    )

    actions = ["mark_as_preparing", "mark_as_ready", "mark_as_delivered", "mark_as_paid"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.annotate(items_total=Count("items"))
        return queryset

    @admin.display(description="Items")
    def get_items_count(self, obj):
        return getattr(obj, "items_total", 0)

    @admin.action(description="Marcar como Preparando")
    def mark_as_preparing(self, request, queryset):
        queryset.update(status=Order.Status.PREPARING)

    @admin.action(description="Marcar como Listo")
    def mark_as_ready(self, request, queryset):
        queryset.update(status=Order.Status.READY)

    @admin.action(description="Marcar como Entregado")
    def mark_as_delivered(self, request, queryset):
        from django.utils import timezone
        from .models import OrderItem
        
        # Marcar pedidos como entregados
        queryset.update(status=Order.Status.DELIVERED, completed_at=timezone.now())
        
        # Marcar todos los items de los pedidos como entregados
        order_ids = queryset.values_list('pk', flat=True)
        OrderItem.objects.filter(order_id__in=order_ids, is_delivered=False).update(
            is_delivered=True,
            delivered_at=timezone.now()
        )

    @admin.action(description="Marcar como pagado")
    def mark_as_paid(self, request, queryset):
        queryset.update(is_paid=True)


@admin.register(OrderItem)
class OrderItemAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "order",
        "product_variant",
        "quantity",
        "unit_price",
        "subtotal",
    ]
    list_filter = ["order__status", "product_variant__product__category"]
    search_fields = [
        "order__order_number",
        "product_variant__product__name",
    ]
    autocomplete_fields = ["order", "product_variant"]


@admin.register(Table)
class TableAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "floor",
        "table_number",
        "table_name",
        "visit_count",
        "is_active",
        "created_at",
        "updated_at",
    ]
    list_filter = ["floor", "is_active", "created_at"]
    search_fields = ["table_name", "table_number"]
    readonly_fields = ["visit_count", "created_at", "updated_at"]
    ordering = ["floor", "table_number"]

    fieldsets = (
        ("Información de Mesa", {"fields": ("floor", "table_number", "table_name", "is_active")}),
        ("Estadísticas", {"fields": ("visit_count",)}),
        ("Fechas", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(PageVisit)
class PageVisitAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "path",
        "page_name",
        "visit_count",
        "is_active",
        "created_at",
        "updated_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["path", "page_name"]
    readonly_fields = ["visit_count", "created_at", "updated_at"]
    ordering = ["-visit_count"]

    fieldsets = (
        ("Información de Página", {"fields": ("path", "page_name", "is_active")}),
        ("Estadísticas", {"fields": ("visit_count",)}),
        ("Fechas", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(StoreSettings)
class StoreSettingsAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "__str__",
        "is_open",
        "delivery_fee",
        "delivery_radius_km",
        "customer_ordering_enabled",
        "updated_at",
    ]
    readonly_fields = ["status_changed_at", "status_changed_by", "updated_at"]

    fieldsets = (
        ("Estado del local", {"fields": ("is_open", "status_changed_at", "status_changed_by")}),
        ("Domicilio", {
            "fields": (
                "delivery_fee",
                "delivery_radius_km",
                "delivery_area",
                "customer_ordering_enabled",
            ),
            "description": (
                "La zona se dibuja desde el dashboard; el JSON de aquí es para "
                "emergencias. Con la zona vacía manda el radio."
            ),
        }),
        ("Atención al cliente", {
            "fields": (
                "pickup_enabled",
                "opening_time",
                "eta_min_minutes",
                "eta_max_minutes",
            ),
            "description": (
                "Lo que el agente de WhatsApp le dice al cliente: si se puede pasar a "
                "recoger, a qué hora abrimos normalmente y cuánto nos demoramos. La "
                "demora es una estimación, no una promesa."
            ),
        }),
        ("Metadatos", {"fields": ("updated_at",)}),
    )

    def has_add_permission(self, request):
        # Singleton: solo se permite crear la primera instancia
        return not StoreSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
