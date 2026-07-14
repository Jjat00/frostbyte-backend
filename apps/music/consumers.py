import json
from channels.generic.websocket import AsyncWebsocketConsumer


class MusicConsumer(AsyncWebsocketConsumer):
    """Consumer WebSocket para actualizaciones de solicitudes de música en tiempo real"""

    GROUP_NAME = 'music_updates'

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

    async def music_changed(self, event):
        """Notificar al cliente que las solicitudes de música cambiaron"""
        await self.send(text_data=json.dumps({
            'type': 'music_changed',
            'floor': event.get('floor'),
        }))


def broadcast_music_update(floor=None):
    """Helper para enviar notificación de cambio desde las vistas.

    floor=None significa cambio global (ej. music-settings); los clientes
    lo tratan como relevante para cualquier piso.
    """
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        MusicConsumer.GROUP_NAME,
        {'type': 'music_changed', 'floor': floor}
    )
