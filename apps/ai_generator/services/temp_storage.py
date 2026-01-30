"""
Servicio para manejar almacenamiento temporal de imágenes
"""
import os
import uuid
import time
import tempfile
from typing import Optional
from django.core.files.uploadedfile import UploadedFile


class TempImageStorage:
    """Maneja almacenamiento temporal de imágenes antes de guardar a R2"""

    def __init__(self):
        self.temp_dir = os.path.join(tempfile.gettempdir(), 'ai_generations')
        os.makedirs(self.temp_dir, exist_ok=True)

    def save_temp_file(self, uploaded_file: UploadedFile, prefix: str = '') -> str:
        """
        Guarda archivo subido en storage temporal

        Args:
            uploaded_file: Archivo de Django
            prefix: Prefijo para el nombre (ej: 'original', 'reference')

        Returns:
            str: Ruta absoluta del archivo temporal
        """
        ext = uploaded_file.name.rsplit('.', 1)[-1] if '.' in uploaded_file.name else 'png'
        filename = f"{prefix}_{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(self.temp_dir, filename)

        with open(filepath, 'wb+') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

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
