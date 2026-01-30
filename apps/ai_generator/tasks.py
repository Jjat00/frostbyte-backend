from django.core.files.base import ContentFile
from .models import AIImageGeneration
from .openai_integration import OpenAIImageGenerator
import logging

logger = logging.getLogger(__name__)


def generate_image_sync(generation_id):
    """Genera imagen con OpenAI de forma síncrona"""
    try:
        generation = AIImageGeneration.objects.get(id=generation_id)
    except AIImageGeneration.DoesNotExist:
        logger.error(f"Generation {generation_id} not found")
        return

    generation.status = 'processing'
    generation.save()

    try:
        generator = OpenAIImageGenerator()
        result = generator.generate_professional_menu_image(
            original_image_path=generation.original_image.path,
            reference_image_path=generation.reference_image.path if generation.reference_image else None,
            user_prompt=generation.user_prompt,
            transparent_background=generation.transparent_background
        )

        image_content = ContentFile(result['image_data'])
        generation.generated_image.save(
            f"gen_{generation.id}.png", image_content, save=False)
        generation.status = 'completed'
        generation.save()

        logger.info(f"Generation {generation_id} completed")

    except Exception as e:
        logger.error(f"Generation {generation_id} failed: {e}")
        generation.status = 'failed'
        generation.error_message = str(e)
        generation.save()
