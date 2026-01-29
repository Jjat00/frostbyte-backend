from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AIImageGenerationViewSet

app_name = 'ai_generator'

router = DefaultRouter()
router.register(r'generations', AIImageGenerationViewSet, basename='ai-generation')

urlpatterns = [
    path('', include(router.urls)),
]

# Endpoints generados automáticamente:
#
# POST   /api/ai/generations/                        - Crear nueva generación
# GET    /api/ai/generations/                        - Listar historial
# GET    /api/ai/generations/{id}/                   - Detalle de generación
# POST   /api/ai/generations/{id}/save_to_product/   - Guardar en producto
# POST   /api/ai/generations/{id}/regenerate/        - Regenerar imagen
# GET    /api/ai/generations/quota_status/           - Estado de cuota
# GET    /api/ai/generations/stats/                  - Estadísticas del usuario
