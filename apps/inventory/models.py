from django.db import models
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
