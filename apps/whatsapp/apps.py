import os
import sys

from django.apps import AppConfig


# Comandos que NO deben levantar el vigía: durante `migrate` o `collectstatic`
# (que corren en el startCommand de Railway) no hay nadie esperando respuesta,
# y en los tests el barrido correría contra la base de prueba.
_SKIP_WATCHDOG_COMMANDS = {
    "migrate",
    "makemigrations",
    "collectstatic",
    "shell",
    "test",
    "createsuperuser",
    "loaddata",
    "dumpdata",
    "check",
    "rescatar_esperando",
}


def _should_start_watchdog() -> bool:
    if os.getenv("DISABLE_WHATSAPP_WATCHDOG") == "1":
        return False
    if len(sys.argv) >= 2 and sys.argv[1] in _SKIP_WATCHDOG_COMMANDS:
        return False
    return True


class WhatsappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.whatsapp"
    verbose_name = "WhatsApp"

    def ready(self):
        from . import signals  # noqa: F401

        if _should_start_watchdog():
            from .watchdog import start

            start()
