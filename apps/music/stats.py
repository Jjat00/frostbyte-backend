"""Estadísticas de la música pedida: qué suena, en qué piso y a qué hora.

Todo se calcula sobre `SongRequest`, que es el registro de lo que la gente
pidió desde la carta. Dos decisiones que cambian los números y conviene tener
presentes al leerlos:

1. **El día es la noche, no la fecha.** Una canción pedida a la 1 a. m. del
   sábado pertenece a la noche del viernes: el corte está en
   `OPERATING_DAY_CUTOFF_HOUR`, no a medianoche. Sin esto cada noche aparece
   partida en dos y los sábados se llevan lo que pasó el viernes.
2. **El género es el del artista principal.** Viene de `ArtistGenre`, que se
   llena una vez por artista con el clasificador; lo que aún no ha pasado por
   ahí se cuenta aparte como "sin clasificar" en vez de desaparecer del total.

Las series se arman en Python sobre las filas del rango porque el volumen es
pequeño (miles de filas) y así el mismo recorrido alimenta las nueve vistas de
la pantalla sin nueve consultas.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from django.utils import timezone

from .genres import LABELS, ORDER, UNCLASSIFIED
from .models import ArtistGenre, SongRequest, primary_artist

# Antes de esta hora, lo pedido cuenta para la noche anterior.
OPERATING_DAY_CUTOFF_HOUR = 6
# La gráfica por hora empieza al mediodía: el local abre en la tarde y cierra
# de madrugada, así que un eje de 0 a 23 parte la noche por la mitad.
DAY_START_HOUR = 12

WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

TOP_LIMIT = 15


def _operating_day(dt):
    """La noche a la que pertenece un momento, en hora local."""
    local = timezone.localtime(dt)
    if local.hour < OPERATING_DAY_CUTOFF_HOUR:
        local -= timedelta(days=1)
    return local.date()


def _share(part, total):
    return round(part / total, 4) if total else 0.0


class _Bucket:
    """Un contador que además reparte por piso."""

    def __init__(self):
        self.total = 0
        self.by_floor = Counter()

    def add(self, floor, n=1):
        self.total += n
        self.by_floor[floor] += n

    def as_dict(self, floors, total_general=None):
        data = {"total": self.total}
        for floor in floors:
            data[f"floor_{floor}"] = self.by_floor.get(floor, 0)
        if total_general is not None:
            data["share"] = _share(self.total, total_general)
        return data


def build_stats(start=None, end=None, floor=None):
    """Todas las series de la pantalla de estadísticas de música.

    `start`/`end` son fechas (inclusive) en hora local; `floor` filtra a un
    piso o los trae todos si es None.
    """
    qs = SongRequest.objects.all()
    if start:
        qs = qs.filter(created_at__gte=timezone.make_aware(
            timezone.datetime.combine(start, timezone.datetime.min.time())
        ))
    if end:
        limite = timezone.make_aware(
            timezone.datetime.combine(end + timedelta(days=1), timezone.datetime.min.time())
        )
        qs = qs.filter(created_at__lt=limite)
    if floor:
        qs = qs.filter(floor=floor)

    filas = list(
        qs.values(
            "floor",
            "song_name",
            "artist_name",
            "spotify_track_uri",
            "spotify_track_image",
            "spotify_track_duration_ms",
            "created_at",
        )
    )

    generos_por_clave = dict(ArtistGenre.objects.values_list("artist_key", "genre"))

    floors = sorted({fila["floor"] for fila in filas}) or ([floor] if floor else [2, 3])

    total = len(filas)
    por_genero = defaultdict(_Bucket)
    por_artista = defaultdict(_Bucket)
    por_cancion = defaultdict(_Bucket)
    por_hora = defaultdict(_Bucket)
    por_dia_semana = defaultdict(_Bucket)
    por_noche = defaultdict(_Bucket)
    por_mes_genero = defaultdict(Counter)
    por_piso = defaultdict(_Bucket)

    artistas_por_piso = defaultdict(set)
    canciones_por_piso = defaultdict(set)
    imagen_de = {}
    artista_de = {}
    genero_de_artista = {}
    duracion_total = 0

    for fila in filas:
        piso = fila["floor"]
        artista = primary_artist(fila["artist_name"]) or "Desconocido"
        clave = ArtistGenre.key_for(fila["artist_name"])
        genero = generos_por_clave.get(clave, UNCLASSIFIED)
        genero_de_artista[artista] = genero
        local = timezone.localtime(fila["created_at"])
        cancion = (fila["song_name"] or "").strip()
        cancion_id = fila["spotify_track_uri"] or f"{cancion}|{artista}".lower()

        por_genero[genero].add(piso)
        por_artista[artista].add(piso)
        por_cancion[cancion_id].add(piso)
        por_hora[local.hour].add(piso)
        por_dia_semana[local.weekday()].add(piso)
        por_noche[_operating_day(fila["created_at"])].add(piso)
        por_mes_genero[local.strftime("%Y-%m")][genero] += 1
        por_piso[piso].add(piso)

        artistas_por_piso[piso].add(artista)
        canciones_por_piso[piso].add(cancion_id)
        duracion_total += fila["spotify_track_duration_ms"] or 0
        imagen_de.setdefault(cancion_id, fila["spotify_track_image"] or "")
        artista_de.setdefault(cancion_id, (cancion, fila["artist_name"]))

    noches = sorted(por_noche.items())
    mejor_noche = max(noches, key=lambda par: par[1].total, default=None)

    generos = sorted(
        (
            {
                "slug": slug,
                "label": LABELS.get(slug, slug),
                **bucket.as_dict(floors, total),
            }
            for slug, bucket in por_genero.items()
        ),
        key=lambda g: (-g["total"], ORDER.index(g["slug"]) if g["slug"] in ORDER else 99),
    )

    def _top(contador, formato):
        ordenado = sorted(contador.items(), key=lambda par: -par[1].total)[:TOP_LIMIT]
        return [formato(clave, bucket) for clave, bucket in ordenado]

    top_artistas = _top(
        por_artista,
        lambda nombre, bucket: {
            "artist": nombre,
            "genre": genero_de_artista.get(nombre, UNCLASSIFIED),
            "label": LABELS.get(genero_de_artista.get(nombre, UNCLASSIFIED)),
            **bucket.as_dict(floors, total),
        },
    )
    top_canciones = _top(
        por_cancion,
        lambda cid, bucket: {
            "song": artista_de.get(cid, ("", ""))[0],
            "artist": artista_de.get(cid, ("", ""))[1],
            "image": imagen_de.get(cid, ""),
            "genre": genero_de_artista.get(primary_artist(artista_de.get(cid, ("", ""))[1]), UNCLASSIFIED),
            **bucket.as_dict(floors, total),
        },
    )

    horas = [
        {
            "hour": (DAY_START_HOUR + i) % 24,
            **por_hora.get((DAY_START_HOUR + i) % 24, _Bucket()).as_dict(floors),
        }
        for i in range(24)
    ]

    dias_semana = [
        {"weekday": i, "label": WEEKDAYS[i], **por_dia_semana.get(i, _Bucket()).as_dict(floors)}
        for i in range(7)
    ]

    linea = [
        {"date": dia.isoformat(), **bucket.as_dict(floors)}
        for dia, bucket in noches
    ]

    meses = sorted(por_mes_genero)
    slugs_visibles = [g["slug"] for g in generos[:6]]
    evolucion = {
        "periods": meses,
        "series": [
            {
                "slug": slug,
                "label": LABELS.get(slug, slug),
                "values": [por_mes_genero[mes].get(slug, 0) for mes in meses],
            }
            for slug in slugs_visibles
        ],
    }

    clasificados = total - por_genero.get(UNCLASSIFIED, _Bucket()).total

    pisos = []
    for piso in floors:
        bucket = por_piso.get(piso, _Bucket())
        genero_top = max(
            ((g["slug"], g.get(f"floor_{piso}", 0)) for g in generos),
            key=lambda par: par[1],
            default=(UNCLASSIFIED, 0),
        )
        pisos.append(
            {
                "floor": piso,
                "total": bucket.total,
                "share": _share(bucket.total, total),
                "tracks": len(canciones_por_piso.get(piso, ())),
                "artists": len(artistas_por_piso.get(piso, ())),
                "top_genre": {
                    "slug": genero_top[0],
                    "label": LABELS.get(genero_top[0], genero_top[0]),
                    "total": genero_top[1],
                    "share": _share(genero_top[1], bucket.total),
                },
            }
        )

    return {
        "range": {
            "start": start.isoformat() if start else (noches[0][0].isoformat() if noches else None),
            "end": end.isoformat() if end else (noches[-1][0].isoformat() if noches else None),
            "floor": floor,
            "floors": floors,
        },
        "summary": {
            "total": total,
            "tracks": len({cid for cid in por_cancion}),
            "artists": len(por_artista),
            "nights": len(noches),
            "per_night": round(total / len(noches), 1) if noches else 0,
            "hours_of_music": round(duracion_total / 3_600_000, 1),
            "best_night": (
                {"date": mejor_noche[0].isoformat(), "total": mejor_noche[1].total}
                if mejor_noche
                else None
            ),
            "classified_share": _share(clasificados, total),
            "floors": pisos,
        },
        "genres": generos,
        "top_artists": top_artistas,
        "top_tracks": top_canciones,
        "by_hour": horas,
        "by_weekday": dias_semana,
        "timeline": linea,
        "genre_timeline": evolucion,
    }
