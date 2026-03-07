from django.apps import AppConfig


class MusicConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.music"
    verbose_name = "Música"

    def ready(self):
        from apps.music.services.spotify_sync import start_sync
        start_sync()

