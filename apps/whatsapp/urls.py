from django.urls import path

from .views import KapsoWebhookView

urlpatterns = [
    path("webhook/", KapsoWebhookView.as_view(), name="kapso-webhook"),
]
