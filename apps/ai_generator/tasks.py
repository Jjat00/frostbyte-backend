"""
Tareas asíncronas para generación de imágenes con IA.

Nota: Este módulo está preparado para Celery pero puede funcionar
sin él en modo síncrono para desarrollo.
"""
from django.core.files.base import ContentFile
from django.utils import timezone
from .models import AIImageGeneration, AIGenerationUsageQuota
from .openai_integration import OpenAIImageGenerator
import logging
import time

logger = logging.getLogger(__name__)

# Importar Celery solo si está disponible
try:
    from celery import shared_task
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    logger.warning(
        "Celery no está disponible. Las tareas se ejecutarán de forma síncrona.")

    # Mock decorator para cuando Celery no está disponible
    def shared_task(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


@shared_task(bind=True, max_retries=3, default_retry_delay=60) if CELERY_AVAILABLE else shared_task()
def generate_image_task(self, generation_id):
    """
    Tarea asíncrona para generar imagen con OpenAI

    Args:
        generation_id: UUID de la generación
    """
    try:
        generation = AIImageGeneration.objects.get(id=generation_id)
    except AIImageGeneration.DoesNotExist:
        logger.error(f"Generation {generation_id} not found")
        return

    # Actualizar estado a processing
    generation.status = 'processing'
    generation.save()

    start_time = time.time()

    try:
        # Inicializar generador de OpenAI
        generator = OpenAIImageGenerator()

        logger.info(f"Starting image generation for {generation_id}")

        # Generar imagen
        result = generator.generate_professional_menu_image(
            original_image_path=generation.original_image.path,
            reference_image_path=generation.reference_image.path if generation.reference_image else None,
            user_prompt=generation.user_prompt,
            transparent_background=generation.transparent_background
        )

        logger.info(f"Image generated successfully for {generation_id}")

        # Guardar resultado
        image_content = ContentFile(result['image_data'])
        generation.generated_image.save(
            f"generated_{generation.id}.png",
            image_content,
            save=False
        )

        # Actualizar metadata
        generation.full_prompt = result['full_prompt']
        generation.status = 'completed'
        generation.generation_time_seconds = round(time.time() - start_time, 2)
        generation.cost_usd = result['cost_usd']
        generation.metadata = result.get('metadata', {})
        generation.completed_at = timezone.now()
        generation.save()

        # Actualizar cuota del usuario
        quota, created = AIGenerationUsageQuota.objects.get_or_create(
            user=generation.user,
            defaults={'monthly_limit': 10}
        )
        quota.increment_usage(cost=result['cost_usd'])

        logger.info(
            f"Generation {generation_id} completed successfully in "
            f"{generation.generation_time_seconds}s. Cost: ${generation.cost_usd}"
        )

        return {
            'success': True,
            'generation_id': str(generation.id),
            'time_seconds': float(generation.generation_time_seconds),
            'cost_usd': float(generation.cost_usd)
        }

    except Exception as e:
        logger.error(
            f"Error generating image for {generation_id}: {e}", exc_info=True)

        generation.status = 'failed'
        generation.error_message = str(e)
        generation.generation_time_seconds = round(time.time() - start_time, 2)
        generation.save()

        # Reintentar si no ha excedido max_retries (solo con Celery)
        if CELERY_AVAILABLE and hasattr(self, 'request') and self.request.retries < 3:
            raise self.retry(exc=e)

        return {
            'success': False,
            'generation_id': str(generation.id),
            'error': str(e)
        }


def generate_image_sync(generation_id):
    """
    Versión síncrona de la tarea para desarrollo sin Celery.

    Args:
        generation_id: UUID de la generación

    Returns:
        Dict con resultado de la generación
    """
    # generate_image_task tiene firma (self, generation_id) por @shared_task(bind=True).
    # Sin Celery no hay instancia; pasamos None como self.
    return generate_image_task(None, generation_id)


@shared_task
def cleanup_old_generations():
    """
    Tarea programada para limpiar generaciones antiguas.

    Se ejecuta diariamente para eliminar generaciones no asociadas
    a productos después de 90 días.
    """
    from datetime import timedelta

    cutoff_date = timezone.now() - timedelta(days=90)

    old_generations = AIImageGeneration.objects.filter(
        created_at__lt=cutoff_date,
        product__isnull=True  # No asociadas a productos
    )

    deleted_count = 0
    for gen in old_generations:
        # Eliminar archivos
        try:
            if gen.original_image:
                gen.original_image.delete(save=False)
            if gen.reference_image:
                gen.reference_image.delete(save=False)
            if gen.generated_image:
                gen.generated_image.delete(save=False)
        except Exception as e:
            logger.warning(f"Error deleting files for {gen.id}: {e}")

        gen.delete()
        deleted_count += 1

    logger.info(f"Cleaned up {deleted_count} old generations")
    return {'deleted_count': deleted_count}


@shared_task
def reset_monthly_quotas():
    """
    Tarea programada para resetear cuotas mensuales.

    Se ejecuta el primer día de cada mes.
    """
    from datetime import date

    today = date.today()
    quotas_to_reset = AIGenerationUsageQuota.objects.filter(
        last_reset__month__lt=today.month
    ) | AIGenerationUsageQuota.objects.filter(
        last_reset__year__lt=today.year
    )

    reset_count = 0
    for quota in quotas_to_reset:
        quota.reset_monthly_usage()
        reset_count += 1

    logger.info(f"Reset {reset_count} monthly quotas")
    return {'reset_count': reset_count}
