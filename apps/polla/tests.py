"""Tests de la Polla: motor de puntaje, misiones, posiciones y API."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient

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
