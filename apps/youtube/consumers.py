import json
from channels.generic.websocket import AsyncWebsocketConsumer


class YouTubeConsumer(AsyncWebsocketConsumer):
    """Consumer WebSocket para actualizaciones de videos en tiempo real.
    Se usa para sincronizar la pantalla TV con las solicitudes de video."""

    GROUP_NAME = 'youtube_updates'

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
            msg_type = data.get('type')

            if msg_type == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))

            elif msg_type == 'tv_playing':
                # La pantalla TV reporta que video esta sonando actualmente
                # (incluyendo videos del Mix automatico)
                from .models import TVState
                from asgiref.sync import sync_to_async

                video_id = data.get('video_id', '')
                title = data.get('title', '')
                channel_name = data.get('channel_name', '')
                is_mix = bool(data.get('is_mix', False))
                thumbnail = (
                    data.get('thumbnail')
                    or (f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else '')
                )

                @sync_to_async
                def save_state():
                    state = TVState.get_state()
                    state.video_id = video_id
                    state.title = title
                    state.channel_name = channel_name
                    state.thumbnail = thumbnail
                    state.is_mix = is_mix
                    state.save()

                await save_state()

                # Notificar a todos los clientes (publico, admin, etc.)
                await self.channel_layer.group_send(
                    self.GROUP_NAME,
                    {'type': 'youtube_changed'}
                )

            elif msg_type == 'video_ended':
                # La pantalla TV notifica que el video termino
                video_id = data.get('video_id')
                if video_id:
                    from .models import VideoRequest
                    from asgiref.sync import sync_to_async

                    # Marcar como completado
                    @sync_to_async
                    def complete_video():
                        try:
                            vr = VideoRequest.objects.filter(
                                video_id=video_id,
                                status=VideoRequest.Status.PLAYING,
                            ).first()
                            if vr:
                                vr.mark_as_completed()
                        except Exception:
                            pass

                    await complete_video()

                    # Notificar a todos que hay cambios
                    await self.channel_layer.group_send(
                        self.GROUP_NAME,
                        {'type': 'youtube_changed'}
                    )

            elif msg_type == 'request_next':
                # La pantalla TV pide el siguiente video
                await self.channel_layer.group_send(
                    self.GROUP_NAME,
                    {'type': 'youtube_changed'}
                )

        except json.JSONDecodeError:
            pass

    async def youtube_changed(self, event):
        """Notificar al cliente que las solicitudes cambiaron"""
        await self.send(text_data=json.dumps({
            'type': 'youtube_changed'
        }))

    async def youtube_play(self, event):
        """Notificar a la pantalla TV que reproduzca un video especifico"""
        await self.send(text_data=json.dumps({
            'type': 'play_video',
            'video_id': event.get('video_id', ''),
            'title': event.get('title', ''),
        }))

    async def youtube_control(self, event):
        """Enviar comando de control a la pantalla TV (pause, resume, skip)"""
        await self.send(text_data=json.dumps({
            'type': 'player_control',
            'action': event.get('action', ''),
        }))


def broadcast_youtube_update():
    """Helper para enviar notificacion de cambio desde las vistas"""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        YouTubeConsumer.GROUP_NAME,
        {'type': 'youtube_changed'}
    )


def broadcast_youtube_play(video_id, title=""):
    """Enviar comando para reproducir un video especifico en la pantalla TV"""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        YouTubeConsumer.GROUP_NAME,
        {
            'type': 'youtube_play',
            'video_id': video_id,
            'title': title,
        }
    )


def broadcast_youtube_control(action):
    """Enviar comando de control (pause, resume, skip) a la pantalla TV"""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        YouTubeConsumer.GROUP_NAME,
        {
            'type': 'youtube_control',
            'action': action,
        }
    )
