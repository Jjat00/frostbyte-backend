"""
Comando de gestión para crear mesas de prueba con códigos QR.

Uso:
    python manage.py create_test_tables
    
Crea 6 mesas (Barra=0, Mesas 1-5) con códigos QR únicos.
"""
from django.core.management.base import BaseCommand
from apps.games.models import Table


class Command(BaseCommand):
    help = 'Crea mesas de prueba con códigos QR'

    def handle(self, *args, **options):
        # Crear Barra (mesa 0) y mesas 1-5
        tables_to_create = [
            (0, "Barra"),
            (1, "Mesa 1"),
            (2, "Mesa 2"),
            (3, "Mesa 3"),
            (4, "Mesa 4"),
            (5, "Mesa 5"),
        ]

        created_count = 0
        updated_count = 0

        for table_number, label in tables_to_create:
            qr_code = f"FROSTBYTE-TABLE{table_number:02d}"
            
            table, created = Table.objects.update_or_create(
                table_number=table_number,
                defaults={
                    "qr_code": qr_code,
                    "is_active": True,
                }
            )

            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✓ Creada {label} (QR: {qr_code})'
                    )
                )
            else:
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'↻ Actualizada {label} (QR: {qr_code})'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✅ Proceso completado: {created_count} creadas, {updated_count} actualizadas'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                '\n📋 Códigos QR generados:'
            )
        )
        for table_number, label in tables_to_create:
            qr_code = f"FROSTBYTE-TABLE{table_number:02d}"
            self.stdout.write(f'  {label}: {qr_code}')

