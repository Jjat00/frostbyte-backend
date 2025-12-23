from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UnitOfMeasureViewSet,
    RawMaterialViewSet,
    RecipeViewSet,
    PurchaseOrderViewSet,
)

router = DefaultRouter()
router.register(r"units", UnitOfMeasureViewSet, basename="unit")
router.register(r"raw-materials", RawMaterialViewSet, basename="raw-material")
router.register(r"recipes", RecipeViewSet, basename="recipe")
router.register(r"purchase-orders", PurchaseOrderViewSet, basename="purchase-order")

urlpatterns = [
    path("", include(router.urls)),
]
