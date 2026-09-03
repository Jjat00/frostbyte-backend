from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import AgentSettingsView, StickerViewSet
from .views import KapsoWebhookView, sticker_file

router = DefaultRouter()
router.register(r"stickers", StickerViewSet, basename="whatsapp-sticker-admin")

urlpatterns = [
    path("webhook/", KapsoWebhookView.as_view(), name="kapso-webhook"),
    # Antes del router: lo que descargan los servidores de Meta, sin autenticar
    path("stickers/<int:pk>.webp", sticker_file, name="whatsapp-sticker"),
    # Módulo de configuración del agente en el panel (solo admin)
    path("agent-settings/", AgentSettingsView.as_view(), name="whatsapp-agent-settings"),
    path("", include(router.urls)),
]
