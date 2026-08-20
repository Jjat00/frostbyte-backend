"""Tests de la separacion entre gasto corriente e inversion.

Lo que se protege aqui es una sola idea: comprar algo que dura no puede
restar del margen del mes. Si esto se rompe, el dashboard vuelve a mostrar en
perdida un mes rentable, que es exactamente el problema que motivo la feature.
"""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.business.models import Business
from apps.expenses.models import ExpenseCategory, ExpenseKind, OperationalExpense
from apps.expenses.serializers import OperationalExpenseCreateSerializer


class BaseGastos(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.negocio = Business.objects.create(name="Local de prueba", slug="local-prueba")
        call_command('load_expense_categories', stdout=StringIO())
        cls.suministros = ExpenseCategory.objects.get(slug='supplies')
        cls.equipos = ExpenseCategory.objects.get(slug='equipment')
        cls.montaje = ExpenseCategory.objects.get(slug='remodeling')

    def gasto(self, categoria, monto, descripcion='Gasto', dia=15, **extra):
        return OperationalExpense.objects.create(
            business=self.negocio,
            category=categoria,
            description=descripcion,
            amount=Decimal(monto),
            expense_date=date(2026, 5, dia),
            status=OperationalExpense.Status.PAID,
            **extra,
        )


class CategoriasTests(BaseGastos):
    def test_las_categorias_de_inversion_se_cargan_marcadas(self):
        self.assertEqual(self.equipos.kind, ExpenseKind.INVESTMENT)
        self.assertEqual(self.montaje.kind, ExpenseKind.INVESTMENT)
        self.assertEqual(self.suministros.kind, ExpenseKind.OPERATIONAL)

    def test_el_comando_es_idempotente(self):
        antes = ExpenseCategory.objects.count()
        call_command('load_expense_categories', stdout=StringIO())
        self.assertEqual(ExpenseCategory.objects.count(), antes)


class TipoDelGastoTests(BaseGastos):
    def test_por_defecto_un_gasto_es_corriente(self):
        gasto = self.gasto(self.suministros, 50000)
        self.assertEqual(gasto.kind, ExpenseKind.OPERATIONAL)
        self.assertFalse(gasto.is_investment)

    def test_el_serializer_hereda_el_tipo_de_la_categoria(self):
        serializer = OperationalExpenseCreateSerializer(data={
            'business': self.negocio.id,
            'category_id': self.equipos.id,
            'description': 'Nevera nueva',
            'amount': '1760000',
            'expense_date': '2026-05-12',
            'status': 'paid',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        gasto = serializer.save()
        self.assertEqual(gasto.kind, ExpenseKind.INVESTMENT)

    def test_el_tipo_explicito_gana_sobre_la_categoria(self):
        """Una reparacion cargada en una categoria de inversion sigue siendo gasto."""
        serializer = OperationalExpenseCreateSerializer(data={
            'business': self.negocio.id,
            'category_id': self.equipos.id,
            'kind': ExpenseKind.OPERATIONAL,
            'description': 'Reparacion de la nevera',
            'amount': '120000',
            'expense_date': '2026-05-12',
            'status': 'paid',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.save().kind, ExpenseKind.OPERATIONAL)


class MargenTests(BaseGastos):
    """El corazon: la inversion no puede tocar el margen."""

    def setUp(self):
        from apps.analytics.views import FinancialAnalyticsViewSet
        self.vista = FinancialAnalyticsViewSet()
        self.inicio = timezone.make_aware(
            timezone.datetime(2026, 5, 1, 0, 0, 0)
        )
        self.fin = timezone.make_aware(
            timezone.datetime(2026, 5, 31, 23, 59, 59)
        )

    def test_el_gasto_corriente_y_la_inversion_no_se_mezclan(self):
        self.gasto(self.suministros, 500000, 'Insumos de aseo')
        self.gasto(self.equipos, 1760000, 'Nevera', kind=ExpenseKind.INVESTMENT)
        self.gasto(self.montaje, 3000000, 'Pago contratista', kind=ExpenseKind.INVESTMENT)

        corriente = self.vista._calculate_operational_expenses(self.inicio, self.fin)
        inversion = self.vista._calculate_investment(self.inicio, self.fin)

        self.assertEqual(corriente, Decimal('500000'))
        self.assertEqual(inversion, Decimal('4760000'))

    def test_los_gastos_no_pagados_no_cuentan(self):
        self.gasto(self.equipos, 900000, 'Equipo pedido', kind=ExpenseKind.INVESTMENT)
        OperationalExpense.objects.create(
            business=self.negocio,
            category=self.equipos,
            kind=ExpenseKind.INVESTMENT,
            description='Equipo por pagar',
            amount=Decimal('700000'),
            expense_date=date(2026, 5, 20),
            status=OperationalExpense.Status.PENDING,
        )
        self.assertEqual(
            self.vista._calculate_investment(self.inicio, self.fin),
            Decimal('900000'),
        )

    def test_la_inversion_de_otro_mes_no_entra(self):
        self.gasto(self.equipos, 400000, 'Celular', dia=15, kind=ExpenseKind.INVESTMENT)
        OperationalExpense.objects.create(
            business=self.negocio,
            category=self.equipos,
            kind=ExpenseKind.INVESTMENT,
            description='TV de junio',
            amount=Decimal('2200000'),
            expense_date=date(2026, 6, 28),
            status=OperationalExpense.Status.PAID,
        )
        self.assertEqual(
            self.vista._calculate_investment(self.inicio, self.fin),
            Decimal('400000'),
        )


class ReclasificarTests(BaseGastos):
    def test_la_simulacion_no_escribe(self):
        gasto = self.gasto(self.suministros, 1760000, 'Nevera')
        salida = StringIO()
        call_command('reclassify_investments', stdout=salida)
        gasto.refresh_from_db()
        self.assertEqual(gasto.kind, ExpenseKind.OPERATIONAL)
        self.assertIn('Simulacion', salida.getvalue())

    def test_apply_reclasifica_y_mueve_de_categoria(self):
        gasto = self.gasto(self.suministros, 1760000, 'Nevera')
        call_command('reclassify_investments', '--apply', stdout=StringIO())
        gasto.refresh_from_db()
        self.assertEqual(gasto.kind, ExpenseKind.INVESTMENT)
        self.assertEqual(gasto.category.slug, 'equipment')

    def test_el_umbral_deja_pasar_lo_pequeno(self):
        chico = self.gasto(self.suministros, 60000, 'Sillas')
        call_command('reclassify_investments', '--apply', stdout=StringIO())
        chico.refresh_from_db()
        self.assertEqual(chico.kind, ExpenseKind.OPERATIONAL)

    def test_el_montaje_no_tiene_umbral(self):
        """Los abonos al contratista se capitalizan aunque uno sea pequeno."""
        abono = self.gasto(self.suministros, 50000, 'Abono implementacion piso 3')
        call_command('reclassify_investments', '--apply', stdout=StringIO())
        abono.refresh_from_db()
        self.assertEqual(abono.category.slug, 'remodeling')

    def test_una_reparacion_no_es_inversion(self):
        arreglo = self.gasto(self.suministros, 300000, 'Repuestos de maquina')
        call_command('reclassify_investments', '--apply', stdout=StringIO())
        arreglo.refresh_from_db()
        self.assertEqual(arreglo.kind, ExpenseKind.OPERATIONAL)

    def test_la_camara_de_comercio_no_es_una_camara(self):
        tramite = self.gasto(self.suministros, 264000, 'Renovacion camara de comercio')
        call_command('reclassify_investments', '--apply', stdout=StringIO())
        tramite.refresh_from_db()
        self.assertEqual(tramite.kind, ExpenseKind.OPERATIONAL)

    def test_exclude_ids_respeta_lo_que_ya_se_reviso(self):
        gasto = self.gasto(self.suministros, 1760000, 'Nevera')
        call_command(
            'reclassify_investments', '--apply',
            '--exclude-ids', str(gasto.id), stdout=StringIO(),
        )
        gasto.refresh_from_db()
        self.assertEqual(gasto.kind, ExpenseKind.OPERATIONAL)

    def test_keyword_propio_del_local(self):
        """El nombre del contratista lo pone quien conoce el local."""
        pago = self.gasto(self.suministros, 2379500, 'Abono a Constructora Mirador')
        call_command(
            'reclassify_investments', '--apply',
            '--keyword', 'remodeling:mirador', stdout=StringIO(),
        )
        pago.refresh_from_db()
        self.assertEqual(pago.category.slug, 'remodeling')
        self.assertEqual(pago.kind, ExpenseKind.INVESTMENT)

    def test_sin_el_keyword_ese_pago_no_se_toca(self):
        pago = self.gasto(self.suministros, 2379500, 'Abono a Constructora Mirador')
        call_command('reclassify_investments', '--apply', stdout=StringIO())
        pago.refresh_from_db()
        self.assertEqual(pago.kind, ExpenseKind.OPERATIONAL)
