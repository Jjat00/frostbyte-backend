from django.contrib import admin
from .models import (
    WordCategory,
    Word,
    ImpostorGameSession,
    ImpostorPlayer,
    ImpostorRound,
    ImpostorRoundPlayer,
)


class WordInline(admin.TabularInline):
    model = Word
    extra = 1
    fields = ["word", "trap_word", "image_url", "is_active"]


@admin.register(WordCategory)
class WordCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "icon", "word_count", "has_images", "is_active"]
    list_filter = ["is_active", "has_images"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [WordInline]


@admin.register(Word)
class WordAdmin(admin.ModelAdmin):
    list_display = ["word", "trap_word", "category", "is_active"]
    list_filter = ["category", "is_active"]
    search_fields = ["word", "trap_word"]


class ImpostorPlayerInline(admin.TabularInline):
    model = ImpostorPlayer
    extra = 0
    readonly_fields = ["joined_at"]


class ImpostorRoundInline(admin.TabularInline):
    model = ImpostorRound
    extra = 0
    readonly_fields = ["created_at"]


@admin.register(ImpostorGameSession)
class ImpostorGameSessionAdmin(admin.ModelAdmin):
    list_display = [
        "short_id",
        "player_count",
        "total_rounds",
        "impostor_count",
        "status",
        "ai_words_used",
        "started_at",
        "finished_at",
    ]
    list_filter = ["status", "impostor_count", "ai_words_used", "started_at"]
    search_fields = ["id"]
    readonly_fields = ["id", "started_at", "finished_at", "duration_seconds"]
    inlines = [ImpostorPlayerInline, ImpostorRoundInline]

    def short_id(self, obj):
        return str(obj.id)[:8]
    short_id.short_description = "ID"


@admin.register(ImpostorPlayer)
class ImpostorPlayerAdmin(admin.ModelAdmin):
    list_display = ["name", "color", "total_score", "session", "joined_at"]
    list_filter = ["joined_at"]
    search_fields = ["name"]
    readonly_fields = ["joined_at"]


class ImpostorRoundPlayerInline(admin.TabularInline):
    model = ImpostorRoundPlayer
    extra = 0


@admin.register(ImpostorRound)
class ImpostorRoundAdmin(admin.ModelAdmin):
    list_display = [
        "session",
        "round_number",
        "word",
        "trap_word",
        "category_slug",
        "impostor_caught",
    ]
    list_filter = ["impostor_caught", "category_slug"]
    search_fields = ["word", "trap_word"]
    readonly_fields = ["created_at"]
    inlines = [ImpostorRoundPlayerInline]


@admin.register(ImpostorRoundPlayer)
class ImpostorRoundPlayerAdmin(admin.ModelAdmin):
    list_display = [
        "player",
        "round",
        "role",
        "voted_for",
        "votes_received",
        "points_earned",
    ]
    list_filter = ["role"]
    search_fields = ["player__name"]
