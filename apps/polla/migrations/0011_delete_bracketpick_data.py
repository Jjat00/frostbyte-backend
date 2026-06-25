"""Limpia los picks de avance (BracketPick) legacy.

La mecanica de "elegir quien avanza" quedo deprecada: la fase eliminatoria
ahora se juega marcando el marcador (Prediction), igual que grupos, y los
puntos suben por fase. Esos picks ya no se puntuan (ver scoring.recompute_scores,
que dejo de sumar bracket_points), asi que aqui borramos los registros viejos
para que no queden datos muertos. El modelo se conserva.
"""
from django.db import migrations


def delete_bracket_picks(apps, schema_editor):
    BracketPick = apps.get_model("polla", "BracketPick")
    BracketPick.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("polla", "0010_granizadoreward"),
    ]

    operations = [
        # Solo borra datos; es irreversible (no se pueden reconstruir los picks).
        migrations.RunPython(delete_bracket_picks, migrations.RunPython.noop),
    ]
