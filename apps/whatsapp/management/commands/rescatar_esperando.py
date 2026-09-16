"""Una pasada del vigía a mano: quién está esperando y qué se le contesta."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.whatsapp import watchdog


class Command(BaseCommand):
    help = "Busca clientes de WhatsApp sin respuesta y deja que el agente retome"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo lista a quién se rescataría, sin escribirle a nadie",
        )

    def handle(self, *args, **options):
        ahora = timezone.now()
        pendientes = watchdog.esperando(ahora)
        if not pendientes:
            self.stdout.write("Nadie está esperando respuesta.")
            return
        for contact, ultimo in pendientes:
            espera = watchdog._cuanto(ahora - ultimo.created_at)
            self.stdout.write(f"{contact.phone} · {espera} · {ultimo.body[:70]}")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"{len(pendientes)} sin contestar (dry-run)"))
            return
        rescatados = watchdog.barrer(ahora)
        self.stdout.write(self.style.SUCCESS(f"{rescatados} conversaciones retomadas"))
