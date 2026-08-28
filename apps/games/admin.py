from django.contrib import admin
from .models import Table, GameRoom, GameParticipant, GameRound, GameRoundResult, GameUsage
from apps.search import PlainSearchAdminMixin


@admin.register(Table)
class TableAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ["table_number", "qr_code", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["qr_code", "table_number"]
    readonly_fields = ["qr_code", "created_at", "updated_at"]


class GameParticipantInline(admin.TabularInline):
    model = GameParticipant
    extra = 0
    readonly_fields = ["joined_at", "last_active"]


class GameRoundInline(admin.TabularInline):
    model = GameRound
    extra = 0
    readonly_fields = ["started_at", "finished_at"]


@admin.register(GameRoom)
class GameRoomAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "room_code",
        "table",
        "game_type",
        "status",
        "total_rounds",
        "current_round",
        "created_at",
        "expires_at",
    ]
    list_filter = ["game_type", "status", "created_at"]
    search_fields = ["room_code", "room_link", "table__table_number"]
    readonly_fields = [
        "room_code",
        "room_link",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    ]
    fieldsets = (
        ("Información básica", {
            "fields": ("room_code", "room_link", "table", "order", "game_type")
        }),
        ("Configuración", {
            "fields": ("status", "total_rounds", "current_round")
        }),
        ("Apuestas (solo para juegos con apuestas)", {
            "fields": ("wager_product", "wager_quantity"),
            "classes": ("collapse",),
            "description": "Estos campos solo se usan para futuros juegos con apuestas"
        }),
        ("Fechas", {
            "fields": ("created_at", "updated_at", "started_at", "finished_at", "expires_at")
        }),
    )
    inlines = [GameParticipantInline, GameRoundInline]


@admin.register(GameParticipant)
class GameParticipantAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = ["player_name", "room", "joined_at", "last_active"]
    list_filter = ["joined_at"]
    search_fields = ["player_name", "player_device_id", "room__room_code"]
    readonly_fields = ["joined_at", "last_active"]


class GameRoundResultInline(admin.TabularInline):
    model = GameRoundResult
    extra = 0
    readonly_fields = ["tapped_at", "created_at"]


@admin.register(GameRound)
class GameRoundAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "room",
        "round_number",
        "signal_delay_ms",
        "is_finished",
        "started_at",
    ]
    list_filter = ["is_finished", "started_at"]
    search_fields = ["room__room_code"]
    readonly_fields = ["started_at", "finished_at"]
    inlines = [GameRoundResultInline]


@admin.register(GameRoundResult)
class GameRoundResultAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "participant",
        "round",
        "reaction_time_ms",
        "is_disqualified",
        "did_not_tap",
        "is_loser",
        "tapped_at",
    ]
    list_filter = ["is_disqualified", "did_not_tap", "is_loser", "tapped_at"]
    search_fields = ["participant__player_name", "round__room__room_code"]
    readonly_fields = ["tapped_at", "created_at"]
    fieldsets = (
        ("Información básica", {
            "fields": ("round", "participant")
        }),
        ("Resultados", {
            "fields": ("reaction_time_ms", "is_disqualified", "did_not_tap", "tapped_at")
        }),
        ("Apuestas (solo para juegos con apuestas)", {
            "fields": ("is_loser",),
            "classes": ("collapse",),
            "description": "Campo para identificar al perdedor en juegos con apuestas (futuro)"
        }),
        ("Fechas", {
            "fields": ("created_at",)
        }),
    )


@admin.register(GameUsage)
class GameUsageAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = [
        "player_name",
        "player_device_id_short",
        "table",
        "total_rounds",
        "room",
        "created_at",
        "game_finished_at",
    ]
    list_filter = ["created_at", "table", "total_rounds"]
    search_fields = [
        "player_name",
        "player_device_id",
        "room__room_code",
        "table__table_number",
    ]
    readonly_fields = ["created_at", "game_finished_at"]
    fieldsets = (
        ("Información del jugador", {
            "fields": ("player_name", "player_device_id")
        }),
        ("Información del juego", {
            "fields": ("room", "table", "total_rounds")
        }),
        ("Fechas", {
            "fields": ("created_at", "game_finished_at")
        }),
    )
    ordering = ["-created_at"]

    def player_device_id_short(self, obj):
        """Muestra solo los primeros 8 caracteres del device_id"""
        return f"{obj.player_device_id[:8]}..." if len(obj.player_device_id) > 8 else obj.player_device_id
    player_device_id_short.short_description = "ID Dispositivo"
