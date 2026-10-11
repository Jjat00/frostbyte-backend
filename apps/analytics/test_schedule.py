"""Pruebas del horario real del local (aperturas y cierres por jornada).

Lo que no se puede romper: que un cierre despues de medianoche cuente en la
jornada que empezo el dia anterior, y que abrir o cerrar desde el panel deje
el evento registrado.
"""

from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import StoreSettings, StoreStatusEvent

URL = "/api/v1/analytics/schedule/daily/"


def _at(days_ago, hour, minute=0):
    """Instante local de hace `days_ago` dias a la hora dada."""
    base = timezone.localtime() - timedelta(days=days_ago)
    tz = timezone.get_current_timezone()
    return datetime(base.year, base.month, base.day, hour, minute, tzinfo=tz)


class ScheduleAnalyticsTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="x", role=User.Role.ADMIN)
        self.client.force_login(self.admin)

    def _event(self, is_open, when):
        StoreStatusEvent.objects.create(is_open=is_open, created_at=when)

    def test_solo_admin(self):
        employee = User.objects.create_user(username="mesero", password="x")
        self.client.force_login(employee)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_sin_historial(self):
        body = self.client.get(URL).json()
        self.assertEqual(body["data"], [])
        self.assertIsNone(body["usual"]["open"])
        self.assertIsNone(body["tracking_since"])

    def test_cierre_despues_de_medianoche_cuenta_en_la_noche_anterior(self):
        self._event(True, _at(3, 13, 30))
        self._event(False, _at(2, 1, 30))  # 1:30 a. m. del dia siguiente

        data = self.client.get(URL).json()["data"]

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["date"], _at(3, 13).date().isoformat())
        self.assertEqual(data[0]["open"], 13.5)
        self.assertEqual(data[0]["close"], 25.5)

    def test_primera_apertura_y_ultimo_cierre_de_la_jornada(self):
        self._event(True, _at(2, 14))
        self._event(False, _at(2, 17))  # cierre de un rato
        self._event(True, _at(2, 17, 30))
        self._event(False, _at(2, 23, 15))

        row = self.client.get(URL).json()["data"][0]

        self.assertEqual((row["open"], row["close"]), (14, 23.25))

    def test_lo_normal_es_la_mediana(self):
        for days_ago, open_h, close_h in ((5, 13, 22), (4, 14, 23), (3, 18, 23)):
            self._event(True, _at(days_ago, open_h))
            self._event(False, _at(days_ago, close_h))

        usual = self.client.get(URL).json()["usual"]

        self.assertEqual((usual["open"], usual["close"]), (14, 23))

    def test_ventana_deja_fuera_lo_viejo(self):
        self._event(True, _at(40, 14))
        self._event(True, _at(2, 15))

        self.assertEqual(len(self.client.get(URL, {"days": 30}).json()["data"]), 1)
        self.assertEqual(len(self.client.get(URL, {"days": 90}).json()["data"]), 2)

    def test_abrir_y_cerrar_desde_el_panel_queda_registrado(self):
        StoreSettings.objects.update_or_create(pk=1, defaults={"is_open": False})
        url = "/api/v1/orders/store-settings/"

        self.client.patch(url, {"is_open": True}, content_type="application/json")
        self.client.patch(url, {"is_open": True}, content_type="application/json")  # sin cambio
        self.client.patch(url, {"is_open": False}, content_type="application/json")

        events = list(StoreStatusEvent.objects.values_list("is_open", "changed_by"))
        self.assertEqual(events, [(True, self.admin.id), (False, self.admin.id)])
