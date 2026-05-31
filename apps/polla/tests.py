"""Tests de la Polla: motor de puntaje, misiones, posiciones y API."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient

from .bracket import (
    is_unlocked,
    predicted_standings,
    resolve_bracket,
)
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
    UserScore,
)
from .scoring import (
    group_standings,
    recompute_all,
    score_prediction,
)

User = get_user_model()


class ScoringPureTests(TestCase):
    def test_exact_score(self):
        self.assertEqual(score_prediction(2, 1, 2, 1), (3, True, True))

    def test_correct_outcome_not_exact(self):
        # Predijo victoria local 3-0, quedó 2-1 (también victoria local)
        self.assertEqual(score_prediction(3, 0, 2, 1), (1, False, True))

    def test_correct_draw(self):
        self.assertEqual(score_prediction(0, 0, 1, 1), (1, False, True))

    def test_wrong_outcome(self):
        self.assertEqual(score_prediction(2, 0, 0, 1), (0, False, False))

    def test_no_result_yet(self):
        self.assertEqual(score_prediction(1, 0, None, None), (0, False, False))


def _make_team(code, group=None):
    return Team.objects.create(code=code, name=code, iso2=code[:2].lower(), group=group)


def _make_match(number, home, away, **kwargs):
    return Match.objects.create(
        number=number, slug=f"m{number}",
        kickoff=timezone.now() + timedelta(days=1),
        home_team=home, away_team=away, **kwargs,
    )


class StandingsTests(TestCase):
    def test_standings_order(self):
        g = Group.objects.create(letter="A", name="Grupo A", display_order=1)
        a, b, c, d = (_make_team(x, g) for x in ["AAA", "BBB", "CCC", "DDD"])
        # A vence a B 2-0; C empata D 1-1
        _make_match(1, a, b, group=g, status=Match.Status.FINISHED, home_score=2, away_score=0)
        _make_match(2, c, d, group=g, status=Match.Status.FINISHED, home_score=1, away_score=1)
        rows = group_standings(g)
        self.assertEqual(rows[0]["team"].code, "AAA")
        self.assertEqual(rows[0]["pts"], 3)
        self.assertEqual(rows[0]["dg"], 2)
        # C y D con 1 pt cada uno
        pts = {r["team"].code: r["pts"] for r in rows}
        self.assertEqual(pts["CCC"], 1)
        self.assertEqual(pts["DDD"], 1)
        self.assertEqual(pts["BBB"], 0)


class RecomputeTests(TestCase):
    def setUp(self):
        self.g = Group.objects.create(letter="A", name="Grupo A", display_order=1)
        self.home = _make_team("HOM", self.g)
        self.away = _make_team("AWY", self.g)
        self.match = _make_match(
            1, self.home, self.away,
            status=Match.Status.FINISHED, home_score=2, away_score=1,
            stage=Match.Stage.GROUP,
        )
        self.user = User.objects.create(username="u1", email="u1@x.com")
        Mission.objects.create(
            code="first", title="Primer", desc="d", reward="+10 pts",
            kind=Mission.Kind.FIRST_PREDICTION, target=1, bonus_points=10, display_order=1,
        )

    def test_exact_prediction_scores_three_and_ranks(self):
        Prediction.objects.create(
            user=self.user, match=self.match, home_score=2, away_score=1
        )
        recompute_all()
        score = UserScore.objects.get(user=self.user)
        # 3 (exacto) + 10 (misión primer pronóstico) = 13
        self.assertEqual(score.match_points, 3)
        self.assertEqual(score.mission_points, 10)
        self.assertEqual(score.points, 13)
        self.assertEqual(score.exact_hits, 1)
        self.assertEqual(score.position, 1)

    def test_wrong_prediction_scores_zero_match_points(self):
        Prediction.objects.create(
            user=self.user, match=self.match, home_score=0, away_score=3
        )
        recompute_all()
        score = UserScore.objects.get(user=self.user)
        self.assertEqual(score.match_points, 0)
        # Aún suma la misión de primer pronóstico
        self.assertEqual(score.mission_points, 10)


class PredictionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.g = Group.objects.create(letter="A", name="Grupo A", display_order=1)
        self.home = _make_team("HOM", self.g)
        self.away = _make_team("AWY", self.g)
        # Partido futuro (no bloqueado)
        self.match = Match.objects.create(
            number=1, slug="m1", kickoff=timezone.now() + timedelta(days=2),
            home_team=self.home, away_team=self.away,
        )
        self.user = User.objects.create(username="cliente", email="c@x.com")

    def test_requires_auth(self):
        resp = self.client.put(f"/api/v1/polla/matches/m1/predict/",
                               {"home_score": 1, "away_score": 0}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_upsert_prediction(self):
        self.client.force_authenticate(self.user)
        resp = self.client.put(f"/api/v1/polla/matches/m1/predict/",
                               {"home_score": 1, "away_score": 0}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            Prediction.objects.filter(user=self.user, match=self.match,
                                      home_score=1, away_score=0).exists()
        )
        # Idempotente: segundo PUT actualiza, no duplica
        self.client.put(f"/api/v1/polla/matches/m1/predict/",
                        {"home_score": 3, "away_score": 2}, format="json")
        self.assertEqual(Prediction.objects.filter(user=self.user, match=self.match).count(), 1)

    def test_locked_match_rejected(self):
        self.client.force_authenticate(self.user)
        self.match.kickoff = timezone.now() - timedelta(hours=1)
        self.match.save(update_fields=["kickoff"])
        resp = self.client.put(f"/api/v1/polla/matches/m1/predict/",
                               {"home_score": 1, "away_score": 0}, format="json")
        self.assertEqual(resp.status_code, 400)


class MatchListAPITests(TestCase):
    def test_list_includes_my_prediction(self):
        client = APIClient()
        g = Group.objects.create(letter="A", name="Grupo A", display_order=1)
        home, away = _make_team("HOM", g), _make_team("AWY", g)
        match = _make_match(1, home, away)
        user = User.objects.create(username="cliente", email="c@x.com")
        Prediction.objects.create(user=user, match=match, home_score=2, away_score=2)
        client.force_authenticate(user)
        resp = client.get("/api/v1/polla/matches/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data[0]
        self.assertEqual(row["my_prediction"]["home_score"], 2)
        self.assertEqual(row["home"]["code"], "HOM")


# ── Bracket encadenado ──────────────────────────────────────────────────────
_LETTERS = "ABCDEFGHIJKL"
_RR = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]  # round-robin de 4 equipos


class BracketTests(TestCase):
    """Fase de grupos completa (72) + un mini-bracket para probar el encadenado."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="cli", email="cli@x.com")
        self.locked = User.objects.create(username="lock", email="lock@x.com")
        self.teams = {}

        number = 0
        for gi, L in enumerate(_LETTERS):
            g = Group.objects.create(letter=L, name=f"Grupo {L}", display_order=gi + 1)
            ts = []
            for k in range(4):
                code = f"{L}{k + 1}"
                t = Team.objects.create(code=code, name=code, iso2="xx", group=g)
                self.teams[code] = t
                ts.append(t)
            for i, j in _RR:  # el de menor índice juega de local
                number += 1
                Match.objects.create(
                    number=number, slug=f"m{number}",
                    kickoff=timezone.now() + timedelta(days=5),
                    home_team=ts[i], away_team=ts[j],
                    stage=Match.Stage.GROUP, group=g, round_label="Jornada",
                )
        assert number == 72

        # El usuario pronostica TODO: el local (menor índice) gana 2-0 siempre,
        # con lo que cada grupo queda ordenado 1>2>3>4.
        for m in Match.objects.filter(stage=Match.Stage.GROUP):
            Prediction.objects.create(user=self.user, match=m, home_score=2, away_score=0)

        # Mini-bracket: dos dieciseisavos (slots simples) y un octavo encadenado.
        self.m73 = Match.objects.create(
            number=73, slug="m73", stage=Match.Stage.R32, round_label="Dieciseisavos",
            kickoff=timezone.now() + timedelta(days=20),
            home_placeholder="1A", away_placeholder="2B",
        )
        self.m74 = Match.objects.create(
            number=74, slug="m74", stage=Match.Stage.R32, round_label="Dieciseisavos",
            kickoff=timezone.now() + timedelta(days=20),
            home_placeholder="1B", away_placeholder="2A",
        )
        self.m75 = Match.objects.create(
            number=75, slug="m75", stage=Match.Stage.R16, round_label="Octavos",
            kickoff=timezone.now() + timedelta(days=22),
            home_placeholder="Ganador 73", away_placeholder="Ganador 74",
        )

    def test_predicted_standings_order(self):
        by_group, thirds = predicted_standings(self.user)
        self.assertEqual([t.code for t in by_group["A"]], ["A1", "A2", "A3", "A4"])
        self.assertEqual(len(thirds), 12)  # un tercero por grupo

    def test_resolution_seeds_r32_from_predictions(self):
        self.assertTrue(is_unlocked(self.user))
        resolved = resolve_bracket(self.user)
        self.assertEqual(resolved[73]["home"].code, "A1")  # 1A
        self.assertEqual(resolved[73]["away"].code, "B2")  # 2B
        self.assertEqual(resolved[74]["home"].code, "B1")  # 1B
        self.assertEqual(resolved[74]["away"].code, "A2")  # 2A
        self.assertIsNone(resolved[75]["home"])  # aún sin pick

    def test_pick_propagates_to_next_round(self):
        self.client.force_authenticate(self.user)
        r = self.client.put("/api/v1/polla/bracket/m73/pick/",
                            {"winner_code": "A1"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.client.put("/api/v1/polla/bracket/m74/pick/",
                        {"winner_code": "B1"}, format="json")
        resolved = resolve_bracket(self.user)
        self.assertEqual(resolved[75]["home"].code, "A1")
        self.assertEqual(resolved[75]["away"].code, "B1")

    def test_invalid_pick_rejected(self):
        self.client.force_authenticate(self.user)
        r = self.client.put("/api/v1/polla/bracket/m73/pick/",
                            {"winner_code": "C1"}, format="json")  # C1 no está en el cruce
        self.assertEqual(r.status_code, 400)

    def test_cascade_invalidates_downstream_pick(self):
        self.client.force_authenticate(self.user)
        self.client.put("/api/v1/polla/bracket/m73/pick/", {"winner_code": "A1"}, format="json")
        self.client.put("/api/v1/polla/bracket/m74/pick/", {"winner_code": "B1"}, format="json")
        self.client.put("/api/v1/polla/bracket/m75/pick/", {"winner_code": "A1"}, format="json")
        self.assertTrue(BracketPick.objects.filter(user=self.user, match=self.m75).exists())
        # Cambiar el ganador de m73: A1 ya no llega a m75 -> su pick se invalida.
        self.client.put("/api/v1/polla/bracket/m73/pick/", {"winner_code": "B2"}, format="json")
        self.assertFalse(BracketPick.objects.filter(user=self.user, match=self.m75).exists())

    def test_bracket_scoring_and_total(self):
        self.client.force_authenticate(self.user)
        self.client.put("/api/v1/polla/bracket/m73/pick/", {"winner_code": "A1"}, format="json")
        # Resultado real: A1 gana 1-0.
        self.m73.home_team = self.teams["A1"]
        self.m73.away_team = self.teams["B2"]
        self.m73.home_score, self.m73.away_score = 1, 0
        self.m73.status = Match.Status.FINISHED
        self.m73.save()
        recompute_all()
        bp = BracketPick.objects.get(user=self.user, match=self.m73)
        self.assertEqual(bp.points_earned, 2)  # dieciseisavos = 2
        self.assertTrue(bp.scored)
        score = UserScore.objects.get(user=self.user)
        self.assertEqual(score.bracket_points, 2)
        self.assertEqual(score.points, 2)  # grupos aún sin jugar -> solo avance

    def test_bracket_scoring_penalty_winner(self):
        # Cruce que termina empatado y se define por penales: winner_team manda.
        self.client.force_authenticate(self.user)
        self.client.put("/api/v1/polla/bracket/m73/pick/", {"winner_code": "B2"}, format="json")
        self.m73.home_team = self.teams["A1"]
        self.m73.away_team = self.teams["B2"]
        self.m73.home_score, self.m73.away_score = 1, 1  # empate en 90/120'
        self.m73.winner_team = self.teams["B2"]  # B2 avanza por penales
        self.m73.status = Match.Status.FINISHED
        self.m73.save()
        recompute_all()
        bp = BracketPick.objects.get(user=self.user, match=self.m73)
        self.assertEqual(bp.points_earned, 2)
        self.assertTrue(bp.scored)

    def test_draw_without_winner_stays_unscored(self):
        # Empate finalizado SIN winner_team: no se puede puntuar avance todavía.
        self.client.force_authenticate(self.user)
        self.client.put("/api/v1/polla/bracket/m73/pick/", {"winner_code": "A1"}, format="json")
        self.m73.home_team = self.teams["A1"]
        self.m73.away_team = self.teams["B2"]
        self.m73.home_score, self.m73.away_score = 0, 0
        self.m73.status = Match.Status.FINISHED
        self.m73.save()
        recompute_all()
        bp = BracketPick.objects.get(user=self.user, match=self.m73)
        self.assertEqual(bp.points_earned, 0)
        self.assertFalse(bp.scored)

    def test_gate_locked_until_72(self):
        self.client.force_authenticate(self.locked)
        self.assertFalse(is_unlocked(self.locked))
        r = self.client.get("/api/v1/polla/bracket/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["unlocked"])
        r = self.client.put("/api/v1/polla/bracket/m73/pick/",
                            {"winner_code": "A1"}, format="json")
        self.assertEqual(r.status_code, 400)
