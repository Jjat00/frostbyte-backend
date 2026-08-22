import json
from channels.generic.websocket import AsyncWebsocketConsumer


class ReservationsConsumer(AsyncWebsocketConsumer):
    """Consumer WebSocket para actualizaciones de reservas en tiempo real"""

    GROUP_NAME = 'reservations_updates'

    async def connect(self):
        await self.channel_layer.group_add(
            self.GROUP_NAME,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.GROUP_NAME,
            self.channel_name
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
        except json.JSONDecodeError:
            pass

    async def reservations_changed(self, event):
        """Notificar al cliente que las reservas cambiaron"""
        await self.send(text_data=json.dumps({
            'type': 'reservations_changed'
        }))


def broadcast_reservations_update():
    """Helper para enviar notificación de cambio desde las vistas"""
    from apps.realtime import broadcast

    broadcast(ReservationsConsumer.GROUP_NAME, {'type': 'reservations_changed'})
