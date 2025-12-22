from django.core.management.base import BaseCommand
from apps.products.models import Category, Product, ProductVariant


class Command(BaseCommand):
    help = "Carga datos de ejemplo para Frostbyte"

    def handle(self, *args, **options):
        self.stdout.write("Cargando datos de ejemplo...")

        # Crear categorías
        categories_data = [
            {"name": "Granizados", "description": "Hielo triturado a la perfección con los sabores frutales más intensos.", "display_order": 1},
            {"name": "Frappés", "description": "Cremosas y heladas creaciones que fusionan sabores clásicos con energía del futuro.", "display_order": 2},
            {"name": "Sodas Micheladas", "description": "Bebidas refrescantes con soda y preparado de michelada.", "display_order": 3},
            {"name": "Micheladas", "description": "La combinación perfecta de cerveza, limón, salsas y especias.", "display_order": 4},
            {"name": "Cervezas", "description": "Selección de las mejores cervezas nacionales e importadas.", "display_order": 5},
            {"name": "Cuates", "description": "Combos especiales para compartir.", "display_order": 6},
            {"name": "Mocktails", "description": "Cócteles sin alcohol, toda la diversión sin el mareo.", "display_order": 7},
            {"name": "Shots", "description": "Shots de licor premium para agregar a tus bebidas.", "display_order": 8},
            {"name": "Vinos", "description": "Selección de vinos por copa o botella.", "display_order": 9},
            {"name": "Desguayabator", "description": "Bebida especial anti-resaca para los valientes.", "display_order": 10},
        ]

        categories = {}
        for cat_data in categories_data:
            category, created = Category.objects.get_or_create(
                name=cat_data["name"],
                defaults=cat_data
            )
            categories[cat_data["name"]] = category
            status = "✓ Creada" if created else "→ Ya existe"
            self.stdout.write(f"  {status}: {category.name}")

        # Crear productos de Granizados
        granizados = [
            {"name": "Mango Biche", "description": "La acidez perfecta del mango verde con sal y limón."},
            {"name": "Maracuyá", "description": "Pura fruta de la pasión convertida en una experiencia helada vibrante."},
            {"name": "Lulo", "description": "El sabor único y ácido del lulo colombiano en su máxima expresión."},
            {"name": "Fresa", "description": "Dulce y refrescante granizado de fresa natural con un toque de frescura."},
        ]

        for prod_data in granizados:
            product, created = Product.objects.get_or_create(
                name=prod_data["name"],
                category=categories["Granizados"],
                defaults={"description": prod_data["description"]}
            )
            if created:
                # Crear variantes de tamaño
                ProductVariant.objects.create(product=product, name="Pequeño", sku=f"GRAN-{product.slug.upper()[:4]}-P")
                ProductVariant.objects.create(product=product, name="Grande", sku=f"GRAN-{product.slug.upper()[:4]}-G", is_default=True)
                self.stdout.write(f"    ✓ Producto creado: {product.name}")
            else:
                self.stdout.write(f"    → Ya existe: {product.name}")

        # Crear productos de Frappés
        frappes = [
            {"name": "Café", "description": "El clásico e intenso sabor del café en su versión más helada y cremosa."},
            {"name": "Oreo", "description": "Irresistible mezcla de galletas oreo trituradas con crema de vainilla."},
            {"name": "Fresa Frappé", "description": "Refrescante y dulce frappé elaborado con fresas naturales seleccionadas.", "is_coming_soon": True},
            {"name": "Brownie", "description": "Decadente frappé de chocolate con trozos reales de brownie húmedo."},
        ]

        for prod_data in frappes:
            is_coming_soon = prod_data.pop("is_coming_soon", False)
            product, created = Product.objects.get_or_create(
                name=prod_data["name"],
                category=categories["Frappés"],
                defaults={"description": prod_data["description"], "is_coming_soon": is_coming_soon}
            )
            if created:
                if product.name in ["Oreo", "Brownie"]:
                    # Variantes de base
                    ProductVariant.objects.create(product=product, name="Base Chocolisto", sku=f"FRAP-{product.slug.upper()[:4]}-CHOC")
                    ProductVariant.objects.create(product=product, name="Base Milo", sku=f"FRAP-{product.slug.upper()[:4]}-MILO", is_default=True)
                else:
                    ProductVariant.objects.create(product=product, name="Regular", sku=f"FRAP-{product.slug.upper()[:4]}-REG", is_default=True)
                self.stdout.write(f"    ✓ Producto creado: {product.name}")
            else:
                self.stdout.write(f"    → Ya existe: {product.name}")

        # Crear productos de Micheladas
        micheladas = [
            {"name": "Michelada Poker", "description": "Cerveza Poker bien fría con nuestra mezcla secreta de salsas, limón y especias.", "sku": "MICH-POKER"},
            {"name": "Michelada Budweiser", "description": "Cerveza Budweiser premium con nuestra mezcla clásica de clamato, salsas y limón.", "sku": "MICH-BUD"},
            {"name": "Michelada Corona", "description": "Cerveza Corona Extra con nuestra receta especial de michelada y un toque de limón fresco.", "sku": "MICH-CORONA"},
        ]

        for prod_data in micheladas:
            sku = prod_data.pop("sku")
            product, created = Product.objects.get_or_create(
                name=prod_data["name"],
                category=categories["Micheladas"],
                defaults={"description": prod_data["description"]}
            )
            if created:
                ProductVariant.objects.create(product=product, name="Regular", sku=sku, is_default=True)
                self.stdout.write(f"    ✓ Producto creado: {product.name}")
            else:
                self.stdout.write(f"    → Ya existe: {product.name}")

        # Crear shots (para envenenar)
        shots = [
            {"name": "Ginebra Beefeater", "description": "Shot de ginebra premium Beefeater para agregar a tu bebida.", "sku": "SHOT-GINEBRA"},
            {"name": "Vodka Absolut", "description": "Shot de vodka sueco Absolut para potenciar tu granizado.", "sku": "SHOT-VODKA"},
            {"name": "Whisky Jack Daniels", "description": "Shot de whisky Tennessee Jack Daniel's.", "sku": "SHOT-WHISKY"},
            {"name": "Tequila Jose Cuervo", "description": "Shot de tequila mexicano Jose Cuervo.", "sku": "SHOT-TEQUILA"},
            {"name": "Ron Bacardi", "description": "Shot de ron blanco Bacardi.", "sku": "SHOT-RON"},
            {"name": "Aguardiente Nariño", "description": "Shot de aguardiente premium Nariño.", "sku": "SHOT-AGUARD"},
        ]

        for prod_data in shots:
            sku = prod_data.pop("sku")
            product, created = Product.objects.get_or_create(
                name=prod_data["name"],
                category=categories["Shots"],
                defaults={"description": prod_data["description"]}
            )
            if created:
                ProductVariant.objects.create(product=product, name="Shot", sku=sku, is_default=True)
                self.stdout.write(f"    ✓ Producto creado: {product.name}")
            else:
                self.stdout.write(f"    → Ya existe: {product.name}")

        self.stdout.write(self.style.SUCCESS("\n✅ Datos de ejemplo cargados exitosamente!"))
        self.stdout.write(f"\nResumen:")
        self.stdout.write(f"  - Categorías: {Category.objects.count()}")
        self.stdout.write(f"  - Productos: {Product.objects.count()}")
        self.stdout.write(f"  - Variantes: {ProductVariant.objects.count()}")

