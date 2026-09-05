"""Las palabras que el negocio le prohibió decir a Frosty.

Pedirlo en el prompt no basta, y no es culpa del modelo: el tono «Parcero» le
ordena hablar con "parce" en el bloque QUIÉN ERES, se lo repite al final en TU
VOZ y encima le deja un saludo de muestra que la usa. Contra tres refuerzos y
un ejemplo, una línea suelta que diga "no digas parce" pierde tarde o
temprano. Una prohibición que se cumple casi siempre no es una prohibición: es
una sugerencia, y quien la escribió en el panel la lee como una orden.

Así que aquí la prohibición deja de depender del modelo. Las palabras se
limpian del saludo de muestra que le damos, se le repiten como regla dura
donde mandan sobre su personalidad, y lo que escriba se revisa antes de salir.
Quitarlas del texto no lo rompe: lo que uno prohíbe son muletillas y vocativos
("Listo parce, ya sale" es "Listo, ya sale"), no datos ni verbos.

De dónde salen: del campo dedicado del panel, y además de lo que el dueño
escribió entrecomillado en sus ajustes de tono ("nunca digas «pana»"), que es
donde lo escribió antes de que este campo existiera. Solo se leen las citas
tras una negación: sin comillas, "no uses palabras raras" bloquearía "raras".
"""

import re
import unicodedata

# Negación + cita en la misma frase: «nunca digas "pana"», «nada de «parce»».
# El hueco entre una y otra es corto a propósito, para no cruzar dos ideas
# distintas de una misma línea.
_NEGATED_QUOTE = re.compile(
    r"(?:nunca|jamas|jamás|no|sin|evita|evites|nada de|prohibid[oa]s?|elimina|quita)"
    r"[^.;\n]{0,60}?"
    r"[\"'«“]\s*(?P<word>[^\"'»”\n]{1,40}?)\s*[\"'»”]",
    re.IGNORECASE,
)

# Lo que separa una palabra de la siguiente dentro de una misma prohibición.
_SEPARATORS = re.compile(r"\s*(?:,|;|/|\bni\b|\bno\b|\by\b|\bo\b)\s*", re.IGNORECASE)

# Una palabra del mensaje del agente, para compararla con las vetadas.
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Basura que queda cuando se saca una muletilla de en medio de la frase.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?…])")
_REPEATED_COMMA = re.compile(r",(\s*,)+")
_LEADING_JUNK = re.compile(r"(^|[\n¿¡(])[\s,;:]+")
_SPACES = re.compile(r"[ \t]{2,}")


def _plain(text):
    """Minúsculas y sin tildes: "Parcé" y "PARCE" son la misma palabra."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def parse(raw):
    """Las palabras de una lista separada por comas, ya normalizadas.

    Solo palabras sueltas: una prohibición de varias palabras no se puede
    quitar de una frase sin dejarla coja, y no es lo que nadie escribe aquí.
    """
    words = set()
    for part in _SEPARATORS.split(str(raw or "")):
        word = _plain(part).strip(" \t\"'«»“”.!?…")
        if word and " " not in word and len(word) > 1:
            words.add(word)
    return words


def from_notes(notes):
    """Las palabras entrecomilladas que los ajustes de tono ya prohibían.

    El campo de texto libre existía antes que el de las palabras, así que ahí
    es donde están escritas las de quien no ha vuelto a la pantalla. Se leen
    solo las que van entre comillas después de una negación: es lo bastante
    explícito como para no adivinar de más.
    """
    words = set()
    for match in _NEGATED_QUOTE.finditer(str(notes or "")):
        words |= parse(match.group("word"))
    return words


def words_for(config):
    """Todo lo que el negocio le prohibió decir, venga de donde venga."""
    return parse(getattr(config, "banned_words", "")) | from_notes(getattr(config, "tone", ""))


def found(text, words):
    """Las palabras vetadas que aparecen en el texto, en orden de aparición."""
    if not words or not text:
        return []
    hits = []
    for match in _WORD.finditer(str(text)):
        word = _plain(match.group())
        if word in words and word not in hits:
            hits.append(word)
    return hits


def clean(text, words):
    """El texto sin las palabras vetadas y con la frase todavía en pie.

    Borrar una palabra deja detrás la puntuación que la sostenía —un espacio
    antes de la coma, una coma al principio de la frase—, y eso se nota más
    que la palabra que se quitó. Se limpia lo que quedó suelto y se devuelve
    la mayúscula al arranque si la tenía.
    """
    if not words or not text:
        return text
    out = _WORD.sub(lambda m: "" if _plain(m.group()) in words else m.group(), str(text))
    if out == text:
        return text
    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    out = _REPEATED_COMMA.sub(",", out)
    out = _LEADING_JUNK.sub(r"\1", out)
    out = _SPACES.sub(" ", out)
    out = "\n".join(line.strip() for line in out.split("\n")).strip()
    if out and text[:1].isupper():
        out = out[0].upper() + out[1:]
    return out
