"""Siembra el concurso de disfraces de Halloween 2026, oculto y cerrado.

Nace sin publicar: el staff lo enciende desde el panel cuando se anuncie.
"""
import datetime
from decimal import Decimal

from django.db import migrations

SLUG = "disfraces-halloween-2026"


def crear(apps, schema_editor):
    Contest = apps.get_model("contests", "Contest")
    Contest.objects.get_or_create(
        slug=SLUG,
        defaults={
            "title": "Concurso de disfraces",
            "description": (
                "La noche de Halloween elegimos el mejor disfraz de Frostbyte. "
                "Inscríbete con tu cuenta de Google, paga la inscripción en la "
                "barra y síguenos en Instagram para quedar confirmado."
            ),
            "event_date": datetime.date(2026, 10, 31),
            "entry_fee": Decimal("10000.00"),
            "min_age": 18,
            "requires_instagram_follow": True,
            "is_published": False,
            "registrations_open": False,
        },
    )


def borrar(apps, schema_editor):
    apps.get_model("contests", "Contest").objects.filter(slug=SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("contests", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear, borrar),
    ]
