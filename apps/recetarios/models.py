from django.db import models
from django.utils.text import slugify


class RecipeBook(models.Model):
    """Recetario: guía de preparación paso a paso para bebidas"""

    class Difficulty(models.TextChoices):
        EASY = "easy", "Fácil"
        MEDIUM = "medium", "Media"
        HARD = "hard", "Difícil"

    name = models.CharField(max_length=200, verbose_name="Nombre")
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True, verbose_name="Descripción")
    category = models.ForeignKey(
        "products.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipe_books",
        verbose_name="Categoría",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipe_books",
        verbose_name="Producto",
    )
    product_variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipe_books",
        verbose_name="Variante de producto",
    )
    difficulty = models.CharField(
        max_length=20,
        choices=Difficulty.choices,
        default=Difficulty.MEDIUM,
        verbose_name="Dificultad",
    )
    prep_time = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Tiempo de preparación",
        help_text="Tiempo en minutos",
    )
    servings = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Porciones",
        help_text="Ej: 1 vaso de 16oz",
    )
    tips = models.TextField(blank=True, verbose_name="Tips y notas")
    video_url = models.URLField(blank=True, verbose_name="URL de video")
    image_url = models.URLField(blank=True, verbose_name="URL de imagen principal")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Recetario"
        verbose_name_plural = "Recetarios"
        ordering = ["category__display_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class RecipeBookStep(models.Model):
    """Paso de preparación de una receta"""

    recipe = models.ForeignKey(
        RecipeBook,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name="Receta",
    )
    step_number = models.PositiveIntegerField(verbose_name="Número de paso")
    instruction = models.TextField(verbose_name="Instrucción")
    image_url = models.URLField(blank=True, verbose_name="URL de imagen")
    tip = models.CharField(max_length=500, blank=True, verbose_name="Tip")

    class Meta:
        verbose_name = "Paso de receta"
        verbose_name_plural = "Pasos de receta"
        ordering = ["step_number"]
        unique_together = ["recipe", "step_number"]

    def __str__(self):
        return f"Paso {self.step_number} - {self.recipe.name}"


class RecipeBookIngredient(models.Model):
    """Ingrediente de una receta (para instrucciones de preparación)"""

    recipe = models.ForeignKey(
        RecipeBook,
        on_delete=models.CASCADE,
        related_name="ingredients",
        verbose_name="Receta",
    )
    name = models.CharField(max_length=200, verbose_name="Nombre")
    quantity = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Cantidad",
        help_text="Ej: 2 cucharadas, 200ml, al gusto",
    )
    unit = models.CharField(max_length=50, blank=True, verbose_name="Unidad")
    is_optional = models.BooleanField(default=False, verbose_name="Opcional")

    class Meta:
        verbose_name = "Ingrediente de receta"
        verbose_name_plural = "Ingredientes de receta"
        ordering = ["id"]

    def __str__(self):
        parts = [self.name]
        if self.quantity:
            parts.insert(0, self.quantity)
        if self.unit:
            parts.append(self.unit)
        return " ".join(parts)


class RecipeBookImage(models.Model):
    """Imagen adicional de una receta (galería)"""

    recipe = models.ForeignKey(
        RecipeBook,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="Receta",
    )
    image_url = models.URLField(verbose_name="URL de imagen")
    caption = models.CharField(max_length=200, blank=True, verbose_name="Descripción")
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden")

    class Meta:
        verbose_name = "Imagen de receta"
        verbose_name_plural = "Imágenes de receta"
        ordering = ["display_order"]

    def __str__(self):
        return f"Imagen {self.display_order} - {self.recipe.name}"
