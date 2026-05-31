"""Bracket ENCADENADO de la Polla Mundialista.

Resuelve la fase de eliminación a partir de los pronósticos de grupos del
usuario y de sus picks de avance, y calcula el puntaje por avance.

Modelo:
  1. Los marcadores que el usuario pronostica en la fase de grupos definen SU
     tabla de posiciones (1.º/2.º de cada grupo + 8 mejores terceros).
  2. Esos clasificados siembran los dieciseisavos (R32).
  3. De octavos en adelante, cada cruce toma los ganadores que el propio
     usuario fue eligiendo (``BracketPick``), propagándose hasta el campeón.
  4. El puntaje por avance se otorga comparando el ganador que el usuario
     eligió para el cruce N contra el ganador REAL del cruce N, escalado por
     ronda (un semifinalista vale mucho más que un dieciseisavista).

Todo es PURO sobre la BD y idempotente: ``recompute_bracket`` puede correrse
las veces que sea y deja el mismo estado.
"""
from __future__ import annotations

import re

from django.contrib.auth import get_user_model

from .models import BracketPick, Group, Match, Prediction, Team, outcome_of

User = get_user_model()

# Partidos de fase de grupos que hay que pronosticar para abrir la llave.
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


# ── Tabla pronosticada por el usuario ──────────────────────────────────────
def group_predicted_count(user):
    """Cuántos partidos de la fase de grupos pronosticó el usuario."""
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    return Prediction.objects.filter(
        user=user, match__stage=Match.Stage.GROUP
    ).count()


def is_unlocked(user):
    """La llave se abre cuando el usuario pronosticó los 72 de grupos."""
    return group_predicted_count(user) >= GROUP_TOTAL


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


def predicted_standings(user):
    """Tabla pronosticada por el usuario.

    Devuelve ``(by_group, thirds)``:
      - ``by_group``: {letra: [team_1, team_2, team_3, team_4]} ordenado por
        puntos pronosticados (desempate: dif. de gol, goles a favor, nombre).
      - ``thirds``: lista global de dicts {team, letter, ...} de los terceros
        de cada grupo, ordenada mejor→peor.

    Solo cuentan los partidos que el usuario pronosticó; si la fase de grupos
    está incompleta la tabla queda parcial (el desbloqueo exige los 72).
    """
    preds = {
        p.match_id: p
        for p in Prediction.objects.filter(
            user=user, match__stage=Match.Stage.GROUP
        )
    }
    by_group = {}
    thirds = []
    for g in Group.objects.prefetch_related("teams", "matches").all():
        table = {t.id: _row(t) for t in g.teams.all()}
        for m in g.matches.all():
            if m.home_team_id is None or m.away_team_id is None:
                continue
            p = preds.get(m.id)
            if not p:
                continue
            _accumulate(table, m.home_team_id, m.away_team_id, p.home_score, p.away_score)
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
    combinación encaja (caso raro con terceros atípicos), cae a un reparto
    relajado para garantizar que los 8 slots queden llenos.
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

    # Fallback relajado: primero un tercero permitido; si no, el siguiente libre.
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


# ── Resolución del bracket de un usuario ────────────────────────────────────
def resolve_bracket(user, unlocked=None):
    """Resuelve los competidores y el pick validado de cada cruce.

    Devuelve {match_number: {"home": Team|None, "away": Team|None,
                             "pick": Team|None}}.

    - R32: competidores desde la tabla pronosticada (1X/2X y terceros).
    - R16+: competidores desde los ganadores (picks) de los cruces que
      alimentan ("Ganador N"); el tercer puesto desde los perdedores
      ("Perdedor N").
    - ``pick`` es el ``BracketPick`` del usuario SOLO si sigue siendo uno de
      los dos competidores actuales (si un pick de arriba cambió, los de abajo
      se invalidan en cascada).
    """
    if unlocked is None:
        unlocked = is_unlocked(user)

    matches = list(Match.objects.exclude(stage=Match.Stage.GROUP).order_by("number"))
    picks = {
        bp.match_id: bp.winner_team
        for bp in BracketPick.objects.filter(user=user).select_related("winner_team")
    }

    resolved = {}
    if not unlocked:
        for m in matches:
            resolved[m.number] = {"home": None, "away": None, "pick": None}
        return resolved

    by_group, thirds = predicted_standings(user)

    # Reparto de terceros a sus slots de R32.
    third_slots = []
    for m in matches:
        if m.stage != Match.Stage.R32:
            continue
        gs = _third_slot(m.home_placeholder) or _third_slot(m.away_placeholder)
        if gs is not None:
            third_slots.append((m.number, gs))
    third_by_number = _assign_thirds(third_slots, thirds[:8])

    def from_side(ph, number):
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
            info = resolved.get(int(mw.group(1)))
            return info["pick"] if info else None
        ml = _LOSER_RE.match(ph)
        if ml:
            info = resolved.get(int(ml.group(1)))
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
        home = from_side(m.home_placeholder, m.number)
        away = from_side(m.away_placeholder, m.number)
        pick = picks.get(m.id)
        valid_ids = {t.id for t in (home, away) if t}
        if pick is not None and pick.id not in valid_ids:
            pick = None
        resolved[m.number] = {"home": home, "away": away, "pick": pick}

    return resolved


def prune_invalid_picks(user):
    """Borra los ``BracketPick`` cuyo ganador ya no es competidor del cruce.

    Útil tras cambiar un pick de arriba: invalida en cascada los de abajo.
    Itera hasta estabilizar (la profundidad de la llave es pequeña).
    """
    for _ in range(8):
        resolved = resolve_bracket(user, unlocked=True)
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
def real_winner(match):
    """Equipo que realmente avanzó del cruce finalizado, o None.

    Prioriza ``winner_team`` (lo define el admin o ``polla_sync``), de modo que
    los cruces resueltos por penales —marcador empatado pero con ganador— sí
    puntúan. Si no está, lo infiere del marcador.
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
