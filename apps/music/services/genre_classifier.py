"""Clasificador de artistas por género, con el modelo de lenguaje.

Es trabajo mecánico y de una sola vez por artista: el resultado se guarda en
`ArtistGenre` y no se vuelve a preguntar. Por eso va con el modelo barato y sin
razonamiento, y no con el del agente.

El modelo solo puede responder con un slug de `genres.GENRES`; cualquier otra
cosa se descarta y el artista queda sin clasificar para el siguiente intento.
"""

import json
import logging

from django.conf import settings

from ..genres import GENRES, taxonomy_for_prompt

logger = logging.getLogger(__name__)

BATCH_SIZE = 40

PROMPT = """Eres un clasificador de música para un bar en Cumbal, Nariño (Colombia), cerca de la frontera con Ecuador.

Clasifica cada artista en UNO de estos géneros, usando exactamente el slug:

{taxonomia}

Reglas:
- Responde solo con el slug, nunca con un género inventado.
- Si el artista canta varios géneros, elige por el que más se le conoce.
- Si no reconoces al artista, deduce por el nombre (los grupos de "Los ..." del sur de Colombia suelen ser populares o andinos) y, si aun así no tienes idea, usa "otros".
- No expliques nada.

Devuelve un JSON con esta forma exacta, una entrada por artista recibido:
{{"clasificacion": [{{"artista": "<nombre tal como te llegó>", "genero": "<slug>"}}]}}

Artistas:
{artistas}"""


def _client():
    from openai import OpenAI

    return OpenAI(api_key=settings.OPENAI_API_KEY)


def classify_batch(artist_names, model=None):
    """Devuelve {nombre recibido: slug} para los artistas que el modelo acertó.

    Los nombres que no vuelven, o vuelven con un género que no existe, quedan
    fuera del diccionario: el que llama decide si reintenta.
    """
    if not artist_names:
        return {}

    model = model or settings.MUSIC_GENRE_MODEL
    prompt = PROMPT.format(
        taxonomia=taxonomy_for_prompt(),
        artistas="\n".join(f"- {name}" for name in artist_names),
    )

    from apps.whatsapp.llm import completion_params

    response = _client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        **completion_params(
            model,
            temperature=0,
            max_output_tokens=60 * len(artist_names) + 200,
            effort="none",
        ),
    )

    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("El clasificador de géneros devolvió algo que no es JSON: %s", raw[:200])
        return {}

    pedidos = {name: name for name in artist_names}
    resultado = {}
    for fila in data.get("clasificacion", []):
        nombre = (fila or {}).get("artista", "")
        genero = (fila or {}).get("genero", "")
        if nombre in pedidos and genero in GENRES:
            resultado[nombre] = genero
    return resultado
