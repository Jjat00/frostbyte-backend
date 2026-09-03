from django.urls import path

from .views import KapsoWebhookView, sticker_file

urlpatterns = [
    path("webhook/", KapsoWebhookView.as_view(), name="kapso-webhook"),
    path("stickers/<int:pk>.webp", sticker_file, name="whatsapp-sticker"),
]
