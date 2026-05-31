"""API de la Polla Mundialista.

Lecturas publicas (se enriquecen con datos del usuario si viene autenticado);
escrituras (pronosticos, menciones) requieren sesion de cliente (JWT).
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    UserMission,
    UserScore,
)
from .scoring import group_standings, recompute_missions_for_user, recompute_scores
from .serializers import (
    AwardSerializer,
    GroupSerializer,
    MatchSerializer,
    MissionSerializer,
    PredictionSerializer,
    PredictionWriteSerializer,
    TournamentSerializer,
)


def _predictions_map(user, matches=None):
    """{match_id: Prediction} del usuario (vacio si anonimo)."""
    if not user or not user.is_authenticated:
        return {}
    qs = Prediction.objects.filter(user=user)
    if matches is not None:
        qs = qs.filter(match__in=matches)
    return {p.match_id: p for p in qs}


def _display_name(user):
    return user.get_full_name() or user.first_name or user.username


# ── Torneo ────────────────────────────────────────────────────────────────
class TournamentView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        t = Tournament.get_active()
        if not t:
            return Response({"detail": "No hay torneo activo."}, status=404)
        data = TournamentSerializer(t).data
        data["counts"] = {
            "teams": Team.objects.count(),
            "matches": Match.objects.count(),
            "players": Player.objects.count(),
            "participants": UserScore.objects.count(),
        }
        return Response(data)


# ── Grupos y posiciones ─────────────────────────────────────────────────--
class GroupsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        groups = Group.objects.prefetch_related("teams").all()
        return Response(GroupSerializer(groups, many=True).data)


class StandingsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        out = []
        for g in Group.objects.prefetch_related("teams", "matches").all():
            rows = [
                {
                    "code": r["team"].code, "name": r["team"].name,
                    "iso2": r["team"].iso2, "rank": r["rank"],
                    "pj": r["pj"], "g": r["g"], "e": r["e"], "p": r["p"],
                    "gf": r["gf"], "gc": r["gc"], "dg": r["dg"], "pts": r["pts"],
                }
                for r in group_standings(g)
            ]
            out.append({"letter": g.letter, "name": g.name, "rows": rows})
        return Response(out)


# ── Partidos + pronosticos ─────────────────────────────────────────────────
class MatchViewSet(viewsets.ReadOnlyModelViewSet):
    """Lista/detalle de partidos. Soporta filtros y el pronostico propio."""

    serializer_class = MatchSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
    pagination_class = None

    def get_queryset(self):
        qs = Match.objects.select_related(
            "home_team", "away_team", "group"
        ).all()
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("stage"):
            qs = qs.filter(stage=p["stage"])
        if p.get("group"):
            qs = qs.filter(group__letter=p["group"].upper())
        if p.get("featured") in ("1", "true", "True"):
            qs = qs.filter(featured=True)
        team = p.get("team")
        if team:
            from django.db.models import Q
            qs = qs.filter(
                Q(home_team__code=team.upper()) | Q(away_team__code=team.upper())
            )
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["predictions"] = _predictions_map(self.request.user)
        return ctx

    @action(detail=True, methods=["put", "post"], permission_classes=[IsAuthenticated])
    def predict(self, request, slug=None):
        """Crea o actualiza el pronostico del usuario para este partido."""
        match = self.get_object()
        if match.is_locked:
            return Response(
                {"detail": "Los pronósticos de este partido ya están cerrados."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = PredictionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pred, _ = Prediction.objects.update_or_create(
            user=request.user, match=match,
            defaults=serializer.validated_data,
        )
        # Recalcular misiones/puntaje de este usuario (rapido; partido aun sin resultado)
        recompute_missions_for_user(request.user)
        recompute_scores()
        return Response(PredictionSerializer(pred).data, status=status.HTTP_200_OK)


class MyPredictionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        preds = (
            Prediction.objects.filter(user=request.user)
            .select_related("match")
            .order_by("match__kickoff")
        )
        return Response(PredictionSerializer(preds, many=True).data)


# ── Menciones ───────────────────────────────────────────────────────────---
class AwardsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        awards = Award.objects.all()
        picks = {}
        if request.user.is_authenticated:
            picks = {
                p.award_id: p
                for p in AwardPick.objects.filter(user=request.user).select_related(
                    "team", "player", "player__team"
                )
            }
        data = AwardSerializer(awards, many=True, context={"picks": picks}).data

        teams = Team.objects.all().order_by("name")
        players = Player.objects.filter(is_keeper=False).select_related("team").order_by("name")
        keepers = Player.objects.filter(is_keeper=True).select_related("team").order_by("name")
        options = {
            "team": [{"code": t.code, "name": t.name, "iso2": t.iso2} for t in teams],
            "player": [
                {"id": p.id, "name": p.name, "team_code": p.team.code, "iso2": p.team.iso2}
                for p in players
            ],
            "keeper": [
                {"id": p.id, "name": p.name, "team_code": p.team.code, "iso2": p.team.iso2}
                for p in keepers
            ],
        }
        return Response({"awards": data, "options": options})


class AwardPickView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, code=None):
        try:
            award = Award.objects.get(code=code)
        except Award.DoesNotExist:
            return Response({"detail": "Mención no encontrada."}, status=404)
        if award.resolved:
            return Response(
                {"detail": "Esta mención ya está resuelta; no se puede cambiar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        team = player = None
        if award.award_type == Award.AwardType.TEAM:
            code_in = request.data.get("team_code")
            team = Team.objects.filter(code=code_in).first()
            if not team:
                return Response({"detail": "Selección inválida."}, status=400)
        else:
            pid = request.data.get("player_id")
            player = Player.objects.filter(pk=pid).first()
            if not player:
                return Response({"detail": "Jugador inválido."}, status=400)
            if award.award_type == Award.AwardType.KEEPER and not player.is_keeper:
                return Response({"detail": "Debe ser un arquero."}, status=400)

        pick, _ = AwardPick.objects.update_or_create(
            user=request.user, award=award,
            defaults={"team": team, "player": player},
        )
        data = AwardSerializer(award, context={"picks": {award.id: pick}}).data
        return Response(data, status=status.HTTP_200_OK)


# ── Ranking ────────────────────────────────────────────────────────────────
class RankingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        scores = (
            UserScore.objects.select_related("user")
            .order_by("position", "-points")[:100]
        )
        me_id = request.user.id if request.user.is_authenticated else None
        rows = [
            {
                "pos": s.position,
                "name": _display_name(s.user),
                "points": s.points,
                "exact": s.exact_hits,
                "is_you": s.user_id == me_id,
            }
            for s in scores
        ]
        my_row = None
        if me_id and not any(r["is_you"] for r in rows):
            s = UserScore.objects.filter(user_id=me_id).select_related("user").first()
            if s:
                my_row = {
                    "pos": s.position, "name": _display_name(s.user),
                    "points": s.points, "exact": s.exact_hits, "is_you": True,
                }
        return Response({"ranking": rows, "me": my_row, "total": UserScore.objects.count()})


# ── Misiones + stats ────────────────────────────────────────────────────---
class MissionsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        missions = Mission.objects.all()
        user_missions = {}
        if request.user.is_authenticated:
            user_missions = {
                um.mission_id: um
                for um in UserMission.objects.filter(user=request.user)
            }
        data = MissionSerializer(
            missions, many=True, context={"user_missions": user_missions}
        ).data
        return Response({"missions": data, "my_stats": _my_stats(request.user)})


class MyStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_my_stats(request.user))


def _my_stats(user):
    total = UserScore.objects.count()
    if not user or not user.is_authenticated:
        return {"points": 0, "position": 0, "total": total,
                "exact_hits": 0, "correct_hits": 0, "predicted": 0}
    s = UserScore.objects.filter(user=user).first()
    if not s:
        return {"points": 0, "position": 0, "total": total,
                "exact_hits": 0, "correct_hits": 0, "predicted": 0}
    return {
        "points": s.points, "position": s.position, "total": total,
        "exact_hits": s.exact_hits, "correct_hits": s.correct_hits,
        "predicted": s.predicted,
    }
