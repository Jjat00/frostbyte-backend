"""Tests de la Polla: motor de puntaje, misiones, posiciones y API."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient

from .bracket import (
    advance_real_bracket,
    all_groups_complete,
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
    Referral,
    Team,
    Tournament,
    UserMission,
    UserScore,
)
from .scoring import (
    _streak_stats,
    group_standings,
    recompute_all,
    recompute_referrals,
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

    def test_open_progressively_as_groups_finish(self):
        # Con todos los grupos cerrados, la llave está abierta y completa.
        self.assertTrue(is_open())
        self.assertTrue(all_groups_complete())
        # Reabrir UN partido del grupo A no cierra la llave: los otros 11 grupos
        # siguen completos y ya tienen clasificados que mostrar...
        gm = self.group_matches[0]  # primer partido del grupo A
        gm.status = Match.Status.UPCOMING
        gm.save(update_fields=["status"])
        self.assertTrue(is_open())              # hay clasificados firmes
        self.assertFalse(all_groups_complete())  # ...pero aún no terminan TODOS

    def test_seeds_progressively_before_all_groups_finish(self):
        # Reabrimos el grupo B por completo: 1A (grupo A, cerrado) ya es firme;
        # 2B (grupo B, abierto) y los terceros aún no.
        for gm in Match.objects.filter(stage=Match.Stage.GROUP, group__letter="B"):
            gm.status = Match.Status.UPCOMING
            gm.save(update_fields=["status"])
        self.assertTrue(is_open())
        self.assertFalse(all_groups_complete())
        resolved = resolve_bracket(self.user)
        self.assertEqual(resolved[73]["home"].code, "A1")  # grupo A cerrado
        self.assertIsNone(resolved[73]["away"])            # 2B: grupo B abierto
        self.assertEqual(resolved[74]["away"].code, "A2")  # 2A firme
        self.assertIsNone(resolved[74]["home"])            # 1B: grupo B abierto
        # advance no debe persistir el lado todavía indefinido.
        advance_real_bracket()
        self.m73.refresh_from_db()
        self.assertEqual(self.m73.home_team.code, "A1")
        self.assertIsNone(self.m73.away_team_id)

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

    def test_ignores_stale_team_on_unplayed_derived_slot(self):
        # Residuo de simulación: un equipo quedó persistido en un octavo cuyo
        # alimentador (m73) aún no se jugó. Es imposible conocerlo: se ignora.
        self.m75.home_team = self.teams["A1"]
        self.m75.save(update_fields=["home_team"])
        resolved = resolve_bracket(self.user)
        self.assertIsNone(resolved[75]["home"])  # m73 sin jugar -> se ignora
        # Cuando m73 se juega y A1 avanza, el octavo sí toma el ganador real.
        self.m73.home_team = self.teams["A1"]
        self.m73.away_team = self.teams["B2"]
        self.m73.home_score, self.m73.away_score = 2, 0
        self.m73.status = Match.Status.FINISHED
        self.m73.save()
        resolved = resolve_bracket(self.user)
        self.assertEqual(resolved[75]["home"].code, "A1")

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

    def test_elimination_scored_by_prediction_with_stage_points(self):
        # La eliminatoria se puntua por MARCADOR (Prediction) con puntos por
        # fase, NO por "elegir quien avanza" (BracketPick quedo deprecado).
        advance_real_bracket()  # siembra m73 = A1 vs B2 (grupos terminados)
        self.m73.refresh_from_db()
        self.client.force_authenticate(self.user)
        # Marcador exacto en dieciseisavos (R32): vale 4.
        r = self.client.put("/api/v1/polla/matches/m73/predict/",
                            {"home_score": 2, "away_score": 0}, format="json")
        self.assertEqual(r.status_code, 200)
        # Regresion: un pick legacy con puntos NO debe sumar al total.
        BracketPick.objects.create(
            user=self.user, match=self.m73, winner_team=self.teams["A1"],
            points_earned=99, scored=True,
        )
        self.m73.home_score, self.m73.away_score = 2, 0
        self.m73.status = Match.Status.FINISHED
        self.m73.save()
        recompute_all()
        pred = Prediction.objects.get(user=self.user, match=self.m73)
        self.assertEqual(pred.points_earned, 4)  # R32 exacto
        self.assertTrue(pred.is_exact)
        score = UserScore.objects.get(user=self.user)
        self.assertEqual(score.match_points, 4)
        self.assertEqual(score.bracket_points, 0)  # el pick de 99 no cuenta
        self.assertEqual(score.points, 4)

    def test_elimination_outcome_points_ignore_penalties(self):
        # Empate definido por penales: el pronostico se puntua por el MARCADOR
        # (resultado de la fase); el ganador por penales solo arma la llave.
        advance_real_bracket()
        self.m73.refresh_from_db()
        self.client.force_authenticate(self.user)
        # Predice empate, no exacto: resultado correcto en R32 vale 2.
        self.client.put("/api/v1/polla/matches/m73/predict/",
                        {"home_score": 0, "away_score": 0}, format="json")
        self.m73.home_score, self.m73.away_score = 1, 1
        self.m73.winner_team = self.teams["B2"]  # B2 avanza por penales
        self.m73.status = Match.Status.FINISHED
        self.m73.save()
        recompute_all()
        pred = Prediction.objects.get(user=self.user, match=self.m73)
        self.assertEqual(pred.points_earned, 2)  # R32 resultado (empate)
        self.assertFalse(pred.is_exact)
        score = UserScore.objects.get(user=self.user)
        self.assertEqual(score.bracket_points, 0)

    def test_closed_when_no_group_complete(self):
        # Si NINGÚN grupo está cerrado, no hay nada firme: la llave está cerrada.
        # Reabrimos un partido de cada grupo (los _RR son 6 partidos por grupo).
        for i in range(0, len(self.group_matches), len(_RR)):
            gm = self.group_matches[i]
            gm.status = Match.Status.UPCOMING
            gm.save(update_fields=["status"])
        self.assertFalse(is_open())
        r = self.client.get("/api/v1/polla/bracket/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["open"])
        self.assertFalse(r.data["groups_finished"])
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


# ── Referidos (invitar amigos) ──────────────────────────────────────────────
def _customer(username):
    """Crea un cliente (rol customer => el signal le crea UserScore con código)."""
    return User.objects.create(
        username=username, email=f"{username}@x.com", role=User.Role.CUSTOMER
    )


class ParticipantAwardsVisibilityTests(TestCase):
    """Las menciones ajenas solo se publican cuando ya no se pueden cambiar."""

    def setUp(self):
        self.client = APIClient()
        self.user = _customer("diego")
        self.team = _make_team("ARG")
        self.award = Award.objects.create(
            code="champion", title="Campeón", points=25,
            award_type=Award.AwardType.TEAM, display_order=1,
        )
        AwardPick.objects.create(user=self.user, award=self.award, team=self.team)

    def _profile(self):
        r = self.client.get(f"/api/v1/polla/participants/{self.user.id}/")
        self.assertEqual(r.status_code, 200)
        return r.data

    def test_open_awards_stay_hidden(self):
        # Menciones aún abiertas: mostrarlas dejaría copiarse del rival.
        Tournament.objects.create(
            kickoff=timezone.now(),
            awards_lock_at=timezone.now() + timedelta(days=1),
        )
        data = self._profile()
        self.assertFalse(data["awards_locked"])
        self.assertEqual(data["awards"], [])

    def test_locked_awards_go_public(self):
        Tournament.objects.create(
            kickoff=timezone.now(),
            awards_lock_at=timezone.now() - timedelta(hours=1),
        )
        data = self._profile()
        self.assertTrue(data["awards_locked"])
        [a] = data["awards"]
        self.assertEqual(a["code"], "champion")
        self.assertEqual(a["pick"]["team_code"], "ARG")
        self.assertFalse(a["resolved"])
        self.assertIsNone(a["result"])
        self.assertFalse(a["won"])

    def test_resolved_award_shows_result_and_points(self):
        # Sin cierre global: la mención individualmente resuelta sí se publica.
        self.award.resolved = True
        self.award.resolved_team = self.team
        self.award.save()
        recompute_all()
        data = self._profile()
        [a] = data["awards"]
        self.assertTrue(a["won"])
        self.assertEqual(a["points_earned"], 25)
        self.assertEqual(a["result"]["team_code"], "ARG")


class ReferralTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.g = Group.objects.create(letter="A", name="Grupo A", display_order=1)
        self.home = _make_team("HOM", self.g)
        self.away = _make_team("AWY", self.g)
        # Partido futuro (no bloqueado) para poder pronosticar.
        self.match = Match.objects.create(
            number=1, slug="m1", kickoff=timezone.now() + timedelta(days=2),
            home_team=self.home, away_team=self.away,
        )
        self.inviter = _customer("inviter")
        self.invitee = _customer("invitee")

    def _code(self, user):
        return UserScore.objects.get(user=user).referral_code

    def _predict(self, user, home=1, away=0):
        self.client.force_authenticate(user)
        resp = self.client.put(
            "/api/v1/polla/matches/m1/predict/",
            {"home_score": home, "away_score": away}, format="json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_signal_assigns_unique_referral_code(self):
        self.assertTrue(self._code(self.inviter))
        self.assertNotEqual(self._code(self.inviter), self._code(self.invitee))

    def test_claim_creates_pending_referral(self):
        self.client.force_authenticate(self.invitee)
        resp = self.client.post(
            "/api/v1/polla/referral/claim/",
            {"code": self._code(self.inviter)}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        ref = Referral.objects.get(invitee=self.invitee)
        self.assertEqual(ref.inviter, self.inviter)
        self.assertFalse(ref.qualified)

    def test_first_prediction_qualifies_and_awards_one_point(self):
        Referral.objects.create(inviter=self.inviter, invitee=self.invitee)
        self._predict(self.invitee)
        ref = Referral.objects.get(invitee=self.invitee)
        self.assertTrue(ref.qualified)
        self.assertIsNotNone(ref.qualified_at)
        # El predict ya disparó recompute_scores: el invitador suma 1 punto.
        score = UserScore.objects.get(user=self.inviter)
        self.assertEqual(score.referral_points, 1)
        self.assertEqual(score.points, 1)

    def test_no_point_until_invitee_plays(self):
        Referral.objects.create(inviter=self.inviter, invitee=self.invitee)
        recompute_all()
        score = UserScore.objects.get(user=self.inviter)
        self.assertEqual(score.referral_points, 0)

    def test_self_referral_rejected(self):
        self.client.force_authenticate(self.inviter)
        resp = self.client.post(
            "/api/v1/polla/referral/claim/",
            {"code": self._code(self.inviter)}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Referral.objects.filter(invitee=self.inviter).exists())

    def test_unknown_code_returns_404(self):
        self.client.force_authenticate(self.invitee)
        resp = self.client.post(
            "/api/v1/polla/referral/claim/", {"code": "ZZZZZZ"}, format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_claim_rejected_if_already_played(self):
        self._predict(self.invitee)  # juega ANTES de reclamar
        self.client.force_authenticate(self.invitee)
        resp = self.client.post(
            "/api/v1/polla/referral/claim/",
            {"code": self._code(self.inviter)}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Referral.objects.filter(invitee=self.invitee).exists())

    def test_double_claim_is_idempotent(self):
        self.client.force_authenticate(self.invitee)
        code = self._code(self.inviter)
        first = self.client.post(
            "/api/v1/polla/referral/claim/", {"code": code}, format="json")
        second = self.client.post(
            "/api/v1/polla/referral/claim/", {"code": code}, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Referral.objects.filter(invitee=self.invitee).count(), 1)

    def test_points_capped_at_five(self):
        # 6 amigos se unen y juegan: el invitador topa en 5 puntos.
        for i in range(6):
            friend = _customer(f"friend{i}")
            Referral.objects.create(inviter=self.inviter, invitee=friend)
            self._predict(friend)
        score = UserScore.objects.get(user=self.inviter)
        self.assertEqual(Referral.objects.filter(
            inviter=self.inviter, qualified=True).count(), 6)
        self.assertEqual(score.referral_points, 5)

    def test_qualified_is_one_way_latch(self):
        # Aunque el invitado perdiera sus pronósticos, no se degrada el punto.
        Referral.objects.create(inviter=self.inviter, invitee=self.invitee)
        self._predict(self.invitee)
        Prediction.objects.filter(user=self.invitee).delete()
        recompute_referrals()
        recompute_all()
        ref = Referral.objects.get(invitee=self.invitee)
        self.assertTrue(ref.qualified)
        score = UserScore.objects.get(user=self.inviter)
        self.assertEqual(score.referral_points, 1)

    def test_referral_endpoint_reports_state(self):
        Referral.objects.create(
            inviter=self.inviter, invitee=self.invitee, qualified=True)
        self.client.force_authenticate(self.inviter)
        resp = self.client.get("/api/v1/polla/referral/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["code"], self._code(self.inviter))
        self.assertEqual(resp.data["invited"], 1)
        self.assertEqual(resp.data["qualified"], 1)
        self.assertEqual(resp.data["points"], 1)
        self.assertEqual(resp.data["cap"], 5)


class TopScorersFromEventsTests(TestCase):
    """La tabla de goleadores se arma desde los eventos de gol de los partidos
    cuando el endpoint agregado de la API aún no consolida."""

    def setUp(self):
        from .management.commands.polla_sync import Command
        self.cmd = Command()
        self.mex = _make_team("MEX")
        self.rsa = _make_team("RSA")
        self.raul = Player.objects.create(name="Raúl Jiménez", team=self.mex)
        self.julian = Player.objects.create(name="Julián Quiñones", team=self.mex)

    def _goal(self, side, player, detail="Normal Goal"):
        return {"type": "goal", "side": side, "detail": detail, "player": player}

    def test_counts_goals_and_links_seeded_players(self):
        # Nombres abreviados como los entrega la API ("R. Jimenez").
        _make_match(
            1, self.mex, self.rsa,
            status=Match.Status.FINISHED, home_score=2, away_score=0,
            events=[self._goal("home", "J. Quinones"),
                    self._goal("home", "R. Jimenez", detail="Penalty")],
        )
        rows = {r.name: r for r in self.cmd._scorers_from_events()}
        self.assertIn("Raúl Jiménez", rows)
        self.assertIn("Julián Quiñones", rows)
        self.assertEqual(rows["Raúl Jiménez"].goals, 1)
        self.assertEqual(rows["Raúl Jiménez"].player_id, self.raul.id)
        self.assertEqual(rows["Julián Quiñones"].team.code, "MEX")

    def test_excludes_own_goals_and_missed_penalties(self):
        _make_match(
            2, self.mex, self.rsa,
            status=Match.Status.FINISHED, home_score=1, away_score=0,
            events=[self._goal("away", "S. Defensa", detail="Own Goal"),
                    self._goal("home", "R. Jimenez", detail="Missed Penalty")],
        )
        self.assertEqual(self.cmd._scorers_from_events(), [])

    def test_accumulates_goals_across_matches(self):
        _make_match(
            3, self.mex, self.rsa,
            status=Match.Status.FINISHED, home_score=1, away_score=0,
            events=[self._goal("home", "R. Jimenez")],
        )
        _make_match(
            4, self.rsa, self.mex,
            status=Match.Status.FINISHED, home_score=0, away_score=1,
            events=[self._goal("away", "R. Jimenez")],
        )
        raul = next(r for r in self.cmd._scorers_from_events()
                    if r.player_id == self.raul.id)
        self.assertEqual(raul.goals, 2)

    def test_counts_assists_but_excludes_assist_only_players(self):
        # Quiñones asiste el gol de Jiménez: Jiménez entra (1 gol, 0 asist),
        # Quiñones NO entra a la tabla de goleadores (0 goles, solo 1 asist).
        _make_match(
            7, self.mex, self.rsa,
            status=Match.Status.FINISHED, home_score=1, away_score=0,
            events=[{"type": "goal", "side": "home", "detail": "Normal Goal",
                     "player": "R. Jimenez", "assist": "J. Quinones"}],
        )
        rows = {r.name: r for r in self.cmd._scorers_from_events()}
        self.assertIn("Raúl Jiménez", rows)
        self.assertEqual(rows["Raúl Jiménez"].goals, 1)
        self.assertEqual(rows["Raúl Jiménez"].assists, 0)
        self.assertNotIn("Julián Quiñones", rows)  # solo asistió: fuera

    def test_ignores_non_goal_events_and_upcoming_matches(self):
        _make_match(
            5, self.mex, self.rsa,
            status=Match.Status.FINISHED, home_score=0, away_score=0,
            events=[{"type": "card", "side": "home", "detail": "Yellow Card",
                     "player": "R. Jimenez"}],
        )
        _make_match(
            6, self.mex, self.rsa,
            status=Match.Status.UPCOMING,
            events=[self._goal("home", "J. Quinones")],
        )
        self.assertEqual(self.cmd._scorers_from_events(), [])


class PlayerTournamentStatsTests(TestCase):
    """Aporte de un jugador en el torneo (goles/asistencias/tarjetas) leído de
    los eventos, desambiguando entre jugadores del mismo equipo."""

    def setUp(self):
        self.mex = _make_team("MEX")
        self.rsa = _make_team("RSA")
        self.raul = Player.objects.create(name="Raúl Jiménez", team=self.mex)
        self.julian = Player.objects.create(name="Julián Quiñones", team=self.mex)

    def test_counts_per_player_and_disambiguates(self):
        from .views import _player_tournament_stats
        _make_match(
            1, self.mex, self.rsa,
            status=Match.Status.FINISHED, home_score=2, away_score=0,
            events=[
                {"type": "goal", "side": "home", "detail": "Normal Goal",
                 "player": "R. Jimenez", "assist": "J. Quinones"},
                {"type": "goal", "side": "home", "detail": "Penalty", "player": "R. Jimenez"},
                {"type": "card", "side": "home", "detail": "Yellow Card", "player": "J. Quinones"},
            ],
        )
        raul = _player_tournament_stats(self.raul)
        self.assertEqual(raul["goals"], 2)
        self.assertEqual(raul["assists"], 0)
        self.assertEqual(raul["yellow"], 0)
        self.assertEqual(len(raul["matches"]), 1)

        julian = _player_tournament_stats(self.julian)
        self.assertEqual(julian["goals"], 0)
        self.assertEqual(julian["assists"], 1)  # asistió el primer gol de Raúl
        self.assertEqual(julian["yellow"], 1)   # y vio la amarilla, no Raúl

    def test_endpoint_returns_player_card(self):
        from rest_framework.test import APIClient
        resp = APIClient().get(f"/api/v1/polla/players/{self.raul.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Raúl Jiménez")
        self.assertEqual(resp.data["team"]["code"], "MEX")
        self.assertIn("tournament", resp.data)


class StreakMissionTests(TestCase):
    """La misión 'En racha' mide la racha VIVA (un fallo la baja a 0), con
    latch: una vez alcanzado el objetivo queda cumplida para siempre."""

    def setUp(self):
        self.g = Group.objects.create(letter="A", name="Grupo A", display_order=1)
        self.home = _make_team("HOM", self.g)
        self.away = _make_team("AWY", self.g)
        self.user = User.objects.create(username="su", email="su@x.com")
        self.mission = Mission.objects.create(
            code="streak5", title="En racha", desc="d", reward="+40 pts",
            kind=Mission.Kind.STREAK, target=5, bonus_points=40, display_order=4,
        )

    def _played(self, n, home_score, away_score, pred_home, pred_away):
        """Crea un partido finalizado (orden cronológico por número) y el
        pronóstico del usuario, y devuelve el partido."""
        match = Match.objects.create(
            number=n, slug=f"m{n}",
            kickoff=timezone.now() + timedelta(days=n),
            home_team=self.home, away_team=self.away,
            status=Match.Status.FINISHED, home_score=home_score, away_score=away_score,
            stage=Match.Stage.GROUP,
        )
        Prediction.objects.create(
            user=self.user, match=match,
            home_score=pred_home, away_score=pred_away,
        )
        return match

    def _progress(self):
        return UserMission.objects.get(user=self.user, mission=self.mission)

    def test_streak_stats_returns_best_and_live(self):
        # Secuencia cronológica: acierto, acierto, fallo, acierto.
        self._played(1, 1, 0, 2, 0)  # gana local (no exacto) -> acierto
        self._played(2, 0, 0, 1, 1)  # empate (no exacto) -> acierto
        self._played(3, 0, 1, 2, 0)  # gana visita, predijo local -> fallo
        self._played(4, 3, 0, 1, 0)  # gana local -> acierto
        recompute_all()
        preds = list(
            Prediction.objects.filter(user=self.user)
            .select_related("match").order_by("match__kickoff")
        )
        # mejor racha = 2 (los dos primeros); racha viva = 1 (solo el último)
        self.assertEqual(_streak_stats(preds), (2, 1))

    def test_progress_reflects_live_streak_and_resets_on_miss(self):
        # Acierta el primero (resultado, no exacto) y luego falla: la barra
        # debe bajar a 0, no quedarse en el récord.
        self._played(1, 2, 1, 3, 0)  # gana local (no exacto) -> acierto
        self._played(2, 0, 2, 1, 0)  # gana visita, predijo local -> fallo
        recompute_all()
        um = self._progress()
        self.assertEqual(um.progress, 0)
        self.assertFalse(um.done)

    def test_single_non_exact_outcome_counts(self):
        self._played(1, 2, 1, 3, 0)  # gana local sin acertar marcador
        recompute_all()
        self.assertEqual(self._progress().progress, 1)

    def test_done_latches_even_if_streak_breaks_later(self):
        # Cinco aciertos seguidos completan la misión...
        for n in range(1, 6):
            self._played(n, 1, 0, 2, 0)  # gana local (no exacto) -> acierto
        recompute_all()
        um = self._progress()
        self.assertTrue(um.done)
        self.assertEqual(um.progress, 5)
        # ...y aunque después falle, sigue cumplida y con la barra llena.
        self._played(6, 0, 1, 2, 0)  # fallo
        recompute_all()
        um = self._progress()
        self.assertTrue(um.done)
        self.assertEqual(um.progress, 5)


from .models import GranizadoReward  # noqa: E402
from .scoring import (  # noqa: E402
    issue_granizado_rewards,
    recompute_predictions,
)


class GranizadoRewardTests(TestCase):
    """Granizado por acertar el marcador EXACTO de un partido de Colombia (24 h)."""

    def setUp(self):
        self.col = _make_team("COL")
        self.opp = _make_team("BRA")
        self.user = User.objects.create_user(
            username="g1", password="x", role=User.Role.CUSTOMER, email="g1@x.com"
        )

    def _won_exact(self, number, hs, away_score, home=None, away=None):
        """Partido finalizado + pronóstico exacto del usuario; recalcula y emite."""
        m = _make_match(
            number, home or self.col, away or self.opp,
            status=Match.Status.FINISHED, home_score=hs, away_score=away_score,
        )
        Prediction.objects.create(user=self.user, match=m, home_score=hs, away_score=away_score)
        recompute_predictions()
        issue_granizado_rewards()
        return m

    def test_exact_colombia_issues_reward(self):
        m = _make_match(1, self.col, self.opp, status=Match.Status.FINISHED, home_score=2, away_score=1)
        Prediction.objects.create(user=self.user, match=m, home_score=2, away_score=1)
        recompute_predictions()
        self.assertEqual(issue_granizado_rewards(), 1)
        r = GranizadoReward.objects.get(user=self.user, match=m)
        self.assertEqual(r.status, "pending")
        self.assertTrue(r.code.startswith("GRZ-"))
        self.assertGreater(r.expires_at, timezone.now() + timedelta(hours=23))

    def test_correct_outcome_but_not_exact_gives_nothing(self):
        m = _make_match(2, self.col, self.opp, status=Match.Status.FINISHED, home_score=2, away_score=1)
        Prediction.objects.create(user=self.user, match=m, home_score=1, away_score=0)  # gana local, no exacto
        recompute_predictions()
        self.assertEqual(issue_granizado_rewards(), 0)
        self.assertFalse(GranizadoReward.objects.filter(user=self.user).exists())

    def test_exact_but_not_colombia_gives_nothing(self):
        arg = _make_team("ARG")
        m = _make_match(3, arg, self.opp, status=Match.Status.FINISHED, home_score=2, away_score=1)
        Prediction.objects.create(user=self.user, match=m, home_score=2, away_score=1)
        recompute_predictions()
        self.assertEqual(issue_granizado_rewards(), 0)

    def test_unfinished_match_gives_nothing(self):
        m = _make_match(4, self.col, self.opp, status=Match.Status.UPCOMING)
        Prediction.objects.create(user=self.user, match=m, home_score=2, away_score=1)
        recompute_predictions()
        self.assertEqual(issue_granizado_rewards(), 0)

    def test_idempotent(self):
        self._won_exact(5, 0, 0)
        self.assertEqual(issue_granizado_rewards(), 0)  # segunda corrida no duplica
        self.assertEqual(GranizadoReward.objects.filter(user=self.user).count(), 1)

    def test_redeem_marks_delivered(self):
        m = self._won_exact(6, 3, 1)
        r = GranizadoReward.objects.get(user=self.user, match=m)
        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.post(f"/api/v1/polla/granizado/{r.id}/redeem/")
        self.assertEqual(resp.status_code, 200)
        r.refresh_from_db()
        self.assertTrue(r.redeemed)
        self.assertEqual(r.status, "redeemed")
        # Un segundo intento ya no procede.
        self.assertEqual(client.post(f"/api/v1/polla/granizado/{r.id}/redeem/").status_code, 400)

    def test_redeem_after_expiry_fails(self):
        m = self._won_exact(7, 1, 0)
        r = GranizadoReward.objects.get(user=self.user, match=m)
        r.expires_at = timezone.now() - timedelta(minutes=1)
        r.save(update_fields=["expires_at"])
        self.assertEqual(r.status, "expired")
        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.post(f"/api/v1/polla/granizado/{r.id}/redeem/")
        self.assertEqual(resp.status_code, 400)
        r.refresh_from_db()
        self.assertFalse(r.redeemed)

    def test_other_user_cannot_redeem(self):
        m = self._won_exact(8, 2, 2)
        r = GranizadoReward.objects.get(user=self.user, match=m)
        other = User.objects.create_user(
            username="g2", password="x", role=User.Role.CUSTOMER, email="g2@x.com"
        )
        client = APIClient()
        client.force_authenticate(other)
        resp = client.post(f"/api/v1/polla/granizado/{r.id}/redeem/")
        self.assertEqual(resp.status_code, 404)
        r.refresh_from_db()
        self.assertFalse(r.redeemed)

    def test_list_endpoint(self):
        self._won_exact(9, 1, 1)
        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.get("/api/v1/polla/granizado/")
        self.assertEqual(resp.status_code, 200)
        rewards = resp.json()["rewards"]
        self.assertEqual(len(rewards), 1)
        self.assertEqual(rewards[0]["status"], "pending")
        self.assertIn("vs", rewards[0]["match"])
