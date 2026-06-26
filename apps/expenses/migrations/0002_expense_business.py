import django.db.models.deletion
from django.db import migrations, models

MAIN_BUSINESS_SLUG = "frostbyte"


def assign_main_business(apps, schema_editor):
    """Backfill: gastos y plantillas existentes pertenecen al negocio principal."""
    Business = apps.get_model("business", "Business")
    OperationalExpense = apps.get_model("expenses", "OperationalExpense")
    RecurringExpenseTemplate = apps.get_model("expenses", "RecurringExpenseTemplate")

    main = Business.objects.get(slug=MAIN_BUSINESS_SLUG)
    OperationalExpense.objects.filter(business__isnull=True).update(business=main)
    RecurringExpenseTemplate.objects.filter(business__isnull=True).update(business=main)


def backfill_not_reversible(apps, schema_editor):
    raise NotImplementedError(
        "Esta migracion no es reversible automaticamente: revertirla elimina la "
        "columna business y reaplicarla reasignaria TODO al negocio principal, "
        "perdiendo la separacion de Frostbyte Food. Revierte manualmente con backup."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0001_initial"),
        ("business", "0002_seed_businesses"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalexpense",
            name="business",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="operational_expenses",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.AddField(
            model_name="recurringexpensetemplate",
            name="business",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="recurring_expense_templates",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.RunPython(assign_main_business, backfill_not_reversible),
        migrations.AlterField(
            model_name="operationalexpense",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="operational_expenses",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.AlterField(
            model_name="recurringexpensetemplate",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="recurring_expense_templates",
                to="business.business",
                verbose_name="Negocio",
            ),
        ),
        migrations.AddIndex(
            model_name="operationalexpense",
            index=models.Index(
                fields=["business", "-expense_date"],
                name="expenses_op_business_idx",
            ),
        ),
    ]
