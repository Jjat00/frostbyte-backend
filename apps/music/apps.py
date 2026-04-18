import os
import sys

from django.apps import AppConfig


# Comandos de management que NO deben arrancar el hilo de sincronizacion.
# Durante `migrate` o `collectstatic` (que corren en el startCommand de Railway)
# no tiene sentido pegarle a la API de Spotify.
_SKIP_SYNC_COMMANDS = {
    "migrate",
    "makemigrations",
    "collectstatic",
    "shell",
    "test",
    "createsuperuser",
    "loaddata",
    "dumpdata",
    "check",
}


def _should_start_sync() -> bool:
    if os.getenv("DISABLE_SPOTIFY_SYNC") == "1":
        return False
    if len(sys.argv) >= 2 and sys.argv[1] in _SKIP_SYNC_COMMANDS:
        return False
    return True


class MusicConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.music"
    verbose_name = "Música"

    def ready(self):
        if not _should_start_sync():
            return
        from apps.music.services.spotify_sync import start_sync
        start_sync()
