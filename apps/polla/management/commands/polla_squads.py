"""Enriquece la plantilla local con la nómina oficial de API-Football.

``/players/squads?team=X`` devuelve la nómina de cada selección con foto,
dorsal, posición y edad. Este comando la cruza con los ``Player`` sembrados
(que son las opciones de las menciones y la plantilla de la ficha de equipo)
y les copia esos datos. Es UNA llamada por selección (48 en total) y la
nómina ya no cambia durante el torneo: basta correrlo una vez, y repetirlo
es seguro (idempotente).

Por defecto hace DRY-RUN (no escribe). Revisá el reporte y volvé a correr con
``--apply`` para persistir.

    python manage.py polla_squads             # simulación (no escribe)
    python manage.py polla_squads --apply     # persiste

Opciones:
  --apply           Persiste los cambios (por defecto solo simula).
  --team CODE       Limita a una selección (p.ej. --team COL).
  --create-missing  Crea los jugadores de la nómina oficial que no matchean
                    con ninguno sembrado. OJO: si el jugador sí existe local
                    con otra grafía, esto lo duplica; revisá el dry-run antes.

Matching por jugador (la API suele abreviar, p.ej. "L. Díaz"):
  1. ``api_player_id`` ya enlazado (p.ej. por el sync de goleadores).
  2. Nombre completo normalizado idéntico, o el nombre local como comienzo
     del de la API ("Pau Cubarsí" dentro de "Pau Cubarsí Paredes").
  3. Apellido exacto como palabra (también el compuesto: "O Neill"/"O'Neill");
     si hay varios candidatos desempata la inicial del nombre.
  4. Difuso (difflib) para transliteraciones ("Bayindir"/"Bayındır",
     "Sayfiev"/"Sayfiyev"): solo decide con score alto y margen claro sobre
     el segundo candidato, para no enlazar mal a nadie.
"""
from __future__ import annotations

import difflib
import unicodedata

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.polla.models import Player, Team
from apps.polla.providers import get_provider

# Letras que NFKD no descompone (la ı turca, ø nórdica, etc.).
_TRANSLIT = str.maketrans({"ı": "i", "ø": "o", "ł": "l", "đ": "d", "ß": "ss",
                           "æ": "ae", "œ": "oe"})


def _norm(s: str) -> str:
    """minúsculas, sin acentos/apóstrofes, puntos y guiones como espacio."""
    s = (s or "").replace("'", "").replace("’", "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().translate(_TRANSLIT)
    return " ".join(s.replace(".", " ").replace("-", " ").split())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _find_local(sp, locals_by_api_id, pool):
    """Empareja un jugador de la nómina API con un ``Player`` local del pool."""
    if sp.api_player_id and sp.api_player_id in locals_by_api_id:
        p = locals_by_api_id[sp.api_player_id]
        return p if p in pool else None

    nn = _norm(sp.name)
    parts = nn.split()
    if not parts:
        return None
    norms = {p: _norm(p.name) for p in pool}

    # 1) Nombre completo idéntico.
    exact = [p for p in pool if norms[p] == nn]
    if len(exact) == 1:
        return exact[0]

    # 2) El nombre local es el comienzo del de la API ("Alisson" en
    #    "Alisson Becker", "Pau Cubarsí" en "Pau Cubarsí Paredes").
    pref = [p for p in pool if nn.startswith(norms[p] + " ")]
    if len(pref) == 1:
        return pref[0]

    def words_of(p):
        ws = norms[p].split()
        return set(ws) | {a + b for a, b in zip(ws, ws[1:])}

    # 3) Apellido exacto como palabra; también el compuesto de las últimas
    #    dos ("O Neill" -> "oneill" contra "O'Neill" sin apóstrofe).
    last = parts[-1]
    last2 = "".join(parts[-2:]) if len(parts) >= 2 else last
    if len(last) >= 3:
        cands = [p for p in pool if last in words_of(p) or last2 in words_of(p)]
        if len(cands) > 1:
            cands = [p for p in cands if norms[p][:1] == parts[0][0]]
        if len(cands) == 1:
            return cands[0]

    # 4a) Difuso por nombre completo (grafías casi iguales).
    scored = sorted(pool, key=lambda p: _ratio(nn, norms[p]), reverse=True)
    if scored:
        best = _ratio(nn, norms[scored[0]])
        runner = _ratio(nn, norms[scored[1]]) if len(scored) > 1 else 0.0
        if best >= 0.90 and best - runner >= 0.03:
            return scored[0]

    # 4b) Difuso por apellido contra cada palabra (y pares) del nombre local.
    if len(last) < 3:
        return None

    def last_score(p):
        return max((_ratio(last, w) for w in words_of(p) if len(w) >= 3),
                   default=0.0)

    scored = sorted(pool, key=last_score, reverse=True)
    if not scored:
        return None
    best = last_score(scored[0])
    runner = last_score(scored[1]) if len(scored) > 1 else 0.0
    if best >= 0.82:
        if best - runner >= 0.03:
            return scored[0]
        # Empate técnico: desempata la inicial del nombre, si queda uno solo.
        tied = [p for p in scored
                if last_score(p) > best - 0.03 and norms[p][:1] == parts[0][0]]
        if len(tied) == 1:
            return tied[0]
    return None


class Command(BaseCommand):
    help = "Trae la nómina oficial (foto/dorsal/posición/edad) de cada selección."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Persistir cambios (por defecto solo simula).")
        parser.add_argument("--team", default=None,
                            help="Limitar a una selección por código (p.ej. COL).")
        parser.add_argument("--create-missing", action="store_true",
                            help="Crear jugadores de la nómina sin match local.")

    def handle(self, *args, **options):
        provider = get_provider()
        if not provider.available:
            self.stderr.write(self.style.ERROR(
                "Sin API_FOOTBALL_KEY: configurá la key antes de traer nóminas."))
            return

        teams = Team.objects.exclude(api_team_id=None).order_by("code")
        if options["team"]:
            teams = teams.filter(code=options["team"].upper())
        teams = list(teams)
        if not teams:
            self.stderr.write(self.style.ERROR(
                "No hay selecciones con api_team_id (¿corriste polla_map_api?)."))
            return

        total_api_missing, total_local_missing = 0, 0
        to_update, to_create = [], []  # se llenan equipo a equipo

        for team in teams:
            try:
                squad = provider.fetch_squad(team.api_team_id)
            except Exception as exc:  # noqa: BLE001 - seguir con el resto
                self.stderr.write(f"  {team.code}: error consultando la API: {exc}")
                continue
            if not squad:
                self.stdout.write(self.style.WARNING(
                    f"  {team.code}: la API no devolvió nómina todavía."))
                continue

            locals_ = list(team.players.all())
            locals_by_api_id = {p.api_player_id: p for p in locals_ if p.api_player_id}
            pool = list(locals_)  # locales aún sin emparejar
            api_missing = []

            for sp in squad:
                p = _find_local(sp, locals_by_api_id, pool)
                if not p:
                    api_missing.append(sp)
                    continue
                pool.remove(p)
                changed = []
                for attr, value in (
                    ("api_player_id", sp.api_player_id or p.api_player_id),
                    ("photo_url", sp.photo_url or p.photo_url),
                    ("position", sp.position or p.position),
                    ("number", sp.number if sp.number is not None else p.number),
                    ("age", sp.age if sp.age is not None else p.age),
                    ("is_keeper", sp.position == "GK" if sp.position else p.is_keeper),
                ):
                    if getattr(p, attr) != value:
                        setattr(p, attr, value)
                        changed.append(attr)
                if changed:
                    to_update.append((p, changed))

            if options["create_missing"]:
                for sp in api_missing:
                    to_create.append(Player(
                        name=sp.name, team=team, is_keeper=sp.position == "GK",
                        api_player_id=sp.api_player_id, photo_url=sp.photo_url,
                        position=sp.position, number=sp.number, age=sp.age,
                    ))

            matched = len(squad) - len(api_missing)
            line = f"  {team.code}: {matched}/{len(squad)} de la nómina emparejados"
            if api_missing:
                line += f" · sin match API: {', '.join(sp.name for sp in api_missing)}"
            if pool:
                line += f" · locales sin datos: {', '.join(p.name for p in pool)}"
            self.stdout.write(line)
            total_api_missing += len(api_missing)
            total_local_missing += len(pool)

        total_updated = len(to_update)
        total_created = len(to_create)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nJugadores a actualizar: {total_updated}"
            f" · a crear: {total_created}"
            f" · nómina API sin match: {total_api_missing}"
            f" · locales sin datos: {total_local_missing}"))

        if not options["apply"]:
            self.stdout.write(self.style.NOTICE(
                "\n[DRY-RUN] No se escribió nada. Repetí con --apply para persistir."))
            return

        with transaction.atomic():
            for p, changed in to_update:
                p.save(update_fields=changed)
            if to_create:
                Player.objects.bulk_create(to_create)
        self.stdout.write(self.style.SUCCESS(
            f"\nGuardado: {total_updated} actualizados, {total_created} creados."))
