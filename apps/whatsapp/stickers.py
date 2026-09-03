"""Convierte cualquier imagen en un sticker que WhatsApp acepte.

WhatsApp no manda un sticker: manda un WebP con requisitos estrictos (512x512
exactos, 100 KB si es fijo y 500 KB si es animado) y rechaza el mensaje entero
si algo no cuadra. Como el banco lo llena una persona desde el admin subiendo
lo que tenga a mano —un PNG, un JPG, un GIF—, la conversión ocurre aquí y no
en la cabeza de quien sube el archivo.

El fondo transparente es lo que hace que un sticker se vea como un sticker: si
la imagen no lo trae, el borde blanco de la foto queda visible sobre el fondo
del chat. Eso no se puede arreglar por código, así que se avisa al subirlo.
"""

import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

SIZE = 512
MAX_STATIC_BYTES = 100 * 1024
MAX_ANIMATED_BYTES = 500 * 1024

# De mejor a peor: se baja la calidad solo hasta donde haga falta para entrar
# en el límite, así el sticker se ve lo mejor que su peso permita.
QUALITY_STEPS = (90, 80, 70, 60, 50, 40, 30, 20)


class StickerError(ValueError):
    """La imagen no se pudo convertir en un sticker válido."""


def _fit_square(frame):
    """Encaja el frame en un lienzo transparente de 512x512 sin deformarlo.

    WhatsApp exige el cuadrado exacto. Estirar la imagen para llenarlo se ve
    mal en cualquier dibujo que no sea ya cuadrado, así que se escala al lado
    mayor y lo que sobra queda transparente.
    """
    frame = frame.convert("RGBA")
    frame.thumbnail((SIZE, SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(frame, ((SIZE - frame.width) // 2, (SIZE - frame.height) // 2), frame)
    return canvas


def _encode_static(frame):
    for quality in QUALITY_STEPS:
        buffer = io.BytesIO()
        frame.save(buffer, format="WEBP", quality=quality, method=6)
        data = buffer.getvalue()
        if len(data) <= MAX_STATIC_BYTES:
            return data
    raise StickerError(
        "La imagen no baja de 100 KB ni con la calidad al mínimo. Suele pasar con "
        "fotografías: un sticker necesita un dibujo de pocos colores y fondo transparente."
    )


def _frames_of(image):
    """Todos los frames del animado, ya cuadrados, con sus duraciones."""
    frames, durations = [], []
    for index in range(getattr(image, "n_frames", 1)):
        image.seek(index)
        frames.append(_fit_square(image.copy()))
        durations.append(image.info.get("duration", 100))
    return frames, durations


def _encode_animated(image):
    """WebP animado dentro del límite, soltando frames antes que calidad.

    Bajar la calidad de una animación la ensucia entera; quitar uno de cada dos
    frames solo la hace un poco menos fluida, que se nota mucho menos.
    """
    frames, durations = _frames_of(image)
    for skip in (1, 2, 3):
        kept = frames[::skip]
        kept_durations = [sum(durations[i : i + skip]) for i in range(0, len(durations), skip)]
        for quality in QUALITY_STEPS:
            buffer = io.BytesIO()
            kept[0].save(
                buffer,
                format="WEBP",
                save_all=True,
                append_images=kept[1:],
                duration=kept_durations,
                loop=0,
                quality=quality,
                method=4,
            )
            data = buffer.getvalue()
            if len(data) <= MAX_ANIMATED_BYTES:
                return data, True
    # Antes que rechazar el archivo, se conserva el primer frame: un sticker
    # quieto sirve, y quien lo subió puede reemplazarlo si no le gusta.
    logger.warning("Animación demasiado pesada; se guarda solo el primer frame")
    return _encode_static(frames[0]), False


def normalize(raw):
    """Devuelve (bytes_webp, es_animado) listos para mandar por WhatsApp.

    Lanza StickerError con un mensaje para la persona que subió el archivo:
    esto corre desde el admin y el error se le muestra a ella.
    """
    if not raw:
        raise StickerError("El archivo llegó vacío.")
    try:
        image = Image.open(io.BytesIO(raw))
    except Exception as exc:  # Pillow lanza de todo ante un archivo corrupto
        raise StickerError(f"No se pudo leer la imagen: {exc}") from exc

    if getattr(image, "n_frames", 1) > 1:
        return _encode_animated(image)
    return _encode_static(_fit_square(image)), False


def has_transparency(raw):
    """True si la imagen trae fondo transparente (solo para avisar al subirla)."""
    try:
        image = Image.open(io.BytesIO(raw))
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            alpha = image.convert("RGBA").getchannel("A")
            return alpha.getextrema()[0] < 255
    except Exception:
        pass
    return False
