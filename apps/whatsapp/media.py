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


def describe_image(media_id, phone_number_id):
    """Describe una imagen (con ojo de comprobante de pago) y devuelve el texto."""
    content, mime = download_media(media_id, phone_number_id)
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
                    {"type": "text", "text": IMAGE_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        **completion_params(model, temperature=0, max_output_tokens=200, effort=VISION_REASONING_EFFORT),
    )
    return (result.choices[0].message.content or "").strip()
