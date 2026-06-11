from rest_framework import serializers

from .models import (
    Award,
    AwardPick,
    Group,
    Match,
    Mission,
    Player,
    Prediction,
    Team,
    Tournament,
)


class TournamentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tournament
        fields = [
            "name", "slug", "kickoff", "prize", "prize_note",
            "api_season", "is_active",
        ]


class TeamSerializer(serializers.ModelSerializer):
    group = serializers.CharField(source="group.letter", default=None, read_only=True)
    confederation_label = serializers.CharField(
        source="get_confederation_display", read_only=True
    )

    class Meta:
        model = Team
        fields = [
            "code", "name", "iso2", "confederation", "confederation_label",
            "group", "is_host", "recent_form",
        ]


class PlayerSerializer(serializers.ModelSerializer):
    team = serializers.SerializerMethodField()

    class Meta:
        model = Player
        fields = ["id", "name", "is_keeper", "team"]

    def get_team(self, obj):
        return {"code": obj.team.code, "name": obj.team.name, "iso2": obj.team.iso2}


def _side(team, placeholder):
    """Serializa un lado del partido: selección real o slot por definir."""
    if team:
        return {"code": team.code, "name": team.name, "iso2": team.iso2,
                "recent_form": team.recent_form or [], "placeholder": None}
    return {"code": None, "name": placeholder or "Por definir", "iso2": None,
            "recent_form": [], "placeholder": placeholder or None}


class MatchSerializer(serializers.ModelSerializer):
    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    group = serializers.CharField(source="group.letter", default=None, read_only=True)
    home = serializers.SerializerMethodField()
    away = serializers.SerializerMethodField()
    is_locked = serializers.BooleanField(read_only=True)
    my_prediction = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = [
            "slug", "number", "stage", "stage_display", "group", "round_label",
            "home", "away", "kickoff", "venue_city", "venue_stadium",
            "status", "minute", "home_score", "away_score",
            "featured", "is_locked", "my_prediction",
        ]

    def get_home(self, obj):
        return _side(obj.home_team, obj.home_placeholder)

    def get_away(self, obj):
        return _side(obj.away_team, obj.away_placeholder)

    def get_my_prediction(self, obj):
        preds = self.context.get("predictions") or {}
        p = preds.get(obj.id)
        if not p:
            return None
        return {
            "home_score": p.home_score,
            "away_score": p.away_score,
            "points_earned": p.points_earned,
            "is_exact": p.is_exact,
            "is_correct_outcome": p.is_correct_outcome,
            "scored": p.scored,
        }


class MatchDetailSerializer(MatchSerializer):
    """Detalle de un partido: agrega eventos y estadisticas en vivo.

    Solo se usa en el retrieve (``/matches/{slug}/``) para no engordar la
    lista de partidos con bloques que solo ve quien abre el detalle.
    """

    events = serializers.JSONField(read_only=True)
    stats = serializers.JSONField(source="statistics", read_only=True)

    class Meta(MatchSerializer.Meta):
        fields = MatchSerializer.Meta.fields + ["events", "stats"]


class GroupSerializer(serializers.ModelSerializer):
    teams = TeamSerializer(many=True, read_only=True)

    class Meta:
        model = Group
        fields = ["letter", "name", "teams"]


class PredictionWriteSerializer(serializers.Serializer):
    home_score = serializers.IntegerField(min_value=0, max_value=30)
    away_score = serializers.IntegerField(min_value=0, max_value=30)


class PredictionSerializer(serializers.ModelSerializer):
    match = serializers.CharField(source="match.slug", read_only=True)

    class Meta:
        model = Prediction
        fields = [
            "match", "home_score", "away_score", "points_earned",
            "is_exact", "is_correct_outcome", "scored", "updated_at",
        ]


class AwardSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="award_type", read_only=True)
    my_pick = serializers.SerializerMethodField()

    class Meta:
        model = Award
        fields = ["code", "title", "hint", "points", "type", "resolved", "my_pick"]

    def get_my_pick(self, obj):
        picks = self.context.get("picks") or {}
        pick = picks.get(obj.id)
        if not pick:
            return None
        if pick.team_id:
            t = pick.team
            return {"kind": "team", "team_code": t.code, "name": t.name, "iso2": t.iso2}
        if pick.player_id:
            p = pick.player
            return {
                "kind": "player", "player_id": p.id, "name": p.name,
                "team_code": p.team.code, "iso2": p.team.iso2,
            }
        return None


class MissionSerializer(serializers.ModelSerializer):
    progress = serializers.SerializerMethodField()
    done = serializers.SerializerMethodField()
    total = serializers.IntegerField(source="target", read_only=True)

    class Meta:
        model = Mission
        fields = ["code", "title", "desc", "reward", "kind", "total", "progress", "done"]

    def _um(self, obj):
        return (self.context.get("user_missions") or {}).get(obj.id)

    def get_progress(self, obj):
        um = self._um(obj)
        return um.progress if um else 0

    def get_done(self, obj):
        um = self._um(obj)
        return bool(um.done) if um else False
