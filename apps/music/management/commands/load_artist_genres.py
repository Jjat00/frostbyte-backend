"""Carga los géneros ya clasificados desde `fixtures/artist_genres.json`.

Clasificar mil artistas cuesta una tanda de llamadas al modelo; el resultado
vive en el repositorio para que producción (o cualquier entorno nuevo) no tenga
que repetirla:

    manage.py load_artist_genres

Es idempotente y respeta lo corregido a mano: una fila con `source=manual` no se
toca. Después de clasificar artistas nuevos en cualquier entorno, se vuelve a
volcar el archivo con `--dump` para que el repositorio quede al día.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.music.genres import GENRES
from apps.music.models import ArtistGenre

RUTA = Path(__file__).resolve().parents[2] / "fixtures" / "artist_genres.json"


class Command(BaseCommand):
    help = "Carga (o vuelca) los géneros de artistas ya clasificados"

    def add_arguments(self, parser):
        parser.add_argument("--dump", action="store_true", help="Escribe el archivo desde la base de datos")

    def handle(self, *args, **options):
        if options["dump"]:
            datos = {
                artista.artist_name: artista.genre
                for artista in ArtistGenre.objects.order_by("artist_name")
            }
            RUTA.write_text(
                json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.stdout.write(self.style.SUCCESS(f"Volcados {len(datos)} artistas en {RUTA.name}"))
            return

        if not RUTA.exists():
            self.stderr.write(self.style.ERROR(f"No existe {RUTA}"))
            return

        datos = json.loads(RUTA.read_text(encoding="utf-8"))
        manuales = set(
            ArtistGenre.objects.filter(source=ArtistGenre.Source.MANUAL).values_list("artist_key", flat=True)
        )

        creados, actualizados, respetados, invalidos = 0, 0, 0, 0
        for nombre, genero in datos.items():
            if genero not in GENRES:
                invalidos += 1
                continue
            clave = ArtistGenre.key_for(nombre)
            if clave in manuales:
                respetados += 1
                continue
            _, creado = ArtistGenre.objects.update_or_create(
                artist_key=clave,
                defaults={"artist_name": nombre, "genre": genero, "source": ArtistGenre.Source.AI},
            )
            creados += creado
            actualizados += not creado

        self.stdout.write(
            self.style.SUCCESS(
                f"Nuevos: {creados}. Actualizados: {actualizados}. "
                f"Corregidos a mano que se respetan: {respetados}."
            )
        )
        if invalidos:
            self.stdout.write(self.style.WARNING(f"Géneros desconocidos ignorados: {invalidos}"))
