from django.core.management.base import BaseCommand
from decimal import Decimal

from apps.inventory.models import UnitOfMeasure, RawMaterial


class Command(BaseCommand):
    help = "Agrega materiales faltantes al inventario según lista de Frostbyte"

    def handle(self, *args, **options):
        # Obtener o crear unidades
        g, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="g", defaults={"name": "Gramos"})
        ml, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="ml", defaults={"name": "Mililitros"})
        und, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="und", defaults={"name": "Unidad"})
        kg, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="kg", defaults={"name": "Kilogramos"})
        L, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="L", defaults={"name": "Litros"})

        materials = [
            # === FRAPPERS ===
            {"name": "Oreo", "unit": und, "cost": 500, "stock": 100,
                "min_stock": 30, "supplier": "Tienda", "category": "Frappers"},
            {"name": "Chocolate Líquido", "unit": L, "cost": 18000, "stock": 5,
                "min_stock": 2, "supplier": "Hershey's", "category": "Frappers"},
            {"name": "Agua", "unit": L, "cost": 1000, "stock": 50,
                "min_stock": 20, "supplier": "Brisa", "category": "Frappers"},
            {"name": "Chips de Chocolate", "unit": g, "cost": 30, "stock": 1000,
                "min_stock": 300, "supplier": "Tienda", "category": "Frappers"},
            {"name": "Barquillos", "unit": und, "cost": 200, "stock": 200,
                "min_stock": 50, "supplier": "Panadería", "category": "Frappers"},
            {"name": "Brownie", "unit": und, "cost": 1500, "stock": 50,
                "min_stock": 15, "supplier": "Panadería", "category": "Frappers"},
            {"name": "Arequipe", "unit": kg, "cost": 25000, "stock": 3,
                "min_stock": 1, "supplier": "Alpina", "category": "Frappers"},
            {"name": "Esencia de Vainilla", "unit": ml, "cost": 50, "stock": 500,
                "min_stock": 100, "supplier": "Tienda", "category": "Frappers"},
            {"name": "Crema de Fresas", "unit": L, "cost": 15000, "stock": 5,
                "min_stock": 2, "supplier": "Torani", "category": "Frappers"},
            {"name": "Crema Chantilly", "unit": L, "cost": 12000, "stock": 8,
                "min_stock": 3, "supplier": "Alpina", "category": "Frappers"},
            {"name": "Azúcar", "unit": kg, "cost": 4000, "stock": 10,
                "min_stock": 3, "supplier": "Tienda", "category": "Frappers"},

            # === GRANIZADOS - Bubols ===
            {"name": "Aguardiente Antioqueño", "unit": ml, "cost": 50, "stock": 3000,
                "min_stock": 1000, "supplier": "FLA", "category": "Granizados"},
            {"name": "Bubols Tamarindo", "unit": und, "cost": 100, "stock": 500,
                "min_stock": 100, "supplier": "Bubols", "category": "Granizados"},
            {"name": "Bubols Blueberry", "unit": und, "cost": 100, "stock": 500,
                "min_stock": 100, "supplier": "Bubols", "category": "Granizados"},
            {"name": "Bubols Chicle", "unit": und, "cost": 100, "stock": 500,
                "min_stock": 100, "supplier": "Bubols", "category": "Granizados"},
            {"name": "Bubols Fresa", "unit": und, "cost": 100, "stock": 500,
                "min_stock": 100, "supplier": "Bubols", "category": "Granizados"},
            {"name": "Bubols Lyche", "unit": und, "cost": 100, "stock": 500,
                "min_stock": 100, "supplier": "Bubols", "category": "Granizados"},
            {"name": "Bubols Naranja", "unit": und, "cost": 100, "stock": 500,
                "min_stock": 100, "supplier": "Bubols", "category": "Granizados"},
            {"name": "Bubols Mango Viche", "unit": und, "cost": 100, "stock": 500,
                "min_stock": 100, "supplier": "Bubols", "category": "Granizados"},

            # === GRANIZADOS - Toppings ===
            {"name": "Bolitas de Caramelos", "unit": g, "cost": 20, "stock": 2000,
                "min_stock": 500, "supplier": "Dulcería", "category": "Granizados"},
            {"name": "Chicle Molido", "unit": g, "cost": 30, "stock": 1000,
                "min_stock": 300, "supplier": "Dulcería", "category": "Granizados"},
            {"name": "Sirope de Fresa", "unit": L, "cost": 12000, "stock": 5,
                "min_stock": 2, "supplier": "Torani", "category": "Granizados"},
            {"name": "Sirope de Maracuyá", "unit": L, "cost": 12000, "stock": 5,
                "min_stock": 2, "supplier": "Torani", "category": "Granizados"},
            {"name": "Sirope de Mango Viche", "unit": L, "cost": 12000, "stock": 5,
                "min_stock": 2, "supplier": "Local", "category": "Granizados"},
            {"name": "Sirope de Lulo", "unit": L, "cost": 12000, "stock": 5,
                "min_stock": 2, "supplier": "Local", "category": "Granizados"},
            {"name": "Moritas Gomitas", "unit": g, "cost": 25, "stock": 1500,
                "min_stock": 400, "supplier": "Dulcería", "category": "Granizados"},
            {"name": "Gusanitos Gomitas", "unit": g, "cost": 25, "stock": 1500,
                "min_stock": 400, "supplier": "Dulcería", "category": "Granizados"},
            {"name": "Gomas Mandarina", "unit": g, "cost": 25, "stock": 1000,
                "min_stock": 300, "supplier": "Dulcería", "category": "Granizados"},
            {"name": "Barras Candy Sandía", "unit": und, "cost": 300, "stock": 200,
                "min_stock": 50, "supplier": "Dulcería", "category": "Granizados"},
            {"name": "Barras Candy Maracuyá", "unit": und, "cost": 300, "stock": 200,
                "min_stock": 50, "supplier": "Dulcería", "category": "Granizados"},
            {"name": "Gomas Fini Cintas", "unit": g, "cost": 30, "stock": 1000,
                "min_stock": 300, "supplier": "Fini", "category": "Granizados"},
            {"name": "Polvo Escarchador Maracumango", "unit": g, "cost": 40, "stock": 500,
                "min_stock": 150, "supplier": "Local", "category": "Granizados"},
            {"name": "Polvo Escarchador Limón", "unit": g, "cost": 40, "stock": 500,
                "min_stock": 150, "supplier": "Local", "category": "Granizados"},
            {"name": "Escarchador Sirope", "unit": L, "cost": 15000, "stock": 3,
                "min_stock": 1, "supplier": "Local", "category": "Granizados"},
            {"name": "Tajín", "unit": g, "cost": 50, "stock": 500, "min_stock": 150,
                "supplier": "Importadora", "category": "Granizados"},
            {"name": "Chamoy", "unit": L, "cost": 25000, "stock": 3,
                "min_stock": 1, "supplier": "Importadora", "category": "Granizados"},

            # === GRANIZADOS - Desechables ===
            {"name": "Vasos 9oz", "unit": und, "cost": 250, "stock": 500,
                "min_stock": 200, "supplier": "Desechables SAT", "category": "Granizados"},
            {"name": "Cucharas de Plástico", "unit": und, "cost": 50, "stock": 1000,
                "min_stock": 300, "supplier": "Desechables SAT", "category": "Granizados"},

            # === CERVEZAS ===
            {"name": "Corona Lata 269ml", "unit": und, "cost": 4500, "stock": 24,
                "min_stock": 12, "supplier": "Bavaria", "category": "Cervezas"},
            {"name": "Budweiser Lata 269ml", "unit": und, "cost": 2500, "stock": 24,
                "min_stock": 12, "supplier": "Bavaria", "category": "Cervezas"},

            # === SODAS ITALIANAS ===
            {"name": "Soda Italiana Base", "unit": L, "cost": 5000, "stock": 20,
                "min_stock": 8, "supplier": "Schweppes", "category": "Sodas"},
            {"name": "Sal Verde", "unit": g, "cost": 20, "stock": 500,
                "min_stock": 150, "supplier": "Local", "category": "Sodas"},
            {"name": "Limones Frescos", "unit": und, "cost": 200, "stock": 100,
                "min_stock": 30, "supplier": "Plaza", "category": "Sodas"},
            {"name": "Pulpa de Mango", "unit": kg, "cost": 16000, "stock": 5,
                "min_stock": 2, "supplier": "Fruver", "category": "Sodas"},

            # === CÓCTELES ===
            {"name": "Sirope Margarita", "unit": L, "cost": 18000, "stock": 5,
                "min_stock": 2, "supplier": "Torani", "category": "Cócteles"},
            {"name": "Tequila Especial Reposado", "unit": ml, "cost": 150, "stock": 2000,
                "min_stock": 500, "supplier": "Licorería", "category": "Cócteles"},
            {"name": "London Dry 24", "unit": ml, "cost": 180, "stock": 1500,
                "min_stock": 500, "supplier": "Licorería", "category": "Cócteles"},
            {"name": "Jarabe de Jengibre", "unit": L, "cost": 20000, "stock": 3,
                "min_stock": 1, "supplier": "Torani", "category": "Cócteles"},
            {"name": "Refresco de Lima", "unit": L, "cost": 8000, "stock": 10,
                "min_stock": 4, "supplier": "Schweppes", "category": "Cócteles"},
            {"name": "Naranja Fresca", "unit": und, "cost": 300, "stock": 50,
                "min_stock": 20, "supplier": "Plaza", "category": "Cócteles"},
            {"name": "Cereza Marrasquino", "unit": und, "cost": 200, "stock": 200,
                "min_stock": 50, "supplier": "Tienda", "category": "Cócteles"},
            {"name": "Lima Fresca", "unit": und, "cost": 250, "stock": 50,
                "min_stock": 20, "supplier": "Plaza", "category": "Cócteles"},
            {"name": "Menta Fresca", "unit": g, "cost": 50, "stock": 300,
                "min_stock": 100, "supplier": "Plaza", "category": "Cócteles"},

            # === LICORES ===
            {"name": "Jack Daniels", "unit": ml, "cost": 200, "stock": 1000,
                "min_stock": 300, "supplier": "Licorería Mayor", "category": "Licores"},
        ]

        created_count = 0
        existing_count = 0

        for mat in materials:
            material, created = RawMaterial.objects.get_or_create(
                name=mat["name"],
                defaults={
                    "unit": mat["unit"],
                    "cost_per_unit": Decimal(str(mat["cost"])),
                    "current_stock": Decimal(str(mat["stock"])),
                    "minimum_stock": Decimal(str(mat["min_stock"])),
                    "supplier": mat["supplier"],
                },
            )
            if created:
                self.stdout.write(f"  ✓ [{mat['category']}] {material.name}")
                created_count += 1
            else:
                existing_count += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"✅ {created_count} materiales nuevos agregados"))
        self.stdout.write(f"ℹ️  {existing_count} materiales ya existían")
        self.stdout.write(
            f"📊 Total en inventario: {RawMaterial.objects.count()}")
