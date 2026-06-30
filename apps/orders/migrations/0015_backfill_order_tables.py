# Backfill: vincula los pedidos existentes a una mesa concreta (FK) y
# denormaliza el piso. Los datos previos eran de un solo piso, así que se
# asume el piso por defecto (2). Crea la mesa si el número aún no existe.
from django.db import migrations

DEFAULT_FLOOR = 2


def backfill_order_tables(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Table = apps.get_model("orders", "Table")

    table_cache = {}

    def get_or_create_table(number):
        key = (number, DEFAULT_FLOOR)
        if key in table_cache:
            return table_cache[key]
        table = Table.objects.filter(
            table_number=number, floor=DEFAULT_FLOOR).first()
        if table is None:
            name = "Barra" if number == 0 else f"Mesa {number}"
            table = Table.objects.create(
                table_number=number,
                floor=DEFAULT_FLOOR,
                table_name=name,
                is_active=True,
            )
        table_cache[key] = table
        return table

    orders = Order.objects.filter(
        table__isnull=True).exclude(table_number__isnull=True)
    for order in orders.iterator():
        table = get_or_create_table(order.table_number)
        order.table = table
        order.table_floor = table.floor
        order.save(update_fields=["table", "table_floor"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0014_alter_table_options_order_table_order_table_floor_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_order_tables, noop),
    ]
