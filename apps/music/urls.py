from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SongRequestViewSet,
    SpotifyAuthView,
    SpotifyCallbackView,
    SpotifyDisconnectView,
    MusicSettingsView,
    MusicStatsView,
)

router = DefaultRouter()
router.register(r"song-requests", SongRequestViewSet, basename="song-request")

urlpatterns = [
    path("", include(router.urls)),
    path("music-settings/", MusicSettingsView.as_view(), name="music-settings"),
    path("music-stats/", MusicStatsView.as_view(), name="music-stats"),
    path("spotify/auth/", SpotifyAuthView.as_view(), name="spotify-auth"),
    path("spotify/callback/", SpotifyCallbackView.as_view(), name="spotify-callback"),
    path("spotify/disconnect/", SpotifyDisconnectView.as_view(), name="spotify-disconnect"),
]

