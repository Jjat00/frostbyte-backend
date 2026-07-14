from django.apps import AppConfig


class WhatsappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.whatsapp"
    verbose_name = "WhatsApp"

    def ready(self):
        from . import signals  # noqa: F401
