"""Descarga y comprensión de media de WhatsApp (notas de voz e imágenes).

Kapso expone los media de Meta en dos pasos: el GET del media id devuelve un
download_url firmado (expira a los ~4 min) y ese download_url entrega el
binario sin más auth. Como el agente solo lee texto, los audios se transcriben
y las imágenes se describen con OpenAI antes de entrar a la conversación.

Esto es trabajo mecánico y va con modelos baratos propios
(`WHATSAPP_TRANSCRIBE_MODEL`, `WHATSAPP_VISION_MODEL`): el modelo del agente,
que es el caro, se reserva para conversar y decidir.
"""

import base64
import io
import logging

import requests
from django.conf import settings

from .llm import completion_params

logger = logging.getLogger(__name__)

MEDIA_URL = "https://api.kapso.ai/meta/whatsapp/v24.0/{media_id}"

# Leer un comprobante no necesita más razonamiento que este, y el cliente está
# esperando en el chat: la visión no sigue al esfuerzo del agente.
VISION_REASONING_EFFORT = "low"

# Extensión por mime: la API de transcripción detecta el formato por el nombre
_AUDIO_EXT = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/amr": "amr",
    "audio/wav": "wav",
    "audio/webm": "webm",
}

IMAGE_PROMPT = """Un cliente envió esta imagen por WhatsApp a Frostbyte, un negocio de \
granizados, cocteles y comida rápida en Colombia. Descríbela breve y fielmente en español.

- Si es un comprobante de pago o transferencia (Nequi, Daviplata, Bancolombia, etc.), \
extrae con exactitud: monto, fecha y hora, nombre del remitente, destinatario y número de \
referencia o comprobante. No inventes datos que no se lean.
- Si es una foto de comida, un menú o un pantallazo de la carta, di qué productos se ven.
- Si es una captura de una conversación o un pedido, resume qué dice.
- Cualquier otra cosa: descríbela en una o dos frases.

Responde SOLO con la descripción, sin preámbulos. Máximo 80 palabras."""

STICKER_PROMPT = """Un cliente mandó este sticker por WhatsApp a un negocio de comida en \
Colombia. Un sticker es un gesto: lo que importa no es el dibujo sino qué está diciendo con él.

Responde en UNA frase de máximo 20 palabras, en español: qué se ve y qué gesto hace (saludar, \
agradecer, celebrar, reírse, quejarse, decir que sí, decir que no, despedirse, apurar, \
enternecerse). Si trae texto escrito, cítalo tal cual.

Si no logras entender qué gesto es, responde exactamente: NO_SE_ENTIENDE"""

# Lo que responde la visión cuando el sticker no se deja leer. Ese sticker se
# trata igual que uno que no se pudo descargar: el agente no lo ve.
UNREADABLE = "NO_SE_ENTIENDE"


def download_media(media_id, phone_number_id):
    """Descarga un media de WhatsApp vía Kapso. Devuelve (bytes, mime_type)."""
    response = requests.get(
        MEDIA_URL.format(media_id=media_id),
        params={"phone_number_id": phone_number_id},
        headers={"X-API-Key": settings.KAPSO_API_KEY},
        timeout=15,
    )
    response.raise_for_status()
    meta = response.json()
    download = requests.get(meta["download_url"], timeout=30)
    download.raise_for_status()
    mime = (meta.get("mime_type") or download.headers.get("Content-Type") or "").split(";")[0].strip()
    return download.content, mime


def _openai_client():
    from openai import OpenAI

    return OpenAI(api_key=settings.OPENAI_API_KEY)


def transcribe_audio(media_id, phone_number_id):
    """Transcribe una nota de voz y devuelve el texto."""
    content, mime = download_media(media_id, phone_number_id)
    buffer = io.BytesIO(content)
    buffer.name = f"audio.{_AUDIO_EXT.get(mime, 'ogg')}"
    result = _openai_client().audio.transcriptions.create(
        model=settings.WHATSAPP_TRANSCRIBE_MODEL,
        file=buffer,
        language="es",
    )
    return (result.text or "").strip()


def _look(content, mime, prompt, max_output_tokens):
    """Le muestra una imagen al modelo de visión y devuelve lo que dice."""
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    data_uri = f"data:{mime};base64,{base64.b64encode(content).decode()}"
    model = settings.WHATSAPP_VISION_MODEL
    result = _openai_client().chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        **completion_params(
            model, temperature=0, max_output_tokens=max_output_tokens, effort=VISION_REASONING_EFFORT
        ),
    )
    return (result.choices[0].message.content or "").strip()


def describe_image(media_id, phone_number_id):
    """Describe una imagen (con ojo de comprobante de pago) y devuelve el texto."""
    content, mime = download_media(media_id, phone_number_id)
    return _look(content, mime, IMAGE_PROMPT, max_output_tokens=200)


def _first_frame(content):
    """El sticker convertido a PNG, y si es animado solo su primer frame.

    Los stickers de WhatsApp son WebP y los animados traen varios frames, que
    la visión no admite. El primero es donde está el dibujo, así que se manda
    ese. Si Pillow no puede abrirlo se devuelve tal cual: que decida el
    proveedor en vez de perder el sticker aquí.
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as image:
            image.seek(0)
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, format="PNG")
            return buffer.getvalue(), "image/png"
    except Exception:
        logger.warning("No se pudo convertir el sticker a PNG; se manda tal cual")
        return content, "image/webp"


def describe_sticker(media_id, phone_number_id):
    """Lee el gesto que hace un sticker. Devuelve "" si no se entiende.

    Un sticker no se describe como una imagen cualquiera: al cliente no le
    interesa que le cuenten el dibujo, sino que le contesten al gesto. Y si no
    se entiende, la cadena vacía es la respuesta correcta —quien llama sabe
    que entonces el sticker no llega al agente (ver worker._resolve_media)—,
    porque contestarle cualquier cosa a un gesto que no viste es peor que
    quedarse callado.
    """
    content, _ = download_media(media_id, phone_number_id)
    content, mime = _first_frame(content)
    gesto = _look(content, mime, STICKER_PROMPT, max_output_tokens=60)
    if UNREADABLE in gesto.upper():
        return ""
    return gesto
