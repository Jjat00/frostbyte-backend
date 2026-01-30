from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AIImageGenerationViewSet

router = DefaultRouter()
router.register(r'generations', AIImageGenerationViewSet, basename='ai-generation')

urlpatterns = [
    path('', include(router.urls)),
]

# POST   /api/v1/ai/generations/                     - Crear generación
# GET    /api/v1/ai/generations/                     - Listar
# GET    /api/v1/ai/generations/{id}/                - Detalle
# POST   /api/v1/ai/generations/{id}/save_to_product/ - Guardar en producto
