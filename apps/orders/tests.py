"""Cobrar un pedido es lo que mas duele si sale a medias.

mark_paid escribe en dos sitios -los items y el pedido- y hasta ahora lo hacia
suelto. Si el request moria en medio (un timeout, daphne matando la conexion:
paso el 22/08 con el pedido 5645) los items quedaban pagados y el pedido
marcado como NO pagado, que en pantalla es un pedido que se cobra dos veces.
Estas pruebas fijan que las dos escrituras van juntas o no van.
"""

from decimal import Decimal
from unittest import mock

from django.db.models import QuerySet
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.business.models import Business
from apps.orders.models import Order, OrderItem
from apps.products.models import Category, Product, ProductVariant


class MarkPaidTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="cajero", password="x", email="cajero@frostbyte.test")
        self.client.force_login(self.user)

        # Nombres propios del test: el negocio "Frostbyte" ya lo crea una
        # migracion de datos y sus slugs son unicos.
        business = Business.objects.create(name="Negocio de prueba")
        category = Category.objects.create(
            business=business, name="Categoria de prueba")
        product = Product.objects.create(
            business=business, category=category, name="Producto de prueba",
            description="para la prueba",
        )
        self.variant = ProductVariant.objects.create(
            product=product, name="Grande", sku="TEST-GR", price=Decimal("10000"),
        )

        self.order = Order.objects.create(
            order_number="20260822-TEST01",
            customer_name="Mesa de prueba",
            subtotal=Decimal("20000"),
            total=Decimal("20000"),
        )
        self.items = [
            OrderItem.objects.create(
                order=self.order,
                product_variant=self.variant,
                quantity=1,
                unit_price=Decimal("10000"),
                subtotal=Decimal("10000"),
            )
            for _ in range(2)
        ]

    def _url(self):
        return reverse("order-mark-paid", args=[self.order.pk])

    def _post(self, **data):
        return self.client.post(self._url(), data, content_type="application/json")

    def test_cobra_todos_los_items_y_marca_el_pedido(self):
        response = self._post(payment_method="cash")

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_paid)
        self.assertEqual(self.order.payment_method, "cash")
        for item in self.items:
            item.refresh_from_db()
            self.assertTrue(item.is_paid)
            self.assertEqual(item.payment_method, "cash")
            self.assertIsNotNone(item.paid_at)

    def test_si_falla_la_segunda_escritura_no_quedan_items_pagados(self):
        # El caso del 22/08: la primera escritura pasa y la segunda no llega.
        # Sin transaccion, los items quedaban cobrados y el pedido sin cobrar.
        #
        # Solo puede fallar el UPDATE del pedido: si tumbamos todos los UPDATE,
        # el de los items tampoco corre y la prueba pasaria sola, sin probar nada.
        original_update = QuerySet.update

        def falla_solo_el_pedido(queryset, *args, **kwargs):
            if queryset.model is Order:
                raise RuntimeError("la base se cayo a mitad del cobro")
            return original_update(queryset, *args, **kwargs)

        with mock.patch.object(QuerySet, "update", falla_solo_el_pedido):
            with self.assertRaises(RuntimeError):
                self._post(payment_method="cash")

        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)
        for item in self.items:
            item.refresh_from_db()
            self.assertFalse(
                item.is_paid,
                "el item quedo cobrado aunque el pedido no: cobro duplicado",
            )

    def test_rechaza_un_metodo_de_pago_que_el_local_no_acepta(self):
        response = self._post(payment_method="card")

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)

    def test_volver_a_cobrar_un_pedido_ya_pagado_no_lo_rompe(self):
        # El front reintenta cuando un request se queda colgado, asi que la
        # segunda llamada tiene que ser inofensiva.
        self._post(payment_method="cash")
        first = OrderItem.objects.get(pk=self.items[0].pk).paid_at

        response = self._post(payment_method="cash")

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_paid)
        self.assertEqual(
            OrderItem.objects.get(pk=self.items[0].pk).paid_at, first,
            "el reintento piso la hora de pago original",
        )
