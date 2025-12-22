from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UnitOfMeasureViewSet, RawMaterialViewSet, RecipeViewSet

router = DefaultRouter()
router.register(r"units", UnitOfMeasureViewSet, basename="unit")
router.register(r"raw-materials", RawMaterialViewSet, basename="raw-material")
router.register(r"recipes", RecipeViewSet, basename="recipe")

urlpatterns = [
    path("", include(router.urls)),
]

