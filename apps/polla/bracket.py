"""Bracket de eliminación de la Polla Mundialista (modelo de EQUIPOS REALES).

La llave NO depende de los pronósticos del usuario: se arma con los clasificados
REALES y se va resolviendo con la realidad. El usuario predice quién avanza en
cada cruce y suma cuando acierta.

Modelo:
  1. Al terminar la fase de grupos REAL, los clasificados reales (1.º/2.º de cada
     grupo + 8 mejores terceros) siembran los dieciseisavos (R32).
  2. De octavos en adelante, cada cruce toma el ganador REAL del cruce que lo
     alimenta si ya se jugó; si aún no, muestra la proyección del usuario (su
     ``BracketPick``). Así, cuando un cruce se juega, las rondas siguientes se
     "reconectan" con la realidad y el usuario nunca queda con equipos eliminados.
  3. El puntaje por avance se otorga comparando el ganador que el usuario eligió
     para el cruce N contra el ganador REAL del cruce N, escalado por ronda.

``advance_real_bracket`` persiste los competidores reales en los ``Match`` (lo
hace ``recompute_all``); ``resolve_bracket`` arma la vista de cada usuario
(realidad donde existe + su proyección donde aún no). Todo idempotente.
"""
from __future__ import annotations

import re

from django.contrib.auth import get_user_model

from .models import BracketPick, Group, Match, Prediction, outcome_of

User = get_user_model()

# Partidos de fase de grupos del torneo (referencia informativa).
GROUP_TOTAL = 72

# Puntos por acertar quién avanza, escalados por ronda.
BRACKET_POINTS = {
    Match.Stage.R32: 2,
    Match.Stage.R16: 4,
    Match.Stage.QF: 7,
    Match.Stage.SF: 12,
    Match.Stage.THIRD: 5,
    Match.Stage.FINAL: 20,
}

# Orden de rondas para presentar la llave.
ROUND_ORDER = [
    Match.Stage.R32,
    Match.Stage.R16,
    Match.Stage.QF,
    Match.Stage.SF,
    Match.Stage.THIRD,
    Match.Stage.FINAL,
]


# ── Estado de apertura ──────────────────────────────────────────────────────
def group_predicted_count(user):
    """Cuántos partidos de la fase de grupos pronosticó el usuario (informativo)."""
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    return Prediction.objects.filter(
        user=user, match__stage=Match.Stage.GROUP
    ).count()


def is_open():
    """La eliminación se abre cuando TODOS los partidos de grupos terminaron.

    Recién ahí se conocen los clasificados reales que siembran la llave.
    """
    qs = Match.objects.filter(stage=Match.Stage.GROUP)
    total = qs.count()
    if total == 0:
        return False
    done = qs.filter(status=Match.Status.FINISHED).count()
    return done == total


# ── Tabla REAL de la fase de grupos ─────────────────────────────────────────
def _row(team):
    return {"team": team, "pj": 0, "gf": 0, "gc": 0, "pts": 0}


def _accumulate(table, home_id, away_id, hs, as_):
    h, a = table.get(home_id), table.get(away_id)
    if not h or not a:
        return
    h["pj"] += 1
    a["pj"] += 1
    h["gf"] += hs
    h["gc"] += as_
    a["gf"] += as_
    a["gc"] += hs
    res = outcome_of(hs, as_)
    if res == "home":
        h["pts"] += 3
    elif res == "away":
        a["pts"] += 3
    else:
        h["pts"] += 1
        a["pts"] += 1


def real_standings():
    """Tabla REAL de cada grupo, calculada desde los resultados finales.

    Devuelve ``(by_group, thirds)``:
      - ``by_group``: {letra: [team_1, team_2, team_3, team_4]} ordenado por
        puntos reales (desempate: dif. de gol, goles a favor, nombre).
      - ``thirds``: terceros de cada grupo, ordenados mejor→peor (para repartir
        los 8 mejores a sus slots de R32).
    """
    by_group = {}
    thirds = []
    for g in Group.objects.prefetch_related("teams", "matches").all():
        table = {t.id: _row(t) for t in g.teams.all()}
        for m in g.matches.all():
            if m.home_team_id is None or m.away_team_id is None:
                continue
            if not m.is_finished or m.home_score is None or m.away_score is None:
                continue
            _accumulate(table, m.home_team_id, m.away_team_id, m.home_score, m.away_score)
        rows = list(table.values())
        for r in rows:
            r["dg"] = r["gf"] - r["gc"]
        rows.sort(key=lambda r: (-r["pts"], -r["dg"], -r["gf"], r["team"].name))
        by_group[g.letter] = [r["team"] for r in rows]
        if len(rows) >= 3:
            t = rows[2]
            thirds.append(
                {"team": t["team"], "letter": g.letter,
                 "pts": t["pts"], "dg": t["dg"], "gf": t["gf"]}
            )
    thirds.sort(key=lambda r: (-r["pts"], -r["dg"], -r["gf"], r["team"].name))
    return by_group, thirds


# ── Parsing de los slots del bracket (placeholders del seed) ────────────────
_SIMPLE_RE = re.compile(r"^([12])([A-L])$")
_WINNER_RE = re.compile(r"^Ganador\s+(\d+)$")
_LOSER_RE = re.compile(r"^Perdedor\s+(\d+)$")


def _simple_slot(ph):
    """('1','A') -> (rank:int, letter:str) o None."""
    m = _SIMPLE_RE.match((ph or "").strip())
    return (int(m.group(1)), m.group(2)) if m else None


def _third_slot(ph):
    """'3ro A/B/C/D/F' -> set('A','B','C','D','F') o None."""
    s = (ph or "").strip()
    if s.startswith("3ro"):
        return set(re.findall(r"[A-L]", s[3:]))
    return None


def _assign_thirds(slots, thirds_top8):
    """Empareja los 8 mejores terceros con los 8 slots '3ro ...'.

    ``slots``: lista de (match_number, set_de_letras_permitidas).
    Hace backtracking respetando la restricción de grupos; si ninguna
    combinación encaja (caso raro), cae a un reparto relajado.
    """
    n = len(slots)
    thirds = list(thirds_top8)
    used = [False] * len(thirds)
    out = {}

    def bt(i):
        if i == n:
            return True
        number, allowed = slots[i]
        for j, t in enumerate(thirds):
            if used[j] or t["letter"] not in allowed:
                continue
            used[j] = True
            out[number] = t["team"]
            if bt(i + 1):
                return True
            del out[number]
            used[j] = False
        return False

    if n and bt(0):
        return out

    out = {}
    rem = list(thirds)
    for number, allowed in slots:
        chosen = next((t for t in rem if t["letter"] in allowed), None)
        if chosen is None and rem:
            chosen = rem[0]
        if chosen is not None:
            out[number] = chosen["team"]
            rem.remove(chosen)
    return out


# ── Ganador / perdedor real de un cruce ─────────────────────────────────────
def real_winner(match):
    """Equipo que realmente avanzó del cruce finalizado, o None.

    Prioriza ``winner_team`` (lo define el admin o ``polla_sync``), de modo que
    los cruces resueltos por penales —empate con ganador— sí puntúan. Si no
    está, lo infiere del marcador.
    """
    if not match.is_finished:
        return None
    if match.winner_team_id:
        return match.winner_team
    res = outcome_of(match.home_score, match.away_score)
    if res == "home":
        return match.home_team
    if res == "away":
        return match.away_team
    return None


def real_loser(match):
    """Equipo que NO avanzó del cruce finalizado (para el tercer puesto)."""
    w = real_winner(match)
    if w is None:
        return None
    if match.home_team_id and match.home_team_id != w.id:
        return match.home_team
    if match.away_team_id and match.away_team_id != w.id:
        return match.away_team
    return None


def _third_slots_for(matches):
    """Slots '3ro ...' de R32: [(match_number, set_letras)]."""
    out = []
    for m in matches:
        if m.stage != Match.Stage.R32:
            continue
        gs = _third_slot(m.home_placeholder) or _third_slot(m.away_placeholder)
        if gs is not None:
            out.append((m.number, gs))
    return out


# ── Sembrado REAL de la llave (persiste en los Match) ───────────────────────
def advance_real_bracket():
    """Rellena ``home_team``/``away_team`` de los cruces con los equipos REALES.

    - R32: clasificados reales (1X/2X y los 8 mejores terceros).
    - R16+: el ganador/perdedor REAL del cruce que lo alimenta, en cuanto ese
      cruce termina.
    Solo asigna lo que aún esté vacío (respeta lo que ya puso ``polla_sync``).
    Idempotente; no hace nada si los grupos aún no terminaron.
    """
    if not is_open():
        return 0

    by_group, thirds = real_standings()
    matches = list(Match.objects.exclude(stage=Match.Stage.GROUP).order_by("number"))
    by_number = {m.number: m for m in matches}
    third_by_number = _assign_thirds(_third_slots_for(matches), thirds[:8])

    def real_side(ph, number):
        ph = (ph or "").strip()
        if _third_slot(ph) is not None:
            return third_by_number.get(number)
        simple = _simple_slot(ph)
        if simple:
            rank, letter = simple
            order = by_group.get(letter) or []
            return order[rank - 1] if len(order) >= rank else None
        mw = _WINNER_RE.match(ph)
        if mw:
            mn = by_number.get(int(mw.group(1)))
            return real_winner(mn) if mn is not None else None
        ml = _LOSER_RE.match(ph)
        if ml:
            mn = by_number.get(int(ml.group(1)))
            return real_loser(mn) if mn is not None else None
        return None

    changed = []
    for m in matches:  # ascendente: las dependencias ya quedaron asignadas
        upd = False
        if m.home_team_id is None:
            h = real_side(m.home_placeholder, m.number)
            if h is not None:
                m.home_team = h
                upd = True
        if m.away_team_id is None:
            a = real_side(m.away_placeholder, m.number)
            if a is not None:
                m.away_team = a
                upd = True
        if upd:
            changed.append(m)
    if changed:
        Match.objects.bulk_update(changed, ["home_team", "away_team"])
    return len(changed)


# ── Resolución del bracket de un usuario (realidad + su proyección) ──────────
def resolve_bracket(user, open_=None):
    """Resuelve los competidores y el pick validado de cada cruce para el usuario.

    Devuelve {match_number: {"home": Team|None, "away": Team|None,
                             "pick": Team|None}}.

    Para cada lado de un cruce:
      - si el ``Match`` ya tiene el equipo REAL asignado, ese manda;
      - si no, se deriva: R32 desde los clasificados reales; R16+ desde el
        ganador real del cruce que lo alimenta (si ya se jugó) o, en su defecto,
        desde la proyección del usuario (su pick en ese cruce).
    ``pick`` es el ``BracketPick`` del usuario solo si sigue siendo uno de los
    dos competidores actuales (si la realidad/cambios lo dejaron fuera, se anula).
    """
    if open_ is None:
        open_ = is_open()

    matches = list(Match.objects.exclude(stage=Match.Stage.GROUP).order_by("number"))
    picks = {}
    if user is not None and getattr(user, "is_authenticated", False):
        picks = {
            bp.match_id: bp.winner_team
            for bp in BracketPick.objects.filter(user=user).select_related("winner_team")
        }

    resolved = {}
    if not open_:
        for m in matches:
            resolved[m.number] = {"home": None, "away": None, "pick": None}
        return resolved

    by_number = {m.number: m for m in matches}
    by_group, thirds = real_standings()
    third_by_number = _assign_thirds(_third_slots_for(matches), thirds[:8])

    def side(m, which):
        real = m.home_team if which == "home" else m.away_team
        if real is not None:
            return real  # la realidad ya definió este competidor
        ph = (m.home_placeholder if which == "home" else m.away_placeholder).strip()
        if _third_slot(ph) is not None:
            return third_by_number.get(m.number)
        simple = _simple_slot(ph)
        if simple:
            rank, letter = simple
            order = by_group.get(letter) or []
            return order[rank - 1] if len(order) >= rank else None
        mw = _WINNER_RE.match(ph)
        if mw:
            n = int(mw.group(1))
            mn = by_number.get(n)
            if mn is not None and mn.is_finished:
                return real_winner(mn)
            info = resolved.get(n)
            return info["pick"] if info else None
        ml = _LOSER_RE.match(ph)
        if ml:
            n = int(ml.group(1))
            mn = by_number.get(n)
            if mn is not None and mn.is_finished:
                return real_loser(mn)
            info = resolved.get(n)
            if not info or not info["pick"]:
                return None
            pid = info["pick"].id
            if info["home"] and info["home"].id == pid:
                return info["away"]
            if info["away"] and info["away"].id == pid:
                return info["home"]
            return None
        return None

    for m in matches:  # orden ascendente => dependencias ya resueltas
        home = side(m, "home")
        away = side(m, "away")
        pick = picks.get(m.id)
        valid_ids = {t.id for t in (home, away) if t}
        if pick is not None and pick.id not in valid_ids:
            pick = None
        resolved[m.number] = {"home": home, "away": away, "pick": pick}

    return resolved


def prune_invalid_picks(user):
    """Borra los ``BracketPick`` cuyo ganador ya no es competidor del cruce.

    Útil tras cambiar un pick de arriba o tras reconectar con la realidad:
    invalida en cascada los de abajo. Itera hasta estabilizar.
    """
    for _ in range(8):
        resolved = resolve_bracket(user, open_=True)
        stale = []
        for bp in BracketPick.objects.filter(user=user).select_related("match"):
            info = resolved.get(bp.match.number) or {}
            ids = {t.id for t in (info.get("home"), info.get("away")) if t}
            if bp.winner_team_id not in ids:
                stale.append(bp.id)
        if not stale:
            break
        BracketPick.objects.filter(id__in=stale).delete()


# ── Puntaje por avance ──────────────────────────────────────────────────────
def recompute_bracket():
    """Recalcula el puntaje por avance de todos los picks de bracket."""
    user_ids = list(BracketPick.objects.values_list("user_id", flat=True).distinct())
    users = {u.pk: u for u in User.objects.filter(pk__in=user_ids)}
    updated = []
    for uid in user_ids:
        user = users.get(uid)
        if user is None:
            continue
        resolved = resolve_bracket(user)
        bps = list(
            BracketPick.objects.filter(user_id=uid).select_related(
                "match", "match__winner_team", "match__home_team", "match__away_team"
            )
        )
        for bp in bps:
            m = bp.match
            info = resolved.get(m.number) or {}
            pick = info.get("pick")
            winner = real_winner(m)
            if pick is not None and winner is not None and pick.id == winner.id:
                bp.points_earned = BRACKET_POINTS.get(m.stage, 0)
                bp.scored = True
            else:
                bp.points_earned = 0
                bp.scored = winner is not None
        if bps:
            updated.extend(bps)
    if updated:
        BracketPick.objects.bulk_update(
            updated, ["points_earned", "scored"], batch_size=500
        )
    return len(updated)
