"""A mano: a quién se le avisaría que los domicilios ya están activos.

Solo mira. El vigía del servidor barre cada minuto y es quien escribe: este
proceso no ve sus turnos vivos —son memoria del otro—, así que mandar desde
aquí podría escribirle encima a una conversación que el agente está
contestando ahora mismo.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.whatsapp import domicilios


class Command(BaseCommand):
    help = "Lista los clientes que se quedaron sin domicilio mientras estaba apagado"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Se acepta por costumbre: este command nunca escribe",
        )

    def handle(self, *args, **options):
        ahora = timezone.now()
        desde = domicilios.reactivacion(ahora=ahora)
        if desde is None:
            self.stdout.write(
                "Los domicilios no están activos (o acaban de prenderse): nada que avisar."
            )
            return
        self.stdout.write(f"Domicilios activos desde {timezone.localtime(desde):%d/%m %H:%M}.")
        listos = domicilios.pendientes(ahora)
        if not listos:
            self.stdout.write("Nadie se quedó sin domicilio en la ventana.")
            return
        for contact in listos:
            espera = domicilios._cuanto(ahora - contact.delivery_missed_at)
            nombre = contact.customer_name or contact.profile_name or "sin nombre"
            self.stdout.write(f"{contact.phone} · {nombre} · chocó hace {espera}")
        self.stdout.write(
            self.style.WARNING(f"{len(listos)} por avisar; los manda el vigía, no este command")
        )
