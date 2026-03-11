from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import WordCategoryViewSet, ImpostorSessionViewSet

router = DefaultRouter()
router.register(r"categories", WordCategoryViewSet, basename="impostor-category")
router.register(r"sessions", ImpostorSessionViewSet, basename="impostor-session")

urlpatterns = [
    path("", include(router.urls)),
]
