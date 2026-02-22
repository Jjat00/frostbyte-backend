import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from datetime import timedelta
from .models import GameRoom, GameParticipant
from .serializers import GameRoomDetailSerializer


class GameRoomConsumer(AsyncWebsocketConsumer):
    """Consumer WebSocket para salas de juego en tiempo real"""

    async def connect(self):
        """Conectar al WebSocket"""
        self.room_id = int(self.scope['url_route']['kwargs']['room_id'])
        self.room_group_name = f'game_room_{self.room_id}'

        # Verificar que la sala existe
        room_exists = await self.room_exists()
        if not room_exists:
            await self.close(code=4001)
            return

        # Unirse al grupo de la sala
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        # Enviar estado inicial
        await self.send_room_update()

    async def disconnect(self, close_code):
        """Desconectar del WebSocket"""
        # NO eliminar al participante automáticamente cuando se desconecta el WebSocket
        # Los jugadores solo deben salir cuando explícitamente usan el botón de salir
        # Esto evita que se eliminen jugadores por problemas de conexión temporal
        
        # Salir del grupo
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        """Recibir mensaje del cliente"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')

            if message_type == 'ping':
                # Responder a ping con pong
                await self.send(text_data=json.dumps({
                    'type': 'pong'
                }))
            elif message_type == 'request_update':
                # Cliente solicita actualización
                await self.send_room_update()
            elif message_type == 'temp_round_selection':
                # Reenviar selección temporal a todos los demás en el grupo
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'temp_round_selection',
                        'rounds': data.get('rounds')
                    }
                )

        except json.JSONDecodeError:
            pass

    async def room_update(self, event):
        """Enviar actualización de sala a todos los clientes"""
        await self.send(text_data=json.dumps({
            'type': 'room_update',
            'data': event['data']
        }))

    async def temp_round_selection(self, event):
        """Enviar selección temporal de rondas a todos los clientes"""
        await self.send(text_data=json.dumps({
            'type': 'temp_round_selection',
            'rounds': event['rounds']
        }))


    @database_sync_to_async
    def room_exists(self):
        """Verificar que la sala existe"""
        return GameRoom.objects.filter(id=self.room_id).exists()

    @database_sync_to_async
    def get_room_data(self):
        """Obtener datos actualizados de la sala"""
        try:
            room = GameRoom.objects.select_related("table", "order").prefetch_related(
                "participants", "rounds", "rounds__results", "rounds__results__participant"
            ).get(id=self.room_id)
            serializer = GameRoomDetailSerializer(room)
            return serializer.data
        except GameRoom.DoesNotExist:
            return None

    async def send_room_update(self):
        """Enviar actualización de la sala"""
        room_data = await self.get_room_data()
        if room_data:
            await self.send(text_data=json.dumps({
                'type': 'room_update',
                'data': room_data
            }))


class GamesAdminConsumer(AsyncWebsocketConsumer):
    """Consumer WebSocket para el panel de administración de juegos"""

    GROUP_NAME = 'games_admin_updates'

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

    async def games_admin_changed(self, event):
        """Notificar al cliente que los datos de admin cambiaron"""
        await self.send(text_data=json.dumps({
            'type': 'games_admin_changed'
        }))


def broadcast_games_admin_update():
    """Helper para enviar notificación de cambio al panel admin de juegos"""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        GamesAdminConsumer.GROUP_NAME,
        {'type': 'games_admin_changed'}
    )


def broadcast_room_update(room_id):
    """Función helper para enviar actualización desde las vistas"""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    
    channel_layer = get_channel_layer()
    room_group_name = f'game_room_{room_id}'
    
    # Obtener datos actualizados
    try:
        room = GameRoom.objects.select_related("table", "order").prefetch_related(
            "participants", "rounds", "rounds__results", "rounds__results__participant"
        ).get(id=room_id)
        serializer = GameRoomDetailSerializer(room)
        room_data = serializer.data
    except GameRoom.DoesNotExist:
        return
    
    # Enviar actualización al grupo
    async_to_sync(channel_layer.group_send)(
        room_group_name,
        {
            'type': 'room_update',
            'data': room_data
        }
    )


