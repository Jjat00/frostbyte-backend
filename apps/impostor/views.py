from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.utils import timezone
from django.db.models import Count, Avg, Q

from apps.accounts.permissions import IsEmployeeOrAdmin
from .services.word_generator import generate_words
from .models import (
    WordCategory,
    Word,
    ImpostorGameSession,
    ImpostorPlayer,
    ImpostorRound,
    ImpostorRoundPlayer,
)
from .serializers import (
    WordCategoryListSerializer,
    WordCategoryDetailSerializer,
    ImpostorGameSessionListSerializer,
    ImpostorGameSessionDetailSerializer,
    ImpostorPlayerSerializer,
    CreateSessionSerializer,
    SaveRoundSerializer,
    FinishSessionSerializer,
)


class WordCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Categorías y palabras para el juego del impostor"""

    queryset = WordCategory.objects.filter(is_active=True)
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return WordCategoryDetailSerializer
        return WordCategoryListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            qs = qs.annotate(word_count=Count("words", filter=Q(words__is_active=True)))
        return qs

    lookup_field = "slug"

    @action(detail=True, methods=["get"])
    def words(self, request, slug=None):
        """Obtener palabras de una categoría"""
        category = self.get_object()
        words = category.words.filter(is_active=True)
        from .serializers import WordSerializer
        serializer = WordSerializer(words, many=True)
        return Response({
            "category": category.name,
            "slug": category.slug,
            "words": serializer.data,
        })


class ImpostorSessionViewSet(viewsets.ModelViewSet):
    """Sesiones de juego del impostor para trazabilidad y stats"""

    queryset = ImpostorGameSession.objects.prefetch_related(
        "players", "rounds", "rounds__player_results",
        "rounds__player_results__player", "rounds__player_results__voted_for"
    )
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "list":
            return ImpostorGameSessionListSerializer
        return ImpostorGameSessionDetailSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve", "stats_dashboard"]:
            return [IsEmployeeOrAdmin()]
        return [AllowAny()]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            qs = qs.annotate(player_count=Count("players"))
        return qs

    @action(detail=False, methods=["post"], url_path="create-session")
    def create_session(self, request):
        """Crear una nueva sesión al iniciar partida con jugadores"""
        serializer = CreateSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        session = ImpostorGameSession.objects.create(
            total_rounds=data["total_rounds"],
            impostor_count=data["impostor_count"],
            hint_enabled=data["hint_enabled"],
            trap_word_enabled=data["trap_word_enabled"],
            spy_enabled=data["spy_enabled"],
            turn_timer_seconds=data.get("turn_timer_seconds"),
            ai_words_used=data["ai_words_used"],
        )

        players_created = []
        for player_data in data["players"]:
            player = ImpostorPlayer.objects.create(
                session=session,
                name=player_data["name"],
                color=player_data["color"],
            )
            players_created.append(player)

        response_serializer = ImpostorGameSessionDetailSerializer(session)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="save-round")
    def save_round(self, request, pk=None):
        """Guardar resultado de una ronda completa"""
        session = self.get_object()
        serializer = SaveRoundSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        round_obj = ImpostorRound.objects.create(
            session=session,
            round_number=data["round_number"],
            category_slug=data["category_slug"],
            word=data["word"],
            trap_word=data["trap_word"],
            impostor_caught=data["impostor_caught"],
        )

        for result in data["player_results"]:
            player_id = result["player_id"]
            try:
                player = ImpostorPlayer.objects.get(id=player_id, session=session)
            except ImpostorPlayer.DoesNotExist:
                continue

            voted_for = None
            if result.get("voted_for_id"):
                try:
                    voted_for = ImpostorPlayer.objects.get(
                        id=result["voted_for_id"], session=session
                    )
                except ImpostorPlayer.DoesNotExist:
                    pass

            points = result.get("points_earned", 0)

            ImpostorRoundPlayer.objects.create(
                round=round_obj,
                player=player,
                role=result["role"],
                voted_for=voted_for,
                votes_received=result.get("votes_received", 0),
                points_earned=points,
                guessed_word=result.get("guessed_word", ""),
            )

            player.total_score += points
            player.save(update_fields=["total_score"])

        return Response({"message": "Ronda guardada"}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="finish")
    def finish_session(self, request, pk=None):
        """Marcar sesión como completada"""
        session = self.get_object()
        serializer = FinishSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        session.status = ImpostorGameSession.Status.COMPLETED
        session.finished_at = timezone.now()
        session.save(update_fields=["status", "finished_at"])

        if serializer.validated_data.get("player_scores"):
            for score_data in serializer.validated_data["player_scores"]:
                try:
                    player = ImpostorPlayer.objects.get(
                        id=score_data["player_id"], session=session
                    )
                    player.total_score = score_data.get("total_score", player.total_score)
                    player.save(update_fields=["total_score"])
                except (ImpostorPlayer.DoesNotExist, KeyError):
                    continue

        response_serializer = ImpostorGameSessionDetailSerializer(session)
        return Response(response_serializer.data)

    @action(detail=False, methods=["post"], url_path="generate-words")
    def generate_ai_words(self, request):
        """Generar palabras con IA para una categoria"""
        category_name = request.data.get("category_name", "")
        count = min(int(request.data.get("count", 5)), 10)
        exclude_words = request.data.get("exclude_words", [])

        if not category_name:
            return Response(
                {"error": "category_name es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        words = generate_words(category_name, count, exclude_words)

        if words is None:
            return Response(
                {"error": "No se pudieron generar palabras"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({"words": words, "category": category_name})

    @action(detail=False, methods=["get"], url_path="stats-dashboard")
    def stats_dashboard(self, request):
        """Estadísticas generales del juego impostor (admin)"""
        total_sessions = ImpostorGameSession.objects.count()
        completed_sessions = ImpostorGameSession.objects.filter(
            status=ImpostorGameSession.Status.COMPLETED
        )

        total_completed = completed_sessions.count()
        total_players = ImpostorPlayer.objects.count()

        avg_players = completed_sessions.annotate(
            pc=Count("players")
        ).aggregate(avg=Avg("pc"))["avg"]

        from django.db.models import F, ExpressionWrapper, DurationField
        avg_duration = None
        if total_completed > 0:
            sessions_with_duration = completed_sessions.exclude(
                finished_at=None
            ).annotate(
                dur=ExpressionWrapper(
                    F("finished_at") - F("started_at"),
                    output_field=DurationField()
                )
            )
            durations = [
                s.dur.total_seconds()
                for s in sessions_with_duration
                if s.dur is not None
            ]
            avg_duration = sum(durations) / len(durations) if durations else None

        total_rounds = ImpostorRound.objects.count()
        impostor_caught = ImpostorRound.objects.filter(impostor_caught=True).count()
        impostor_escaped = total_rounds - impostor_caught

        category_stats = (
            ImpostorRound.objects.values("category_slug")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        ai_usage = completed_sessions.filter(ai_words_used=True).count()

        return Response({
            "total_sessions": total_sessions,
            "total_completed": total_completed,
            "total_players": total_players,
            "avg_players_per_session": round(avg_players, 1) if avg_players else 0,
            "avg_duration_seconds": round(avg_duration) if avg_duration else 0,
            "total_rounds": total_rounds,
            "impostor_caught": impostor_caught,
            "impostor_escaped": impostor_escaped,
            "catch_rate_percent": round(
                (impostor_caught / total_rounds * 100) if total_rounds > 0 else 0, 1
            ),
            "top_categories": list(category_stats),
            "ai_words_usage": ai_usage,
            "ai_words_usage_percent": round(
                (ai_usage / total_completed * 100) if total_completed > 0 else 0, 1
            ),
        })
