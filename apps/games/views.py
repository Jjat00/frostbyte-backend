import random
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction

from apps.orders.models import Order
from .models import Table, GameRoom, GameParticipant, GameRound, GameRoundResult
from .consumers import broadcast_room_update
from .serializers import (
    TableSerializer,
    GameRoomListSerializer,
    GameRoomDetailSerializer,
    GameRoomCreateSerializer,
    GameRoomJoinSerializer,
    GameRoomConfigSerializer,
    GameRoomStartSerializer,
    GameParticipantSerializer,
    GameRoundSerializer,
    GameRoundResultSerializer,
    GameRoundResultCreateSerializer,
)


def order_allows_game(order):
    """
    Verifica si un pedido permite el juego.

    El juego está disponible si el pedido NO está entregado Y pagado.
    Si el pedido está entregado pero aún no está pagado, el juego sigue disponible.
    Solo cuando está entregado Y pagado, el juego ya no está disponible.
    """
    # Si está cancelado, no permite juego
    if order.status == Order.Status.CANCELLED:
        return False
    # Si está entregado y pagado, no permite juego
    if order.status == Order.Status.DELIVERED and order.is_paid:
        return False
    # En cualquier otro caso (PENDING, PREPARING, READY, o DELIVERED sin pagar), permite juego
    return True


class TableViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet para mesas (solo lectura)"""

    queryset = Table.objects.all()
    serializer_class = TableSerializer


class GameRoomViewSet(viewsets.ModelViewSet):
    """ViewSet para salas de juego"""

    queryset = GameRoom.objects.select_related("table", "order").prefetch_related(
        "participants", "rounds", "rounds__results", "rounds__results__participant"
    )

    def get_serializer_class(self):
        if self.action == "list":
            return GameRoomListSerializer
        elif self.action in ["retrieve", "status"]:
            return GameRoomDetailSerializer
        elif self.action == "create_from_qr":
            return GameRoomCreateSerializer
        elif self.action == "join":
            return GameRoomJoinSerializer
        elif self.action == "configure":
            return GameRoomConfigSerializer
        elif self.action == "start":
            return GameRoomStartSerializer
        return GameRoomDetailSerializer

    @action(detail=False, methods=["post"], url_path="create-from-qr")
    def create_from_qr(self, request):
        """
        Crea una sala desde el escaneo de QR de una mesa.

        Valida que:
        - El QR pertenece a una mesa activa
        - La mesa tiene un pedido activo (PENDING, PREPARING o READY)
        - El pedido está en la misma mesa que el QR escaneado

        Ejemplo: Si se escanea el QR de la mesa 3, solo se activa si hay
        un pedido activo en la mesa 3.
        """
        serializer = GameRoomCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        qr_code = serializer.validated_data["qr_code"]
        player_name = serializer.validated_data["player_name"]
        player_device_id = serializer.validated_data["player_device_id"]

        # Buscar mesa por QR
        try:
            table = Table.objects.get(qr_code=qr_code, is_active=True)
        except Table.DoesNotExist:
            return Response(
                {"error": "Mesa no encontrada o inactiva"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Buscar pedido que permita el juego para esta mesa específica
        # IMPORTANTE: El pedido debe estar en la misma mesa que el QR escaneado
        # El juego está disponible si el pedido NO está entregado Y pagado
        # Incluir DELIVERED en la búsqueda porque puede estar entregado pero no pagado
        # Ordenar por fecha de creación descendente para obtener el pedido más reciente
        active_order = Order.objects.filter(
            table_number=table.table_number,
            status__in=[Order.Status.PENDING, Order.Status.PREPARING,
                        Order.Status.READY, Order.Status.DELIVERED]
        ).exclude(
            status=Order.Status.CANCELLED
        ).order_by('-created_at').first()

        # Validar que el pedido permita el juego (no entregado Y pagado)
        if not active_order or not order_allows_game(active_order):
            mesa_label = "Barra" if table.table_number == 0 else f"Mesa {table.table_number}"
            return Response(
                {"error": f"No hay pedido activo en la {mesa_label}. El juego solo está disponible cuando hay un pedido que no esté entregado y pagado."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buscar si ya existe una sala activa para esta mesa
        existing_room = GameRoom.objects.filter(
            table=table,
            status__in=[GameRoom.Status.WAITING,
                        GameRoom.Status.CONFIGURING, GameRoom.Status.PLAYING]
        ).first()

        if existing_room and not existing_room.is_expired:
            # Validar que el pedido de la sala existente siga permitiendo el juego
            if not order_allows_game(existing_room.order):
                return Response(
                    {"error": "El pedido asociado a esta sala ya no permite el juego (pedido entregado y pagado)"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Si la sala existe y no está expirada, agregar al participante
            participant, created = GameParticipant.objects.get_or_create(
                room=existing_room,
                player_device_id=player_device_id,
                defaults={"player_name": player_name}
            )
            if not created:
                # Actualizar nombre si ya existe
                participant.player_name = player_name
                participant.save()

            existing_room.extend_expiration()
            existing_room.save()
            broadcast_room_update(existing_room.id)
            serializer = GameRoomDetailSerializer(existing_room)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # Crear nueva sala
        with transaction.atomic():
            room = GameRoom.objects.create(
                table=table,
                order=active_order,
                status=GameRoom.Status.WAITING,
                total_rounds=3,  # Por defecto 3 rondas
            )

            # Agregar primer participante
            GameParticipant.objects.create(
                room=room,
                player_name=player_name,
                player_device_id=player_device_id
            )

        serializer = GameRoomDetailSerializer(room)
        broadcast_room_update(room.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def join(self, request):
        """Unirse a una sala existente usando room_code o room_link"""
        serializer = GameRoomJoinSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        room_code = serializer.validated_data.get("room_code")
        room_link = serializer.validated_data.get("room_link")
        player_name = serializer.validated_data["player_name"]
        player_device_id = serializer.validated_data["player_device_id"]

        # Buscar sala
        try:
            if room_code:
                room = GameRoom.objects.get(room_code=room_code)
            else:
                room = GameRoom.objects.get(room_link=room_link)
        except GameRoom.DoesNotExist:
            return Response(
                {"error": "Sala no encontrada"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validar que la sala esté activa
        if room.is_expired:
            room.status = GameRoom.Status.EXPIRED
            room.save()
            return Response(
                {"error": "La sala ha expirado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if room.status == GameRoom.Status.FINISHED:
            return Response(
                {"error": "La sala ya ha finalizado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validar que el pedido siga permitiendo el juego
        if not order_allows_game(room.order):
            return Response(
                {"error": "El pedido asociado ya no permite el juego (pedido entregado y pagado)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Agregar o actualizar participante
        participant, created = GameParticipant.objects.get_or_create(
            room=room,
            player_device_id=player_device_id,
            defaults={"player_name": player_name}
        )
        if not created:
            participant.player_name = player_name
            participant.save()

        # Extender expiración
        room.extend_expiration()
        room.save()
        broadcast_room_update(room.id)
        serializer = GameRoomDetailSerializer(room)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        """Obtener estado actual de la sala"""
        room = self.get_object()

        # Validar expiración
        if room.is_expired and room.status not in [GameRoom.Status.FINISHED, GameRoom.Status.EXPIRED]:
            room.status = GameRoom.Status.EXPIRED
            room.save()

        serializer = GameRoomDetailSerializer(room)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def configure(self, request, pk=None):
        """Configurar número de rondas antes de iniciar (permite editar si ya está configurado)"""
        room = self.get_object()

        if room.status not in [GameRoom.Status.WAITING, GameRoom.Status.CONFIGURING]:
            return Response(
                {"error": "Solo se puede configurar cuando la sala está esperando jugadores o configurando"},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = GameRoomConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        total_rounds = serializer.validated_data["total_rounds"]
        room.total_rounds = total_rounds
        room.status = GameRoom.Status.CONFIGURING
        room.save()
        broadcast_room_update(room.id)
        serializer_response = GameRoomDetailSerializer(room)
        return Response(serializer_response.data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        """Iniciar el juego"""
        room = self.get_object()

        # Refrescar la relación de participantes para obtener el conteo actualizado
        participant_count = room.participants.count()

        # Verificaciones detalladas para mejor mensaje de error
        if room.status not in [GameRoom.Status.WAITING, GameRoom.Status.CONFIGURING]:
            return Response(
                {"error": f"El juego no puede iniciarse. Estado actual: {room.status}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if participant_count < 2:
            return Response(
                {"error": f"Se requieren al menos 2 jugadores. Actualmente hay {participant_count} jugador(es)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verificar que el pedido permita el juego
        order_allows = not (room.order.status ==
                            Order.Status.DELIVERED and room.order.is_paid)
        order_allows = order_allows and room.order.status != Order.Status.CANCELLED

        if not order_allows:
            return Response(
                {"error": "El pedido no permite iniciar el juego. Verifica que el pedido esté activo"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if room.is_expired:
            return Response(
                {"error": "La sala ha expirado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        room.status = GameRoom.Status.PLAYING
        room.current_round = 1
        room.started_at = timezone.now()
        room.save()
        broadcast_room_update(room.id)
        serializer = GameRoomDetailSerializer(room)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="rounds/(?P<round_number>[0-9]+)/signal")
    def signal_round(self, request, pk=None, round_number=None):
        """
        Crea una ronda y genera el delay aleatorio para la señal.
        Esto se llama antes de iniciar la cuenta regresiva.
        """
        room = self.get_object()
        round_number = int(round_number)

        if room.status != GameRoom.Status.PLAYING:
            return Response(
                {"error": "El juego no está en curso"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if round_number < 1 or round_number > room.total_rounds:
            return Response(
                {"error": "Número de ronda inválido"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verificar si la ronda ya existe
        round_obj, created = GameRound.objects.get_or_create(
            room=room,
            round_number=round_number,
            defaults={
                # Entre 2 y 5 segundos
                "signal_delay_ms": random.randint(2000, 5000)
            }
        )

        if not created:
            # Si ya existe, solo devolver los datos
            pass

        serializer = GameRoundSerializer(round_obj)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="rounds/(?P<round_number>[0-9]+)/result")
    def submit_result(self, request, pk=None, round_number=None):
        """Registrar resultado de un jugador en una ronda"""
        room = self.get_object()
        round_number = int(round_number)

        try:
            round_obj = GameRound.objects.get(
                room=room, round_number=round_number)
        except GameRound.DoesNotExist:
            return Response(
                {"error": "Ronda no encontrada"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = GameRoundResultCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        participant_id = serializer.validated_data["participant_id"]
        reaction_time_ms = serializer.validated_data.get("reaction_time_ms")

        try:
            participant = GameParticipant.objects.get(
                id=participant_id, room=room)
        except GameParticipant.DoesNotExist:
            return Response(
                {"error": "Participante no encontrado"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Crear o actualizar resultado
        result, created = GameRoundResult.objects.get_or_create(
            round=round_obj,
            participant=participant,
            defaults={
                "reaction_time_ms": reaction_time_ms,
                "tapped_at": timezone.now() if reaction_time_ms else None,
                "did_not_tap": reaction_time_ms is None,
            }
        )

        if not created and reaction_time_ms:
            result.reaction_time_ms = reaction_time_ms
            result.tapped_at = timezone.now()
            result.did_not_tap = False
            result.is_disqualified = False
            result.save()

        # Verificar si todos los participantes ya respondieron
        participants_count = room.participants.count()
        results_count = round_obj.results.count()

        if results_count >= participants_count:
            # Todos respondieron, marcar ronda como finalizada
            round_obj.mark_finished()

            # Verificar si hay más rondas o si el juego terminó
            if round_number < room.total_rounds:
                room.current_round = round_number + 1
            else:
                # Juego terminado
                room.status = GameRoom.Status.FINISHED
                room.finished_at = timezone.now()
            room.save()
            broadcast_room_update(room.id)

        serializer_response = GameRoundResultSerializer(result)
        return Response(serializer_response.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="rounds/(?P<round_number>[0-9]+)/disqualify")
    def disqualify_participant(self, request, pk=None, round_number=None):
        """Descalificar a un participante por tocar antes de la señal (anticipación)"""
        room = self.get_object()
        round_number = int(round_number)

        try:
            round_obj = GameRound.objects.get(
                room=room, round_number=round_number)
        except GameRound.DoesNotExist:
            return Response(
                {"error": "Ronda no encontrada"},
                status=status.HTTP_404_NOT_FOUND
            )

        participant_id = request.data.get("participant_id")
        if not participant_id:
            return Response(
                {"error": "participant_id es requerido"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            participant = GameParticipant.objects.get(
                id=participant_id, room=room)
        except GameParticipant.DoesNotExist:
            return Response(
                {"error": "Participante no encontrado"},
                status=status.HTTP_404_NOT_FOUND
            )

        result, created = GameRoundResult.objects.get_or_create(
            round=round_obj,
            participant=participant,
            defaults={
                "is_disqualified": True,
                "did_not_tap": False,
            }
        )

        if not created:
            result.is_disqualified = True
            result.did_not_tap = False
            result.save()

        serializer = GameRoundResultSerializer(result)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        """Salir de la sala (eliminar participante)"""
        room = self.get_object()

        player_device_id = request.data.get("player_device_id")
        if not player_device_id:
            return Response(
                {"error": "player_device_id es requerido"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buscar y eliminar participante
        try:
            participant = GameParticipant.objects.get(
                room=room,
                player_device_id=player_device_id
            )
            participant.delete()

            # Si no quedan participantes, no hacemos nada especial
            # La sala seguirá existiendo por si alguien quiere volver a unirse

            broadcast_room_update(room.id)
            return Response(
                {"message": "Has salido de la sala"},
                status=status.HTTP_200_OK
            )
        except GameParticipant.DoesNotExist:
            return Response(
                {"error": "No estás en esta sala"},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=["post"])
    def rematch(self, request, pk=None):
        """Crear una nueva partida (revancha) en la misma sala"""
        room = self.get_object()

        if room.status != GameRoom.Status.FINISHED:
            return Response(
                {"error": "Solo se puede hacer revancha después de finalizar el juego"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validar que el pedido siga permitiendo el juego
        if not order_allows_game(room.order):
            return Response(
                {"error": "El pedido asociado ya no permite el juego (pedido entregado y pagado)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Resetear sala para nueva partida
        room.status = GameRoom.Status.WAITING
        room.current_round = 0
        # Resetear a valor por defecto (se configurará después)
        room.total_rounds = 3
        room.started_at = None
        room.finished_at = None
        room.expires_at = timezone.now() + timezone.timedelta(hours=1)

        # Eliminar rondas anteriores y sus resultados
        GameRound.objects.filter(room=room).delete()

        room.save()
        broadcast_room_update(room.id)
        serializer = GameRoomDetailSerializer(room)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def terminate(self, request, pk=None):
        """Terminar el juego y hacer que todos los participantes salgan de la sala"""
        room = self.get_object()

        # Permitir terminar el juego en cualquier estado
        # Eliminar todos los participantes de la sala
        GameParticipant.objects.filter(room=room).delete()

        # Marcar la sala como terminada
        room.status = GameRoom.Status.FINISHED
        room.finished_at = timezone.now()
        room.save()

        # Notificar a todos los jugadores (aunque ya no estén en la sala, por si acaso)
        broadcast_room_update(room.id)

        serializer = GameRoomDetailSerializer(room)
        return Response(serializer.data)
