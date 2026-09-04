"""Los stickers de Frosty: cómo se fabrican y cómo se entregan.

Convierte cualquier imagen en un sticker que WhatsApp acepte.

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


def _encode_frames(frames, durations):
    """WebP animado dentro del límite, soltando frames antes que calidad.

    Bajar la calidad de una animación la ensucia entera; quitar uno de cada dos
    frames solo la hace un poco menos fluida, que se nota mucho menos.
    """
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


def _encode_animated(image):
    """WebP animado a partir de un GIF o WebP animado ya abierto por Pillow."""
    return _encode_frames(*_frames_of(image))


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


# Un sticker animado es un bucle corto: más de esto no cabe en 500 KB con una
# calidad decente, y tampoco se ve como un sticker.
VIDEO_MAX_SECONDS = 3
VIDEO_FPS = 12


def _ffmpeg_binary():
    """Ruta a ffmpeg: el del sistema, o el binario que trae imageio-ffmpeg.

    En local suele estar instalado; en Railway (nixpacks + pip) no, así que el
    paquete de Python lleva el suyo y evita depender de la imagen del build.
    """
    from shutil import which

    found = which("ffmpeg")
    if found:
        return found
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return None


def from_video(raw):
    """Convierte un video corto en un sticker animado.

    ffmpeg solo saca los cuadros; el ensamblado y el ajuste al límite de peso
    los hace el mismo código que los GIF, para que un sticker animado se vea
    igual venga de donde venga.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    binary = _ffmpeg_binary()
    if not binary:
        raise StickerError(
            "No hay ffmpeg disponible para leer el video. Manda la imagen o el GIF."
        )

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = folder / "input"
        source.write_bytes(raw)
        try:
            subprocess.run(
                [
                    binary, "-nostdin", "-y",
                    "-i", str(source),
                    "-t", str(VIDEO_MAX_SECONDS),
                    "-vf", f"fps={VIDEO_FPS}",
                    "-vsync", "0",
                    str(folder / "frame%04d.png"),
                ],
                capture_output=True,
                timeout=60,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise StickerError("El video tardó demasiado en procesarse.") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode(errors="replace").strip().splitlines()
            logger.warning("ffmpeg falló: %s", detail[-1] if detail else "sin detalle")
            raise StickerError("No se pudo leer el video. Prueba con otro o con un GIF.") from exc

        files = sorted(folder.glob("frame*.png"))
        if not files:
            raise StickerError("El video no tenía cuadros que convertir.")
        frames = []
        for path in files:
            with Image.open(path) as frame:
                frames.append(_fit_square(frame))

    if len(frames) == 1:
        return _encode_static(frames[0]), False
    return _encode_frames(frames, [int(1000 / VIDEO_FPS)] * len(frames))


def from_upload(raw, kind="image"):
    """Convierte a sticker lo que llegó, sea imagen, GIF, sticker o video."""
    if kind == "video":
        return from_video(raw)
    return normalize(raw)


def deliver(contact, sticker, phone_number_id):
    """Manda al chat un sticker del banco y lo apunta en la memoria corta.

    El envío vive aquí y no en la tool que lo elige porque el sticker sale
    DESPUÉS del texto del turno (ver tools.TurnContext): quien lo manda es el
    worker, cuando el mensaje ya está en el chat. Apuntarlo antes de que salga
    de verdad falsearía el pulso —el enfriamiento y el tope del día se cuentan
    sobre stickers que el cliente vio (ver mood.py)—, así que las dos cosas
    ocurren juntas y solo si Kapso lo aceptó.
    """
    from django.db.models import F

    from . import kapso
    from .models import Sticker

    if kapso.send_sticker(phone_number_id, contact.phone, sticker.url) is None:
        logger.warning("No se pudo entregar el sticker %s a %s", sticker.label, contact.phone)
        return False
    Sticker.objects.filter(pk=sticker.pk).update(sent_count=F("sent_count") + 1)
    contact.remember_sticker(sticker.label)
    return True
