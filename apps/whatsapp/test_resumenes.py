"""Pruebas de los resúmenes de ventas al dueño (resumenes.py).

Lo que no se puede romper: que la jornada del día cuente lo vendido pasada la
medianoche y nada de después del cierre, que la semana y el mes salgan una
sola vez y solo el día que terminan, y que una plantilla rechazada no deje al
dueño sin resumen.
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.business.models import Business
from apps.orders.models import Order, OrderItem, StoreSettings, StoreStatusEvent
from apps.products.models import Category, Product, ProductVariant

from . import kapso, resumenes
from .models import AgentSettings, OwnerReport


def _at(y, m, d, h, mi=0):
    return timezone.make_aware(datetime(y, m, d, h, mi))


class ResumenBase(TestCase):
    def setUp(self):
        business = Business.objects.create(name="Negocio resumen")
        category = Category.objects.create(business=business, name="Granizados")
        self.lujuria = ProductVariant.objects.create(
            product=Product.objects.create(
                business=business, category=category, name="Lujuria", description="x"),
            name="Grande", sku="RES-LUJ", price=Decimal("10000"),
        )
        self.salchi = ProductVariant.objects.create(
            product=Product.objects.create(
                business=business, category=category, name="Salchipapa", description="x"),
            name="Personal", sku="RES-SAL", price=Decimal("15000"),
        )

    def _pedido(self, when, variant, qty, source=Order.Source.STAFF, paid=True,
                status=Order.Status.DELIVERED):
        total = variant.price * qty
        order = Order.objects.create(
            source=source, customer_name="Cliente", status=status, is_paid=paid,
            subtotal=total, total=total,
        )
        OrderItem.objects.create(
            order=order, product_variant=variant, quantity=qty,
            unit_price=variant.price, subtotal=total, is_paid=paid,
        )
        Order.objects.filter(pk=order.pk).update(created_at=when)
        return order


class ResumenDiaTests(ResumenBase):
    def test_cuenta_la_jornada_hasta_el_cierre(self):
        StoreStatusEvent.objects.create(is_open=True, created_at=_at(2026, 11, 14, 14))
        cierre = _at(2026, 11, 15, 1, 30)
        StoreStatusEvent.objects.create(is_open=False, created_at=cierre)

        self._pedido(_at(2026, 11, 14, 5), self.lujuria, 9)  # jornada anterior
        self._pedido(_at(2026, 11, 14, 15), self.lujuria, 2)
        self._pedido(_at(2026, 11, 14, 20), self.salchi, 1, source=Order.Source.WHATSAPP)
        self._pedido(_at(2026, 11, 15, 0, 40), self.lujuria, 1)  # pasada medianoche: cuenta
        self._pedido(_at(2026, 11, 14, 21), self.salchi, 4, status=Order.Status.CANCELLED)
        self._pedido(_at(2026, 11, 15, 1, 45), self.salchi, 3)  # después del cierre

        params = resumenes.resumen_dia(cierre)

        self.assertEqual(params[0], "sábado 14 de noviembre")
        self.assertEqual(params[1], "$45.000")
        self.assertEqual(params[2], "3")
        self.assertEqual(params[3], "$15.000")
        self.assertEqual(params[4], "Local 2 · WhatsApp 1")
        self.assertEqual(params[5], "Lujuria (3)")
        self.assertEqual(params[6], "2:00 p. m. a 1:30 a. m. (11 h 30 min)")

    def test_dia_sin_ventas_no_deja_variables_vacias(self):
        params = resumenes.resumen_dia(_at(2026, 11, 14, 22))
        self.assertTrue(all(params))
        self.assertEqual(params[4], "sin pedidos")
        self.assertEqual(params[6], "hasta las 10:00 p. m.")


class ResumenPeriodoTests(ResumenBase):
    def test_semana_compara_con_la_anterior_y_cuenta_dias(self):
        self._pedido(_at(2026, 11, 5, 18), self.lujuria, 2)  # semana anterior: $20.000
        self._pedido(_at(2026, 11, 10, 18), self.lujuria, 3)
        self._pedido(_at(2026, 11, 16, 2), self.salchi, 1)  # domingo pasada medianoche
        self._pedido(_at(2026, 11, 16, 7), self.salchi, 5)  # ya es la semana siguiente
        for d in (10, 13):
            StoreStatusEvent.objects.create(is_open=True, created_at=_at(2026, 11, d, 14))
            StoreStatusEvent.objects.create(is_open=False, created_at=_at(2026, 11, d, 22))

        params = resumenes.resumen_periodo(
            date(2026, 11, 9), date(2026, 11, 15),
            resumenes.etiqueta_semana(date(2026, 11, 9), date(2026, 11, 15)),
            date(2026, 11, 2), "la semana anterior",
        )

        self.assertEqual(params[0], "la semana del 9 al 15 de noviembre")
        self.assertEqual(params[1], "$45.000")
        self.assertEqual(params[2], "+125 % vs. la semana anterior")
        self.assertEqual(params[3], "2")
        self.assertEqual(params[7], "2 (16 h)")
        self.assertEqual(params[8], "$0")
        self.assertEqual(params[9], "100 %")

    def test_etiquetas(self):
        self.assertEqual(
            resumenes.etiqueta_semana(date(2026, 11, 30), date(2026, 12, 6)),
            "la semana del 30 de noviembre al 6 de diciembre",
        )
        self.assertEqual(resumenes.etiqueta_mes(date(2026, 10, 1)), "octubre 2026")


@override_settings(OWNER_REPORTS_ENABLED=True)
class BarridoTests(TestCase):
    def test_periodos_terminados(self):
        lunes = _at(2026, 11, 16, 6, 5)
        self.assertEqual(resumenes.semana_terminada(lunes), (date(2026, 11, 9), date(2026, 11, 15)))
        # A las 5:59 del lunes la jornada del domingo sigue abierta.
        self.assertEqual(
            resumenes.semana_terminada(_at(2026, 11, 16, 5, 59)),
            (date(2026, 11, 2), date(2026, 11, 8)),
        )
        self.assertEqual(
            resumenes.mes_terminado(_at(2026, 12, 1, 6)), (date(2026, 11, 1), date(2026, 11, 30))
        )

    @patch("apps.whatsapp.resumenes._enviar")
    def test_semana_sale_una_sola_vez_el_lunes(self, enviar):
        resumenes.barrer(_at(2026, 11, 16, 7, 59))  # antes de las 8: nada
        self.assertEqual(enviar.call_count, 0)

        resumenes.barrer(_at(2026, 11, 16, 8, 1))
        resumenes.barrer(_at(2026, 11, 16, 8, 2))
        self.assertEqual(enviar.call_count, 1)
        self.assertTrue(
            OwnerReport.objects.filter(kind=OwnerReport.Kind.WEEK, period_start=date(2026, 11, 9)).exists()
        )

    @patch("apps.whatsapp.resumenes._enviar")
    def test_no_manda_resumenes_viejos_al_desplegar(self, enviar):
        # Un despliegue el domingo no puede mandar la semana anterior ni el mes pasado.
        resumenes.barrer(_at(2026, 11, 15, 12))
        self.assertEqual(enviar.call_count, 0)

    @patch("apps.whatsapp.resumenes._enviar")
    def test_lunes_uno_manda_semana_y_mes(self, enviar):
        resumenes.barrer(_at(2027, 2, 1, 8, 1))  # lunes 1 de febrero
        self.assertEqual(enviar.call_count, 2)

    @override_settings(OWNER_REPORTS_ENABLED=False)
    @patch("apps.whatsapp.resumenes._enviar")
    def test_apagado_no_hace_nada(self, enviar):
        resumenes.barrer(_at(2026, 11, 16, 8, 1))
        self.assertEqual(enviar.call_count, 0)
        self.assertFalse(OwnerReport.objects.exists())


@override_settings(KAPSO_PHONE_NUMBER_IDS=["pn-1"])
class EnvioTests(TestCase):
    def setUp(self):
        config = AgentSettings.load()
        config.owner_phones = "+57 316 427 7879"
        config.save()

    @patch("apps.whatsapp.kapso.send_text", return_value={"ok": True})
    @patch("apps.whatsapp.kapso.send_template", return_value=None)
    def test_plantilla_rechazada_cae_a_texto_libre(self, template, text):
        params = ["sábado 14 de noviembre", "$45.000", "3", "$15.000", "Local 3", "Lujuria (3)", "x"]
        self.assertTrue(resumenes._enviar("reporte_cierre_dia", resumenes.TEMPLATE_DAY_TEXT, params))

        template.assert_called_once()
        self.assertEqual(template.call_args.args[1], "573164277879")
        texto = text.call_args.args[2]
        self.assertIn("Cierre del sábado 14 de noviembre", texto)
        self.assertNotIn("{{", texto)

    @patch("apps.whatsapp.kapso.send_text")
    @patch("apps.whatsapp.kapso.send_template", return_value={"messages": [{"id": "w"}]})
    def test_plantilla_aceptada_no_duplica(self, template, text):
        resumenes._enviar("resumen_periodo", resumenes.TEMPLATE_PERIOD_TEXT, ["a"] * 10)
        text.assert_not_called()

    def test_plantillas_tienen_tantas_variables_como_parametros(self):
        self.assertIn("{{7}}", resumenes.TEMPLATE_DAY_TEXT)
        self.assertNotIn("{{8}}", resumenes.TEMPLATE_DAY_TEXT)
        self.assertIn("{{10}}", resumenes.TEMPLATE_PERIOD_TEXT)
        self.assertNotIn("{{11}}", resumenes.TEMPLATE_PERIOD_TEXT)
        # Meta rechaza plantillas que empiezan o terminan en variable.
        for texto in (resumenes.TEMPLATE_DAY_TEXT, resumenes.TEMPLATE_PERIOD_TEXT):
            self.assertFalse(texto.strip().endswith("}}"))

    @patch("apps.whatsapp.kapso._post_message")
    def test_send_template_aplana_saltos_de_linea(self, post):
        kapso.send_template("pn-1", "573164277879", "reporte_cierre_dia", "es", ["a\nb", "", "c    d"])
        payload = post.call_args.args[1]
        textos = [p["text"] for p in payload["template"]["components"][0]["parameters"]]
        self.assertEqual(textos, ["a b", "-", "c d"])
        self.assertEqual(payload["template"]["language"], {"code": "es"})


@override_settings(OWNER_REPORTS_ENABLED=True)
class CierreDesdeElPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="x", role=User.Role.ADMIN)
        self.client.force_login(self.admin)
        StoreSettings.objects.update_or_create(pk=1, defaults={"is_open": False})

    @patch("apps.whatsapp.resumenes.resumen_dia", return_value=["x"] * 7)
    @patch("apps.whatsapp.resumenes._enviar")
    def test_solo_el_cierre_manda_resumen(self, enviar, _resumen):
        url = "/api/v1/orders/store-settings/"
        with patch("apps.whatsapp.resumenes.threading.Thread") as hilo:
            hilo.side_effect = lambda target, **kw: type("T", (), {"start": staticmethod(target)})()
            with self.captureOnCommitCallbacks(execute=True):
                self.client.patch(url, {"is_open": True}, content_type="application/json")
            self.assertEqual(enviar.call_count, 0)
            with self.captureOnCommitCallbacks(execute=True):
                self.client.patch(url, {"is_open": False}, content_type="application/json")
        self.assertEqual(enviar.call_count, 1)


@override_settings(OWNER_REPORTS_ENABLED=True)
class SemanaYMesAlCerrarTests(TestCase):
    """La semana sale al cerrar el domingo y el mes al cerrar el último día."""

    def _plantillas(self, enviar):
        return [c.args[0] for c in enviar.call_args_list]

    @patch("apps.whatsapp.resumenes._enviar")
    def test_cierre_entre_semana_solo_manda_el_dia(self, enviar):
        resumenes.resumenes_del_cierre(_at(2026, 11, 21, 23))  # sábado
        self.assertEqual(self._plantillas(enviar), ["reporte_cierre_dia"])
        self.assertFalse(OwnerReport.objects.exists())

    @patch("apps.whatsapp.resumenes._enviar")
    def test_cierre_del_domingo_manda_dia_y_semana(self, enviar):
        resumenes.resumenes_del_cierre(_at(2026, 11, 23, 1, 30))  # jornada del domingo 22
        self.assertEqual(self._plantillas(enviar), ["reporte_cierre_dia", "resumen_periodo"])
        self.assertEqual(enviar.call_args_list[1].args[2][0], "la semana del 16 al 22 de noviembre")

        # El respaldo de las 8 a. m. del lunes ya no la repite.
        resumenes.barrer(_at(2026, 11, 23, 8, 1))
        self.assertEqual(enviar.call_count, 2)

    @patch("apps.whatsapp.resumenes._enviar")
    def test_cierre_del_ultimo_dia_del_mes_manda_dia_y_mes(self, enviar):
        resumenes.resumenes_del_cierre(_at(2026, 11, 30, 23))  # lunes 30
        self.assertEqual(self._plantillas(enviar), ["reporte_cierre_dia", "resumen_periodo"])
        self.assertEqual(enviar.call_args_list[1].args[2][0], "noviembre 2026")
        resumenes.barrer(_at(2026, 12, 1, 8, 1))
        self.assertEqual(enviar.call_count, 2)

    @patch("apps.whatsapp.resumenes._enviar")
    def test_domingo_fin_de_mes_manda_los_tres(self, enviar):
        resumenes.resumenes_del_cierre(_at(2027, 1, 31, 23))  # domingo 31 de enero
        self.assertEqual(
            self._plantillas(enviar),
            ["reporte_cierre_dia", "resumen_periodo", "resumen_periodo"],
        )

    @patch("apps.whatsapp.resumenes._enviar")
    def test_cerrar_dos_veces_el_domingo_reenvia_con_cifras_al_dia(self, enviar):
        resumenes.resumenes_del_cierre(_at(2026, 11, 22, 18))
        resumenes.resumenes_del_cierre(_at(2026, 11, 22, 23))
        self.assertEqual(enviar.call_count, 4)
        self.assertEqual(OwnerReport.objects.count(), 1)
