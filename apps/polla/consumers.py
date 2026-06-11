import json
from channels.generic.websocket import AsyncWebsocketConsumer


class PollaConsumer(AsyncWebsocketConsumer):
    """Consumer WebSocket de la Polla Mundialista.

    No envía datos: emite una señal de invalidación (``polla_changed``) cuando
    el sync detecta cambios (goles, partidos que terminan, ranking recalculado).
    El frontend reacciona refetcheando sus queries REST, que siguen siendo la
    única fuente de verdad.
    """

    GROUP_NAME = 'polla_updates'

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

    async def polla_changed(self, event):
        """Notificar al cliente que los datos de la Polla cambiaron."""
        await self.send(text_data=json.dumps({
            'type': 'polla_changed'
        }))


def broadcast_polla_update():
    """Notifica a todos los clientes conectados que hay datos frescos.

    Funciona desde cualquier proceso que comparta el channel layer de Redis
    (en particular, desde el servicio cron que corre ``polla_sync``). Con el
    InMemoryChannelLayer de desarrollo la señal no cruza procesos.
    """
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        PollaConsumer.GROUP_NAME,
        {'type': 'polla_changed'}
    )
