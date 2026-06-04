"""Motor de puntaje de la Polla.

Contiene la logica PURA de puntaje (``score_prediction``, ``group_standings``)
y los servicios de recalculo que aplica ``polla_sync`` sobre la base de datos
(``recompute_all``). Todo es idempotente: correrlo N veces da el mismo estado.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .bracket import advance_real_bracket, recompute_bracket
from .models import (
    POINTS_EXACT,
    POINTS_NONE,
    POINTS_OUTCOME,
    REFERRAL_CAP,
    REFERRAL_POINTS_PER,
    Award,
    AwardPick,
    BracketPick,
    Match,
    Mission,
    Prediction,
    Referral,
    UserMission,
    UserScore,
    outcome_of,
)

User = get_user_model()


# ── Logica pura ───────────────────────────────────────────────────────────
def score_prediction(pred_home, pred_away, real_home, real_away):
    """Devuelve (puntos, es_exacto, resultado_correcto).

    - Marcador exacto -> 3 pts (tambien cuenta como resultado correcto)
    - Resultado correcto (ganador/empate) sin marcador exacto -> 1 pt
    - Incorrecto -> 0 pts
    """
    if real_home is None or real_away is None:
        return POINTS_NONE, False, False
    if pred_home == real_home and pred_away == real_away:
        return POINTS_EXACT, True, True
    if outcome_of(pred_home, pred_away) == outcome_of(real_home, real_away):
        return POINTS_OUTCOME, False, True
    return POINTS_NONE, False, False


def group_standings(group):
    """Tabla de posiciones de un grupo, calculada desde los partidos jugados.

    Devuelve filas ordenadas (pts, dif. de gol, goles a favor, nombre) con la
    forma que consume el frontend.
    """
    table = {}
    for team in group.teams.all():
        table[team.id] = {
            "team": team, "pj": 0, "g": 0, "e": 0, "p": 0,
            "gf": 0, "gc": 0, "dg": 0, "pts": 0,
        }

    finished = group.matches.filter(
        status=Match.Status.FINISHED,
        home_team__isnull=False, away_team__isnull=False,
        home_score__isnull=False, away_score__isnull=False,
    )
    for m in finished:
        h, a = table.get(m.home_team_id), table.get(m.away_team_id)
        if not h or not a:
            continue
        h["pj"] += 1; a["pj"] += 1
        h["gf"] += m.home_score; h["gc"] += m.away_score
        a["gf"] += m.away_score; a["gc"] += m.home_score
        res = outcome_of(m.home_score, m.away_score)
        if res == "home":
            h["g"] += 1; h["pts"] += 3; a["p"] += 1
        elif res == "away":
            a["g"] += 1; a["pts"] += 3; h["p"] += 1
        else:
            h["e"] += 1; a["e"] += 1; h["pts"] += 1; a["pts"] += 1

    rows = list(table.values())
    for r in rows:
        r["dg"] = r["gf"] - r["gc"]
    rows.sort(key=lambda r: (-r["pts"], -r["dg"], -r["gf"], r["team"].name))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


# ── Servicios de recalculo (mutan la BD) ───────────────────────────────────
def recompute_predictions():
    """Recalcula puntaje de todos los pronosticos segun resultados finales."""
    preds = list(Prediction.objects.select_related("match"))
    for p in preds:
        m = p.match
        if m.is_finished and m.home_score is not None and m.away_score is not None:
            pts, exact, correct = score_prediction(
                p.home_score, p.away_score, m.home_score, m.away_score
            )
            p.points_earned, p.is_exact, p.is_correct_outcome, p.scored = (
                pts, exact, correct, True,
            )
        else:
            p.points_earned, p.is_exact, p.is_correct_outcome, p.scored = (
                0, False, False, False,
            )
    if preds:
        Prediction.objects.bulk_update(
            preds, ["points_earned", "is_exact", "is_correct_outcome", "scored"],
            batch_size=500,
        )
    return len(preds)


def recompute_awards():
    """Recalcula puntaje de las menciones que ya esten resueltas."""
    picks = list(AwardPick.objects.select_related("award", "team", "player"))
    for pick in picks:
        a = pick.award
        won = False
        if a.resolved:
            if a.award_type == Award.AwardType.TEAM:
                won = pick.team_id is not None and pick.team_id == a.resolved_team_id
            else:  # player / keeper
                won = pick.player_id is not None and pick.player_id == a.resolved_player_id
        pick.points_earned = a.points if won else 0
        pick.scored = a.resolved
    if picks:
        AwardPick.objects.bulk_update(picks, ["points_earned", "scored"], batch_size=500)
    return len(picks)


def _longest_streak(predictions_sorted):
    """Mayor racha de resultados correctos consecutivos (sobre finalizados)."""
    best = cur = 0
    for p in predictions_sorted:
        if not p.match.is_finished:
            continue
        if p.is_correct_outcome:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def recompute_missions_for_user(user, missions=None):
    """Actualiza el progreso de cada misión para un usuario."""
    if missions is None:
        missions = list(Mission.objects.all())

    preds = list(
        Prediction.objects.filter(user=user)
        .select_related("match", "match__home_team", "match__away_team")
        .order_by("match__kickoff")
    )
    total_predicted = len(preds)
    exact_count = sum(1 for p in preds if p.is_exact and p.scored)
    group_predicted = sum(1 for p in preds if p.match.stage == Match.Stage.GROUP)

    for mission in missions:
        progress = 0
        if mission.kind == Mission.Kind.FIRST_PREDICTION:
            progress = min(mission.target, total_predicted)
        elif mission.kind == Mission.Kind.EXACT_SCORES:
            progress = min(mission.target, exact_count)
        elif mission.kind == Mission.Kind.GROUP_STAGE_COMPLETE:
            progress = min(mission.target, group_predicted)
        elif mission.kind == Mission.Kind.TEAM_MATCHES:
            code = (mission.param or "").upper()
            progress = sum(
                1 for p in preds
                if (p.match.home_team and p.match.home_team.code == code)
                or (p.match.away_team and p.match.away_team.code == code)
            )
            progress = min(mission.target, progress)
        elif mission.kind == Mission.Kind.STREAK:
            progress = min(mission.target, _longest_streak(preds))
        elif mission.kind == Mission.Kind.INVITE:
            # Social: no hay mecanismo automatico; se respeta el progreso actual.
            existing = UserMission.objects.filter(user=user, mission=mission).first()
            progress = existing.progress if existing else 0

        done = progress >= mission.target
        um, _ = UserMission.objects.get_or_create(user=user, mission=mission)
        changed = um.progress != progress or um.done != done
        um.progress = progress
        if done and not um.done:
            um.completed_at = timezone.now()
        um.done = done
        if changed or um.completed_at is None and not done:
            um.save()


def recompute_referrals():
    """Marca como calificadas las invitaciones cuyo invitado ya jugó.

    Es un *latch* de un solo sentido: una invitación calificada nunca se
    degrada (si el invitado jugó, el punto del invitador es permanente, aunque
    a futuro se permitiera borrar pronósticos). Idempotente.
    """
    return (
        Referral.objects.filter(
            qualified=False, invitee__polla_predictions__isnull=False
        )
        .distinct()
        .update(qualified=True, qualified_at=timezone.now())
    )


def recompute_scores():
    """Agrega puntajes por usuario en ``UserScore`` y asigna posiciones."""
    # Agregaciones agrupadas por usuario (una query por fuente, sin N+1).
    pred_agg = {
        r["user_id"]: r
        for r in Prediction.objects.values("user_id").annotate(
            match_points=Sum("points_earned"),
            predicted=Count("id"),
            exact_hits=Count("id", filter=Q(is_exact=True, scored=True)),
            correct_hits=Count("id", filter=Q(is_correct_outcome=True, scored=True)),
        )
    }
    award_agg = {
        r["user_id"]: r["s"] or 0
        for r in AwardPick.objects.values("user_id").annotate(s=Sum("points_earned"))
    }
    bracket_agg = {
        r["user_id"]: r["s"] or 0
        for r in BracketPick.objects.values("user_id").annotate(s=Sum("points_earned"))
    }
    mission_agg = {
        r["user_id"]: r["s"] or 0
        for r in UserMission.objects.filter(done=True)
        .values("user_id")
        .annotate(s=Sum("mission__bonus_points"))
    }
    # Invitaciones calificadas por invitador (1 pt c/u, con tope).
    referral_agg = {
        r["inviter_id"]: r["c"] or 0
        for r in Referral.objects.filter(qualified=True)
        .values("inviter_id")
        .annotate(c=Count("id"))
    }

    # Todo cliente entra al ranking, haya jugado o no (aparece en 0 puntos).
    customer_ids = set(
        User.objects.filter(role=User.Role.CUSTOMER).values_list("id", flat=True)
    )
    user_ids = (
        set(pred_agg) | set(award_agg) | set(bracket_agg) | set(mission_agg)
        | set(referral_agg) | customer_ids
    )

    for uid in user_ids:
        pa = pred_agg.get(uid) or {}
        match_points = pa.get("match_points") or 0
        predicted = pa.get("predicted") or 0
        exact_hits = pa.get("exact_hits") or 0
        correct_hits = pa.get("correct_hits") or 0
        award_points = award_agg.get(uid, 0)
        bracket_points = bracket_agg.get(uid, 0)
        mission_points = mission_agg.get(uid, 0)
        referral_points = min(referral_agg.get(uid, 0), REFERRAL_CAP) * REFERRAL_POINTS_PER
        UserScore.objects.update_or_create(
            user_id=uid,
            defaults={
                "match_points": match_points,
                "award_points": award_points,
                "mission_points": mission_points,
                "bracket_points": bracket_points,
                "referral_points": referral_points,
                "points": (
                    match_points + award_points + mission_points
                    + bracket_points + referral_points
                ),
                "exact_hits": exact_hits,
                "correct_hits": correct_hits,
                "predicted": predicted,
            },
        )

    # Asignar posiciones (1 = más puntos). Empates: por exactos/aciertos y, como
    # último criterio estable, por antigüedad del usuario (user_id ascendente).
    position = 0
    for score in UserScore.objects.order_by(
        "-points", "-exact_hits", "-correct_hits", "user_id"
    ):
        position += 1
        if score.position != position:
            score.position = position
            score.save(update_fields=["position"])


def recompute_all():
    """Recalcula todo: pronosticos, menciones, bracket, misiones y ranking."""
    recompute_predictions()
    recompute_awards()
    advance_real_bracket()  # arma la llave real con los clasificados de grupos
    recompute_bracket()
    missions = list(Mission.objects.all())
    for uid in set(Prediction.objects.values_list("user_id", flat=True)) | set(
        AwardPick.objects.values_list("user_id", flat=True)
    ):
        user = User.objects.get(pk=uid)
        recompute_missions_for_user(user, missions)
    recompute_referrals()
    recompute_scores()
