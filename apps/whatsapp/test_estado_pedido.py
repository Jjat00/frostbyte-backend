"""Cómo le cuenta consultar_pedido al modelo el estado del pedido.

Chat real del 24-10-2026: el equipo le dijo a la clienta que su domicilio ya
iba en camino, la pausa venció, llegó el comprobante y Frosty contestó "tu
pedido ya está listo para salir". La tool decía "estado: Listo", que es el
nombre del panel, y en un domicilio READY es que ya salió.
"""

from django.test import TestCase

from apps.orders.models import Order

from .models import WhatsAppContact
from .tools import build_tools

PHONE = "573001234567"


class EstadoDelPedidoComoLoViveElClienteTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.consultar = {t.name: t for t in build_tools(self.contact)}["consultar_pedido"]

    def _estado(self, order_type, status):
        Order.objects.create(
            source=Order.Source.WHATSAPP,
            customer_phone=PHONE,
            customer_name="Cliente",
            order_type=order_type,
            status=status,
        )
        return self.consultar.invoke({}).splitlines()[0]

    def test_un_domicilio_listo_ya_va_en_camino(self):
        linea = self._estado(Order.OrderType.DELIVERY, Order.Status.READY)
        self.assertIn("en camino", linea)
        self.assertNotIn("Listo", linea)

    def test_un_pedido_para_recoger_listo_espera_al_cliente(self):
        linea = self._estado(Order.OrderType.PICKUP, Order.Status.READY)
        self.assertIn("pase por él", linea)
        self.assertNotIn("en camino", linea)

    def test_un_pedido_en_el_local_listo_va_a_la_mesa(self):
        linea = self._estado(Order.OrderType.DINE_IN, Order.Status.READY)
        self.assertIn("mesa", linea)
        self.assertNotIn("en camino", linea)

    def test_en_preparacion_esta_en_la_cocina(self):
        linea = self._estado(Order.OrderType.DELIVERY, Order.Status.PREPARING)
        self.assertIn("cocina", linea)
        self.assertNotIn("en camino", linea)
