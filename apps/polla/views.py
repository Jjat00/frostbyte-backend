"""API de la Polla Mundialista.

Lecturas publicas (se enriquecen con datos del usuario si viene autenticado);
escrituras (pronosticos, menciones) requieren sesion de cliente (JWT).
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import bracket as bracket_logic
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


# ── Bracket de eliminación (encadenado) ─────────────────────────────────────
def _team_lite(team):
    if not team:
        return None
    return {"code": team.code, "name": team.name, "iso2": team.iso2}


def _bracket_payload(user):
    """Arma la llave del usuario: rondas, competidores resueltos y picks."""
    predicted = bracket_logic.group_predicted_count(user)
    unlocked = predicted >= bracket_logic.GROUP_TOTAL

    matches = list(
        Match.objects.exclude(stage=Match.Stage.GROUP)
        .select_related("home_team", "away_team")
        .order_by("number")
    )
    authed = bool(user and user.is_authenticated)
    resolved = bracket_logic.resolve_bracket(user, unlocked=unlocked) if authed else {}
    preds = _predictions_map(user, matches)
    bracket_picks = (
        {bp.match_id: bp for bp in BracketPick.objects.filter(user=user)} if authed else {}
    )
    stage_label = dict(Match.Stage.choices)

    by_stage = {}
    for m in matches:
        info = resolved.get(m.number) or {}
        pick = info.get("pick")
        pred = preds.get(m.id)
        bp = bracket_picks.get(m.id)
        by_stage.setdefault(m.stage, []).append({
            "slug": m.slug, "number": m.number, "stage": m.stage,
            "round_label": m.round_label, "kickoff": m.kickoff,
            "venue_city": m.venue_city, "venue_stadium": m.venue_stadium,
            "status": m.status, "minute": m.minute,
            "home_score": m.home_score, "away_score": m.away_score,
            "home": _team_lite(info.get("home")),
            "away": _team_lite(info.get("away")),
            "home_source": m.home_placeholder or None,
            "away_source": m.away_placeholder or None,
            "my_pick": pick.code if pick else None,
            "pick_points": bp.points_earned if bp else 0,
            "pick_scored": bool(bp.scored) if bp else False,
            "my_prediction": (
                {
                    "home_score": pred.home_score, "away_score": pred.away_score,
                    "points_earned": pred.points_earned, "is_exact": pred.is_exact,
                    "is_correct_outcome": pred.is_correct_outcome, "scored": pred.scored,
                }
                if pred else None
            ),
            "is_locked": m.is_locked,
        })

    champion = None
    final_number = next(
        (m.number for m in matches if m.stage == Match.Stage.FINAL), None
    )
    if final_number is not None:
        fi = resolved.get(final_number) or {}
        champion = _team_lite(fi.get("pick"))

    rounds = [
        {
            "stage": st,
            "label": stage_label.get(st, st),
            "points": bracket_logic.BRACKET_POINTS.get(st, 0),
            "matches": by_stage.get(st, []),
        }
        for st in bracket_logic.ROUND_ORDER
        if by_stage.get(st)
    ]
    return {
        "unlocked": unlocked,
        "group_predicted": predicted,
        "group_total": bracket_logic.GROUP_TOTAL,
        "champion": champion,
        "rounds": rounds,
    }


class BracketView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(_bracket_payload(request.user))


class BracketPickView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, slug=None):
        match = (
            Match.objects.exclude(stage=Match.Stage.GROUP).filter(slug=slug).first()
        )
        if not match:
            return Response({"detail": "Cruce no encontrado."}, status=404)
        if not bracket_logic.is_unlocked(request.user):
            return Response(
                {"detail": "Completa los 72 pronósticos de grupos para abrir la llave."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if match.is_locked:
            return Response(
                {"detail": "Este cruce ya está cerrado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolved = bracket_logic.resolve_bracket(request.user, unlocked=True)
        info = resolved.get(match.number) or {}
        if not info.get("home") or not info.get("away"):
            return Response(
                {"detail": "Primero define quién llega a este cruce."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        competitors = {t.code: t for t in (info["home"], info["away"])}
        code = request.data.get("winner_code")
        if not isinstance(code, str) or code not in competitors:
            return Response(
                {"detail": "Ese equipo no está en este cruce."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        BracketPick.objects.update_or_create(
            user=request.user, match=match,
            defaults={"winner_team": competitors[code]},
        )
        bracket_logic.prune_invalid_picks(request.user)
        recompute_scores()
        return Response(_bracket_payload(request.user), status=status.HTTP_200_OK)


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
