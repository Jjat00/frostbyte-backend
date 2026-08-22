"""El aviso por WebSocket no puede tumbar ni colgar la operación de negocio.

Cuando estas pruebas fallan, lo que se rompe en producción es la caja: un
``group_send`` lento deja el request colgado hasta que daphne lo mata, con el
pedido ya guardado pero el cajero viendo la app congelada.
"""

import asyncio
from unittest import mock

from django.test import SimpleTestCase

from apps import realtime


class FakeChannelLayer:
    """Channel layer de mentira: registra los envíos o se porta mal a pedido."""

    def __init__(self, delay=0, error=None):
        self.delay = delay
        self.error = error
        self.sent = []

    async def group_send(self, group, message):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.sent.append((group, message))


class BroadcastTests(SimpleTestCase):

    def _broadcast(self, layer, *args, **kwargs):
        with mock.patch("channels.layers.get_channel_layer", return_value=layer):
            return realtime.broadcast(*args, **kwargs)

    def test_entrega_el_mensaje_al_grupo(self):
        layer = FakeChannelLayer()

        entregado = self._broadcast(layer, "orders_updates", {"type": "orders_changed"})

        self.assertTrue(entregado)
        self.assertEqual(layer.sent, [("orders_updates", {"type": "orders_changed"})])

    def test_un_channel_layer_lento_no_cuelga_a_quien_llama(self):
        # Este es el caso que colgaba el POST de mark_paid: group_send sin
        # techo de tiempo. Ahora se corta y la vista sigue.
        layer = FakeChannelLayer(delay=5)

        entregado = self._broadcast(
            layer, "orders_updates", {"type": "orders_changed"}, timeout=0.05)

        self.assertFalse(entregado)
        self.assertEqual(layer.sent, [])

    def test_un_channel_layer_caido_no_rompe_la_vista(self):
        layer = FakeChannelLayer(error=ConnectionError("Redis no responde"))

        entregado = self._broadcast(layer, "orders_updates", {"type": "orders_changed"})

        self.assertFalse(entregado)

    def test_sin_channel_layer_configurado_no_hace_nada(self):
        entregado = self._broadcast(None, "orders_updates", {"type": "orders_changed"})

        self.assertFalse(entregado)
