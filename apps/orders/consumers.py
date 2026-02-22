import json
from channels.generic.websocket import AsyncWebsocketConsumer


class OrdersConsumer(AsyncWebsocketConsumer):
    """Consumer WebSocket para actualizaciones de órdenes en tiempo real"""

    GROUP_NAME = 'orders_updates'

    async def connect(self):
        """Conectar al WebSocket"""
        await self.channel_layer.group_add(
            self.GROUP_NAME,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        """Desconectar del WebSocket"""
        await self.channel_layer.group_discard(
            self.GROUP_NAME,
            self.channel_name
        )

    async def receive(self, text_data):
        """Recibir mensaje del cliente"""
        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
        except json.JSONDecodeError:
            pass

    async def orders_changed(self, event):
        """Notificar al cliente que las órdenes cambiaron"""
        await self.send(text_data=json.dumps({
            'type': 'orders_changed'
        }))


def broadcast_orders_update():
    """Helper para enviar notificación de cambio desde las vistas"""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        OrdersConsumer.GROUP_NAME,
        {'type': 'orders_changed'}
    )
