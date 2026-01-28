from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CategoryViewSet, ProductViewSet, ProductVariantViewSet, ImageUploadView

router = DefaultRouter()
router.register(r"categories", CategoryViewSet, basename="category")
router.register(r"products", ProductViewSet, basename="product")
router.register(r"variants", ProductVariantViewSet, basename="variant")

urlpatterns = [
    path("", include(router.urls)),
    path("upload/image/", ImageUploadView.as_view(), name="image-upload"),
]
