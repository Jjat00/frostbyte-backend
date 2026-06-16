"""API de la Polla Mundialista.

Lecturas publicas (se enriquecen con datos del usuario si viene autenticado);
escrituras (pronosticos, menciones) requieren sesion de cliente (JWT).
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Avg, Max, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminUser

from . import bracket as bracket_logic
from .models import (
    REFERRAL_CAP,
    REFERRAL_POINTS_PER,
    Award,
    AwardPick,
    BracketPick,
    Group,
    Match,
    Mission,
    Player,
    Prediction,
    Referral,
    Team,
    TopScorer,
    Tournament,
    UserMission,
    UserScore,
    outcome_of,
)
from .scoring import group_standings, recompute_missions_for_user, recompute_scores
from .serializers import (
    AwardSerializer,
    GroupSerializer,
    MatchDetailSerializer,
    MatchSerializer,
    MissionSerializer,
    PredictionSerializer,
    PredictionWriteSerializer,
    TeamSerializer,
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


# ── Ficha de una selección ─────────────────────────────────────────────────
class TeamDetailView(APIView):
    """Ficha de una selección: plantilla, forma, posición en grupo y partidos."""

    permission_classes = [AllowAny]

    def get(self, request, code=None):
        team = (
            Team.objects.select_related("group")
            .filter(code=(code or "").upper())
            .first()
        )
        if not team:
            return Response({"detail": "Selección no encontrada."}, status=404)

        standing = None
        if team.group:
            for row in group_standings(team.group):
                if row["team"].id == team.id:
                    standing = {
                        "rank": row["rank"], "pj": row["pj"], "g": row["g"],
                        "e": row["e"], "p": row["p"], "gf": row["gf"],
                        "gc": row["gc"], "dg": row["dg"], "pts": row["pts"],
                    }
                    break

        # Goles en el torneo (tabla de goleadores) para decorar la plantilla.
        scorer_goals = {
            ts.player_id: ts.goals
            for ts in TopScorer.objects.filter(team=team, player__isnull=False)
        }
        players = [
            {
                "id": p.id, "name": p.name, "is_keeper": p.is_keeper,
                "photo": p.photo_url or None, "position": p.position or None,
                "number": p.number, "age": p.age,
                "goals": scorer_goals.get(p.id, 0),
            }
            for p in team.players.order_by("-is_keeper", "name")
        ]

        def side(t, placeholder):
            return _team_lite(t) or {
                "code": None, "name": placeholder or "Por definir", "iso2": None,
            }

        matches = [
            {
                "slug": m.slug,
                "stage": m.stage,
                "stage_display": m.get_stage_display(),
                "group": m.group.letter if m.group else None,
                "round_label": m.round_label,
                "kickoff": m.kickoff,
                "status": m.status,
                "minute": m.minute,
                "home": side(m.home_team, m.home_placeholder),
                "away": side(m.away_team, m.away_placeholder),
                "home_score": m.home_score,
                "away_score": m.away_score,
            }
            for m in Match.objects.filter(Q(home_team=team) | Q(away_team=team))
            .select_related("home_team", "away_team", "group")
            .order_by("kickoff")
        ]

        return Response({
            "team": TeamSerializer(team).data,
            "standing": standing,
            "players": players,
            "matches": matches,
        })


# ── Ficha de un jugador ─────────────────────────────────────────────────────
def _player_tournament_stats(player):
    """Aporte del jugador en el torneo, leído de los eventos de los partidos de
    su equipo: goles, asistencias y tarjetas (desambigua por apellido contra la
    plantilla, igual que la tabla de goleadores), más los partidos en que aportó.
    """
    from .management.commands.polla_sync import _match_player

    team = player.team
    empty = {"goals": 0, "assists": 0, "yellow": 0, "red": 0, "matches": []}
    if not team:
        return empty
    pool_map = {team.id: list(Player.objects.filter(team=team))}

    goals = assists = yellow = red = 0
    per_match = {}
    matches = list(
        Match.objects.filter(Q(home_team=team) | Q(away_team=team))
        .filter(status__in=[Match.Status.LIVE, Match.Status.FINISHED])
        .select_related("home_team", "away_team", "group")
        .order_by("kickoff")
    )
    def is_him(name):
        m = _match_player(name or "", team, pool_map)
        return m is not None and m.pk == player.pk

    for m in matches:
        my_side = "home" if m.home_team_id == team.id else "away"
        rival = m.away_team if my_side == "home" else m.home_team
        for ev in m.events or []:
            if ev.get("side") != my_side:
                continue
            etype = ev.get("type") or ""
            if etype == "goal":
                detail = (ev.get("detail") or "").lower()
                if "own" in detail or "missed" in detail:
                    continue
                slot = per_match.setdefault(
                    m.slug,
                    {"rival": _team_lite(rival), "group": m.group.letter if m.group else None,
                     "goals": 0, "assists": 0},
                )
                if is_him(ev.get("player")):
                    goals += 1
                    slot["goals"] += 1
                if ev.get("assist") and is_him(ev.get("assist")):
                    assists += 1
                    slot["assists"] += 1
            elif etype == "card":
                if is_him(ev.get("player")):
                    d = (ev.get("detail") or "").lower()
                    if "red" in d:
                        red += 1
                    elif "yellow" in d:
                        yellow += 1

    match_list = [
        {"slug": s, **v} for s, v in per_match.items() if v["goals"] or v["assists"]
    ]
    return {"goals": goals, "assists": assists, "yellow": yellow, "red": red,
            "matches": match_list}


class PlayerDetailView(APIView):
    """Ficha de un jugador: datos sembrados + perfil biográfico (API, cacheado
    una semana) + sus números en este torneo (desde los eventos)."""

    permission_classes = [AllowAny]

    def get(self, request, pk=None):
        player = (
            Player.objects.select_related("team", "team__group")
            .filter(pk=pk)
            .first()
        )
        if not player:
            return Response({"detail": "Jugador no encontrado."}, status=404)

        profile = None
        if player.api_player_id:
            ckey = f"polla:player_profile:{player.api_player_id}"
            profile = cache.get(ckey)
            if profile is None:
                try:
                    from apps.polla.providers import get_provider
                    provider = get_provider()
                    if provider.available:
                        profile = provider.fetch_player_profile(player.api_player_id) or {}
                        # El perfil no cambia durante el torneo: cache larga.
                        cache.set(ckey, profile, timeout=7 * 24 * 3600)
                except Exception:  # noqa: BLE001 - la ficha funciona sin el perfil
                    profile = None

        team = player.team
        team_lite = None
        if team:
            team_lite = {**_team_lite(team),
                         "group": team.group.letter if team.group else None,
                         "is_host": team.is_host}

        return Response({
            "id": player.id,
            "name": player.name,
            "number": player.number,
            "position": player.position or None,
            "age": player.age,
            "is_keeper": player.is_keeper,
            "photo": player.photo_url or (profile or {}).get("photo") or None,
            "team": team_lite,
            "profile": profile or None,
            "tournament": _player_tournament_stats(player),
        })


# ── Goleadores del torneo ──────────────────────────────────────────────────
class TopScorersView(APIView):
    """Tabla de goleadores (la refresca ``polla_sync`` desde la API)."""

    permission_classes = [AllowAny]

    def get(self, request):
        scorers = list(TopScorer.objects.select_related("team").order_by("rank"))
        rows = [
            {
                "rank": s.rank,
                "name": s.name,
                "goals": s.goals,
                "assists": s.assists,
                "appearances": s.appearances,
                "photo": s.photo_url or None,
                # id del jugador sembrado (si se pudo enlazar): el frontend lo
                # compara con my_pick.player_id de la mención "Goleador".
                "player_id": s.player_id,
                "team": _team_lite(s.team),
            }
            for s in scorers
        ]
        updated_at = max((s.updated_at for s in scorers), default=None)
        return Response({"scorers": rows, "updated_at": updated_at})


# ── Partidos + pronosticos ─────────────────────────────────────────────────
class MatchViewSet(viewsets.ReadOnlyModelViewSet):
    """Lista/detalle de partidos. Soporta filtros y el pronostico propio."""

    serializer_class = MatchSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
    pagination_class = None

    def get_serializer_class(self):
        # El detalle incluye eventos/estadisticas en vivo; la lista no.
        if self.action == "retrieve":
            return MatchDetailSerializer
        return MatchSerializer

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
            qs = qs.filter(
                Q(home_team__code=team.upper()) | Q(away_team__code=team.upper())
            )
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["predictions"] = _predictions_map(self.request.user)
        return ctx

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def pulse(self, request, slug=None):
        """Pulso de la comunidad: cómo pronosticó la polla este partido.

        Para no sesgar, solo es visible si el usuario ya guardó su pronóstico
        de este partido o si el partido ya está cerrado (arrancó). El agregado
        se cachea 60 s: el conteo no se repite por cada usuario que lo abre.
        """
        match = self.get_object()
        user = request.user
        has_mine = bool(
            user
            and user.is_authenticated
            and Prediction.objects.filter(user=user, match=match).exists()
        )
        if not match.is_locked and not has_mine:
            return Response({"visible": False, "reason": "predict_first"})

        cache_key = f"polla:pulse:{match.slug}"
        data = cache.get(cache_key)
        if data is None:
            preds = list(
                Prediction.objects.filter(match=match)
                .values_list("home_score", "away_score")
            )
            total = len(preds)
            counts = {"home": 0, "draw": 0, "away": 0}
            scores = {}
            for h, a in preds:
                counts[outcome_of(h, a)] += 1
                key = f"{h}-{a}"
                scores[key] = scores.get(key, 0) + 1
            top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            data = {
                "total": total,
                "outcomes": {
                    k: {"count": v, "pct": round(v * 100 / total) if total else 0}
                    for k, v in counts.items()
                },
                "top_scores": [
                    {"score": s, "count": c, "pct": round(c * 100 / total)}
                    for s, c in top
                ],
            }
            cache.set(cache_key, data, timeout=60)
        return Response({"visible": True, **data})

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
        # Si este usuario fue invitado, su primer pronostico califica al
        # invitador (latch idempotente: un UPDATE condicional no duplica).
        Referral.objects.filter(invitee=request.user, qualified=False).update(
            qualified=True, qualified_at=timezone.now()
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

        tournament = Tournament.get_active()
        locks_at = tournament.awards_lock_at if tournament else None
        picks_locked = bool(tournament and tournament.awards_locked)

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
        return Response({
            "awards": data,
            "options": options,
            "locks_at": locks_at,
            "picks_locked": picks_locked,
        })


class AwardPickView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _lock_error(award):
        """Mensaje de bloqueo si la mención ya no se puede tocar, o None."""
        if award.resolved:
            return "Esta mención ya está resuelta; no se puede cambiar."
        tournament = Tournament.get_active()
        if tournament and tournament.awards_locked:
            return "Las menciones ya están cerradas."
        return None

    def put(self, request, code=None):
        try:
            award = Award.objects.get(code=code)
        except Award.DoesNotExist:
            return Response({"detail": "Mención no encontrada."}, status=404)
        locked = self._lock_error(award)
        if locked:
            return Response({"detail": locked}, status=status.HTTP_400_BAD_REQUEST)

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

    def delete(self, request, code=None):
        """Quita la elección del usuario para esta mención (la deja vacía)."""
        try:
            award = Award.objects.get(code=code)
        except Award.DoesNotExist:
            return Response({"detail": "Mención no encontrada."}, status=404)
        locked = self._lock_error(award)
        if locked:
            return Response({"detail": locked}, status=status.HTTP_400_BAD_REQUEST)
        AwardPick.objects.filter(user=request.user, award=award).delete()
        data = AwardSerializer(award, context={"picks": {}}).data
        return Response(data, status=status.HTTP_200_OK)


# ── Bracket de eliminación (encadenado) ─────────────────────────────────────
def _team_lite(team):
    if not team:
        return None
    return {"code": team.code, "name": team.name, "iso2": team.iso2}


def _bracket_payload(user):
    """Arma la llave del usuario: rondas, competidores resueltos y picks.

    La llave se siembra de los clasificados REALES; se abre al terminar los
    grupos. Para usuarios anónimos se muestra la llave real sin picks.
    """
    open_ = bracket_logic.is_open()

    matches = list(
        Match.objects.exclude(stage=Match.Stage.GROUP)
        .select_related("home_team", "away_team")
        .order_by("number")
    )
    authed = bool(user and user.is_authenticated)
    resolved = bracket_logic.resolve_bracket(user, open_=open_)
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
        "open": open_,
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
        if not bracket_logic.is_open():
            return Response(
                {"detail": "La eliminación se abre cuando terminen los grupos del Mundial."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if match.is_locked:
            return Response(
                {"detail": "Este cruce ya está cerrado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolved = bracket_logic.resolve_bracket(request.user, open_=True)
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
def _ensure_customer_scores():
    """Crea las filas de ranking faltantes para los clientes (autocura).

    Garantiza que todo cliente que ya inició sesión aparezca en la tabla,
    aunque la señal de registro no hubiera corrido para él (cuentas previas)
    o aún no se haya ejecutado el recompute. Es idempotente y barato.
    """
    User = get_user_model()
    have = set(UserScore.objects.values_list("user_id", flat=True))
    missing = list(
        User.objects.filter(role=User.Role.CUSTOMER)
        .exclude(id__in=have)
        .values_list("id", flat=True)
    )
    if missing:
        UserScore.objects.bulk_create(
            [UserScore(user_id=uid) for uid in missing], ignore_conflicts=True
        )


def _ranked_scores():
    """Puntajes ordenados como en el ranking (no depende del campo cacheado)."""
    return UserScore.objects.select_related("user").order_by(
        "-points", "-exact_hits", "-correct_hits", "user_id"
    )


class RankingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        _ensure_customer_scores()
        me_id = request.user.id if request.user.is_authenticated else None
        ordered = list(_ranked_scores())
        rows, my_row = [], None
        for i, s in enumerate(ordered, start=1):
            row = {
                "pos": i,
                "user_id": s.user_id,
                "name": _display_name(s.user),
                "avatar": s.user.avatar_url or None,
                "points": s.points,
                "exact": s.exact_hits,
                "is_you": s.user_id == me_id,
            }
            if i <= 100:
                rows.append(row)
            if row["is_you"]:
                my_row = row
        # Si ya aparece dentro del top 100, no hace falta mandarlo aparte.
        if my_row and any(r["is_you"] for r in rows):
            my_row = None
        return Response({"ranking": rows, "me": my_row, "total": len(ordered)})


def _public_breakdown(score, pos):
    """Desglose de puntos para el perfil público (sin datos sensibles)."""
    return {
        "pos": pos,
        "points": score.points,
        "match_points": score.match_points,
        "mission_points": score.mission_points,
        "award_points": score.award_points,
        "bracket_points": score.bracket_points,
        "referral_points": score.referral_points,
        "exact_hits": score.exact_hits,
        "correct_hits": score.correct_hits,
        "predicted": score.predicted,
    }


class PlayerProfileView(APIView):
    """Perfil público de un participante del ranking.

    Cualquiera puede abrirlo al tocar a una persona en la tabla. Muestra de
    dónde salieron sus puntos: desglose por fuente, misiones completadas y el
    historial de sus pronósticos en partidos EN VIVO o YA TERMINADOS con lo que
    sumó en cada uno (en los de en vivo, el marcador parcial). Nunca revela
    pronósticos de partidos sin empezar (UPCOMING): sería un spoiler/ventaja. No
    expone email ni otros datos sensibles, a diferencia del detalle de admin.
    """

    permission_classes = [AllowAny]

    def get(self, request, pk=None):
        User = get_user_model()
        user = User.objects.filter(pk=pk).first()
        if not user:
            return Response({"detail": "Participante no encontrado."}, status=404)

        _ensure_customer_scores()
        ordered_ids = list(_ranked_scores().values_list("user_id", flat=True))
        pos = ordered_ids.index(user.id) + 1 if user.id in ordered_ids else 0
        score = UserScore.objects.filter(user=user).first()
        if not score:
            return Response({"detail": "Participante no encontrado."}, status=404)

        # Pronósticos de partidos ya iniciados (EN VIVO) o terminados, en el ORDEN
        # REAL en que se jugaron (por kickoff), del más reciente al más antiguo.
        # Se incluye el partido en vivo porque al sonar el pitazo el pronóstico
        # queda bloqueado (is_locked): mostrarlo ya no da ventaja y deja ver el
        # marcador parcial. Se excluyen los UPCOMING para no revelar pronósticos
        # de partidos sin empezar. NO se ordena por N.º de partido: el número no
        # es cronológico (p. ej. el #8 se jugó antes del #5), así que ordenar por
        # kickoff hace que las rachas de aciertos consecutivos coincidan con
        # partidos realmente seguidos.
        preds = (
            Prediction.objects.filter(
                user=user,
                match__status__in=[Match.Status.LIVE, Match.Status.FINISHED],
            )
            .select_related("match", "match__home_team", "match__away_team")
            .order_by("-match__kickoff", "-match__number")
        )
        predictions = [
            {
                "match_number": p.match.number,
                "slug": p.match.slug,
                "stage": p.match.stage,
                "stage_display": p.match.get_stage_display(),
                "home": _team_lite(p.match.home_team)
                or {"code": None, "name": p.match.home_placeholder or "—", "iso2": None},
                "away": _team_lite(p.match.away_team)
                or {"code": None, "name": p.match.away_placeholder or "—", "iso2": None},
                "kickoff": p.match.kickoff,
                "status": p.match.status,
                "minute": p.match.minute,
                "real_home": p.match.home_score,
                "real_away": p.match.away_score,
                "pred_home": p.home_score,
                "pred_away": p.away_score,
                "points_earned": p.points_earned,
                "is_exact": p.is_exact,
                "is_correct_outcome": p.is_correct_outcome,
            }
            for p in preds
        ]

        # Misiones: completadas primero, luego por orden de exhibición.
        missions = [
            {
                "code": um.mission.code,
                "title": um.mission.title,
                "desc": um.mission.desc,
                "progress": um.progress,
                "target": um.mission.target,
                "done": um.done,
                "bonus_points": um.mission.bonus_points,
                "completed_at": um.completed_at,
            }
            for um in (
                UserMission.objects.filter(user=user)
                # Las misiones de invitar (INVITE) están desactivadas en toda la
                # app: los referidos son una feature aparte con sus propios
                # puntos (categoría "Referidos"). Excluirlas evita la
                # incoherencia de ver "Trae la banda" en 0/1 con puntos de
                # referidos ya sumados. Mismo criterio que email_reminders.
                .exclude(mission__kind=Mission.Kind.INVITE)
                .select_related("mission")
                .order_by("-done", "mission__display_order")
            )
        ]

        # Referidos: el mismo progreso que la tarjeta "Invita a tu banda" de la
        # pestaña Misiones. Solo el avance público (cuántos amigos ya jugaron y
        # los puntos que sumó); nunca el código de invitación, que es privado.
        ref_qs = user.referrals_made.all()
        ref_qualified = sum(1 for r in ref_qs if r.qualified)
        referral = {
            "invited": ref_qs.count(),
            "qualified": ref_qualified,
            "cap": REFERRAL_CAP,
            "points_per": REFERRAL_POINTS_PER,
            "points": min(ref_qualified, REFERRAL_CAP) * REFERRAL_POINTS_PER,
        }

        return Response({
            "user": {
                "id": user.id,
                "name": _display_name(user),
                "avatar": user.avatar_url or None,
            },
            "score": _public_breakdown(score, pos),
            "missions": missions,
            "referral": referral,
            "predictions": predictions,
            "is_you": request.user.is_authenticated and request.user.id == user.id,
        })


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
    _ensure_customer_scores()
    ordered_ids = list(_ranked_scores().values_list("user_id", flat=True))
    total = len(ordered_ids)
    if not user or not user.is_authenticated:
        return {"points": 0, "position": 0, "total": total,
                "exact_hits": 0, "correct_hits": 0, "predicted": 0}
    s = UserScore.objects.filter(user=user).first()
    if not s:
        return {"points": 0, "position": 0, "total": total,
                "exact_hits": 0, "correct_hits": 0, "predicted": 0}
    position = ordered_ids.index(user.id) + 1 if user.id in ordered_ids else 0
    return {
        "points": s.points, "position": position, "total": total,
        "exact_hits": s.exact_hits, "correct_hits": s.correct_hits,
        "predicted": s.predicted,
    }


# ── Referidos (invitar amigos) ──────────────────────────────────────────────
def _get_or_make_referral_code(user):
    """Devuelve el código de invitación del usuario, generándolo si falta.

    Generación perezosa: cubre las filas creadas por ``bulk_create`` en
    ``_ensure_customer_scores`` (que no pasan por ``UserScore.save``).
    """
    score, _ = UserScore.objects.get_or_create(user=user)
    if not score.referral_code:
        score.save(update_fields=["referral_code"])  # save() genera y reintenta
    return score.referral_code


class ReferralView(APIView):
    """Estado de invitación del cliente: su código, amigos y puntos ganados."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        code = _get_or_make_referral_code(request.user)
        referrals = request.user.referrals_made.all()
        invited = referrals.count()
        qualified = sum(1 for r in referrals if r.qualified)
        points = min(qualified, REFERRAL_CAP) * REFERRAL_POINTS_PER
        return Response({
            "code": code,
            "invited": invited,
            "qualified": qualified,
            "points": points,
            "cap": REFERRAL_CAP,
            "points_per": REFERRAL_POINTS_PER,
        })


class ReferralClaimView(APIView):
    """Registra que el cliente actual fue invitado con un código.

    Solo es válido para cuentas nuevas que aún no han jugado; el punto se
    acredita después, cuando el invitado hace su primer pronóstico.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        # 1. Ya fue invitado antes: idempotente, sin error.
        existing = Referral.objects.filter(invitee=request.user).first()
        if existing:
            return Response(
                {"detail": "Ya estabas vinculado a una invitación.", "claimed": False},
                status=status.HTTP_200_OK,
            )

        # 2. Anti-abuso: no se puede reclamar si la cuenta ya jugó.
        if Prediction.objects.filter(user=request.user).exists():
            return Response(
                {"detail": "Tu cuenta ya tiene pronósticos; el código de invitación "
                           "solo aplica a cuentas nuevas."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = (request.data.get("code") or "").strip().upper()
        if not code:
            return Response(
                {"detail": "Falta el código de invitación."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Resolver al invitador por su código.
        inviter_score = UserScore.objects.filter(referral_code=code).first()
        if not inviter_score:
            return Response(
                {"detail": "Ese código de invitación no existe."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 4. No autoinvitarse.
        if inviter_score.user_id == request.user.id:
            return Response(
                {"detail": "No puedes usar tu propio código."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 5. Crear el vínculo (sin calificar todavía).
        try:
            Referral.objects.create(inviter=inviter_score.user, invitee=request.user)
        except IntegrityError:
            # Race: otra request creó el vínculo en paralelo. Idempotente.
            return Response(
                {"detail": "Ya estabas vinculado a una invitación.", "claimed": False},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"detail": "¡Invitación registrada! Haz tu primer pronóstico para que "
                       "tu amigo sume su punto.", "claimed": True},
            status=status.HTTP_201_CREATED,
        )


# ── Dashboard de administración (solo staff) ────────────────────────────────
def _score_breakdown(score, pos=None):
    """Desglose de puntos de un ``UserScore`` para vistas de admin."""
    return {
        "user_id": score.user_id,
        "name": _display_name(score.user),
        "email": score.user.email,
        "pos": pos if pos is not None else score.position,
        "points": score.points,
        "match_points": score.match_points,
        "mission_points": score.mission_points,
        "award_points": score.award_points,
        "bracket_points": score.bracket_points,
        "referral_points": score.referral_points,
        "exact_hits": score.exact_hits,
        "correct_hits": score.correct_hits,
        "predicted": score.predicted,
        "updated_at": score.updated_at,
    }


class PollaAdminViewSet(viewsets.ViewSet):
    """Tablero de solo lectura para que el staff monitoree la polla.

    Expone KPIs del torneo, la lista de jugadores con su desglose de puntos y
    el detalle de un jugador (pronósticos, misiones y referidos). Todo bajo
    ``IsAdminUser``: rol admin o superusuario, igual que el resto del panel.
    """

    permission_classes = [IsAdminUser]

    def list(self, request):
        """Jugadores con su desglose, ordenados como el ranking. ``?q=`` filtra."""
        _ensure_customer_scores()
        q = (request.query_params.get("q") or "").strip().lower()
        rows = []
        for i, score in enumerate(_ranked_scores(), start=1):
            row = _score_breakdown(score, pos=i)
            if q and q not in f"{row['name']} {row['email']}".lower():
                continue
            rows.append(row)
        return Response({"players": rows, "total": len(rows)})

    @action(detail=False, methods=["get"])
    def overview(self, request):
        """KPIs agregados del torneo y avance global de misiones."""
        _ensure_customer_scores()
        total_players = UserScore.objects.count()
        active_players = Prediction.objects.values("user_id").distinct().count()
        agg = UserScore.objects.aggregate(avg=Avg("points"), top=Max("points"))

        missions = [
            {
                "code": m.code,
                "title": m.title,
                "target": m.target,
                "bonus_points": m.bonus_points,
                "done": UserMission.objects.filter(mission=m, done=True).count(),
            }
            for m in Mission.objects.all().order_by("display_order")
        ]

        return Response({
            "players": {
                "total": total_players,
                "active": active_players,
                "inactive": max(total_players - active_players, 0),
            },
            "predictions": {
                "total": Prediction.objects.count(),
                "group": Prediction.objects.filter(match__stage=Match.Stage.GROUP).count(),
                "scored": Prediction.objects.filter(scored=True).count(),
                "exact": Prediction.objects.filter(is_exact=True, scored=True).count(),
                "correct": Prediction.objects.filter(is_correct_outcome=True, scored=True).count(),
            },
            "points": {
                "avg": round(agg["avg"] or 0, 1),
                "max": agg["top"] or 0,
            },
            "matches": {
                "total": Match.objects.count(),
                "finished": Match.objects.filter(status=Match.Status.FINISHED).count(),
            },
            "missions": missions,
        })

    def retrieve(self, request, pk=None):
        """Detalle de un jugador: score, pronósticos, misiones y referidos."""
        if not str(pk).isdigit():
            return Response({"detail": "Jugador no encontrado."}, status=404)
        User = get_user_model()
        user = User.objects.filter(pk=pk).first()
        if not user:
            return Response({"detail": "Jugador no encontrado."}, status=404)

        _ensure_customer_scores()
        ordered_ids = list(_ranked_scores().values_list("user_id", flat=True))
        pos = ordered_ids.index(user.id) + 1 if user.id in ordered_ids else 0
        score_obj = UserScore.objects.select_related("user").filter(user=user).first()

        # Orden REAL en que se jugaron (por kickoff), no por N.º de partido, que
        # no es cronológico: así las rachas de consecutivos son las reales.
        preds = (
            Prediction.objects.filter(user=user)
            .select_related("match", "match__home_team", "match__away_team")
            .order_by("match__kickoff", "match__number")
        )
        predictions = [
            {
                "match_number": p.match.number,
                "slug": p.match.slug,
                "stage": p.match.stage,
                "stage_display": p.match.get_stage_display(),
                "home": _team_lite(p.match.home_team)
                or {"code": None, "name": p.match.home_placeholder or "—", "iso2": None},
                "away": _team_lite(p.match.away_team)
                or {"code": None, "name": p.match.away_placeholder or "—", "iso2": None},
                "kickoff": p.match.kickoff,
                "status": p.match.status,
                "real_home": p.match.home_score,
                "real_away": p.match.away_score,
                "pred_home": p.home_score,
                "pred_away": p.away_score,
                "points_earned": p.points_earned,
                "is_exact": p.is_exact,
                "is_correct_outcome": p.is_correct_outcome,
                "scored": p.scored,
            }
            for p in preds
        ]

        missions = [
            {
                "code": um.mission.code,
                "title": um.mission.title,
                "progress": um.progress,
                "target": um.mission.target,
                "done": um.done,
                "bonus_points": um.mission.bonus_points,
                "completed_at": um.completed_at,
            }
            for um in (
                UserMission.objects.filter(user=user)
                .select_related("mission")
                .order_by("mission__display_order")
            )
        ]

        referrals = user.referrals_made.all()
        return Response({
            "user": {
                "id": user.id,
                "name": _display_name(user),
                "email": user.email,
                "role": user.role,
                "date_joined": user.date_joined,
                "last_login": user.last_login,
            },
            "score": _score_breakdown(score_obj, pos=pos) if score_obj else None,
            "predictions": predictions,
            "missions": missions,
            "referral": {
                "invited": referrals.count(),
                "qualified": sum(1 for r in referrals if r.qualified),
            },
        })
