"""Sincroniza resultados/posiciones y recalcula puntajes de la Polla.

Pensado para correr periodicamente (cron de Railway) CADA MINUTO durante el
torneo:

    python manage.py polla_sync

El comando es adaptativo: solo llama a la API externa si hay partidos en vivo
(o por arrancar / recién arrancados), o si pasaron >= 5 minutos desde el último
sync base (marca en cache, Redis en producción). Fuera de esas condiciones
termina sin hacer nada, así correr cada minuto no multiplica el consumo de la
API.

Flujo (cuando sí sincroniza):
  1. Si hay ``API_FOOTBALL_KEY`` configurada, trae el estado de los partidos
     desde API-Football y lo aplica (mapeo por ``api_fixture_id``).
  2. Actualiza la "forma reciente" de cada selección desde sus partidos finales.
  3. Recalcula pronósticos, menciones, misiones y la tabla de posiciones.
  4. Si hubo cambios, emite la señal WebSocket ``polla_changed`` para que los
     clientes conectados refresquen sus datos al instante.

Opciones:
  --no-fetch   Solo recalcula (no llama a la API externa).
  --force      Sincroniza aunque no haya partidos en vivo ni sync pendiente.
"""
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.polla.models import Match, Player, Team, TopScorer
from apps.polla.providers import get_provider
from apps.polla.scoring import recompute_all

# Marca (timestamp) del último fetch a la API. En producción vive en Redis,
# compartido entre ejecuciones del servicio cron.
SYNC_CACHE_KEY = "polla:last_fetch_ts"
# Intervalo del sync base cuando no hay partidos en vivo.
BASE_INTERVAL_SECONDS = 5 * 60
# Goleadores: cambian solo con goles; refrescarlos cada 30 min alcanza.
TOPSCORERS_CACHE_KEY = "polla:topscorers_ts"
TOPSCORERS_INTERVAL_SECONDS = 30 * 60
# Detalle (eventos/estadisticas) de partidos en vivo: a lo sumo cada 60 s.
DETAILS_CACHE_KEY = "polla:details_ts"
DETAILS_INTERVAL_SECONDS = 60


class Command(BaseCommand):
    help = "Sincroniza resultados y recalcula puntajes de la Polla."

    def add_arguments(self, parser):
        parser.add_argument("--no-fetch", action="store_true",
                            help="No llamar a la API externa; solo recalcular.")
        parser.add_argument("--force", action="store_true",
                            help="Sincronizar aunque no haya partidos en vivo "
                                 "ni sync base pendiente.")

    def handle(self, *args, **options):
        changed = 0
        if not options["no_fetch"]:
            if not options["force"] and not self._should_fetch():
                self.stdout.write(
                    "Sin partidos en vivo ni sync base pendiente; nada que hacer."
                )
                return
            provider = get_provider()
            changed = self._fetch_live(provider)
            # Descubre cruces de eliminatoria que la API recién publicó (reusa la
            # misma llamada /fixtures gracias al cache del provider).
            self._automap_fixtures(provider)
            changed += self._sync_match_details(provider)
            changed += self._sync_top_scorers(provider)
        self._update_recent_form()
        recompute_all()
        # Con --no-fetch (recalculo manual tras editar en el admin) también
        # avisamos: los clientes no tienen otra forma de enterarse.
        if changed or options["no_fetch"]:
            self._broadcast()
        self.stdout.write(self.style.SUCCESS("Sync de la Polla completado."))

    def _should_fetch(self):
        """Decide si vale la pena llamar a la API en esta corrida.

        Sí cuando hay partidos en vivo, partidos por arrancar en los próximos
        10 min, o partidos cuyo kickoff ya pasó pero siguen como ``upcoming``
        en nuestra BD (estado atrasado). Si no, solo cuando el sync base
        (cada 5 min) está vencido.
        """
        now = timezone.now()
        live_or_imminent = (
            Match.objects.filter(status=Match.Status.LIVE).exists()
            or Match.objects.filter(
                status=Match.Status.UPCOMING,
                kickoff__lte=now + timedelta(minutes=10),
                kickoff__gte=now - timedelta(hours=3),
            ).exists()
        )
        if live_or_imminent:
            return True
        last = cache.get(SYNC_CACHE_KEY)
        return last is None or (now.timestamp() - last) >= BASE_INTERVAL_SECONDS

    def _broadcast(self):
        """Señal WS para que los clientes refresquen. Nunca rompe el cron."""
        try:
            from apps.polla.consumers import broadcast_polla_update
            broadcast_polla_update()
            self.stdout.write("  Señal polla_changed emitida a los clientes.")
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(f"  No se pudo emitir la señal WS: {exc}")

    def _automap_fixtures(self, provider):
        """Mapea cruces de eliminatoria que la API publica al definirse.

        Solo actúa sobre partidos sin ``api_fixture_id`` que NO son de fase de
        grupos (esos ya se mapearon con ``polla_map_api``). Empareja por fecha
        exacta (UTC) con exclusión mutua. Si dos cruces caen al mismo minuto sin
        forma de desambiguar, se omiten (raro en eliminatorias) y se reportan.
        """
        from datetime import timezone as _tz

        pending = list(
            Match.objects.filter(api_fixture_id__isnull=True)
            .exclude(stage=Match.Stage.GROUP)
        )
        if not provider.available or not pending:
            return 0
        try:
            index = provider.fetch_fixture_index()
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(f"  Error en auto-mapeo de eliminatorias: {exc}")
            return 0

        taken = set(
            Match.objects.exclude(api_fixture_id=None)
            .values_list("api_fixture_id", flat=True)
        )
        by_minute = {}
        for f in index:
            if not f.api_fixture_id or not f.kickoff or f.api_fixture_id in taken:
                continue
            key = f.kickoff.astimezone(_tz.utc).replace(second=0, microsecond=0)
            by_minute.setdefault(key, []).append(f)

        mapped = 0
        for m in pending:
            key = m.kickoff.astimezone(_tz.utc).replace(second=0, microsecond=0)
            cands = [f for f in by_minute.get(key, []) if f.api_fixture_id not in taken]
            if len(cands) == 1:
                m.api_fixture_id = cands[0].api_fixture_id
                m.save(update_fields=["api_fixture_id"])
                taken.add(cands[0].api_fixture_id)
                mapped += 1
        if mapped:
            self.stdout.write(
                f"  Auto-mapeados {mapped} cruces de eliminatoria nuevos.")
        return mapped

    def _fetch_live(self, provider):
        # Marca el intento ANTES de llamar: sin key o con la API caída tampoco
        # queremos reintentar el pase completo cada minuto.
        cache.set(SYNC_CACHE_KEY, timezone.now().timestamp(), timeout=None)
        if not provider.available:
            self.stdout.write("  Sin API_FOOTBALL_KEY: se omite la traída en vivo.")
            return 0
        try:
            updates = provider.fetch_fixtures()
        except Exception as exc:  # noqa: BLE001 - no romper el cron por la API
            self.stderr.write(f"  Error trayendo fixtures: {exc}")
            return 0

        by_api_id = {m.api_fixture_id: m for m in Match.objects.exclude(api_fixture_id=None)}
        # Mapeo de IDs de API-Football -> selección (si el admin los completó).
        by_api_team = {
            t.api_team_id: t for t in Team.objects.exclude(api_team_id=None)
        }
        changed = 0
        for u in updates:
            m = by_api_id.get(u.api_fixture_id)
            if not m:
                continue
            before = (
                m.status, m.home_score, m.away_score, m.minute,
                m.home_team_id, m.away_team_id, m.winner_team_id,
            )
            fields = ["status", "home_score", "away_score", "minute", "updated_at"]
            m.status = u.status
            m.home_score = u.home_score
            m.away_score = u.away_score
            m.minute = u.minute if u.status == Match.Status.LIVE else None

            # Resolver equipos reales del cruce (clave en eliminatorias).
            home_t = by_api_team.get(u.home_api_team_id)
            away_t = by_api_team.get(u.away_api_team_id)
            if home_t:
                m.home_team = home_t
                fields.append("home_team")
            if away_t:
                m.away_team = away_t
                fields.append("away_team")

            # Quién avanzó (incluye penales). Solo si el cruce ya terminó.
            if u.status == Match.Status.FINISHED and u.winner in ("home", "away"):
                winner_t = m.home_team if u.winner == "home" else m.away_team
                if winner_t:
                    m.winner_team = winner_t
                    fields.append("winner_team")
            elif u.status != Match.Status.FINISHED and m.winner_team_id:
                m.winner_team = None
                fields.append("winner_team")

            after = (
                m.status, m.home_score, m.away_score, m.minute,
                m.home_team_id, m.away_team_id, m.winner_team_id,
            )
            if after == before:
                continue
            m.save(update_fields=fields)
            changed += 1
        self.stdout.write(f"  Partidos con cambios desde API-Football: {changed}")
        return changed

    def _sync_match_details(self, provider):
        """Eventos y estadisticas de los partidos en vivo (cada 60 s max).

        Tambien refresca los recien terminados (kickoff < 3 h) para capturar
        los eventos finales; despues de esa ventana quedan congelados.
        Una sola llamada por tanda de 20 fixtures (``/fixtures?ids=``).
        """
        if not provider.available:
            return 0
        now = timezone.now()
        targets = list(
            Match.objects.exclude(api_fixture_id=None).filter(
                Q(status=Match.Status.LIVE)
                | Q(status=Match.Status.FINISHED,
                    kickoff__gte=now - timedelta(hours=3))
            )
        )
        if not targets:
            return 0
        last = cache.get(DETAILS_CACHE_KEY)
        if last and (now.timestamp() - last) < DETAILS_INTERVAL_SECONDS:
            return 0
        cache.set(DETAILS_CACHE_KEY, now.timestamp(), timeout=None)
        try:
            details = provider.fetch_fixture_details(
                [m.api_fixture_id for m in targets])
        except Exception as exc:  # noqa: BLE001 - no romper el cron por la API
            self.stderr.write(f"  Error trayendo detalle de partidos: {exc}")
            return 0

        changed = 0
        for m in targets:
            d = details.get(m.api_fixture_id)
            if not d:
                continue
            if m.events != d["events"] or m.statistics != d["statistics"]:
                m.events = d["events"]
                m.statistics = d["statistics"]
                m.save(update_fields=["events", "statistics", "updated_at"])
                changed += 1
        if changed:
            self.stdout.write(
                f"  Partidos con eventos/estadisticas nuevos: {changed}")
        return changed

    def _sync_top_scorers(self, provider):
        """Refresca la tabla de goleadores (a lo sumo cada 30 min).

        Reemplaza la tabla completa solo si el contenido cambió, para que el
        broadcast WS no se dispare en vano. Antes del primer gol del torneo la
        API devuelve vacío y no se toca nada.
        """
        if not provider.available:
            return 0
        now_ts = timezone.now().timestamp()
        last = cache.get(TOPSCORERS_CACHE_KEY)
        if last and (now_ts - last) < TOPSCORERS_INTERVAL_SECONDS:
            return 0
        cache.set(TOPSCORERS_CACHE_KEY, now_ts, timeout=None)
        try:
            rows = provider.fetch_top_scorers(limit=20)
        except Exception as exc:  # noqa: BLE001 - no romper el cron por la API
            self.stderr.write(f"  Error trayendo goleadores: {exc}")
            return 0
        if not rows:
            return 0

        by_api_team = {t.api_team_id: t for t in Team.objects.exclude(api_team_id=None)}
        by_api_player = {
            p.api_player_id: p for p in Player.objects.exclude(api_player_id=None)
        }

        def local_player(row, team):
            """Enlaza al jugador sembrado: por id de API o por nombre+equipo."""
            p = by_api_player.get(row.api_player_id)
            if p or not team:
                return p
            p = Player.objects.filter(team=team, name__iexact=row.name).first()
            if p:
                return p
            # La API suele abreviar ("L. Díaz"): probar por apellido.
            last_name = row.name.split()[-1] if row.name else ""
            if len(last_name) >= 4:
                return Player.objects.filter(team=team, name__icontains=last_name).first()
            return None

        new_rows = []
        for i, row in enumerate(rows, start=1):
            team = by_api_team.get(row.api_team_id)
            new_rows.append(TopScorer(
                rank=i, name=row.name, goals=row.goals, assists=row.assists,
                appearances=row.appearances, photo_url=row.photo_url,
                api_player_id=row.api_player_id, team=team,
                player=local_player(row, team),
            ))

        fingerprint = [(r.rank, r.name, r.goals, r.assists) for r in new_rows]
        current = [
            tuple(c) for c in TopScorer.objects.order_by("rank")
            .values_list("rank", "name", "goals", "assists")
        ]
        if fingerprint == current:
            return 0
        TopScorer.objects.all().delete()
        TopScorer.objects.bulk_create(new_rows)
        self.stdout.write(f"  Goleadores actualizados: {len(new_rows)} filas.")
        return 1

    def _update_recent_form(self):
        """Calcula la forma reciente (últimos 5 finales) de cada selección."""
        for team in Team.objects.all():
            finished = (
                Match.objects.filter(status=Match.Status.FINISHED)
                .filter(home_team=team) | Match.objects.filter(
                    status=Match.Status.FINISHED, away_team=team
                )
            )
            finished = finished.filter(
                home_score__isnull=False, away_score__isnull=False
            ).order_by("-kickoff")[:5]

            form = []
            for m in reversed(list(finished)):
                if m.home_team_id == team.id:
                    gf, gc = m.home_score, m.away_score
                else:
                    gf, gc = m.away_score, m.home_score
                form.append("w" if gf > gc else "l" if gf < gc else "d")
            if team.recent_form != form:
                team.recent_form = form
                team.save(update_fields=["recent_form"])
