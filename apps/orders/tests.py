"""Pruebas de pedidos: el cobro atomico y lo que le decimos al cliente.

Cobrar un pedido es lo que mas duele si sale a medias.

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
from apps.orders.models import Order, OrderItem, StoreSettings, Table
from apps.orders.serializers import OrderCreateSerializer
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


class DemoraEstimadaTests(TestCase):
    """Cuánto nos demoramos, en palabras: una estimación, nunca una promesa."""

    def test_el_rango_se_dice_de_menor_a_mayor(self):
        cfg = StoreSettings.load()
        cfg.eta_min_minutes, cfg.eta_max_minutes = 10, 20
        self.assertEqual(cfg.eta_label(), "de 10 a 20 minutos")

    def test_los_extremos_iguales_dan_un_solo_numero(self):
        cfg = StoreSettings.load()
        cfg.eta_min_minutes = cfg.eta_max_minutes = 15
        self.assertEqual(cfg.eta_label(), "unos 15 minutos")

    def test_invertirlos_por_error_no_produce_un_rango_al_reves(self):
        cfg = StoreSettings.load()
        cfg.eta_min_minutes, cfg.eta_max_minutes = 30, 15
        self.assertEqual(cfg.eta_label(), "de 15 a 30 minutos")


class DomicilioTomadoEnElLocalTests(TestCase):
    """El pedido que se encarga en el local pero se entrega en una casa.

    Pasa poco -alguien llega, pide y dice que se lo lleven- pero hasta ahora
    no habia forma de tomarlo: el panel exigia mesa siempre y la direccion no
    tenia donde escribirse, asi que terminaba de pedido de mesa con la
    direccion metida en las notas.
    """

    def setUp(self):
        business = Business.objects.create(name="Negocio del domicilio")
        category = Category.objects.create(
            business=business, name="Categoria del domicilio")
        product = Product.objects.create(
            business=business, category=category, name="Producto del domicilio",
            description="para la prueba",
        )
        self.variant = ProductVariant.objects.create(
            product=product, name="Personal", sku="DOM-PE", price=Decimal("12000"),
        )
        self.table = Table.objects.create(
            table_number=7, floor=2, table_name="Mesa 7")
        StoreSettings.load()  # singleton con la tarifa por defecto

    def _datos(self, **extra):
        datos = {
            "customer_name": "Quien encarga",
            "items": [{"product_variant_id": self.variant.id, "quantity": 1}],
        }
        datos.update(extra)
        return datos

    def _crear(self, **extra):
        serializer = OrderCreateSerializer(data=self._datos(**extra))
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    def test_el_domicilio_se_crea_sin_mesa_y_con_la_tarifa_del_local(self):
        order = self._crear(
            order_type="delivery",
            customer_phone="3117814338",
            delivery_address="Calle 10 # 5-20",
            delivery_reference="Casa blanca, porton verde",
        )

        self.assertEqual(order.order_type, Order.OrderType.DELIVERY)
        self.assertIsNone(order.table_id)
        self.assertIsNone(order.table_number)
        self.assertIsNone(order.table_floor)
        self.assertEqual(order.delivery_address, "Calle 10 # 5-20")
        self.assertEqual(order.delivery_fee, StoreSettings.load().delivery_fee)
        # El envio entra en el total, no se regala.
        self.assertEqual(
            order.total, Decimal("12000") + StoreSettings.load().delivery_fee)

    def test_la_tarifa_que_pone_el_staff_manda_sobre_la_del_local(self):
        order = self._crear(
            order_type="delivery",
            customer_phone="3117814338",
            delivery_address="Calle 10 # 5-20",
            delivery_fee="0",
        )

        self.assertEqual(order.delivery_fee, Decimal("0.00"))
        self.assertEqual(order.total, Decimal("12000"))

    def test_un_domicilio_sin_direccion_no_se_crea(self):
        serializer = OrderCreateSerializer(data=self._datos(
            order_type="delivery", customer_phone="3117814338"))

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_address", serializer.errors)

    def test_un_domicilio_sin_telefono_no_se_crea(self):
        serializer = OrderCreateSerializer(data=self._datos(
            order_type="delivery", delivery_address="Calle 10 # 5-20"))

        self.assertFalse(serializer.is_valid())
        self.assertIn("customer_phone", serializer.errors)

    def test_el_pedido_de_mesa_sigue_exigiendo_mesa(self):
        serializer = OrderCreateSerializer(data=self._datos())

        self.assertFalse(serializer.is_valid())
        self.assertIn("table_id", serializer.errors)

    def test_el_pedido_de_mesa_no_se_queda_con_datos_de_domicilio(self):
        """El checkbox se marca, se escribe la direccion y se desmarca."""
        order = self._crear(
            table_id=self.table.id,
            delivery_address="Calle 10 # 5-20",
            delivery_fee="3000",
        )

        self.assertEqual(order.order_type, Order.OrderType.DINE_IN)
        self.assertEqual(order.table_id, self.table.id)
        self.assertEqual(order.delivery_address, "")
        self.assertEqual(order.delivery_fee, Decimal("0.00"))
        self.assertEqual(order.total, Decimal("12000"))
