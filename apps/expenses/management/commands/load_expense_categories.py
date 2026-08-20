from django.core.management.base import BaseCommand
from apps.expenses.models import ExpenseCategory, ExpenseKind


class Command(BaseCommand):
    help = 'Carga las categorias de gastos e inversion iniciales'

    def handle(self, *args, **options):
        OPEX = ExpenseKind.OPERATIONAL
        CAPEX = ExpenseKind.INVESTMENT

        categories = [
            # slug, nombre, descripcion, icono, color, recurrente, orden, tipo
            ('payroll', 'Nomina', 'Pagos a empleados', 'Users', 'blue', True, 1, OPEX),
            ('utilities', 'Servicios Publicos', 'Luz, agua, gas, internet', 'Zap', 'yellow', True, 2, OPEX),
            ('rent', 'Alquiler', 'Renta del local', 'Home', 'purple', True, 3, OPEX),
            ('maintenance', 'Mantenimiento', 'Reparaciones y mantenimiento', 'Wrench', 'orange', False, 4, OPEX),
            ('marketing', 'Marketing', 'Publicidad y promocion', 'Megaphone', 'pink', False, 5, OPEX),
            ('insurance', 'Seguros', 'Polizas de seguros', 'Shield', 'green', True, 6, OPEX),
            ('taxes', 'Impuestos', 'Impuestos y contribuciones', 'FileText', 'red', True, 7, OPEX),
            ('supplies', 'Suministros', 'Articulos de oficina y limpieza', 'Package', 'cyan', False, 8, OPEX),

            # Inversion (CAPEX): compra algo que dura, no es costo del mes
            ('equipment', 'Equipos', 'Neveras, televisores, sonido, celulares y maquinaria', 'Monitor', 'indigo', False, 20, CAPEX),
            ('furniture', 'Mobiliario', 'Sillas, mesas, barras y decoracion duradera', 'Armchair', 'amber', False, 21, CAPEX),
            ('remodeling', 'Montaje y adecuacion', 'Obra, contratista y adecuacion de locales o pisos nuevos', 'HardHat', 'teal', False, 22, CAPEX),

            ('other', 'Otros', 'Otros gastos operativos', 'MoreHorizontal', 'gray', False, 99, OPEX),
        ]

        created_count = 0
        updated_count = 0

        for slug, name, desc, icon, color, recurring, order, kind in categories:
            obj, created = ExpenseCategory.objects.update_or_create(
                slug=slug,
                defaults={
                    'name': name,
                    'description': desc,
                    'icon': icon,
                    'color': color,
                    'is_recurring_default': recurring,
                    'display_order': order,
                    'kind': kind,
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Categorias procesadas: {created_count} creadas, {updated_count} actualizadas'
            )
        )
