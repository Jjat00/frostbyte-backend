from django.core.management.base import BaseCommand
from apps.products.models import Category, Product, ProductVariant
from decimal import Decimal
import hashlib


def generate_sku(prefix, product_slug, variant_name):
    """Genera un SKU único basado en el producto y variante"""
    base = f"{prefix}-{product_slug}-{variant_name}"
    # Usar hash corto para garantizar unicidad
    hash_suffix = hashlib.md5(base.encode()).hexdigest()[:4].upper()
    return f"{prefix}-{product_slug[:6].upper()}-{variant_name[:3].upper()}-{hash_suffix}"


class Command(BaseCommand):
    help = "Carga datos de ejemplo para productos de Frostbyte"

    def handle(self, *args, **options):
        self.stdout.write("Cargando datos de ejemplo...")

        # Limpiar datos existentes
        ProductVariant.objects.all().delete()
        Product.objects.all().delete()
        Category.objects.all().delete()

        # ============== CATEGORÍAS ==============
        categories_data = [
            {"name": "Granizados", "slug": "granizados", "description": "Hielo triturado con sabores frutales intensos", "display_order": 1},
            {"name": "Frappés", "slug": "frappes", "description": "Cremosas y heladas creaciones", "display_order": 2},
            {"name": "Sodas Italianas", "slug": "sodas-italianas", "description": "Refrescantes, burbujeantes y llenas de sabor frutal", "display_order": 3},
            {"name": "Micheladas", "slug": "micheladas", "description": "Cerveza con limón, salsas y especias", "display_order": 4},
            {"name": "Cervezas", "slug": "cervezas", "description": "Las mejores cervezas bien frías", "display_order": 5},
            {"name": "Los Cuates", "slug": "cuates", "description": "Cócteles listos con tequila mexicano", "display_order": 6},
            {"name": "Cócteles", "slug": "mocktails", "description": "Bebidas clásicas y creaciones de la casa", "display_order": 7},
            {"name": "Shots", "slug": "shots", "description": "Licores premium servidos puros", "display_order": 8},
            {"name": "Vinos", "slug": "vinos", "description": "Vinos tintos de las mejores viñas chilenas", "display_order": 9},
            {"name": "Desguayabator", "slug": "desguayabator", "description": "La fórmula secreta para revivir", "display_order": 10},
        ]

        categories = {}
        for cat_data in categories_data:
            cat, _ = Category.objects.get_or_create(**cat_data)
            categories[cat.slug] = cat
            self.stdout.write(f"  ✓ Categoría: {cat.name}")

        # ============== GRANIZADOS ==============
        granizados = [
            {"name": "Mango Biche", "description": "La acidez perfecta del mango verde con sal y limón."},
            {"name": "Maracuyá", "description": "Pura fruta de la pasión convertida en una experiencia helada vibrante."},
            {"name": "Lulo", "description": "El sabor único y ácido del lulo colombiano en su máxima expresión."},
            {"name": "Fresa", "description": "Dulce y refrescante granizado de fresa natural con un toque de frescura."},
        ]

        for prod_data in granizados:
            product, _ = Product.objects.get_or_create(
                category=categories["granizados"],
                name=prod_data["name"],
                defaults={"description": prod_data["description"]}
            )
            # Variantes: Pequeño y Grande
            ProductVariant.objects.get_or_create(
                product=product, name="Pequeño",
                defaults={"sku": generate_sku("GRAN", product.slug, "PEQ"), "price": Decimal("8000"), "is_default": False}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Grande",
                defaults={"sku": generate_sku("GRAN", product.slug, "GRA"), "price": Decimal("10000"), "is_default": True}
            )

        self.stdout.write(f"  ✓ Granizados: {len(granizados)} productos")

        # ============== FRAPPÉS ==============
        frappes = [
            {"name": "Café", "description": "El clásico e intenso sabor del café en su versión más helada y cremosa.", "price": Decimal("12000")},
            {"name": "Oreo", "description": "Irresistible mezcla de galletas oreo trituradas con crema de vainilla.", "has_options": True},
            {"name": "Fresa Frappé", "description": "Refrescante y dulce frappé elaborado con fresas naturales seleccionadas.", "price": Decimal("13000"), "coming_soon": True},
            {"name": "Brownie", "description": "Decadente frappé de chocolate con trozos reales de brownie húmedo.", "has_options": True},
        ]

        for prod_data in frappes:
            product, _ = Product.objects.get_or_create(
                category=categories["frappes"],
                name=prod_data["name"],
                defaults={
                    "description": prod_data["description"],
                    "is_coming_soon": prod_data.get("coming_soon", False)
                }
            )
            if prod_data.get("has_options"):
                ProductVariant.objects.get_or_create(
                    product=product, name="Chocolisto",
                    defaults={"sku": generate_sku("FRAP", product.slug, "CHO"), "price": Decimal("13000"), "is_default": True}
                )
                ProductVariant.objects.get_or_create(
                    product=product, name="Milo",
                    defaults={"sku": generate_sku("FRAP", product.slug, "MIL"), "price": Decimal("15000"), "is_default": False}
                )
            else:
                ProductVariant.objects.get_or_create(
                    product=product, name="Regular",
                    defaults={"sku": generate_sku("FRAP", product.slug, "REG"), "price": prod_data.get("price", Decimal("12000")), "is_default": True}
                )

        self.stdout.write(f"  ✓ Frappés: {len(frappes)} productos")

        # ============== SODAS ITALIANAS ==============
        sodas = [
            {"name": "Soda Italiana de Fresa", "description": "Refrescante soda carbonatada con jarabe de fresa natural y hielo.", "price": Decimal("8000")},
            {"name": "Soda Italiana de Maracuyá", "description": "Exótica soda burbujeante infusionada con la acidez tropical del maracuyá.", "price": Decimal("8000"), "coming_soon": True},
            {"name": "Soda Italiana de Mango", "description": "Dulce y tropical soda efervescente con el sabor intenso del mango maduro.", "price": Decimal("8000"), "coming_soon": True},
        ]

        for prod_data in sodas:
            product, _ = Product.objects.get_or_create(
                category=categories["sodas-italianas"],
                name=prod_data["name"],
                defaults={
                    "description": prod_data["description"],
                    "is_coming_soon": prod_data.get("coming_soon", False)
                }
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Regular",
                defaults={"sku": generate_sku("SODA", product.slug, "REG"), "price": prod_data["price"], "is_default": True}
            )

        self.stdout.write(f"  ✓ Sodas Italianas: {len(sodas)} productos")

        # ============== MICHELADAS ==============
        micheladas = [
            {"name": "Michelada Poker", "description": "Cerveza Poker bien fría con nuestra mezcla secreta de salsas, limón y especias.", "price": Decimal("10000")},
            {"name": "Michelada Budweiser", "description": "Cerveza Budweiser premium con nuestra mezcla clásica de clamato, salsas y limón.", "price": Decimal("10000")},
            {"name": "Michelada Corona", "description": "Cerveza Corona Extra con nuestra receta especial de michelada y un toque de limón fresco.", "price": Decimal("12000")},
        ]

        for prod_data in micheladas:
            product, _ = Product.objects.get_or_create(
                category=categories["micheladas"],
                name=prod_data["name"],
                defaults={"description": prod_data["description"]}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Regular",
                defaults={"sku": generate_sku("MICH", product.slug, "REG"), "price": prod_data["price"], "is_default": True}
            )

        self.stdout.write(f"  ✓ Micheladas: {len(micheladas)} productos")

        # ============== CERVEZAS ==============
        cervezas = [
            {"name": "Budweiser", "description": "Cerveza americana premium con sabor distintivo y refrescante. El rey de las cervezas.", "price": Decimal("5000")},
            {"name": "Poker", "description": "Cerveza colombiana clásica, refrescante y de sabor suave. La favorita de todos.", "price": Decimal("6000")},
            {"name": "Coronita", "description": "La versión compacta de Corona. Misma calidad premium en presentación pequeña.", "price": Decimal("7000")},
            {"name": "Corona", "description": "Cerveza mexicana premium con un toque de limón. Perfecta para cualquier ocasión.", "price": Decimal("10000")},
        ]

        for prod_data in cervezas:
            product, _ = Product.objects.get_or_create(
                category=categories["cervezas"],
                name=prod_data["name"],
                defaults={"description": prod_data["description"]}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Botella",
                defaults={"sku": generate_sku("CERV", product.slug, "BOT"), "price": prod_data["price"], "is_default": True}
            )

        self.stdout.write(f"  ✓ Cervezas: {len(cervezas)} productos")

        # ============== LOS CUATES ==============
        cuates = [
            {"name": "Cuates Limón", "description": "Cóctel con tequila mexicano sabor limón clásico. Refrescante y listo para disfrutar. 4% Alc.", "price": Decimal("8000")},
            {"name": "Cuates Fresa", "description": "Cóctel con tequila mexicano sabor fresa jugosa. Dulce, tropical y refrescante. 4% Alc.", "price": Decimal("8000")},
            {"name": "Cuates Mango", "description": "Cóctel con tequila mexicano sabor mango tropical. Exótico y delicioso. 4% Alc.", "price": Decimal("8000")},
        ]

        for prod_data in cuates:
            product, _ = Product.objects.get_or_create(
                category=categories["cuates"],
                name=prod_data["name"],
                defaults={"description": prod_data["description"]}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Lata",
                defaults={"sku": generate_sku("CUAT", product.slug, "LAT"), "price": prod_data["price"], "is_default": True}
            )

        self.stdout.write(f"  ✓ Cuates: {len(cuates)} productos")

        # ============== CÓCTELES ==============
        cocteles = [
            {"name": "Mojito", "description": "Refrescante mezcla de hierbabuena, limón y soda con un toque dulce.", "liquor": "Ron BACARDI Superior", "price_suave": Decimal("15000"), "price_cargado": Decimal("20000")},
            {"name": "Margarita", "description": "Clásico cóctel cítrico con el equilibrio perfecto entre dulce y ácido.", "liquor": "Tequila JOSE CUERVO", "price_suave": Decimal("20000"), "price_cargado": Decimal("25000")},
            {"name": "Margarota", "description": "Versión gigante y atrevida de la clásica margarita para compartir.", "liquor": "Tequila JOSE CUERVO", "price_suave": Decimal("50000"), "price_cargado": Decimal("70000")},
            {"name": "Caipiroshka", "description": "Vodka, lima y azúcar macerados para una explosión de sabor tropical.", "liquor": "Vodka ABSOLUT", "price_suave": Decimal("20000"), "price_cargado": Decimal("30000")},
            {"name": "Cuba Libre", "description": "La combinación atemporal de ron, cola y un toque de lima fresca.", "liquor": "Ron BACARDI Superior", "price_suave": Decimal("15000"), "price_cargado": Decimal("20000")},
            {"name": "Gintonic", "description": "Elegante combinación de ginebra premium con agua tónica y botánicos.", "liquor": "Ginebra BEEFEATER", "price_suave": Decimal("45000"), "price_cargado": Decimal("60000")},
            {"name": "Moscow Mule", "description": "Picante y refrescante combinación de vodka, ginger beer y limón.", "liquor": "Vodka ABSOLUT", "price_suave": Decimal("25000"), "price_cargado": Decimal("35000"), "coming_soon": True},
            {"name": "Blue Long", "description": "Cóctel azul eléctrico con una mezcla potente de licores blancos.", "liquor": "Vodka ABSOLUT", "price_suave": Decimal("20000"), "price_cargado": Decimal("30000"), "coming_soon": True},
        ]

        for prod_data in cocteles:
            product, _ = Product.objects.get_or_create(
                category=categories["mocktails"],
                name=prod_data["name"],
                defaults={
                    "description": prod_data["description"],
                    "is_coming_soon": prod_data.get("coming_soon", False)
                }
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Suave",
                defaults={"sku": generate_sku("COCK", product.slug, "SUA"), "price": prod_data["price_suave"], "is_default": True}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Cargado",
                defaults={"sku": generate_sku("COCK", product.slug, "CAR"), "price": prod_data["price_cargado"], "is_default": False}
            )

        self.stdout.write(f"  ✓ Cócteles: {len(cocteles)} productos")

        # ============== SHOTS ==============
        shots = [
            {"name": "Ginebra Beefeater", "description": "Shot puro de ginebra premium con notas cítricas y botánicos selectos.", "brand": "Beefeater", "licor": "London Dry 24", "price": Decimal("20000")},
            {"name": "Vodka Absolut", "description": "Shot puro de vodka sueco. Suave, limpio y de pureza excepcional.", "brand": "Absolut", "licor": "Original", "price": Decimal("10000")},
            {"name": "Whisky Jack Daniels", "description": "Shot puro de whisky americano con miel natural. Dulce y con carácter.", "brand": "Jack Daniels", "licor": "Tennessee Honey", "price": Decimal("12000")},
            {"name": "Tequila Jose Cuervo", "description": "Shot puro de tequila reposado con notas de agave y madera.", "brand": "Jose Cuervo", "licor": "Especial Reposado", "price": Decimal("9000")},
            {"name": "Ron Bacardi", "description": "Shot puro de ron blanco caribeño. Ligero y con toque tropical.", "brand": "Bacardi", "licor": "Superior", "price": Decimal("6000")},
            {"name": "Aguardiente Nariño", "description": "Shot de aguardiente nariñense premium. Suave, puro y sin azúcar.", "brand": "Nariño", "licor": "Premium Sin Azúcar", "price": Decimal("5000")},
        ]

        for prod_data in shots:
            product, _ = Product.objects.get_or_create(
                category=categories["shots"],
                name=prod_data["name"],
                defaults={"description": prod_data["description"]}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Shot",
                defaults={"sku": generate_sku("SHOT", product.slug, "SHO"), "price": prod_data["price"], "is_default": True}
            )

        self.stdout.write(f"  ✓ Shots: {len(shots)} productos")

        # ============== VINOS ==============
        vinos = [
            {"name": "Gato Negro", "description": "Copa de vino tinto chileno suave y afrutado. Perfecto para acompañar cualquier momento.", "price": Decimal("20000")},
            {"name": "Casillero del Diablo", "description": "Copa de vino tinto reserva premium con cuerpo intenso y notas de frutas maduras.", "price": Decimal("25000")},
        ]

        for prod_data in vinos:
            product, _ = Product.objects.get_or_create(
                category=categories["vinos"],
                name=prod_data["name"],
                defaults={"description": prod_data["description"]}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Copa",
                defaults={"sku": generate_sku("VINO", product.slug, "COP"), "price": prod_data["price"], "is_default": True}
            )

        self.stdout.write(f"  ✓ Vinos: {len(vinos)} productos")

        # ============== DESGUAYABATOR ==============
        desguayabator = [
            {"name": "Desguayabator Maracuyá", "description": "El poder tropical del maracuyá combinado con la hidratación del Electrolit y el boost de Bonfiest. ¡Revive al instante!", "price": Decimal("12000")},
            {"name": "Desguayabator Fresa", "description": "Dulzura refrescante de fresa que te hará olvidar la noche anterior. Hidratación y energía en cada sorbo.", "price": Decimal("12000")},
            {"name": "Desguayabator Coco", "description": "La frescura del coco te transporta a la playa mientras tu cuerpo se recupera. Hidratación tropical máxima.", "price": Decimal("12000")},
            {"name": "Desguayabator Naranja-Mandarina", "description": "Explosión cítrica que despierta todos tus sentidos. Vitaminas y electrolitos para una recuperación express.", "price": Decimal("12000")},
        ]

        for prod_data in desguayabator:
            product, _ = Product.objects.get_or_create(
                category=categories["desguayabator"],
                name=prod_data["name"],
                defaults={"description": prod_data["description"]}
            )
            ProductVariant.objects.get_or_create(
                product=product, name="Regular",
                defaults={"sku": generate_sku("DESG", product.slug, "REG"), "price": prod_data["price"], "is_default": True}
            )

        self.stdout.write(f"  ✓ Desguayabator: {len(desguayabator)} productos")

        # Resumen
        self.stdout.write(self.style.SUCCESS(f"\n✅ Datos cargados exitosamente:"))
        self.stdout.write(f"   - {Category.objects.count()} categorías")
        self.stdout.write(f"   - {Product.objects.count()} productos")
        self.stdout.write(f"   - {ProductVariant.objects.count()} variantes")
