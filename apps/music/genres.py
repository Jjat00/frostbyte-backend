"""Taxonomía de géneros musicales de Frostbyte.

Spotify dejó de exponer el campo `genres` del artista para esta aplicación
(devuelve `[]` en todos los artistas, y los endpoints por lotes responden 403),
así que el género no se puede leer de la API: se clasifica una sola vez por
artista con el modelo de lenguaje y se guarda en `ArtistGenre`.

La lista es CERRADA a propósito. Un género libre por artista produce cien
etiquetas distintas ("corridos tumbados", "corrido bélico", "regional
mexicano") que no se pueden sumar en una gráfica. Estos son los cajones que
tienen sentido para lo que suena en el local; lo que no encaja cae en `otros`,
y lo que todavía no se ha clasificado se cuenta como `sin_clasificar`.

Al añadir un género aquí hay que reclasificar los artistas afectados
(`manage.py classify_artist_genres --force --genre otros`).
"""

UNCLASSIFIED = "sin_clasificar"

# slug -> (etiqueta visible, descripción que lee el modelo al clasificar)
GENRES = {
    "corridos": (
        "Corridos y regional mexicano",
        "corridos tumbados, corridos bélicos, banda, norteño, sierreño, "
        "Fuerza Regida, Lenin Ramírez, Grupo Firme, Tito Double P, Peso Pluma",
    ),
    "popular": (
        "Popular y despecho",
        "música popular colombiana, despecho, carrilera, cantina; "
        "Yeison Jiménez, Luis Alberto Posada, Darío Gómez, Jhonny Rivera, El Andariego",
    ),
    "ranchera": (
        "Ranchera y mariachi",
        "ranchera clásica y mariachi: Vicente Fernández, Alejandro Fernández, Antonio Aguilar",
    ),
    "vallenato": (
        "Vallenato",
        "vallenato clásico y moderno: Binomio de Oro, Diomedes Díaz, Silvestre Dangond, Jorge Celedón",
    ),
    "salsa": (
        "Salsa y son",
        "salsa, salsa romántica, son cubano, timba: Willie Colón, Grupo Niche, Marc Anthony",
    ),
    "cumbia": (
        "Cumbia y andina",
        "cumbia, chicha, tecnocumbia, música andina y sanjuanero del sur del país",
    ),
    "reggaeton": (
        "Reggaetón y urbano",
        "reggaetón, trap latino, urbano: Feid, Blessd, Karol G, Bad Bunny, Ryan Castro",
    ),
    "bachata": (
        "Bachata y merengue",
        "bachata, merengue, bachata urbana: Romeo Santos, Prince Royce, Juan Luis Guerra",
    ),
    "balada": (
        "Balada y romántica",
        "balada, bolero, música romántica en español: Ricardo Arjona, Camilo Sesto, Los Ángeles Azules en clave romántica",
    ),
    "rock": (
        "Rock y metal",
        "rock en cualquier idioma, metal, punk, rock en español: Maná, Soda Stereo, Nirvana",
    ),
    "pop": (
        "Pop",
        "pop en cualquier idioma que no sea urbano ni balada: Shakira, Taylor Swift, Dua Lipa",
    ),
    "rap": (
        "Rap y hip hop",
        "rap, hip hop, freestyle, drill (sin reggaetón)",
    ),
    "electronica": (
        "Electrónica",
        "electrónica, EDM, house, techno, guaracha, aleteo",
    ),
    "reggae": (
        "Reggae y dancehall",
        "reggae, dancehall, ska",
    ),
    "otros": (
        "Otros",
        "cualquier cosa que no encaje en los cajones anteriores: infantil, "
        "instrumental, religioso, bandas sonoras, folclor de otros países",
    ),
}

GENRE_CHOICES = [(slug, label) for slug, (label, _) in GENRES.items()]

# El cajón de los no clasificados no es elegible por el modelo: solo lo pone
# el sistema mientras el artista no ha pasado por el clasificador.
ALL_CHOICES = GENRE_CHOICES + [(UNCLASSIFIED, "Sin clasificar")]

LABELS = {slug: label for slug, (label, _) in GENRES.items()}
LABELS[UNCLASSIFIED] = "Sin clasificar"

# Orden estable para las gráficas: el que más suena primero lo decide el dato,
# pero cuando empatan (o hay que pintar una leyenda) manda este orden.
ORDER = list(GENRES.keys()) + [UNCLASSIFIED]


def label_for(slug):
    return LABELS.get(slug, LABELS[UNCLASSIFIED])


def taxonomy_for_prompt():
    """La taxonomía como texto para el prompt del clasificador."""
    return "\n".join(
        f"- {slug}: {description}" for slug, (_, description) in GENRES.items()
    )
