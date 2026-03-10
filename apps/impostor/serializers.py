from rest_framework import serializers
from .models import (
    WordCategory,
    Word,
    ImpostorGameSession,
    ImpostorPlayer,
    ImpostorRound,
    ImpostorRoundPlayer,
)


# --- Word/Category Serializers ---

class WordSerializer(serializers.ModelSerializer):
    class Meta:
        model = Word
        fields = ["id", "word", "trap_word", "image_url"]


class WordCategoryListSerializer(serializers.ModelSerializer):
    word_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = WordCategory
        fields = ["slug", "name", "icon", "word_count", "has_images"]


class WordCategoryDetailSerializer(serializers.ModelSerializer):
    words = WordSerializer(many=True, read_only=True)

    class Meta:
        model = WordCategory
        fields = ["slug", "name", "icon", "has_images", "words"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["words"] = WordSerializer(
            instance.words.filter(is_active=True), many=True
        ).data
        return data


# --- Game Session Serializers ---

class ImpostorRoundPlayerSerializer(serializers.ModelSerializer):
    player_name = serializers.CharField(source="player.name", read_only=True)
    player_color = serializers.CharField(source="player.color", read_only=True)
    voted_for_name = serializers.CharField(
        source="voted_for.name", read_only=True, default=None
    )

    class Meta:
        model = ImpostorRoundPlayer
        fields = [
            "id",
            "player",
            "player_name",
            "player_color",
            "role",
            "voted_for",
            "voted_for_name",
            "votes_received",
            "points_earned",
            "guessed_word",
        ]


class ImpostorRoundSerializer(serializers.ModelSerializer):
    player_results = ImpostorRoundPlayerSerializer(many=True, read_only=True)

    class Meta:
        model = ImpostorRound
        fields = [
            "id",
            "round_number",
            "category_slug",
            "word",
            "trap_word",
            "impostor_caught",
            "player_results",
            "created_at",
        ]


class ImpostorPlayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImpostorPlayer
        fields = ["id", "name", "color", "total_score", "joined_at"]


class ImpostorGameSessionListSerializer(serializers.ModelSerializer):
    player_count = serializers.IntegerField(read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True)

    class Meta:
        model = ImpostorGameSession
        fields = [
            "id",
            "total_rounds",
            "impostor_count",
            "status",
            "player_count",
            "duration_seconds",
            "ai_words_used",
            "started_at",
            "finished_at",
        ]


class ImpostorGameSessionDetailSerializer(serializers.ModelSerializer):
    players = ImpostorPlayerSerializer(many=True, read_only=True)
    rounds = ImpostorRoundSerializer(many=True, read_only=True)
    player_count = serializers.IntegerField(read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True)

    class Meta:
        model = ImpostorGameSession
        fields = [
            "id",
            "total_rounds",
            "impostor_count",
            "hint_enabled",
            "trap_word_enabled",
            "spy_enabled",
            "turn_timer_seconds",
            "ai_words_used",
            "status",
            "player_count",
            "duration_seconds",
            "players",
            "rounds",
            "started_at",
            "finished_at",
        ]


# --- Create/Update Serializers ---

class CreateSessionSerializer(serializers.Serializer):
    total_rounds = serializers.IntegerField(min_value=1, max_value=10)
    impostor_count = serializers.IntegerField(min_value=1, max_value=2)
    hint_enabled = serializers.BooleanField(default=False)
    trap_word_enabled = serializers.BooleanField(default=True)
    spy_enabled = serializers.BooleanField(default=False)
    turn_timer_seconds = serializers.IntegerField(
        min_value=15, max_value=60, allow_null=True, required=False
    )
    ai_words_used = serializers.BooleanField(default=False)
    players = serializers.ListField(
        child=serializers.DictField(), min_length=3, max_length=10
    )

    def validate_players(self, value):
        for player in value:
            if "name" not in player or "color" not in player:
                raise serializers.ValidationError(
                    "Cada jugador debe tener 'name' y 'color'"
                )
            if not player["name"].strip():
                raise serializers.ValidationError(
                    "El nombre del jugador no puede estar vacío"
                )
        return value


class SaveRoundSerializer(serializers.Serializer):
    round_number = serializers.IntegerField(min_value=1)
    category_slug = serializers.CharField(max_length=50)
    word = serializers.CharField(max_length=100)
    trap_word = serializers.CharField(max_length=100, allow_blank=True, default="")
    impostor_caught = serializers.BooleanField()
    player_results = serializers.ListField(child=serializers.DictField())

    def validate_player_results(self, value):
        for result in value:
            if "player_id" not in result or "role" not in result:
                raise serializers.ValidationError(
                    "Cada resultado debe tener 'player_id' y 'role'"
                )
            if result["role"] not in ["normal", "impostor", "spy"]:
                raise serializers.ValidationError(
                    "role debe ser 'normal', 'impostor' o 'spy'"
                )
        return value


class FinishSessionSerializer(serializers.Serializer):
    player_scores = serializers.ListField(
        child=serializers.DictField(), required=False
    )
