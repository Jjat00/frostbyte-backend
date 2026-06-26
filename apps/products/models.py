from django.db import models
from django.utils.text import slugify

from apps.business.models import Business


def _unique_slug(model, name, instance_pk=None):
    """Genera un slug unico (global) a partir de `name`.

    Mantiene el slug unico en toda la tabla (el lookup por slug de la API lo
    requiere) pero permite que dos negocios usen el mismo nombre: el segundo
    recibe un sufijo numerico.
    """
    base = slugify(name)
    candidate = base
    counter = 1
    while model.objects.exclude(pk=instance_pk).filter(slug=candidate).exists():
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


class Category(models.Model):
    """Categoría de productos (Granizados, Frappés, Micheladas, etc.)"""

    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="categories",
        verbose_name="Negocio",
    )
    name = models.CharField(max_length=100, verbose_name="Nombre")
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True, verbose_name="Descripción")
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden de visualización")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    show_extras = models.BooleanField(default=True, verbose_name="Mostrar sección extra")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Category, self.name, self.pk)
        super().save(*args, **kwargs)


class Product(models.Model):
    """Producto individual (Mango Biche, Café, Corona, etc.)"""

    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="Negocio",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="products",
        verbose_name="Categoría",
    )
    name = models.CharField(max_length=200, verbose_name="Nombre")
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(verbose_name="Descripción")
    history = models.TextField(
        blank=True,
        default="",
        verbose_name="Historia",
        help_text="Breve historia u origen del cóctel (se muestra en el menú digital).",
    )
    image_url = models.URLField(blank=True, verbose_name="URL de imagen")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    is_coming_soon = models.BooleanField(default=False, verbose_name="Próximamente")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        ordering = ["category", "name"]

    def __str__(self):
        return f"{self.name} ({self.category.name})"

    def save(self, *args, **kwargs):
        # El negocio del producto siempre se hereda de su categoría, asi se
        # mantiene consistente product.business == category.business.
        if self.category_id:
            new_business_id = self.category.business_id
            # No permitir mover un producto con ventas a otro negocio: las
            # estadisticas historicas se derivan del negocio del producto.
            if self.pk and self.business_id and self.business_id != new_business_id:
                from apps.orders.models import OrderItem  # import local evita ciclo
                if OrderItem.objects.filter(product_variant__product_id=self.pk).exists():
                    from django.core.exceptions import ValidationError
                    raise ValidationError(
                        "No se puede cambiar el negocio de un producto con ventas registradas."
                    )
            self.business_id = new_business_id
        if not self.slug:
            self.slug = _unique_slug(Product, self.name, self.pk)
        super().save(*args, **kwargs)


class ProductVariant(models.Model):
    """Variantes de producto (tamaños, bases, etc.)"""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
        verbose_name="Producto",
    )
    name = models.CharField(max_length=100, verbose_name="Nombre")  # ej: "Pequeño", "Grande", "Base Milo"
    sku = models.CharField(max_length=50, unique=True, verbose_name="SKU")
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Precio de venta"
    )
    is_default = models.BooleanField(default=False, verbose_name="Es variante por defecto")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Variante de Producto"
        verbose_name_plural = "Variantes de Producto"
        ordering = ["product", "name"]

    def __str__(self):
        return f"{self.product.name} - {self.name}"

    def save(self, *args, **kwargs):
        # Generar SKU automáticamente si no se proporciona, garantizando unicidad
        if not self.sku:
            base = slugify(f"{self.product.name}-{self.name}").upper().replace("-", "")[:42]
            self.sku = base
            # Si el SKU ya existe (otra variante o producto), añadir sufijo numérico
            qs = ProductVariant.objects.filter(sku=self.sku)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                counter = 1
                while True:
                    candidate = f"{base}_{counter}"[:50]
                    if not ProductVariant.objects.filter(sku=candidate).exclude(pk=self.pk).exists():
                        self.sku = candidate
                        break
                    counter += 1
        super().save(*args, **kwargs)


class ModifierGroup(models.Model):
    """Grupo de opciones configurables de un producto.

    Ejemplos para Frostbyte Food: "Carnes" (elige 2-3), "Salsas" (elige 2-3 de 8),
    "Bebida" (opcional, elige 0-1). Pertenece a un negocio y es reutilizable: un
    mismo grupo puede asociarse a varios productos via ProductModifierGroup.

    Reglas de seleccion:
      - `min_select` = 0  -> grupo opcional; > 0 -> obligatorio.
      - `max_select` = 1  -> seleccion unica (radio); > 1 -> multiple (checkbox).
    """

    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="modifier_groups",
        verbose_name="Negocio",
    )
    name = models.CharField(max_length=120, verbose_name="Nombre")
    description = models.TextField(blank=True, default="", verbose_name="Descripción")
    min_select = models.PositiveSmallIntegerField(default=0, verbose_name="Mínimo a elegir")
    max_select = models.PositiveSmallIntegerField(default=1, verbose_name="Máximo a elegir")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Grupo de opciones"
        verbose_name_plural = "Grupos de opciones"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.business.name})"

    @property
    def is_required(self):
        return self.min_select > 0

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.min_select > self.max_select:
            raise ValidationError("El mínimo a elegir no puede ser mayor que el máximo.")


class ModifierOption(models.Model):
    """Opción individual dentro de un grupo (ej: Pollo, Mayonesa, Personal)."""

    group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name="options",
        verbose_name="Grupo",
    )
    name = models.CharField(max_length=120, verbose_name="Nombre")
    price_delta = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Precio adicional",
        help_text="Cuánto suma (o resta, si es negativo) esta opción al precio base.",
    )
    is_default = models.BooleanField(default=False, verbose_name="Preseleccionada")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Opción de grupo"
        verbose_name_plural = "Opciones de grupo"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        # No marcar mas opciones por defecto que el maximo que el cliente puede elegir.
        if self.is_default and self.group_id and self.group.max_select:
            others = self.group.options.filter(is_default=True).exclude(pk=self.pk).count()
            if others + 1 > self.group.max_select:
                raise ValidationError(
                    "Hay más opciones preseleccionadas que el máximo permitido del grupo."
                )


class ProductModifierGroup(models.Model):
    """Asocia un grupo de opciones a un producto, con overrides opcionales.

    Permite reutilizar un grupo "Carnes (elige 2-3)" y, por producto, ajustar el
    minimo/maximo (ej. una variante de salchipapa que obliga exactamente 3).
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="modifier_links",
        verbose_name="Producto",
    )
    group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name="product_links",
        verbose_name="Grupo",
    )
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    min_select_override = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Mínimo (override)"
    )
    max_select_override = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Máximo (override)"
    )
    is_active = models.BooleanField(default=True, verbose_name="Activo")

    class Meta:
        verbose_name = "Grupo de opciones del producto"
        verbose_name_plural = "Grupos de opciones del producto"
        ordering = ["display_order"]
        unique_together = [("product", "group")]

    def __str__(self):
        return f"{self.product.name} · {self.group.name}"

    @property
    def effective_min(self):
        return self.min_select_override if self.min_select_override is not None else self.group.min_select

    @property
    def effective_max(self):
        return self.max_select_override if self.max_select_override is not None else self.group.max_select

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.product_id and self.group_id and self.product.business_id != self.group.business_id:
            raise ValidationError("El grupo y el producto deben pertenecer al mismo negocio.")

        # El rango efectivo (con overrides) debe ser coherente.
        if self.group_id:
            eff_min = self.min_select_override if self.min_select_override is not None else self.group.min_select
            eff_max = self.max_select_override if self.max_select_override is not None else self.group.max_select
            if eff_min is not None and eff_max is not None and eff_min > eff_max:
                raise ValidationError("El mínimo efectivo no puede superar el máximo efectivo.")
