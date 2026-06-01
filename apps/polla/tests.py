"""Tests de la Polla: motor de puntaje, misiones, posiciones y API."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient

from .bracket import (
    advance_real_bracket,
    is_open,
    real_standings,
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


# ── Bracket de equipos reales ───────────────────────────────────────────────
_LETTERS = "ABCDEFGHIJKL"
_RR = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]  # round-robin de 4 equipos


class BracketTests(TestCase):
    """Grupos REALES completos + un mini-bracket para probar la llave real."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="cli", email="cli@x.com")
        self.teams = {}
        self.group_matches = []

        number = 0
        for gi, L in enumerate(_LETTERS):
            g = Group.objects.create(letter=L, name=f"Grupo {L}", display_order=gi + 1)
            ts = []
            for k in range(4):
                code = f"{L}{k + 1}"
                t = Team.objects.create(code=code, name=code, iso2="xx", group=g)
                self.teams[code] = t
                ts.append(t)
            # Resultado REAL: el de menor índice gana 2-0 => grupo ordena 1>2>3>4.
            for i, j in _RR:
                number += 1
                m = Match.objects.create(
                    number=number, slug=f"m{number}",
                    kickoff=timezone.now() - timedelta(days=1),
                    home_team=ts[i], away_team=ts[j],
                    stage=Match.Stage.GROUP, group=g, round_label="Jornada",
                    status=Match.Status.FINISHED, home_score=2, away_score=0,
                )
                self.group_matches.append(m)
        assert number == 72

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

    def test_real_standings_order(self):
        by_group, thirds = real_standings()
        self.assertEqual([t.code for t in by_group["A"]], ["A1", "A2", "A3", "A4"])
        self.assertEqual(len(thirds), 12)  # un tercero por grupo

    def test_open_only_when_groups_finished(self):
        self.assertTrue(is_open())
        # Si un partido de grupos vuelve a "próximo", la llave se cierra.
        gm = self.group_matches[0]
        gm.status = Match.Status.UPCOMING
        gm.save(update_fields=["status"])
        self.assertFalse(is_open())

    def test_seeds_r32_from_real_results(self):
        resolved = resolve_bracket(self.user)
        self.assertEqual(resolved[73]["home"].code, "A1")  # 1A real
        self.assertEqual(resolved[73]["away"].code, "B2")  # 2B real
        self.assertEqual(resolved[74]["home"].code, "B1")  # 1B real
        self.assertEqual(resolved[74]["away"].code, "A2")  # 2A real
        self.assertIsNone(resolved[75]["home"])  # m73 sin jugar y sin pick

    def test_advance_persists_real_competitors(self):
        advance_real_bracket()
        self.m73.refresh_from_db()
        self.m74.refresh_from_db()
        self.assertEqual(self.m73.home_team.code, "A1")
        self.assertEqual(self.m73.away_team.code, "B2")
        self.assertEqual(self.m74.home_team.code, "B1")

    def test_pick_propagates_to_next_round(self):
        self.client.force_authenticate(self.user)
        r = self.client.put("/api/v1/polla/bracket/m73/pick/",
                            {"winner_code": "A1"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.client.put("/api/v1/polla/bracket/m74/pick/",
                        {"winner_code": "B1"}, format="json")
        resolved = resolve_bracket(self.user)
        # Cruces sin jugar: m75 muestra la proyección del usuario (sus picks).
        self.assertEqual(resolved[75]["home"].code, "A1")
        self.assertEqual(resolved[75]["away"].code, "B1")

    def test_reconnects_with_real_winner(self):
        # El usuario proyecta A1, pero realmente avanza B2: la llave se reconecta.
        self.client.force_authenticate(self.user)
        self.client.put("/api/v1/polla/bracket/m73/pick/", {"winner_code": "A1"}, format="json")
        self.client.put("/api/v1/polla/bracket/m74/pick/", {"winner_code": "B1"}, format="json")
        # m73 se juega y avanza B2 (no A1, el pick del usuario).
        self.m73.home_team = self.teams["A1"]
        self.m73.away_team = self.teams["B2"]
        self.m73.home_score, self.m73.away_score = 0, 1
        self.m73.status = Match.Status.FINISHED
        self.m73.save()
        advance_real_bracket()
        resolved = resolve_bracket(self.user)
        # m75 ahora muestra el ganador REAL de m73 (B2), no el pick fallido (A1).
        self.assertEqual(resolved[75]["home"].code, "B2")

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
        # Cambiar el ganador proyectado de m73: A1 ya no llega a m75 -> se invalida.
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

    def test_closed_until_groups_finish(self):
        # Con la fase de grupos incompleta, la llave está cerrada.
        gm = self.group_matches[0]
        gm.status = Match.Status.UPCOMING
        gm.save(update_fields=["status"])
        self.assertFalse(is_open())
        r = self.client.get("/api/v1/polla/bracket/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["open"])
        self.client.force_authenticate(self.user)
        r = self.client.put("/api/v1/polla/bracket/m73/pick/",
                            {"winner_code": "A1"}, format="json")
        self.assertEqual(r.status_code, 400)


class RankingInclusionTests(TestCase):
    """Todo cliente que ingresa aparece en el ranking, juegue o no."""

    def test_customer_gets_score_on_signup(self):
        # Al crear un cliente, la señal le crea su UserScore en 0.
        u = User.objects.create(
            username="nuevo", email="n@x.com", role=User.Role.CUSTOMER
        )
        score = UserScore.objects.filter(user=u).first()
        self.assertIsNotNone(score)
        self.assertEqual(score.points, 0)

    def test_recompute_includes_inactive_customer_with_position(self):
        u = User.objects.create(
            username="cust", email="cust@x.com", role=User.Role.CUSTOMER
        )
        recompute_all()
        score = UserScore.objects.get(user=u)
        self.assertEqual(score.points, 0)
        self.assertGreater(score.position, 0)

    def test_customer_appears_in_ranking_endpoint(self):
        User.objects.create(
            username="visible", email="v@x.com", role=User.Role.CUSTOMER
        )
        recompute_all()
        r = self.client.get("/api/v1/polla/ranking/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(row["name"] for row in r.data["ranking"]))
        self.assertGreaterEqual(r.data["total"], 1)

    def test_employee_without_activity_stays_out(self):
        # El personal interno (rol por defecto) no entra al ranking si no juega.
        emp = User.objects.create(username="emp", email="emp@x.com")
        recompute_all()
        self.assertFalse(UserScore.objects.filter(user=emp).exists())

    def test_ranking_self_heals_missing_customer(self):
        # Cuenta previa sin fila (señal no corrió, recompute tampoco):
        u = User.objects.create(
            username="legacy", email="legacy@x.com", role=User.Role.CUSTOMER
        )
        UserScore.objects.filter(user=u).delete()
        self.assertFalse(UserScore.objects.filter(user=u).exists())
        # Al consultar el ranking se autocura y aparece, sin recompute.
        r = self.client.get("/api/v1/polla/ranking/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(UserScore.objects.filter(user=u).exists())
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["ranking"][0]["pos"], 1)
