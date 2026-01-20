from django.db import models
from django.conf import settings
from apps.products.models import ProductVariant


class UnitOfMeasure(models.Model):
    """Unidades de medida para materia prima"""

    name = models.CharField(max_length=50, verbose_name="Nombre")  # ej: "gramos", "mililitros"
    abbreviation = models.CharField(max_length=10, verbose_name="Abreviatura")  # ej: "g", "ml"

    class Meta:
        verbose_name = "Unidad de medida"
        verbose_name_plural = "Unidades de medida"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.abbreviation})"


class RawMaterial(models.Model):
    """Materia prima / ingredientes"""

    name = models.CharField(max_length=200, verbose_name="Nombre")  # ej: "Pulpa de Mango"
    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="materials",
        verbose_name="Unidad de medida",
    )
    current_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Stock actual",
    )
    minimum_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Stock mínimo",
        help_text="Nivel de stock para alertar reabastecimiento",
    )
    cost_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Costo por unidad",
        help_text="Costo en COP por unidad de medida",
    )
    supplier = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Proveedor",
    )
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Materia prima"
        verbose_name_plural = "Materias primas"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit.abbreviation})"

    @property
    def is_low_stock(self):
        """Verifica si el stock está por debajo del mínimo"""
        return self.current_stock <= self.minimum_stock

    @property
    def stock_status(self):
        """Retorna el estado del stock"""
        if self.current_stock <= 0:
            return "sin_stock"
        elif self.current_stock <= self.minimum_stock:
            return "bajo"
        return "normal"


class Recipe(models.Model):
    """Receta: ingredientes necesarios para cada variante de producto"""

    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="recipe_items",
        verbose_name="Variante de producto",
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.CASCADE,
        related_name="recipe_items",
        verbose_name="Materia prima",
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Cantidad",
        help_text="Cantidad necesaria por unidad de producto",
    )
    notes = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Notas",
        help_text="Instrucciones especiales",
    )

    class Meta:
        verbose_name = "Ingrediente de receta"
        verbose_name_plural = "Ingredientes de receta"
        unique_together = ["product_variant", "raw_material"]
        ordering = ["product_variant", "raw_material__name"]

    def __str__(self):
        return f"{self.product_variant}: {self.quantity} {self.raw_material.unit.abbreviation} de {self.raw_material.name}"

    @property
    def cost(self):
        """Costo de este ingrediente en la receta"""
        return self.quantity * self.raw_material.cost_per_unit


class PurchaseOrder(models.Model):
    """Orden de compra de materia prima"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PURCHASED = "purchased", "Comprado"
        CANCELLED = "cancelled", "Cancelado"

    order_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        verbose_name="Número de orden",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="purchase_orders",
        verbose_name="Creado por",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Estado",
    )
    notes = models.TextField(blank=True, verbose_name="Notas")
    estimated_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Total estimado",
    )
    actual_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Total real",
    )
    purchased_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de compra",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de creación")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Orden de compra"
        verbose_name_plural = "Órdenes de compra"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Orden #{self.order_number} - {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            import uuid
            from django.utils import timezone
            date_str = timezone.now().strftime("%Y%m%d")
            self.order_number = f"PO-{date_str}-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    def calculate_totals(self):
        """Recalcula los totales de la orden usando query directa (sin cache)"""
        from django.db.models import Sum, F, DecimalField
        from django.db.models.functions import Coalesce
        
        # Query directa para evitar problemas con prefetch cache
        items = PurchaseOrderItem.objects.filter(purchase_order=self)
        
        # Calcular total estimado
        estimated = items.aggregate(
            total=Coalesce(
                Sum(F('quantity_needed') * F('estimated_unit_price'), output_field=DecimalField()),
                0,
                output_field=DecimalField()
            )
        )['total']
        
        # Calcular total real (solo items con quantity_purchased y actual_unit_price)
        actual = items.filter(
            quantity_purchased__isnull=False,
            actual_unit_price__isnull=False
        ).aggregate(
            total=Coalesce(
                Sum(F('quantity_purchased') * F('actual_unit_price'), output_field=DecimalField()),
                0,
                output_field=DecimalField()
            )
        )['total']
        
        self.estimated_total = estimated or 0
        self.actual_total = actual or 0
        self.save(update_fields=["estimated_total", "actual_total"])


class PurchaseOrderItem(models.Model):
    """Item individual de una orden de compra"""

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Orden de compra",
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name="purchase_items",
        verbose_name="Materia prima",
    )
    quantity_needed = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Cantidad necesaria",
    )
    quantity_purchased = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Cantidad comprada",
    )
    estimated_unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Precio unitario estimado",
    )
    actual_unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Precio unitario real",
    )
    supplier = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Proveedor",
    )
    notes = models.TextField(blank=True, verbose_name="Notas")
    is_purchased = models.BooleanField(default=False, verbose_name="Comprado")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Item de orden de compra"
        verbose_name_plural = "Items de orden de compra"
        ordering = ["raw_material__name"]

    def __str__(self):
        return f"{self.quantity_needed} {self.raw_material.unit.abbreviation} de {self.raw_material.name}"

    @property
    def estimated_subtotal(self):
        """Subtotal estimado"""
        return self.quantity_needed * self.estimated_unit_price

    @property
    def actual_subtotal(self):
        """Subtotal real (después de la compra)"""
        if self.quantity_purchased and self.actual_unit_price:
            return self.quantity_purchased * self.actual_unit_price
        return None

    def mark_as_purchased(self, quantity, price, supplier=""):
        """Marca el item como comprado y actualiza el stock"""
        self.quantity_purchased = quantity
        self.actual_unit_price = price
        self.supplier = supplier
        self.is_purchased = True
        self.save()

        # Actualizar stock y precio del material
        self.raw_material.current_stock += quantity
        self.raw_material.cost_per_unit = price
        if supplier:
            self.raw_material.supplier = supplier
        self.raw_material.save()

        # Recalcular totales de la orden
        self.purchase_order.calculate_totals()
