from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SongRequestViewSet,
    SpotifyAuthView,
    SpotifyCallbackView,
    SpotifyDisconnectView,
)

router = DefaultRouter()
router.register(r"song-requests", SongRequestViewSet, basename="song-request")

urlpatterns = [
    path("", include(router.urls)),
    path("spotify/auth/", SpotifyAuthView.as_view(), name="spotify-auth"),
    path("spotify/callback/", SpotifyCallbackView.as_view(), name="spotify-callback"),
    path("spotify/disconnect/", SpotifyDisconnectView.as_view(), name="spotify-disconnect"),
]

