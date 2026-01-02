from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TableViewSet, GameRoomViewSet

router = DefaultRouter()
router.register(r"tables", TableViewSet, basename="table")
router.register(r"rooms", GameRoomViewSet, basename="room")

urlpatterns = [
    path("", include(router.urls)),
]

