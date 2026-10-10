"""Pruebas de las estadisticas de canales (domicilios, WhatsApp, app, Google).

Lo que no se puede romper: que cada pedido caiga en su canal, que los
cancelados no cuenten como venta y que los registros de la Polla no inflen
la cifra de clientes nuevos con Google.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order
from apps.polla.models import BracketPick, Match

URL = "/api/v1/analytics/channels/"


class ChannelAnalyticsTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="x", role=User.Role.ADMIN)
        self.client.force_login(self.admin)

        self.app_user = User.objects.create_user(
            username="cliente", password="x", role=User.Role.CUSTOMER,
            provider=User.Provider.GOOGLE, google_sub="g-1")
        self.polla_user = User.objects.create_user(
            username="pollero", password="x", role=User.Role.CUSTOMER,
            provider=User.Provider.GOOGLE, google_sub="g-2")
        match = Match.objects.create(number=1, slug="m1", kickoff=timezone.now())
        BracketPick.objects.create(user=self.polla_user, match=match)

        self._order(Order.Source.STAFF, Order.OrderType.DINE_IN, "10000")
        self._order(Order.Source.CUSTOMER, Order.OrderType.DELIVERY, "30000",
                    user=self.app_user, fee="3000")
        self._order(Order.Source.WHATSAPP, Order.OrderType.DELIVERY, "20000",
                    phone="3001112233", fee="3000")
        self._order(Order.Source.WHATSAPP, Order.OrderType.PICKUP, "15000",
                    phone="3001112233")
        self._order(Order.Source.WHATSAPP, Order.OrderType.DELIVERY, "99000",
                    phone="3009998877", status=Order.Status.CANCELLED)

    def _order(self, source, order_type, total, user=None, phone="", fee="0",
               status=Order.Status.PENDING):
        total = Decimal(total)
        return Order.objects.create(
            source=source, order_type=order_type, user=user,
            customer_name="Cliente", customer_phone=phone, status=status,
            subtotal=total - Decimal(fee), delivery_fee=Decimal(fee), total=total,
        )

    def test_solo_admin(self):
        employee = User.objects.create_user(username="mesero", password="x")
        self.client.force_login(employee)
        self.assertEqual(self.client.get(URL + "summary/").status_code, 403)

    def test_resumen_por_canal_sin_cancelados(self):
        data = self.client.get(URL + "summary/").json()
        channels = {c["source"]: c for c in data["channels"]}

        self.assertEqual(channels["staff"]["orders"], 1)
        self.assertEqual(channels["customer"]["orders"], 1)
        self.assertEqual(channels["customer"]["unique_customers"], 1)
        self.assertEqual(channels["whatsapp"]["orders"], 2)
        self.assertEqual(channels["whatsapp"]["cancelled"], 1)
        self.assertEqual(channels["whatsapp"]["revenue"], 35000)
        self.assertEqual(channels["whatsapp"]["unique_customers"], 1)

        delivery = data["delivery"]
        self.assertEqual(delivery["orders"], 2)
        self.assertEqual(delivery["cancelled"], 1)
        self.assertEqual(delivery["revenue"], 50000)
        self.assertEqual(delivery["delivery_fees"], 6000)
        self.assertEqual(delivery["by_source"], {"customer": 1, "whatsapp": 1})

        self.assertEqual(data["online_order_types"], {"delivery": 2, "pickup": 1})

    def test_google_separa_la_polla(self):
        google = self.client.get(URL + "summary/").json()["google_users"]
        self.assertEqual(google["total"], 2)
        self.assertEqual(google["polla"], 1)
        self.assertEqual(google["new_without_polla"], 1)
        self.assertEqual(google["with_orders"], 1)

    def test_ventana_deja_fuera_lo_viejo(self):
        old = self._order(Order.Source.WHATSAPP, Order.OrderType.DELIVERY, "5000")
        Order.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=20))

        week = self.client.get(URL + "summary/?days=7").json()
        whatsapp = next(c for c in week["channels"] if c["source"] == "whatsapp")
        self.assertEqual(whatsapp["orders"], 2)
        self.assertEqual(whatsapp["previous_orders"], 0)

        everything = self.client.get(URL + "summary/?days=0").json()
        whatsapp = next(c for c in everything["channels"] if c["source"] == "whatsapp")
        self.assertEqual(whatsapp["orders"], 3)
        self.assertNotIn("change", whatsapp)

    def test_mensual_y_lista_de_google(self):
        months = self.client.get(URL + "monthly/?months=3").json()["data"]
        self.assertEqual(len(months), 3)
        current = months[-1]
        self.assertEqual(current["whatsapp"], 2)
        self.assertEqual(current["delivery"], 2)
        self.assertEqual(current["google_users"], 2)
        self.assertEqual(current["google_users_polla"], 1)

        users = self.client.get(URL + "google_users/").json()["results"]
        by_name = {u["name"]: u for u in users}
        self.assertEqual(by_name["cliente"]["app_orders"], 1)
        self.assertTrue(by_name["pollero"]["played_polla"])
        self.assertIsNone(by_name["pollero"]["last_order_at"])
