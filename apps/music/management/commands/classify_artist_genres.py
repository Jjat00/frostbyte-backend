"""Clasifica por género los artistas que aparecen en las canciones pedidas.

    manage.py classify_artist_genres              # solo los que faltan
    manage.py classify_artist_genres --limit 100  # una prueba corta
    manage.py classify_artist_genres --force      # reclasifica todo lo que puso la IA
    manage.py classify_artist_genres --genre otros --force   # solo un cajón

Lo corregido a mano (`source=manual`) nunca se pisa, ni con --force.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.music.genres import LABELS
from apps.music.models import ArtistGenre, SongRequest, primary_artist
from apps.music.services.genre_classifier import BATCH_SIZE, classify_batch


class Command(BaseCommand):
    help = "Clasifica los artistas de las canciones pedidas en géneros, con el modelo de lenguaje"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Máximo de artistas a clasificar")
        parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Artistas por llamada")
        parser.add_argument("--force", action="store_true", help="Reclasifica los que ya tienen género de IA")
        parser.add_argument("--genre", type=str, default="", help="Con --force, reclasifica solo este género")
        parser.add_argument("--dry-run", action="store_true", help="No guarda nada, solo muestra")

    def handle(self, *args, **options):
        pendientes = self._pendientes(options)
        if not pendientes:
            self.stdout.write(self.style.SUCCESS("No hay artistas por clasificar."))
            return

        if options["limit"]:
            pendientes = pendientes[: options["limit"]]

        total = len(pendientes)
        self.stdout.write(f"Artistas por clasificar: {total}")

        tamano = max(1, options["batch_size"])
        guardados, fallidos = 0, []

        for inicio in range(0, total, tamano):
            lote = pendientes[inicio : inicio + tamano]
            nombres = [nombre for nombre, _, _ in lote]
            try:
                clasificados = classify_batch(nombres)
            except Exception as exc:  # el lote se pierde, el resto sigue
                self.stderr.write(self.style.WARNING(f"  lote {inicio // tamano + 1}: {exc}"))
                fallidos.extend(nombres)
                continue

            for nombre, clave, pedidos in lote:
                genero = clasificados.get(nombre)
                if not genero:
                    fallidos.append(nombre)
                    continue
                if options["dry_run"]:
                    self.stdout.write(f"  {nombre[:38]:40} → {LABELS[genero]}  ({pedidos} pedidos)")
                else:
                    ArtistGenre.objects.update_or_create(
                        artist_key=clave,
                        defaults={
                            "artist_name": nombre,
                            "genre": genero,
                            "source": ArtistGenre.Source.AI,
                            "model_used": self._modelo(),
                        },
                    )
                guardados += 1

            self.stdout.write(f"  {min(inicio + tamano, total)}/{total}")

        estilo = self.style.SUCCESS if not fallidos else self.style.WARNING
        self.stdout.write(estilo(f"Clasificados: {guardados}. Sin clasificar: {len(fallidos)}."))
        if fallidos:
            self.stdout.write("  " + ", ".join(fallidos[:20]) + (" …" if len(fallidos) > 20 else ""))

    def _modelo(self):
        from django.conf import settings

        return settings.MUSIC_GENRE_MODEL

    def _pendientes(self, options):
        """[(nombre, clave, nº de pedidos)] ordenado por lo que más suena."""
        ya = dict(ArtistGenre.objects.values_list("artist_key", "source"))
        if options["force"]:
            if options["genre"]:
                conservar = set(
                    ArtistGenre.objects.exclude(genre=options["genre"]).values_list("artist_key", flat=True)
                )
            else:
                conservar = set()
            # Lo corregido a mano se respeta siempre.
            conservar |= {
                clave for clave, source in ya.items() if source == ArtistGenre.Source.MANUAL
            }
        else:
            conservar = set(ya)

        vistos, pendientes = set(), []
        filas = (
            SongRequest.objects.values("artist_name")
            .annotate(n=Count("id"))
            .order_by("-n")
        )
        for fila in filas:
            nombre = primary_artist(fila["artist_name"])
            if not nombre:
                continue
            clave = ArtistGenre.key_for(nombre)
            if not clave or clave in vistos or clave in conservar:
                continue
            vistos.add(clave)
            pendientes.append((nombre, clave, fila["n"]))
        return pendientes
