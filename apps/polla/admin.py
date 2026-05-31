from django.contrib import admin

from .models import (
    Award,
    AwardPick,
    BracketPick,
    Group,
    Match,
    Mission,
    Player,
    Prediction,
    Team,
    Tournament,
    UserMission,
    UserScore,
)


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ["name", "kickoff", "prize", "api_season", "is_active"]
    list_filter = ["is_active"]


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ["letter", "name", "display_order"]
    ordering = ["display_order", "letter"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "group", "confederation", "is_host", "api_team_id"]
    list_filter = ["confederation", "group", "is_host"]
    search_fields = ["code", "name"]


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ["name", "team", "is_keeper", "api_player_id"]
    list_filter = ["is_keeper", "team"]
    search_fields = ["name"]


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = [
        "number", "slug", "stage", "round_label", "home_team", "away_team",
        "kickoff", "status", "home_score", "away_score", "winner_team", "featured",
    ]
    list_filter = ["stage", "status", "group", "featured"]
    search_fields = ["slug", "round_label", "venue_city"]
    ordering = ["kickoff", "number"]
    list_editable = ["status", "home_score", "away_score"]
    raw_id_fields = ["home_team", "away_team", "winner_team"]


@admin.register(Prediction)
class PredictionAdmin(admin.ModelAdmin):
    list_display = [
        "user", "match", "home_score", "away_score",
        "points_earned", "is_exact", "is_correct_outcome", "scored",
    ]
    list_filter = ["scored", "is_exact", "is_correct_outcome"]
    search_fields = ["user__username", "user__email"]
    raw_id_fields = ["user", "match"]


@admin.register(Award)
class AwardAdmin(admin.ModelAdmin):
    list_display = ["code", "title", "award_type", "points", "resolved",
                    "resolved_team", "resolved_player", "display_order"]
    list_filter = ["award_type", "resolved"]


@admin.register(AwardPick)
class AwardPickAdmin(admin.ModelAdmin):
    list_display = ["user", "award", "team", "player", "points_earned", "scored"]
    list_filter = ["award", "scored"]
    raw_id_fields = ["user", "team", "player"]


@admin.register(BracketPick)
class BracketPickAdmin(admin.ModelAdmin):
    list_display = ["user", "match", "winner_team", "points_earned", "scored"]
    list_filter = ["scored", "match__stage"]
    search_fields = ["user__username", "user__email"]
    raw_id_fields = ["user", "match", "winner_team"]


@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    list_display = ["code", "title", "kind", "target", "bonus_points", "param", "display_order"]
    list_filter = ["kind"]


@admin.register(UserMission)
class UserMissionAdmin(admin.ModelAdmin):
    list_display = ["user", "mission", "progress", "done", "completed_at"]
    list_filter = ["done", "mission"]
    raw_id_fields = ["user"]


@admin.register(UserScore)
class UserScoreAdmin(admin.ModelAdmin):
    list_display = [
        "position", "user", "points", "match_points", "award_points",
        "bracket_points", "mission_points", "exact_hits", "correct_hits", "predicted",
    ]
    ordering = ["position"]
    search_fields = ["user__username", "user__email"]
    raw_id_fields = ["user"]
