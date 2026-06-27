"""Siembra el catálogo REAL de Frostbyte Food en producción.

Por ahora Frostbyte Food tiene un único producto: la Salchipapa Personal
(con pollo y queso derretido) a $18.000. Es idempotente (get_or_create), así
que corre seguro en cada deploy: crea lo que falte y no pisa cambios hechos
desde el admin.

    python manage.py seed_food
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.business.models import Business
from apps.products.models import Category, Product, ProductVariant


class Command(BaseCommand):
    help = "Siembra el catálogo real de Frostbyte Food (salchipapa personal)."

    @transaction.atomic
    def handle(self, *args, **options):
        # El negocio lo crea la migración business.0002; guard defensivo por si acaso.
        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food",
            defaults={
                "name": "Frostbyte Food",
                "floor": 3,
                "color": "orange",
                "display_order": 2,
            },
        )

        category, _ = Category.objects.get_or_create(
            name="Salchipapas",
            business=food,
            defaults={
                "display_order": 50,  # después de las bebidas de Frostbyte
                "description": "Salchipapas de Frostbyte Food",
            },
        )

        product, _ = Product.objects.get_or_create(
            name="Salchipapa",
            category=category,
            defaults={"description": "Con pollo y queso derretido."},
        )

        variant, created = ProductVariant.objects.get_or_create(
            product=product,
            name="Personal",
            defaults={"sku": "FF-SALCHIPAPA-PERSONAL", "price": Decimal("18000"), "is_default": True},
        )

        self.stdout.write(self.style.SUCCESS(
            "Frostbyte Food: Salchipapa Personal $18.000 "
            + ("creada." if created else "ya existía (sin cambios).")
        ))
