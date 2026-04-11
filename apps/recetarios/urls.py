from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import RecipeBookViewSet

router = DefaultRouter()
router.register(r"recipes", RecipeBookViewSet, basename="recipe-book")

urlpatterns = [
    path("", include(router.urls)),
]
