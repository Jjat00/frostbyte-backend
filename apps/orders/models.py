from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from decimal import Decimal
import uuid

from apps.products.models import ProductVariant


class Table(models.Model):
    """Mesa del restaurante para tracking de visitas"""

    table_number = models.IntegerField(
        unique=True,
        verbose_name="Número de mesa",
        help_text="Número de la mesa (0=Barra, 1-N=Mesas)"
    )
    table_name = models.CharField(
        max_length=50,
        verbose_name="Nombre",
        help_text="Nombre descriptivo de la mesa"
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
        ordering = ["table_number"]

    def __str__(self):
        if self.table_number == 0:
            return self.table_name
        return f"{self.table_name} (#{self.table_number})"

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
    table_number = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Número de mesa",
        help_text="Número de mesa donde se atiende al cliente (0=Barra, 1-5=Mesas)",
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
        super().save(*args, **kwargs)

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
        self.total = self.subtotal - self.discount
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
        """Total pagado (suma de items pagados)"""
        from django.db.models import Sum
        result = OrderItem.objects.filter(order_id=self.pk, is_paid=True).aggregate(
            total=Sum('subtotal')
        )
        return result['total'] or Decimal("0.00")

    @property
    def pending_total(self):
        """Total pendiente por pagar"""
        return self.total - self.paid_total

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


class OrderItem(models.Model):
    """Item individual de un pedido"""

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
        # Calcular subtotal si no se especificó update_fields
        if not kwargs.get('update_fields'):
            self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)
    
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
