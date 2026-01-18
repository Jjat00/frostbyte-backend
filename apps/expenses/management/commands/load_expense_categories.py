from django.core.management.base import BaseCommand
from apps.expenses.models import ExpenseCategory


class Command(BaseCommand):
    help = 'Carga las categorias de gastos iniciales'

    def handle(self, *args, **options):
        categories = [
            ('payroll', 'Nomina', 'Pagos a empleados', 'Users', 'blue', True, 1),
            ('utilities', 'Servicios Publicos', 'Luz, agua, gas, internet', 'Zap', 'yellow', True, 2),
            ('rent', 'Alquiler', 'Renta del local', 'Home', 'purple', True, 3),
            ('maintenance', 'Mantenimiento', 'Reparaciones y mantenimiento', 'Wrench', 'orange', False, 4),
            ('marketing', 'Marketing', 'Publicidad y promocion', 'Megaphone', 'pink', False, 5),
            ('insurance', 'Seguros', 'Polizas de seguros', 'Shield', 'green', True, 6),
            ('taxes', 'Impuestos', 'Impuestos y contribuciones', 'FileText', 'red', True, 7),
            ('supplies', 'Suministros', 'Articulos de oficina y limpieza', 'Package', 'cyan', False, 8),
            ('other', 'Otros', 'Otros gastos operativos', 'MoreHorizontal', 'gray', False, 9),
        ]

        created_count = 0
        updated_count = 0

        for slug, name, desc, icon, color, recurring, order in categories:
            obj, created = ExpenseCategory.objects.update_or_create(
                slug=slug,
                defaults={
                    'name': name,
                    'description': desc,
                    'icon': icon,
                    'color': color,
                    'is_recurring_default': recurring,
                    'display_order': order,
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
