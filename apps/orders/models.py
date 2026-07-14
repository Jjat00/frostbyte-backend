from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from decimal import Decimal
import uuid
import random
import string

from apps.products.models import ProductVariant
from apps.business.models import Business


class Table(models.Model):
    """Mesa o barra del restaurante. Identifica el punto de atención por piso."""

    table_number = models.IntegerField(
        verbose_name="Número de mesa",
        help_text="Número de la mesa dentro del piso (0=Barra, 1-N=Mesas)"
    )
    floor = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Piso",
        help_text="Piso donde está ubicada la mesa o barra"
    )
    table_name = models.CharField(
        max_length=50,
        verbose_name="Nombre",
        help_text="Nombre descriptivo de la mesa (ej: Mesa 1, Barra)"
    )
    visit_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Visitas",
        help_text="Cantidad de veces que se ha accedido a esta mesa"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activa"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mesa"
        verbose_name_plural = "Mesas"
        ordering = ["floor", "table_number"]
        # El número se reinicia por piso: "Mesa 1" puede existir en piso 2 y piso 3.
        unique_together = (("table_number", "floor"),)

    @property
    def label(self):
        """Etiqueta legible con piso, ej: 'Mesa 1 · Piso 3' o 'Barra · Piso 2'."""
        return f"{self.table_name} · Piso {self.floor}"

    def __str__(self):
        return self.label

    def register_visit(self):
        """Incrementa el contador de visitas"""
        self.visit_count += 1
        self.save(update_fields=["visit_count", "updated_at"])


class Order(models.Model):
    """Pedido de cliente"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PREPARING = "preparing", "Preparando"
        READY = "ready", "Listo"
        DELIVERED = "delivered", "Entregado"
        CANCELLED = "cancelled", "Cancelado"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Efectivo"
        TRANSFER = "transfer", "Transferencia"
        CARD = "card", "Tarjeta"
        NEQUI = "nequi", "Nequi"
        DAVIPLATA = "daviplata", "Daviplata"

    class OrderType(models.TextChoices):
        DINE_IN = "dine_in", "En el local"
        PICKUP = "pickup", "Para recoger"
        DELIVERY = "delivery", "Domicilio"

    class Source(models.TextChoices):
        STAFF = "staff", "Staff"
        CUSTOMER = "customer", "Cliente"

    # Tipo y origen del pedido
    order_type = models.CharField(
        max_length=20,
        choices=OrderType.choices,
        default=OrderType.DINE_IN,
        verbose_name="Tipo de pedido",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.STAFF,
        verbose_name="Origen",
        help_text="staff = creado por el equipo; customer = pedido en línea del cliente",
    )

    # Cliente autenticado (pedidos en línea). Null para pedidos del staff.
    user = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
        verbose_name="Cliente",
    )

    # Código de acceso para el cliente
    access_code = models.CharField(
        max_length=4,
        blank=True,
        verbose_name="Código de acceso",
        help_text="Código de 4 caracteres para que el cliente consulte su pedido",
    )

    # Identificador único del pedido
    order_number = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Número de pedido",
    )

    # Información del cliente
    customer_name = models.CharField(
        max_length=200,
        verbose_name="Nombre del cliente",
    )
    customer_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Teléfono",
    )
    customer_notes = models.TextField(
        blank=True,
        verbose_name="Notas del cliente",
        help_text="Instrucciones especiales o alergias",
    )
    # Mesa/barra donde se atiende al cliente. La FK es la referencia real
    # (los números se repiten por piso); table_number y table_floor quedan
    # denormalizados para mostrar y para no romper consumidores existentes.
    table = models.ForeignKey(
        "orders.Table",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
        verbose_name="Mesa",
    )
    table_number = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Número de mesa",
        help_text="Número de mesa dentro del piso (0=Barra, 1-N=Mesas)",
    )
    table_floor = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Piso",
        help_text="Piso de la mesa donde se atiende al cliente",
    )

    # Domicilio (solo aplica cuando order_type == delivery)
    delivery_address = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Dirección de entrega",
    )
    delivery_reference = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Referencia / indicaciones",
        help_text="Punto de referencia o indicaciones para el domiciliario",
    )
    delivery_lat = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        verbose_name="Latitud",
    )
    delivery_lng = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        verbose_name="Longitud",
    )
    delivery_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Tarifa de envío",
    )

    # Estado y pago
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Estado",
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        blank=True,
        verbose_name="Método de pago",
    )
    is_paid = models.BooleanField(
        default=False,
        verbose_name="Pagado",
    )

    # Totales
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Subtotal",
    )
    discount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Descuento",
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Total",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Creado")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado")
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Completado",
    )

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pedido #{self.order_number} - {self.customer_name}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        if not self.access_code:
            self.access_code = self._generate_access_code()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_access_code():
        """Genera un código de acceso único de 4 dígitos numéricos entre pedidos visibles"""
        from django.db.models import Q
        # Códigos en uso: pedidos activos o entregados sin pagar
        active_codes = set(
            Order.objects.filter(
                Q(status__in=[Order.Status.PENDING, Order.Status.PREPARING, Order.Status.READY])
                | Q(status=Order.Status.DELIVERED, is_paid=False)
            ).exclude(access_code="").values_list("access_code", flat=True)
        )
        for _ in range(100):
            code = "".join(random.choices(string.digits, k=4))
            if code not in active_codes:
                return code
        return uuid.uuid4().hex[:4].upper()

    def _generate_order_number(self):
        """Genera un número de pedido único basado en fecha y UUID"""
        date_part = timezone.now().strftime("%Y%m%d")
        uuid_part = uuid.uuid4().hex[:6].upper()
        return f"{date_part}-{uuid_part}"

    def calculate_totals(self):
        """Calcula subtotal y total basado en los items"""
        from django.db.models import Sum
        # Usar query directa para evitar caché
        result = OrderItem.objects.filter(order_id=self.pk).aggregate(
            total=Sum('subtotal')
        )
        self.subtotal = result['total'] or Decimal("0.00")
        self.total = self.subtotal - self.discount + (self.delivery_fee or Decimal("0.00"))
        return self.total

    def mark_as_preparing(self):
        """Marcar pedido como en preparación"""
        self.status = self.Status.PREPARING
        self.save(update_fields=["status", "updated_at"])

    def mark_as_ready(self):
        """Marcar pedido como listo"""
        self.status = self.Status.READY
        self.save(update_fields=["status", "updated_at"])

    def mark_as_delivered(self):
        """Marcar pedido como entregado"""
        self.status = self.Status.DELIVERED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])
        
        # Marcar todos los items como entregados
        OrderItem.objects.filter(order_id=self.pk, is_delivered=False).update(
            is_delivered=True,
            delivered_at=timezone.now()
        )

    def mark_as_cancelled(self):
        """Cancelar pedido"""
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "updated_at"])

    @property
    def items_count(self):
        """Cantidad total de items en el pedido"""
        from django.db.models import Sum
        result = OrderItem.objects.filter(order_id=self.pk).aggregate(
            total=Sum('quantity')
        )
        return result['total'] or 0

    @property
    def is_active(self):
        """Verifica si el pedido está activo (no completado ni cancelado)"""
        return self.status in [self.Status.PENDING, self.Status.PREPARING, self.Status.READY]

    @property
    def paid_total(self):
        """Total pagado (capado al total del pedido para considerar descuentos)"""
        from django.db.models import Sum
        result = OrderItem.objects.filter(order_id=self.pk, is_paid=True).aggregate(
            total=Sum('subtotal')
        )
        raw_paid = result['total'] or Decimal("0.00")
        return min(raw_paid, self.total)

    @property
    def pending_total(self):
        """Total pendiente por pagar"""
        return max(Decimal("0.00"), self.total - self.paid_total)

    @property
    def paid_items_count(self):
        """Cantidad de items pagados"""
        return OrderItem.objects.filter(order_id=self.pk, is_paid=True).count()

    @property
    def unpaid_items_count(self):
        """Cantidad de items sin pagar"""
        return OrderItem.objects.filter(order_id=self.pk, is_paid=False).count()

    @property
    def delivered_items_count(self):
        """Cantidad de items entregados"""
        return OrderItem.objects.filter(order_id=self.pk, is_delivered=True).count()

    @property
    def undelivered_items_count(self):
        """Cantidad de items sin entregar"""
        return OrderItem.objects.filter(order_id=self.pk, is_delivered=False).count()

    def update_payment_status(self):
        """Actualiza el estado de pago basado en los items"""
        total_items = OrderItem.objects.filter(order_id=self.pk).count()
        paid_items = OrderItem.objects.filter(order_id=self.pk, is_paid=True).count()
        
        if total_items > 0:
            all_paid = (total_items == paid_items)
            if all_paid != self.is_paid:
                self.is_paid = all_paid
                self.save(update_fields=["is_paid", "updated_at"])

    def update_delivery_status(self):
        """Actualiza el estado de entrega basado en los items"""
        total_items = OrderItem.objects.filter(order_id=self.pk).count()
        delivered_items = OrderItem.objects.filter(order_id=self.pk, is_delivered=True).count()

        if total_items > 0:
            all_delivered = (total_items == delivered_items)
            if all_delivered and self.status != self.Status.DELIVERED:
                self.status = self.Status.DELIVERED
                self.completed_at = timezone.now()
                self.save(update_fields=["status", "completed_at", "updated_at"])

    def sync_status_from_items(self):
        """Recalcula el estado del pedido a partir del avance de preparación de
        sus items (lo que marcan las cocinas en el KDS).

        Un pedido con items en dos negocios queda 'listo' solo cuando TODAS las
        cocinas terminaron su parte. No toca pedidos entregados ni cancelados:
        esos estados son terminales y se manejan aparte.
        """
        if self.status in [self.Status.DELIVERED, self.Status.CANCELLED]:
            return

        items = list(
            OrderItem.objects.filter(order_id=self.pk).values_list("prep_status", flat=True)
        )
        if not items:
            return

        if all(s == OrderItem.PrepStatus.READY for s in items):
            new_status = self.Status.READY
        elif any(s in (OrderItem.PrepStatus.PREPARING, OrderItem.PrepStatus.READY) for s in items):
            new_status = self.Status.PREPARING
        else:
            new_status = self.Status.PENDING

        if new_status != self.status:
            self.status = new_status
            self.save(update_fields=["status", "updated_at"])

    @property
    def business_breakdown(self):
        """Resumen por negocio para la vista del mesero: cuántos items hay de
        cada negocio y cuántos ya están listos. Permite mostrar un semáforo
        ('Food: 2/3 listo') sin que el mesero entre a cada cocina.
        """
        from django.db.models import Count, Q

        rows = (
            OrderItem.objects.filter(order_id=self.pk, business__isnull=False)
            .values("business__slug", "business__name", "business__color")
            .annotate(
                total=Count("id"),
                ready=Count("id", filter=Q(prep_status=OrderItem.PrepStatus.READY)),
                delivered=Count("id", filter=Q(is_delivered=True)),
            )
            .order_by("business__name")
        )
        return [
            {
                "slug": r["business__slug"],
                "name": r["business__name"],
                "color": r["business__color"],
                "total_items": r["total"],
                "ready_items": r["ready"],
                "delivered_items": r["delivered"],
            }
            for r in rows
        ]


class OrderItem(models.Model):
    """Item individual de un pedido"""

    class PrepStatus(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PREPARING = "preparing", "Preparando"
        READY = "ready", "Listo"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Pedido",
    )
    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="Producto",
    )
    # Negocio al que pertenece el item (Frostbyte / Frostbyte Food). Se
    # denormaliza desde product_variant.product.business al crear el item para
    # que cada cocina (KDS) filtre por su negocio sin recorrer la relación.
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="Negocio",
    )
    # Estado de preparación a nivel de item: cada cocina marca SOLO sus items.
    # Es independiente del estado del pedido (que se deriva de aquí).
    prep_status = models.CharField(
        max_length=20,
        choices=PrepStatus.choices,
        default=PrepStatus.PENDING,
        verbose_name="Estado de preparación",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="Cantidad",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Precio unitario",
    )
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Subtotal",
    )
    notes = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Notas",
        help_text="Ej: sin azúcar, extra hielo",
    )
    
    # Campos para pago individual
    is_paid = models.BooleanField(
        default=False,
        verbose_name="Pagado",
    )
    payment_method = models.CharField(
        max_length=20,
        choices=Order.PaymentMethod.choices,
        blank=True,
        verbose_name="Método de pago",
    )
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de pago",
    )
    
    # Campos para entrega individual
    is_delivered = models.BooleanField(
        default=False,
        verbose_name="Entregado",
    )
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de entrega",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Item de pedido"
        verbose_name_plural = "Items de pedido"

    def __str__(self):
        product_name = self.product_variant.product.name
        variant_name = self.product_variant.name
        return f"{self.quantity}x {product_name} ({variant_name})"

    def save(self, *args, **kwargs):
        # Heredar el negocio del producto si no se asignó explícitamente.
        if not self.business_id and self.product_variant_id:
            self.business_id = self.product_variant.product.business_id
        # Calcular subtotal si no se especificó update_fields
        if not kwargs.get('update_fields'):
            self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    def mark_preparing(self):
        """La cocina empezó a preparar este item"""
        self.prep_status = self.PrepStatus.PREPARING
        self.save(update_fields=["prep_status"])

    def mark_ready(self):
        """La cocina terminó este item"""
        self.prep_status = self.PrepStatus.READY
        self.save(update_fields=["prep_status"])

    def reset_prep(self):
        """Volver el item a pendiente de preparación"""
        self.prep_status = self.PrepStatus.PENDING
        self.save(update_fields=["prep_status"])

    def mark_as_paid(self, payment_method=""):
        """Marcar item como pagado"""
        self.is_paid = True
        self.payment_method = payment_method
        self.paid_at = timezone.now()
        self.save(update_fields=["is_paid", "payment_method", "paid_at"])

    def change_payment_method(self, payment_method):
        """Cambiar el método de pago de un item"""
        self.payment_method = payment_method
        if payment_method and not self.is_paid:
            self.is_paid = True
            self.paid_at = timezone.now()
        self.save(update_fields=["payment_method", "is_paid", "paid_at"])

    def mark_as_delivered(self):
        """Marcar item como entregado"""
        self.is_delivered = True
        self.delivered_at = timezone.now()
        self.save(update_fields=["is_delivered", "delivered_at"])

    def unmark_as_delivered(self):
        """Desmarcar item como entregado"""
        self.is_delivered = False
        self.delivered_at = None
        self.save(update_fields=["is_delivered", "delivered_at"])


@receiver(post_delete, sender=OrderItem)
def recalculate_order_totals_on_item_delete(sender, instance, **kwargs):
    """Recalcular totales del pedido cuando se elimina un item"""
    try:
        order = instance.order
        order.calculate_totals()
        order.save(update_fields=["subtotal", "total", "updated_at"])
        order.update_payment_status()
    except Order.DoesNotExist:
        pass


class PageVisit(models.Model):
    """Registro de visitas a páginas específicas del sitio"""

    path = models.CharField(
        max_length=200,
        unique=True,
        verbose_name="Ruta",
        help_text="Ruta de la página (ej: /, /menu, /contacto)"
    )
    page_name = models.CharField(
        max_length=100,
        verbose_name="Nombre",
        help_text="Nombre descriptivo de la página"
    )
    visit_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Visitas",
        help_text="Cantidad de veces que se ha accedido a esta página"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activa"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Visita de página"
        verbose_name_plural = "Visitas de páginas"
        ordering = ["-visit_count"]

    def __str__(self):
        return f"{self.page_name} ({self.path})"

    def register_visit(self):
        """Incrementa el contador de visitas"""
        self.visit_count += 1
        self.save(update_fields=["visit_count", "updated_at"])


class StoreSettings(models.Model):
    """Configuración operativa del local (singleton, editable desde el admin).

    Permite abrir/cerrar el local, ajustar la tarifa de domicilio y
    activar/desactivar los pedidos a domicilio del cliente sin redeploy.
    """

    is_open = models.BooleanField(
        default=True,
        verbose_name="Local abierto",
        help_text="Si está cerrado, el cliente ve el estado 'Cerrado' y no puede hacer pedidos.",
    )
    delivery_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("2000.00"),
        verbose_name="Tarifa de envío",
        help_text="Costo fijo del domicilio que se suma al total del pedido",
    )
    customer_ordering_enabled = models.BooleanField(
        default=False,
        verbose_name="Domicilios en línea activos",
        help_text="Interruptor general para habilitar/pausar los pedidos a domicilio del cliente",
    )
    status_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Estado cambiado el",
        help_text="Última vez que se abrió/cerró el local.",
    )
    status_changed_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Estado cambiado por",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado")

    class Meta:
        verbose_name = "Configuración de la tienda"
        verbose_name_plural = "Configuración de la tienda"

    def __str__(self):
        estado = "abierto" if self.is_open else "cerrado"
        return f"Configuración de la tienda ({estado})"

    def save(self, *args, **kwargs):
        # Fuerza singleton: siempre la misma fila
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Devuelve la única instancia de configuración, creándola si no existe."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
