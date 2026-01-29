from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CategoryViewSet, ProductViewSet, ProductVariantViewSet, ImageUploadView
from .services.image_generation_api import (
    ImageGenerationView,
    ImagePreviewView,
    ImageRegenerationView,
    UsageStatsView,
    TransparencyFixView,
    ImageGenerationWithFilesView,
)

router = DefaultRouter()
router.register(r"categories", CategoryViewSet, basename="category")
router.register(r"products", ProductViewSet, basename="product")
router.register(r"variants", ProductVariantViewSet, basename="variant")

urlpatterns = [
    path("", include(router.urls)),
    path("upload/image/", ImageUploadView.as_view(), name="image-upload"),

    # AI Image Generation Endpoints
    path("images/generate/", ImageGenerationView.as_view(), name="image-generate"),
    path("images/generate-upload/", ImageGenerationWithFilesView.as_view(), name="image-generate-upload"),
    path("images/preview/", ImagePreviewView.as_view(), name="image-preview"),
    path("images/regenerate/", ImageRegenerationView.as_view(), name="image-regenerate"),
    path("images/usage/", UsageStatsView.as_view(), name="image-usage"),
    path("images/fix-transparency/", TransparencyFixView.as_view(), name="image-fix-transparency"),
]
