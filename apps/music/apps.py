import os

from django.apps import AppConfig


class MusicConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.music"
    verbose_name = "Música"

    def ready(self):
        # Evitar doble ejecución en el autoreloader de Django
        if os.environ.get("RUN_MAIN") == "true" or "daphne" in os.environ.get("SERVER_SOFTWARE", ""):
            from apps.music.services.spotify_sync import start_sync
            start_sync()

