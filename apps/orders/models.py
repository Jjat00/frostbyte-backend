from django.db import models
from django.utils import timezone
from decimal import Decimal
import uuid

from apps.products.models import ProductVariant


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
        self.subtotal = sum(item.subtotal for item in self.items.all())
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

    def mark_as_cancelled(self):
        """Cancelar pedido"""
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "updated_at"])

    @property
    def items_count(self):
        """Cantidad total de items en el pedido"""
        return sum(item.quantity for item in self.items.all())

    @property
    def is_active(self):
        """Verifica si el pedido está activo (no completado ni cancelado)"""
        return self.status in [self.Status.PENDING, self.Status.PREPARING, self.Status.READY]


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
        verbose_name="Subtotal",
    )
    notes = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Notas",
        help_text="Ej: sin azúcar, extra hielo",
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
        # Calcular subtotal
        self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)
        # Actualizar totales del pedido
        self.order.calculate_totals()
        self.order.save(update_fields=["subtotal", "total", "updated_at"])
