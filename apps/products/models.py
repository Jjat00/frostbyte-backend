from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    """Categoría de productos (Granizados, Frappés, Micheladas, etc.)"""

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
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Product(models.Model):
    """Producto individual (Mango Biche, Café, Corona, etc.)"""

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="products",
        verbose_name="Categoría",
    )
    name = models.CharField(max_length=200, verbose_name="Nombre")
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(verbose_name="Descripción")
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
        if not self.slug:
            self.slug = slugify(self.name)
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
