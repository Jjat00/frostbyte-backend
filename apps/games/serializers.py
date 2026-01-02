from rest_framework import serializers
from django.utils import timezone
from .models import Table, GameRoom, GameParticipant, GameRound, GameRoundResult


class TableSerializer(serializers.ModelSerializer):
    """Serializer para mesas"""
    
    class Meta:
        model = Table
        fields = [
            "id",
            "table_number",
            "qr_code",
            "is_active",
        ]
        read_only_fields = ["id", "qr_code"]


class GameParticipantSerializer(serializers.ModelSerializer):
    """Serializer para participantes"""
    
    class Meta:
        model = GameParticipant
        fields = [
            "id",
            "player_name",
            "player_device_id",
            "joined_at",
            "last_active",
        ]
        read_only_fields = ["id", "joined_at", "last_active"]


class GameParticipantCreateSerializer(serializers.Serializer):
    """Serializer para crear/agregar participante"""
    
    player_name = serializers.CharField(max_length=50)
    player_device_id = serializers.CharField(max_length=200)


class GameRoundResultSerializer(serializers.ModelSerializer):
    """Serializer para resultados de ronda"""
    
    participant_name = serializers.CharField(
        source="participant.player_name",
        read_only=True
    )
    display_time = serializers.CharField(read_only=True)
    
    class Meta:
        model = GameRoundResult
        fields = [
            "id",
            "participant",
            "participant_name",
            "reaction_time_ms",
            "display_time",
            "is_disqualified",
            "did_not_tap",
            "is_loser",
            "tapped_at",
        ]
        read_only_fields = ["id", "tapped_at", "is_loser"]


class GameRoundSerializer(serializers.ModelSerializer):
    """Serializer para rondas"""
    
    results = GameRoundResultSerializer(many=True, read_only=True)
    results_count = serializers.IntegerField(
        source="results.count",
        read_only=True
    )
    
    class Meta:
        model = GameRound
        fields = [
            "id",
            "round_number",
            "signal_delay_ms",
            "is_finished",
            "started_at",
            "finished_at",
            "results",
            "results_count",
        ]
        read_only_fields = [
            "id",
            "signal_delay_ms",
            "is_finished",
            "started_at",
            "finished_at",
        ]


class GameRoundCreateSerializer(serializers.Serializer):
    """Serializer para crear una ronda"""
    
    round_number = serializers.IntegerField(min_value=1)


class GameRoundResultCreateSerializer(serializers.Serializer):
    """Serializer para registrar resultado de ronda"""
    
    participant_id = serializers.IntegerField()
    reaction_time_ms = serializers.IntegerField(min_value=0, allow_null=True)
    tapped_at = serializers.DateTimeField(required=False)


class GameRoomListSerializer(serializers.ModelSerializer):
    """Serializer para listar salas (versión resumida)"""
    
    table_number = serializers.IntegerField(source="table.table_number", read_only=True)
    participant_count = serializers.IntegerField(source="participants.count", read_only=True)
    
    class Meta:
        model = GameRoom
        fields = [
            "id",
            "room_code",
            "room_link",
            "table_number",
            "status",
            "participant_count",
            "total_rounds",
            "current_round",
            "created_at",
            "expires_at",
        ]


class GameRoomDetailSerializer(serializers.ModelSerializer):
    """Serializer para detalle de sala"""
    
    table_number = serializers.IntegerField(source="table.table_number", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    participants = GameParticipantSerializer(many=True, read_only=True)
    participant_count = serializers.IntegerField(source="participants.count", read_only=True)
    rounds = GameRoundSerializer(many=True, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    can_play = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = GameRoom
        fields = [
            "id",
            "room_code",
            "room_link",
            "table",
            "table_number",
            "order",
            "order_number",
            "game_type",
            "status",
            "total_rounds",
            "current_round",
            "participants",
            "participant_count",
            "rounds",
            "wager_product",
            "wager_quantity",
            "is_expired",
            "can_play",
            "created_at",
            "started_at",
            "finished_at",
            "expires_at",
        ]
        read_only_fields = [
            "id",
            "room_code",
            "room_link",
            "game_type",
            "status",
            "current_round",
            "wager_product",
            "wager_quantity",
            "created_at",
            "started_at",
            "finished_at",
        ]
        read_only_fields = [
            "id",
            "room_code",
            "room_link",
            "status",
            "current_round",
            "created_at",
            "started_at",
            "finished_at",
        ]


class GameRoomCreateSerializer(serializers.Serializer):
    """Serializer para crear sala desde QR"""
    
    qr_code = serializers.CharField(max_length=100)
    player_name = serializers.CharField(max_length=50)
    player_device_id = serializers.CharField(max_length=200)


class GameRoomJoinSerializer(serializers.Serializer):
    """Serializer para unirse a una sala"""
    
    room_code = serializers.CharField(max_length=8, required=False)
    room_link = serializers.CharField(max_length=200, required=False)
    player_name = serializers.CharField(max_length=50)
    player_device_id = serializers.CharField(max_length=200)
    
    def validate(self, data):
        """Valida que se proporcione room_code o room_link"""
        if not data.get("room_code") and not data.get("room_link"):
            raise serializers.ValidationError(
                "Debe proporcionar room_code o room_link"
            )
        return data


class GameRoomConfigSerializer(serializers.Serializer):
    """Serializer para configurar número de rondas"""
    
    total_rounds = serializers.ChoiceField(choices=[1, 3, 5])


class GameRoomStartSerializer(serializers.Serializer):
    """Serializer para iniciar el juego"""
    pass  # No necesita datos, solo validar estado

