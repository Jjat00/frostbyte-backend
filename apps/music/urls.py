from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SongRequestViewSet

router = DefaultRouter()
router.register(r"song-requests", SongRequestViewSet, basename="song-request")

urlpatterns = [
    path("", include(router.urls)),
]

