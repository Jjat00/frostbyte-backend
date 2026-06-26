from django.db import migrations

# Slug del negocio principal. Todo lo que existe hoy en el sistema pertenece a
# este negocio; las demas apps hacen backfill de su FK business apuntando aqui.
MAIN_BUSINESS_SLUG = "frostbyte"


def seed_businesses(apps, schema_editor):
    Business = apps.get_model("business", "Business")
    Business.objects.get_or_create(
        slug=MAIN_BUSINESS_SLUG,
        defaults={
            "name": "Frostbyte",
            "description": "Bebidas preparadas y frías: granizados, frappés, cócteles, sodas.",
            "floor": 2,
            "color": "blue",
            "display_order": 1,
            "is_active": True,
        },
    )
    Business.objects.get_or_create(
        slug="frostbyte-food",
        defaults={
            "name": "Frostbyte Food",
            "description": "Comidas rápidas en el 3er piso.",
            "floor": 3,
            "color": "orange",
            "display_order": 2,
            "is_active": True,
        },
    )


def unseed_businesses(apps, schema_editor):
    Business = apps.get_model("business", "Business")
    Business.objects.filter(slug__in=["frostbyte", "frostbyte-food"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("business", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_businesses, unseed_businesses),
    ]
