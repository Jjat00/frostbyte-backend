from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import RecipeCategoryViewSet, RecipeBookViewSet

router = DefaultRouter()
router.register(r"categories", RecipeCategoryViewSet, basename="recipe-category")
router.register(r"recipes", RecipeBookViewSet, basename="recipe-book")

urlpatterns = [
    path("", include(router.urls)),
]
