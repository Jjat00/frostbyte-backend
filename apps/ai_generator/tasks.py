from .models import AIImageGeneration
from .openai_integration import OpenAIImageGenerator
from .services.temp_storage import TempImageStorage
import logging

logger = logging.getLogger(__name__)

# Models that use Gemini provider
GEMINI_MODELS = {'gemini-3-pro-image-preview', 'gemini-3.1-flash-image-preview'}

# Models that use OpenAI provider
OPENAI_MODELS = {'gpt-image-1.5', 'gpt-image-2'}


def _get_generator(model_name: str):
    """Returns the appropriate image generator based on model name."""
    if model_name in GEMINI_MODELS:
        from .gemini_integration import GeminiImageGenerator
        return GeminiImageGenerator(model=model_name)
    if model_name in OPENAI_MODELS:
        return OpenAIImageGenerator(model=model_name)
    # Fallback: OpenAI con su modelo por defecto
    return OpenAIImageGenerator()


def generate_image_sync(generation_id):
    """Genera imagen de forma sincrona usando el proveedor seleccionado"""
    try:
        generation = AIImageGeneration.objects.get(id=generation_id)
    except AIImageGeneration.DoesNotExist:
        logger.error(f"Generation {generation_id} not found")
        return

    generation.status = 'processing'
    generation.save()

    try:
        model_name = generation.ai_model or 'gemini-3-pro-image-preview'
        generator = _get_generator(model_name)
        temp_storage = TempImageStorage()

        logger.info(f"Generation {generation_id} using model: {model_name}")

        result = generator.generate_professional_menu_image(
            original_image_path=generation.temp_original_path,
            reference_image_path=generation.temp_reference_path if generation.temp_reference_path else None,
            user_prompt=generation.user_prompt,
            transparent_background=generation.transparent_background
        )

        # Guardar imagen generada en temp storage
        temp_generated_path = temp_storage.save_generated_image(
            result['image_data'],
            str(generation.id)
        )

        generation.temp_generated_path = temp_generated_path
        generation.status = 'completed'
        generation.save()

        logger.info(f"Generation {generation_id} completed (temp storage)")

    except Exception as e:
        logger.error(f"Generation {generation_id} failed: {e}")
        generation.status = 'failed'
        generation.error_message = str(e)
        generation.save()


def cleanup_old_generations():
    """
    Limpia generaciones antiguas no guardadas
    Ejecutar periódicamente (cada 6 horas)
    """
    from django.utils import timezone
    from datetime import timedelta

    # Eliminar generaciones >24h que no están guardadas
    cutoff_time = timezone.now() - timedelta(hours=24)

    old_generations = AIImageGeneration.objects.filter(
        is_saved_to_r2=False,
        created_at__lt=cutoff_time
    )

    count = 0
    for generation in old_generations:
        generation.cleanup_temp_files()
        generation.delete()
        count += 1

    if count > 0:
        logger.info(f"Cleaned up {count} old generations")

    # Limpiar archivos huérfanos en /tmp/
    temp_storage = TempImageStorage()
    temp_storage.cleanup_old_files(max_age_hours=24)
