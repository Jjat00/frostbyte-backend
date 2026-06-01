from django.apps import AppConfig


class PollaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.polla"
    verbose_name = "Polla Mundialista"

    def ready(self):
        from . import signals  # noqa: F401  (registra los receivers)
