import django.db.models.deletion
from django.db import migrations, models

MAIN_BUSINESS_SLUG = "frostbyte"


def assign_main_business(apps, schema_editor):
    """Backfill: todo el inventario existente pertenece al negocio principal."""
    Business = apps.get_model("business", "Business")
    RawMaterial = apps.get_model("inventory", "RawMaterial")
    PurchaseOrder = apps.get_model("inventory", "PurchaseOrder")

    main = Business.objects.get(slug=MAIN_BUSINESS_SLUG)
    RawMaterial.objects.filter(business__isnull=True).update(business=main)
    PurchaseOrder.objects.filter(business__isnull=True).update(business=main)


def backfill_not_reversible(apps, schema_editor):
    raise NotImplementedError(
        "Esta migracion no es reversible automaticamente: revertirla elimina la "
        "columna business y reaplicarla reasignaria TODO al negocio principal, "
        "perdiendo la separacion de Frostbyte Food. Revierte manualmente con backup."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_purchaseorder_purchaseorderitem"),
        ("business", "0002_seed_businesses"),
    ]

    operations = [
        migrations.AddField(
            model_name="rawmaterial",
            name="business",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="raw_materials",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.AddField(
            model_name="purchaseorder",
            name="business",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_orders",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.RunPython(assign_main_business, backfill_not_reversible),
        migrations.AlterField(
            model_name="rawmaterial",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="raw_materials",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.AlterField(
            model_name="purchaseorder",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_orders",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
    ]
