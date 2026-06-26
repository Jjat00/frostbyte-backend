from django.db import migrations, models
import django.db.models.deletion


def backfill_orderitem_business(apps, schema_editor):
    """Asigna a cada item existente el negocio de su producto.

    Todo lo previo a la dimensión multi-negocio pertenece a Frostbyte (el
    producto ya fue backfilleado a 'frostbyte' en products.0004), así que aquí
    solo copiamos product_variant.product.business hacia el item.
    """
    OrderItem = apps.get_model("orders", "OrderItem")
    Business = apps.get_model("business", "Business")

    main = Business.objects.filter(slug="frostbyte").first()

    items = OrderItem.objects.select_related(
        "product_variant__product"
    ).all()
    for item in items.iterator():
        business_id = None
        product = getattr(item.product_variant, "product", None)
        if product is not None:
            business_id = product.business_id
        item.business_id = business_id or (main.id if main else None)
        item.save(update_fields=["business"])


def noop_reverse(apps, schema_editor):
    raise NotImplementedError(
        "El backfill de OrderItem.business es irreversible a propósito."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0012_order_access_code"),
        ("products", "0004_category_business_product_business"),
        ("business", "0002_seed_businesses"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="prep_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pendiente"),
                    ("preparing", "Preparando"),
                    ("ready", "Listo"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Estado de preparación",
            ),
        ),
        # 1) Agregar nullable para poder backfillear sin default global.
        migrations.AddField(
            model_name="orderitem",
            name="business",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="order_items",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        # 2) Copiar el negocio del producto a cada item.
        migrations.RunPython(backfill_orderitem_business, noop_reverse),
        # 3) Volverlo obligatorio (ya no debe quedar ningún item sin negocio).
        migrations.AlterField(
            model_name="orderitem",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="order_items",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
    ]
