"""
Servicio para manejar almacenamiento temporal de imágenes
"""
import os
import uuid
import time
import logging
import tempfile
from io import BytesIO
from typing import Optional
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Lado maximo que se le manda al proveedor de IA. Los modelos generan a
# 1024 px (OpenAI) o 2K (Gemini), asi que una foto de celular de 12 MP solo
# aporta peso y lentitud.
MAX_DIMENSION = 2048


class InvalidImageUpload(ValueError):
    """La imagen subida no se puede usar (vacia, dañada o formato ilegible)."""


class TempImageStorage:
    """Maneja almacenamiento temporal de imágenes antes de guardar a R2"""

    def __init__(self):
        self.temp_dir = os.path.join(tempfile.gettempdir(), 'ai_generations')
        os.makedirs(self.temp_dir, exist_ok=True)

    def save_temp_file(self, uploaded_file: UploadedFile, prefix: str = '') -> str:
        """
        Normaliza el archivo subido y lo guarda en storage temporal.

        Lo que llega del navegador se copiaba tal cual, y el proveedor de IA
        lo rechazaba con un error incomprensible cuando venia vacio, truncado
        o en un modo de color raro: la validacion de Django solo mira las
        cabeceras (`verify()`), no decodifica. Aqui se decodifica de verdad y
        se guarda siempre un PNG RGB/RGBA sano.

        Args:
            uploaded_file: Archivo de Django
            prefix: Prefijo para el nombre (ej: 'original', 'reference')

        Returns:
            str: Ruta absoluta del archivo temporal

        Raises:
            InvalidImageUpload: si la imagen no se puede decodificar
        """
        try:
            uploaded_file.seek(0)
        except Exception:
            pass

        raw = uploaded_file.read()

        if not raw:
            logger.error(
                f"Upload '{prefix}' llego vacio "
                f"(name={uploaded_file.name!r}, size={uploaded_file.size})"
            )
            raise InvalidImageUpload(
                'La imagen llego vacia. Si la foto esta en Google Fotos o '
                'iCloud, abrela primero para que se descargue al telefono y '
                'vuelve a subirla.'
            )

        try:
            image = Image.open(BytesIO(raw))
            image.load()  # decodifica de verdad, no solo las cabeceras
        except Exception as e:
            logger.error(
                f"Upload '{prefix}' ilegible: {type(e).__name__}: {e} "
                f"(name={uploaded_file.name!r}, bytes={len(raw)})"
            )
            raise InvalidImageUpload(
                'No se pudo leer la imagen: puede estar dañada o a medio '
                'subir. Intenta con otra foto.'
            )

        image = ImageOps.exif_transpose(image)  # fotos de celular giradas

        if image.mode in ('RGBA', 'LA') or (
            image.mode == 'P' and 'transparency' in image.info
        ):
            image = image.convert('RGBA')
        elif image.mode != 'RGB':
            image = image.convert('RGB')

        if max(image.size) > MAX_DIMENSION:
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

        filename = f"{prefix}_{uuid.uuid4().hex}.png"
        filepath = os.path.join(self.temp_dir, filename)
        image.save(filepath, format='PNG')

        logger.info(
            f"Upload '{prefix}' normalizado: {len(raw)} bytes -> "
            f"{os.path.getsize(filepath)} bytes, {image.size}, {image.mode}"
        )

        return filepath

    def save_generated_image(self, image_data: bytes, generation_id: str) -> str:
        """
        Guarda imagen generada en storage temporal

        Args:
            image_data: Datos binarios de la imagen
            generation_id: ID de la generación

        Returns:
            str: Ruta absoluta del archivo temporal
        """
        filename = f"generated_{generation_id}.png"
        filepath = os.path.join(self.temp_dir, filename)

        with open(filepath, 'wb') as f:
            f.write(image_data)

        return filepath

    def read_temp_file(self, filepath: str) -> Optional[bytes]:
        """Lee contenido de archivo temporal"""
        if filepath and os.path.exists(filepath):
            with open(filepath, 'rb') as f:
                return f.read()
        return None

    def delete_temp_file(self, filepath: str) -> bool:
        """Elimina archivo temporal"""
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                return True
            except Exception:
                return False
        return False

    def cleanup_old_files(self, max_age_hours: int = 24):
        """
        Elimina archivos temporales más viejos que max_age_hours

        Args:
            max_age_hours: Edad máxima en horas
        """
        now = time.time()
        max_age_seconds = max_age_hours * 3600

        for filename in os.listdir(self.temp_dir):
            filepath = os.path.join(self.temp_dir, filename)

            if os.path.isfile(filepath):
                file_age = now - os.path.getmtime(filepath)
                if file_age > max_age_seconds:
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
