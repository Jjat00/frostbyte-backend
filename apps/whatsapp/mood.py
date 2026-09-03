"""El pulso de los stickers: cuándo a Frosty le dan ganas de mandar uno.

Un modelo de lenguaje no sabe tirar una moneda. Con la misma conversación
delante decide siempre lo mismo, así que pedirle "a veces sí y a veces no" en
el prompt no produce variedad: produce un sí sistemático o un no sistemático.
Y un sticker que llega puntual en el mismo momento de todas las conversaciones
es justo lo que delata a un bot. El dado se tira aquí, una vez por turno, y el
resultado se le cuenta al modelo dentro de su prompt.

La decisión que importa sigue siendo suya —si este momento pide un gesto y
cuál de los stickers cuadra—; lo de aquí es solo el pulso: que dos
conversaciones iguales no salgan idénticas, que no lleguen dos seguidos y que
un chat no termine empapelado. Un sticker se siente cercano cuando es raro.
"""

import random
from typing import NamedTuple

from django.utils import timezone

# Probabilidad de que el turno tenga permiso, según cuántos lleve ya la
# conversación de hoy. Cae rápido a propósito: el primero cae bien, el segundo
# hace gracia y el tercero ya es un tic. El largo de la tupla ES el tope del
# día: agotada, el resto de la conversación va en texto.
CHANCE_BY_COUNT = (0.45, 0.2, 0.08)

# Si es el cliente el que mandó un sticker, devolverle el gesto es lo que hace
# cualquiera: ahí el dado casi siempre dice que sí.
CHANCE_ANSWERING_STICKER = 0.85

# Minutos que tienen que pasar entre uno y el siguiente. Dos stickers seguidos
# no son cercanía, son ruido.
COOLDOWN_MINUTES = 4

# Lo que lee el modelo. Va en su prompt tal cual, así que está escrito para él
# y no para un log: dice qué puede hacer este turno, no por qué.
NO_URGE = "este turno va sin sticker, responde solo con texto."
FIRST_URGE = (
    "hoy todavía no le has mandado ninguno. Si el momento pide un gesto, mándale el que "
    "cuadre; si no lo pide, no pasa nada: la mayoría de los mensajes van sin sticker."
)
ONE_MORE_URGE = (
    "hoy ya le mandaste uno, el «{label}». Puedes mandar otro solo si el momento lo pide "
    "de verdad, y que sea distinto de ese."
)
ANOTHER_URGE = (
    "hoy ya le mandaste {count}, el último el «{label}». Otro más solo si el momento lo "
    "pide de verdad, y nunca el mismo de antes."
)


class StickerUrge(NamedTuple):
    """Lo que este turno puede hacer con los stickers.

    `allowed`: si no, la tool enviar_sticker se niega aunque el modelo la
    llame. La instrucción del prompt basta casi siempre, pero el permiso es lo
    que hace que "a veces no" sea de verdad a veces y no una sugerencia.
    `note`: lo que se le cuenta al modelo sobre este turno, ya redactado.
    """

    allowed: bool
    note: str


def sticker_urge(contact, answering_sticker=False, roll=None):
    """Tira el dado de este turno para un contacto.

    `answering_sticker`: el cliente acaba de mandar uno.
    `roll`: el resultado del dado, para las pruebas.
    """
    recent = contact.stickers_today()
    if len(recent) >= len(CHANCE_BY_COUNT):
        return StickerUrge(False, NO_URGE)

    if recent:
        elapsed = (timezone.now() - recent[0][1]).total_seconds() / 60
        if elapsed < COOLDOWN_MINUTES:
            return StickerUrge(False, NO_URGE)

    chance = CHANCE_ANSWERING_STICKER if answering_sticker else CHANCE_BY_COUNT[len(recent)]
    if (random.random() if roll is None else roll) >= chance:
        return StickerUrge(False, NO_URGE)

    if not recent:
        return StickerUrge(True, FIRST_URGE)
    if len(recent) == 1:
        return StickerUrge(True, ONE_MORE_URGE.format(label=recent[0][0]))
    return StickerUrge(True, ANOTHER_URGE.format(count=len(recent), label=recent[0][0]))
