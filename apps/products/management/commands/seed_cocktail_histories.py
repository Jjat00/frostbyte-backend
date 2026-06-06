from django.core.management.base import BaseCommand

from apps.products.models import Product

# Historias breves y reales de los cócteles clásicos, indexadas por slug del producto.
# Reglas de contenido:
#   - Cada historia incluye el año exacto mejor documentado de su origen.
#   - Las variaciones cuentan la historia de su cóctel ORIGINAL
#     (Margarota -> Margarita, Caipiroshka -> Caipirinha, Blue Long -> Long Island Iced Tea).
COCKTAIL_HISTORIES = {
    "mojito": (
        "Aunque sus raíces cubanas son mucho más antiguas, la primera receta escrita "
        "del Mojito aparece en La Habana en 1927. Nació del encuentro del ron con la "
        "lima, la menta y el azúcar, y se volvió el trago insignia de la isla."
    ),
    "margarita": (
        "La Margarita fue creada en 1942 por el barman Pancho Morales en Ciudad "
        "Juárez, México. Cuando una clienta pidió un trago que él no recordaba, "
        "improvisó esta mezcla de tequila, lima y triple sec que conquistó al mundo."
    ),
    # Variación de la Margarita: muestra la historia del cóctel original.
    "margarota": (
        "La Margarota parte de la Margarita original, creada en 1942 por el barman "
        "Pancho Morales en Ciudad Juárez, México: una mezcla de tequila, lima y triple "
        "sec que conquistó al mundo, aquí servida en su formato más generoso."
    ),
    # Variación de la Caipirinha: muestra la historia del cóctel original.
    "caipiroshka": (
        "La Caipiroshka nace de la Caipirinha, el cóctel nacional de Brasil, creado "
        "en 1918 en el interior de São Paulo como remedio campesino contra la gripe "
        "española, con cachaça, lima y azúcar."
    ),
    "cuba-libre": (
        "El Cuba Libre nació en La Habana en 1900, cuando la recién llegada Coca-Cola "
        "estadounidense se mezcló con el ron cubano. Un brindis por \"¡Cuba libre!\" "
        "tras la independencia le dio su nombre."
    ),
    "gintonic": (
        "El Gin Tonic surgió en la India colonial británica, donde la ginebra "
        "suavizaba la amarga quinina que combatía la malaria. Su primera mención "
        "impresa como bebida data de 1868."
    ),
    "moscow-mule": (
        "El Moscow Mule se inventó en 1941 en Nueva York, cuando tres comerciantes "
        "unieron un vodka que no se vendía con una cerveza de jengibre sin salida. Su "
        "icónica taza de cobre lo hizo legendario."
    ),
    # Variación del Long Island Iced Tea: muestra la historia del cóctel original.
    "blue-long": (
        "El Blue Long es la versión azul del Long Island Iced Tea, creado en 1972 por "
        "Robert \"Rosebud\" Butt en Long Island, Nueva York, durante un concurso de "
        "coctelería que reunía varios destilados en un solo trago."
    ),
}


class Command(BaseCommand):
    help = "Siembra historias breves para los cócteles clásicos (categoría mocktails)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Sobrescribe la historia aunque el cóctel ya tenga una.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        updated = 0
        skipped = 0
        missing = []

        for slug, history in COCKTAIL_HISTORIES.items():
            product = Product.objects.filter(slug=slug).first()
            if product is None:
                missing.append(slug)
                continue

            if product.history.strip() and not force:
                skipped += 1
                continue

            product.history = history
            product.save(update_fields=["history", "updated_at"])
            updated += 1
            self.stdout.write(self.style.SUCCESS(f"  + {product.name}"))

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Historias actualizadas: {updated} | sin cambios: {skipped}"
            )
        )
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    "Sin producto con estos slugs (omitidos): " + ", ".join(missing)
                )
            )
