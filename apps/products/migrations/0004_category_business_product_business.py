import django.db.models.deletion
from django.db import migrations, models

MAIN_BUSINESS_SLUG = "frostbyte"


def assign_main_business(apps, schema_editor):
    """Backfill: todo el catalogo existente pertenece al negocio principal."""
    Business = apps.get_model("business", "Business")
    Category = apps.get_model("products", "Category")
    Product = apps.get_model("products", "Product")

    main = Business.objects.get(slug=MAIN_BUSINESS_SLUG)
    Category.objects.filter(business__isnull=True).update(business=main)
    Product.objects.filter(business__isnull=True).update(business=main)


def backfill_not_reversible(apps, schema_editor):
    raise NotImplementedError(
        "Esta migracion no es reversible automaticamente: revertirla elimina la "
        "columna business y reaplicarla reasignaria TODO al negocio principal, "
        "perdiendo la separacion de Frostbyte Food. Revierte manualmente con backup."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0003_product_history"),
        ("business", "0002_seed_businesses"),
    ]

    operations = [
        # 1) Agregar el FK como nullable para no romper las filas existentes.
        migrations.AddField(
            model_name="category",
            name="business",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="categories",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="business",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        # 2) Backfill al negocio principal (Frostbyte).
        migrations.RunPython(assign_main_business, backfill_not_reversible),
        # 3) Volver el FK obligatorio (estado final del modelo).
        migrations.AlterField(
            model_name="category",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="categories",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
    ]
