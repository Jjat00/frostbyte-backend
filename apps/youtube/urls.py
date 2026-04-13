from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VideoRequestViewSet

router = DefaultRouter()
router.register(r"video-requests", VideoRequestViewSet, basename="video-request")

urlpatterns = [
    path("", include(router.urls)),
]
