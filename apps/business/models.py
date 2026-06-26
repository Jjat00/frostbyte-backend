from django.db import models
from django.utils.text import slugify

# Slug del negocio principal (Frostbyte). Todo lo que existia antes de la
# dimension multi-negocio pertenece aqui, y es el default cuando una peticion
# no especifica negocio (compatibilidad con el flujo previo).
MAIN_BUSINESS_SLUG = "frostbyte"


class Business(models.Model):
    """Negocio independiente dentro de Frostbyte.

    Cada Business (ej. "Frostbyte" bebidas en el 2º piso, "Frostbyte Food"
    comida en el 3er piso) tiene su propio catalogo, inventario y gastos, pero
    comparte el sistema (clientes, pedidos, dashboard consolidado).
    """

    name = models.CharField(
        max_length=120,
        unique=True,
        verbose_name="Nombre del negocio",
    )
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True, default="", verbose_name="Descripción")
    floor = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Piso",
        help_text="Piso físico del negocio (ej. 2 = bebidas, 3 = comida).",
    )
    color = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="Color de marca",
        help_text="Color para la UI (ej: blue, orange).",
    )
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden de visualización")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Negocio"
        verbose_name_plural = "Negocios"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @classmethod
    def get_main(cls):
        """Negocio principal (Frostbyte). Default cuando no se especifica negocio."""
        return cls.objects.filter(slug=MAIN_BUSINESS_SLUG).first()
