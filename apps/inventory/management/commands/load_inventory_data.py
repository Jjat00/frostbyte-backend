from django.core.management.base import BaseCommand
from decimal import Decimal

from apps.inventory.models import UnitOfMeasure, RawMaterial, Recipe
from apps.products.models import ProductVariant


class Command(BaseCommand):
    help = "Carga datos de ejemplo para inventario (unidades, materia prima, recetas)"

    def handle(self, *args, **options):
        self.stdout.write("🏭 Creando unidades de medida...")
        self.create_units()

        self.stdout.write("📦 Creando materia prima...")
        self.create_raw_materials()

        self.stdout.write("📋 Creando recetas de ejemplo...")
        self.create_recipes()

        self.stdout.write(
            self.style.SUCCESS("✅ Datos de inventario cargados exitosamente!")
        )

    def create_units(self):
        units_data = [
            {"name": "Gramos", "abbreviation": "g"},
            {"name": "Mililitros", "abbreviation": "ml"},
            {"name": "Unidad", "abbreviation": "und"},
            {"name": "Kilogramos", "abbreviation": "kg"},
            {"name": "Litros", "abbreviation": "L"},
            {"name": "Onza", "abbreviation": "oz"},
        ]

        for unit_data in units_data:
            unit, created = UnitOfMeasure.objects.get_or_create(
                abbreviation=unit_data["abbreviation"],
                defaults={"name": unit_data["name"]},
            )
            if created:
                self.stdout.write(f"  ✓ Unidad: {unit.name}")

    def create_raw_materials(self):
        # Obtener unidades
        g = UnitOfMeasure.objects.get(abbreviation="g")
        ml = UnitOfMeasure.objects.get(abbreviation="ml")
        und = UnitOfMeasure.objects.get(abbreviation="und")
        kg = UnitOfMeasure.objects.get(abbreviation="kg")
        L = UnitOfMeasure.objects.get(abbreviation="L")

        materials_data = [
            # Pulpas de fruta
            {"name": "Pulpa de Mango Biche", "unit": kg, "cost": 15000, "stock": 10, "min_stock": 2, "supplier": "Fruver Local"},
            {"name": "Pulpa de Maracuyá", "unit": kg, "cost": 18000, "stock": 8, "min_stock": 2, "supplier": "Fruver Local"},
            {"name": "Pulpa de Lulo", "unit": kg, "cost": 16000, "stock": 6, "min_stock": 2, "supplier": "Fruver Local"},
            {"name": "Pulpa de Fresa", "unit": kg, "cost": 14000, "stock": 12, "min_stock": 3, "supplier": "Fruver Local"},
            {"name": "Pulpa de Coco", "unit": kg, "cost": 20000, "stock": 5, "min_stock": 2, "supplier": "Fruver Local"},

            # Jarabes y bases
            {"name": "Jarabe Simple", "unit": L, "cost": 8000, "stock": 15, "min_stock": 5, "supplier": "Producción Propia"},
            {"name": "Jarabe de Fresa", "unit": L, "cost": 12000, "stock": 8, "min_stock": 3, "supplier": "Torani"},
            {"name": "Jarabe de Maracuyá", "unit": L, "cost": 12000, "stock": 6, "min_stock": 3, "supplier": "Torani"},
            {"name": "Jarabe de Mango", "unit": L, "cost": 12000, "stock": 7, "min_stock": 3, "supplier": "Torani"},
            {"name": "Sirope de Chocolate", "unit": L, "cost": 15000, "stock": 10, "min_stock": 4, "supplier": "Hershey's"},

            # Café y bases de frappé
            {"name": "Café Espresso Molido", "unit": kg, "cost": 45000, "stock": 5, "min_stock": 2, "supplier": "Café Nariño"},
            {"name": "Leche Entera", "unit": L, "cost": 4500, "stock": 20, "min_stock": 10, "supplier": "Alquería"},
            {"name": "Crema de Leche", "unit": L, "cost": 12000, "stock": 8, "min_stock": 4, "supplier": "Alpina"},
            {"name": "Base Milo", "unit": kg, "cost": 35000, "stock": 3, "min_stock": 1, "supplier": "Nestlé"},
            {"name": "Base Chocolisto", "unit": kg, "cost": 28000, "stock": 4, "min_stock": 1, "supplier": "Nacional"},

            # Licores
            {"name": "Ron Bacardi Superior", "unit": ml, "cost": 80, "stock": 3000, "min_stock": 1000, "supplier": "Licorería Mayor"},
            {"name": "Tequila Jose Cuervo", "unit": ml, "cost": 120, "stock": 2500, "min_stock": 1000, "supplier": "Licorería Mayor"},
            {"name": "Vodka Absolut", "unit": ml, "cost": 100, "stock": 2500, "min_stock": 1000, "supplier": "Licorería Mayor"},
            {"name": "Ginebra Beefeater", "unit": ml, "cost": 150, "stock": 1500, "min_stock": 500, "supplier": "Licorería Mayor"},
            {"name": "Aguardiente Nariño Premium", "unit": ml, "cost": 60, "stock": 4000, "min_stock": 1500, "supplier": "ILC Nariño"},
            {"name": "Blue Curaçao", "unit": ml, "cost": 90, "stock": 1000, "min_stock": 500, "supplier": "Licorería Mayor"},

            # Cervezas
            {"name": "Cerveza Budweiser 355ml", "unit": und, "cost": 2800, "stock": 48, "min_stock": 24, "supplier": "Bavaria"},
            {"name": "Cerveza Poker 330ml", "unit": und, "cost": 3200, "stock": 48, "min_stock": 24, "supplier": "Bavaria"},
            {"name": "Cerveza Corona 355ml", "unit": und, "cost": 5500, "stock": 24, "min_stock": 12, "supplier": "Bavaria"},
            {"name": "Cerveza Coronita 210ml", "unit": und, "cost": 3800, "stock": 24, "min_stock": 12, "supplier": "Bavaria"},

            # Vinos
            {"name": "Vino Gato Negro Botella", "unit": ml, "cost": 40, "stock": 3000, "min_stock": 1500, "supplier": "Vinos del Valle"},
            {"name": "Vino Casillero del Diablo Botella", "unit": ml, "cost": 60, "stock": 2000, "min_stock": 1000, "supplier": "Vinos del Valle"},

            # Los Cuates
            {"name": "Los Cuates Limón 355ml", "unit": und, "cost": 4500, "stock": 36, "min_stock": 12, "supplier": "Importadora"},
            {"name": "Los Cuates Fresa 355ml", "unit": und, "cost": 4500, "stock": 36, "min_stock": 12, "supplier": "Importadora"},
            {"name": "Los Cuates Mango 355ml", "unit": und, "cost": 4500, "stock": 36, "min_stock": 12, "supplier": "Importadora"},

            # Shots
            {"name": "Tequila Don Julio", "unit": ml, "cost": 200, "stock": 1000, "min_stock": 300, "supplier": "Licorería Mayor"},

            # Mixers y complementos
            {"name": "Coca Cola", "unit": ml, "cost": 3, "stock": 10000, "min_stock": 5000, "supplier": "Coca Cola"},
            {"name": "Ginger Beer", "unit": ml, "cost": 8, "stock": 5000, "min_stock": 2000, "supplier": "Schweppes"},
            {"name": "Agua Tónica", "unit": ml, "cost": 6, "stock": 6000, "min_stock": 3000, "supplier": "Canada Dry"},
            {"name": "Jugo de Limón", "unit": ml, "cost": 10, "stock": 3000, "min_stock": 1000, "supplier": "Producción Propia"},
            {"name": "Hierbabuena Fresca", "unit": g, "cost": 50, "stock": 500, "min_stock": 200, "supplier": "Plaza de Mercado"},
            {"name": "Sal de Mesa", "unit": g, "cost": 2, "stock": 2000, "min_stock": 500, "supplier": "Refisal"},
            {"name": "Sal Michelada", "unit": g, "cost": 15, "stock": 1000, "min_stock": 300, "supplier": "La Negra"},
            {"name": "Salsa Valentina", "unit": ml, "cost": 20, "stock": 1500, "min_stock": 500, "supplier": "Tapatío"},
            {"name": "Clamato", "unit": ml, "cost": 15, "stock": 3000, "min_stock": 1000, "supplier": "Motts"},

            # Electrolit y complementos Desguayabator
            {"name": "Electrolit Maracuyá", "unit": und, "cost": 6000, "stock": 24, "min_stock": 12, "supplier": "Farmacia"},
            {"name": "Electrolit Fresa", "unit": und, "cost": 6000, "stock": 24, "min_stock": 12, "supplier": "Farmacia"},
            {"name": "Electrolit Coco", "unit": und, "cost": 6000, "stock": 24, "min_stock": 12, "supplier": "Farmacia"},
            {"name": "Electrolit Naranja-Mandarina", "unit": und, "cost": 6000, "stock": 24, "min_stock": 12, "supplier": "Farmacia"},
            {"name": "Bonfiest", "unit": und, "cost": 2000, "stock": 50, "min_stock": 20, "supplier": "Farmacia"},

            # Sodas Italianas
            {"name": "Agua Carbonatada", "unit": L, "cost": 3000, "stock": 30, "min_stock": 15, "supplier": "Bretaña"},

            # Hielo
            {"name": "Hielo en Cubos", "unit": kg, "cost": 2000, "stock": 50, "min_stock": 20, "supplier": "Hielo del Sur"},

            # Vasos y desechables
            {"name": "Vaso 16oz", "unit": und, "cost": 400, "stock": 500, "min_stock": 200, "supplier": "Desechables SAT"},
            {"name": "Vaso 12oz", "unit": und, "cost": 300, "stock": 500, "min_stock": 200, "supplier": "Desechables SAT"},
            {"name": "Copa Margarita", "unit": und, "cost": 600, "stock": 100, "min_stock": 50, "supplier": "Desechables SAT"},
            {"name": "Pitillo Biodegradable", "unit": und, "cost": 100, "stock": 1000, "min_stock": 300, "supplier": "Desechables SAT"},
        ]

        for material_data in materials_data:
            material, created = RawMaterial.objects.get_or_create(
                name=material_data["name"],
                defaults={
                    "unit": material_data["unit"],
                    "cost_per_unit": Decimal(str(material_data["cost"])),
                    "current_stock": Decimal(str(material_data["stock"])),
                    "minimum_stock": Decimal(str(material_data["min_stock"])),
                    "supplier": material_data["supplier"],
                },
            )
            if created:
                self.stdout.write(f"  ✓ Material: {material.name}")

    def create_recipes(self):
        """Crear recetas de ejemplo para algunos productos"""

        # Obtener materiales necesarios
        try:
            pulpa_mango = RawMaterial.objects.get(name="Pulpa de Mango Biche")
            pulpa_maracuya = RawMaterial.objects.get(name="Pulpa de Maracuyá")
            pulpa_fresa = RawMaterial.objects.get(name="Pulpa de Fresa")
            jarabe_simple = RawMaterial.objects.get(name="Jarabe Simple")
            hielo = RawMaterial.objects.get(name="Hielo en Cubos")
            vaso_16oz = RawMaterial.objects.get(name="Vaso 16oz")
            vaso_12oz = RawMaterial.objects.get(name="Vaso 12oz")
            pitillo = RawMaterial.objects.get(name="Pitillo Biodegradable")
            cafe = RawMaterial.objects.get(name="Café Espresso Molido")
            leche = RawMaterial.objects.get(name="Leche Entera")
            electrolit_maracuya = RawMaterial.objects.get(name="Electrolit Maracuyá")
            bonfiest = RawMaterial.objects.get(name="Bonfiest")
            ron = RawMaterial.objects.get(name="Ron Bacardi Superior")
            hierbabuena = RawMaterial.objects.get(name="Hierbabuena Fresca")
            jugo_limon = RawMaterial.objects.get(name="Jugo de Limón")
        except RawMaterial.DoesNotExist as e:
            self.stdout.write(self.style.WARNING(f"  ⚠️ Material no encontrado: {e}"))
            return

        # Recetas de Granizados - Mango Biche Grande
        try:
            mango_grande = ProductVariant.objects.get(
                product__slug="mango-biche", name="Grande"
            )
            recipes_mango = [
                {"variant": mango_grande, "material": pulpa_mango, "quantity": 150, "notes": "Pulpa congelada"},
                {"variant": mango_grande, "material": jarabe_simple, "quantity": 30},
                {"variant": mango_grande, "material": hielo, "quantity": 200},
                {"variant": mango_grande, "material": vaso_16oz, "quantity": 1},
                {"variant": mango_grande, "material": pitillo, "quantity": 1},
            ]
            for recipe_data in recipes_mango:
                recipe, created = Recipe.objects.get_or_create(
                    product_variant=recipe_data["variant"],
                    raw_material=recipe_data["material"],
                    defaults={
                        "quantity": Decimal(str(recipe_data["quantity"])),
                        "notes": recipe_data.get("notes", ""),
                    },
                )
                if created:
                    self.stdout.write(f"  ✓ Receta: {recipe}")
        except ProductVariant.DoesNotExist:
            self.stdout.write(self.style.WARNING("  ⚠️ Variante Mango Biche Grande no encontrada"))

        # Receta Desguayabator Maracuyá
        try:
            desguayabator_maracuya = ProductVariant.objects.get(
                product__slug="desguayabator-maracuya"
            )
            recipes_desg = [
                {"variant": desguayabator_maracuya, "material": electrolit_maracuya, "quantity": 1},
                {"variant": desguayabator_maracuya, "material": bonfiest, "quantity": 1},
                {"variant": desguayabator_maracuya, "material": hielo, "quantity": 150},
                {"variant": desguayabator_maracuya, "material": vaso_16oz, "quantity": 1},
            ]
            for recipe_data in recipes_desg:
                recipe, created = Recipe.objects.get_or_create(
                    product_variant=recipe_data["variant"],
                    raw_material=recipe_data["material"],
                    defaults={
                        "quantity": Decimal(str(recipe_data["quantity"])),
                        "notes": recipe_data.get("notes", ""),
                    },
                )
                if created:
                    self.stdout.write(f"  ✓ Receta: {recipe}")
        except ProductVariant.DoesNotExist:
            self.stdout.write(self.style.WARNING("  ⚠️ Variante Desguayabator Maracuyá no encontrada"))

        # Receta Mojito Suave
        try:
            mojito_suave = ProductVariant.objects.get(
                product__slug="mojito", name="Suave"
            )
            recipes_mojito = [
                {"variant": mojito_suave, "material": ron, "quantity": 45, "notes": "1.5 oz"},
                {"variant": mojito_suave, "material": hierbabuena, "quantity": 10, "notes": "8-10 hojas"},
                {"variant": mojito_suave, "material": jugo_limon, "quantity": 30},
                {"variant": mojito_suave, "material": jarabe_simple, "quantity": 20},
                {"variant": mojito_suave, "material": hielo, "quantity": 100},
                {"variant": mojito_suave, "material": vaso_12oz, "quantity": 1},
            ]
            for recipe_data in recipes_mojito:
                recipe, created = Recipe.objects.get_or_create(
                    product_variant=recipe_data["variant"],
                    raw_material=recipe_data["material"],
                    defaults={
                        "quantity": Decimal(str(recipe_data["quantity"])),
                        "notes": recipe_data.get("notes", ""),
                    },
                )
                if created:
                    self.stdout.write(f"  ✓ Receta: {recipe}")
        except ProductVariant.DoesNotExist:
            self.stdout.write(self.style.WARNING("  ⚠️ Variante Mojito Suave no encontrada"))

        self.stdout.write(f"  📊 Total recetas: {Recipe.objects.count()}")

