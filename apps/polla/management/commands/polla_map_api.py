"""Mapea los IDs de API-Football a los partidos y selecciones de la Polla.

Se corre UNA vez (y se puede repetir sin riesgo) antes de empezar a sincronizar
en vivo. Sin este mapeo, ``polla_sync`` corre sin errores pero no actualiza
ningún partido, porque no sabe qué fixture de la API corresponde a cada
``Match`` local.

Estrategia (sin depender de adivinar nombres en inglés):
  1. Ancla por FECHA: cada ``Match`` local se empareja con el fixture de la API
     cuyo ``kickoff`` (UTC) coincide. Si hay varios a la misma hora exacta, se
     desempata por los nombres de los equipos (normalizados + alias ES→EN).
  2. Deriva equipos: de los partidos de FASE DE GRUPOS (que ya tienen
     ``home_team``/``away_team`` asignados) se infiere el ``api_team_id`` de cada
     selección a partir del fixture emparejado. Cero diccionarios obligatorios.
  3. Refuerzo por nombre: las selecciones que no se hayan podido derivar se
     intentan mapear por nombre (alias ES→EN) contra los equipos vistos en la
     API.

Por defecto hace DRY-RUN (no escribe). Revisá el reporte y volvé a correr con
``--apply`` para persistir.

    python manage.py polla_map_api            # simulación (no escribe)
    python manage.py polla_map_api --apply    # escribe api_fixture_id / api_team_id

Opciones:
  --apply        Persiste los cambios (por defecto solo simula).
  --tolerance N  Minutos de tolerancia al emparejar por fecha (default 0 = hora
                 exacta). Subilo si el calendario sembrado difiere del de la API.
"""
from __future__ import annotations

import unicodedata
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.polla.models import Match, Team
from apps.polla.providers import get_provider


def _norm(s: str) -> str:
    """minúsculas, sin acentos, sin puntuación ni 'fc/national'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    for junk in (".", "-", "'", "&", " and ", " of "):
        s = s.replace(junk, " ")
    return " ".join(s.split())


# Alias por iso2 -> nombres en inglés que usa API-Football (normalizados).
# Solo se usan como desempate/refuerzo; el mapeo principal es por fecha.
ALIASES_BY_ISO2 = {
    "kr": {"south korea", "korea republic"},
    "us": {"usa", "united states"},
    "ir": {"iran", "ir iran"},
    "cz": {"czech republic", "czechia"},
    "ba": {"bosnia herzegovina", "bosnia and herzegovina"},
    "ci": {"ivory coast", "cote divoire"},
    "cd": {"congo dr", "dr congo", "congo kinshasa"},
    "cv": {"cape verde", "cabo verde"},
    "cw": {"curacao"},
    "tr": {"turkey", "turkiye"},
    "gb-eng": {"england"},
    "gb-sct": {"scotland"},
    "nl": {"netherlands"},
    "za": {"south africa"},
    "nz": {"new zealand"},
    "sa": {"saudi arabia"},
    "qa": {"qatar"},
}

# Nombre en español -> inglés base (para los que no están en ALIASES_BY_ISO2).
ES_TO_EN = {
    "argelia": "algeria", "alemania": "germany", "arabia saudita": "saudi arabia",
    "australia": "australia", "austria": "austria", "belgica": "belgium",
    "brasil": "brazil", "cabo verde": "cape verde", "canada": "canada",
    "catar": "qatar", "colombia": "colombia", "corea del sur": "south korea",
    "costa de marfil": "ivory coast", "croacia": "croatia", "curazao": "curacao",
    "ecuador": "ecuador", "egipto": "egypt", "escocia": "scotland",
    "espana": "spain", "estados unidos": "usa", "francia": "france",
    "ghana": "ghana", "haiti": "haiti", "inglaterra": "england", "irak": "iraq",
    "iran": "iran", "japon": "japan", "jordania": "jordan", "marruecos": "morocco",
    "mexico": "mexico", "noruega": "norway", "nueva zelanda": "new zealand",
    "paises bajos": "netherlands", "panama": "panama", "paraguay": "paraguay",
    "portugal": "portugal", "rd congo": "congo dr", "rep checa": "czech republic",
    "senegal": "senegal", "sudafrica": "south africa", "suecia": "sweden",
    "suiza": "switzerland", "tunez": "tunisia", "turquia": "turkey",
    "uruguay": "uruguay", "uzbekistan": "uzbekistan", "bosnia y h": "bosnia herzegovina",
}


def _team_accepts(team: Team, api_name_norm: str) -> bool:
    """¿El nombre de la API corresponde a esta selección local?"""
    if team.iso2 in ALIASES_BY_ISO2 and api_name_norm in ALIASES_BY_ISO2[team.iso2]:
        return True
    en = ES_TO_EN.get(_norm(team.name))
    if en and _norm(en) == api_name_norm:
        return True
    return _norm(team.name) == api_name_norm


class Command(BaseCommand):
    help = "Mapea api_fixture_id / api_team_id de API-Football a la Polla."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Persistir cambios (por defecto solo simula).")
        parser.add_argument("--tolerance", type=int, default=0,
                            help="Minutos de tolerancia al emparejar por fecha.")

    def handle(self, *args, **options):
        provider = get_provider()
        if not provider.available:
            self.stderr.write(self.style.ERROR(
                "Sin API_FOOTBALL_KEY: configurá la key antes de mapear."))
            return

        try:
            index = provider.fetch_fixture_index()
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(self.style.ERROR(f"Error consultando la API: {exc}"))
            return

        index = [f for f in index if f.api_fixture_id and f.kickoff]
        self.stdout.write(f"Fixtures recibidos de la API: {len(index)}")
        if not index:
            self.stderr.write(self.style.ERROR(
                "La API no devolvió fixtures (¿league/season correctos?)."))
            return

        tol = timedelta(minutes=options["tolerance"])

        # Índice de fixtures por timestamp UTC redondeado al minuto.
        by_minute: dict = {}
        for f in index:
            key = f.kickoff.replace(second=0, microsecond=0)
            by_minute.setdefault(key, []).append(f)

        matches = list(Match.objects.select_related("home_team", "away_team").all())
        fixture_assignments = {}   # match.id -> api_fixture_id
        team_assignments = {}      # team.id -> api_team_id
        team_conflicts = []        # (team, id_a, id_b)
        unmatched = []             # Match sin fixture

        def candidates_for(match):
            """Fixtures de la API dentro de la tolerancia de fecha del match."""
            ko = match.kickoff.replace(second=0, microsecond=0)
            if not tol:
                return list(by_minute.get(ko, []))
            out = []
            for f in index:
                if abs(f.kickoff - match.kickoff) <= tol:
                    out.append(f)
            return out

        def record_team(team, api_id):
            if not team or not api_id:
                return
            prev = team_assignments.get(team.id)
            if prev and prev != api_id:
                team_conflicts.append((team, prev, api_id))
                return
            team_assignments[team.id] = api_id

        # Mapa nombre-API-normalizado -> Team local (solo los que el diccionario
        # reconoce). Permite detectar cuando un fixture emparejado por hora es en
        # realidad de OTRO partido (equipos contradictorios), sin bloquear los
        # nombres desconocidos.
        all_teams = list(Team.objects.all())
        name_to_team = {}
        for f in index:
            for nm in (f.home_name, f.away_name):
                nmn = _norm(nm)
                if not nmn or nmn in name_to_team:
                    continue
                for t in all_teams:
                    if _team_accepts(t, nmn):
                        name_to_team[nmn] = t
                        break

        for m in matches:
            cands = candidates_for(m)
            if not cands:
                unmatched.append(m)
                continue
            chosen = None
            if len(cands) == 1:
                chosen = cands[0]
            else:
                # Desempate por equipos (solo aplica a fase de grupos).
                for f in cands:
                    h_ok = m.home_team and _team_accepts(m.home_team, _norm(f.home_name))
                    a_ok = m.away_team and _team_accepts(m.away_team, _norm(f.away_name))
                    if h_ok and a_ok:
                        chosen = f
                        break
                if chosen is None:
                    # Sin desempate fiable: lo dejamos para revisión manual.
                    unmatched.append(m)
                    continue

            # Para partidos de grupo (con equipos definidos), rechazar el
            # emparejamiento si los nombres del fixture apuntan claramente a
            # OTROS equipos (p.ej. un partido desfasado que "roba" el slot
            # horario de un vecino). Se deja para la 2.ª pasada por par.
            if m.stage == Match.Stage.GROUP and m.home_team and m.away_team:
                fh = name_to_team.get(_norm(chosen.home_name))
                fa = name_to_team.get(_norm(chosen.away_name))
                known = {t.id for t in (fh, fa) if t}
                expected = {m.home_team_id, m.away_team_id}
                if known and not (known & expected):
                    unmatched.append(m)
                    continue
                fixture_assignments[m.id] = chosen.api_fixture_id
                # Orientación: si el "home" de la API es el away local (o el
                # away de la API es el home local), está invertido.
                inverted = (fh and fh.id == m.away_team_id) or \
                           (fa and fa.id == m.home_team_id)
                if inverted:
                    record_team(m.home_team, chosen.away_api_team_id)
                    record_team(m.away_team, chosen.home_api_team_id)
                else:
                    record_team(m.home_team, chosen.home_api_team_id)
                    record_team(m.away_team, chosen.away_api_team_id)
            else:
                fixture_assignments[m.id] = chosen.api_fixture_id

        # Refuerzo por nombre para selecciones sin derivar.
        api_team_by_name = {}
        for f in index:
            if f.home_api_team_id:
                api_team_by_name[_norm(f.home_name)] = f.home_api_team_id
            if f.away_api_team_id:
                api_team_by_name[_norm(f.away_name)] = f.away_api_team_id
        for team in Team.objects.all():
            if team.id in team_assignments:
                continue
            for name_norm, api_id in api_team_by_name.items():
                if _team_accepts(team, name_norm):
                    team_assignments[team.id] = api_id
                    break

        # 2.ª pasada: partidos de GRUPO aún sin mapear, por par de equipos ya
        # conocidos (independiente de la hora; cubre kickoffs que difieren entre
        # el calendario sembrado y el de la API).
        fixtures_by_pair = {}
        for f in index:
            if f.home_api_team_id and f.away_api_team_id:
                fixtures_by_pair[frozenset((f.home_api_team_id, f.away_api_team_id))] = f
        still_unmatched = []
        for m in unmatched:
            if m.stage == Match.Stage.GROUP and m.home_team and m.away_team:
                a = team_assignments.get(m.home_team.id)
                b = team_assignments.get(m.away_team.id)
                if a and b and a != b:
                    f = fixtures_by_pair.get(frozenset((a, b)))
                    if f and m.id not in fixture_assignments:
                        fixture_assignments[m.id] = f.api_fixture_id
                        continue
            still_unmatched.append(m)
        unmatched = still_unmatched

        # ── Reporte ──────────────────────────────────────────────────────────
        teams_by_id = {t.id: t for t in Team.objects.all()}
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nPartidos mapeados: {len(fixture_assignments)}/{len(matches)}"))
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Selecciones mapeadas: {len(team_assignments)}/{Team.objects.count()}"))

        # Separar lo esperado (eliminatorias que la API aún no publica) de lo
        # que sí es un problema (partidos de grupo sin emparejar).
        group_unmatched = [m for m in unmatched if m.stage == Match.Stage.GROUP]
        ko_unmatched = [m for m in unmatched if m.stage != Match.Stage.GROUP]

        if group_unmatched:
            self.stdout.write(self.style.ERROR(
                f"\n{len(group_unmatched)} partidos de GRUPO sin fixture (revisar):"))
            for m in group_unmatched:
                h = m.home_team.code if m.home_team else (m.home_placeholder or "?")
                a = m.away_team.code if m.away_team else (m.away_placeholder or "?")
                self.stdout.write(f"  #{m.number:3} {m.kickoff:%Y-%m-%d %H:%M} {h} vs {a}")

        if ko_unmatched:
            self.stdout.write(self.style.NOTICE(
                f"\n{len(ko_unmatched)} partidos de ELIMINATORIAS sin fixture "
                f"(NORMAL: la API los publica cuando se definen los cruces; "
                f"re-corré este comando al avanzar el torneo)."))

        missing_teams = [t for t in teams_by_id.values() if t.id not in team_assignments]
        if missing_teams:
            self.stdout.write(self.style.WARNING(
                f"\n{len(missing_teams)} selecciones SIN api_team_id "
                f"(no aparecen aún en fixtures con equipos definidos):"))
            self.stdout.write("  " + ", ".join(t.code for t in missing_teams))

        if team_conflicts:
            self.stdout.write(self.style.ERROR(
                f"\n{len(team_conflicts)} CONFLICTOS de equipo (revisar):"))
            for team, a, b in team_conflicts:
                self.stdout.write(f"  {team.code}: {a} vs {b}")

        if not options["apply"]:
            self.stdout.write(self.style.NOTICE(
                "\n[DRY-RUN] No se escribió nada. Repetí con --apply para persistir."))
            return

        with transaction.atomic():
            updated_m = 0
            for m in matches:
                api_id = fixture_assignments.get(m.id)
                if api_id and m.api_fixture_id != api_id:
                    m.api_fixture_id = api_id
                    m.save(update_fields=["api_fixture_id"])
                    updated_m += 1
            updated_t = 0
            for team in Team.objects.all():
                api_id = team_assignments.get(team.id)
                if api_id and team.api_team_id != api_id:
                    team.api_team_id = api_id
                    team.save(update_fields=["api_team_id"])
                    updated_t += 1
        self.stdout.write(self.style.SUCCESS(
            f"\nGuardado: {updated_m} partidos y {updated_t} selecciones."))
