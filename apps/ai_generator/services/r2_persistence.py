"""
Servicio para persistir imágenes de generación a Cloudflare R2
"""
import os
import uuid
from datetime import datetime
from typing import Optional
from django.conf import settings
from django.utils import timezone
from apps.products.services.r2_upload import R2UploadService
import logging

logger = logging.getLogger(__name__)


class AIGenerationR2Service:
    """Maneja la persistencia de generaciones de IA a R2"""

    def __init__(self):
        self.r2_service = R2UploadService()

    def _upload_file_from_path(self, filepath: str, prefix: str) -> Optional[str]:
        """
        Sube archivo desde ruta del filesystem a R2

        Args:
            filepath: Ruta absoluta del archivo
            prefix: Prefijo para organizar en R2 (ej: 'ai_generations/originals')

        Returns:
            str: URL pública en R2, o None si falla
        """
        if not filepath or not os.path.exists(filepath):
            return None

        try:
            with open(filepath, 'rb') as f:
                file_content = f.read()

            # Generar nombre único
            ext = filepath.rsplit('.', 1)[-1] if '.' in filepath else 'png'
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_id = uuid.uuid4().hex[:8]
            r2_key = f"{prefix}/{timestamp}_{unique_id}.{ext}"

            # Subir a R2
            self.r2_service.client.put_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=r2_key,
                Body=file_content,
                ContentType='image/png',
            )

            # Construir URL pública
            public_url = f"{settings.R2_PUBLIC_URL.rstrip('/')}/{r2_key}"
            return public_url

        except Exception as e:
            logger.error(f"Error uploading to R2: {e}")
            return None

    def save_generation_to_r2(self, generation) -> bool:
        """
        Persiste una generación completa a R2

        Args:
            generation: Instancia de AIImageGeneration

        Returns:
            bool: True si se guardó exitosamente
        """
        if generation.is_saved_to_r2:
            return True

        if generation.status not in ['completed', 'saved']:
            return False

        try:
            # Subir original
            if generation.temp_original_path:
                url = self._upload_file_from_path(
                    generation.temp_original_path,
                    'ai_generations/originals'
                )
                if url:
                    generation.original_image_url = url

            # Subir reference (si existe)
            if generation.temp_reference_path:
                url = self._upload_file_from_path(
                    generation.temp_reference_path,
                    'ai_generations/references'
                )
                if url:
                    generation.reference_image_url = url

            # Subir generated
            if generation.temp_generated_path:
                url = self._upload_file_from_path(
                    generation.temp_generated_path,
                    'ai_generations/results'
                )
                if url:
                    generation.generated_image_url = url

            # Marcar como guardado
            generation.is_saved_to_r2 = True
            generation.status = 'saved'
            generation.saved_at = timezone.now()
            generation.save()

            # Limpiar archivos temporales
            generation.cleanup_temp_files()

            # Limpiar rutas temporales
            generation.temp_original_path = ''
            generation.temp_reference_path = ''
            generation.temp_generated_path = ''
            generation.save()

            logger.info(f"Generation {generation.id} saved to R2")
            return True

        except Exception as e:
            logger.error(f"Error saving generation to R2: {e}")
            return False

    def delete_from_r2(self, generation) -> bool:
        """Elimina imágenes de una generación de R2"""
        if not generation.is_saved_to_r2:
            return False

        try:
            for url in [generation.original_image_url,
                        generation.reference_image_url,
                        generation.generated_image_url]:
                if url:
                    self.r2_service.delete(url)

            return True
        except Exception as e:
            logger.error(f"Error deleting from R2: {e}")
            return False
