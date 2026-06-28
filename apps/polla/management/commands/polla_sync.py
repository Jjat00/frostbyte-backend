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
import unicodedata
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.polla.models import Match, Player, Team, TopScorer
from apps.polla.providers import get_provider
from apps.polla.scoring import recompute_all


def _norm(s):
    """minúsculas, sin acentos ni puntuación: para comparar nombres."""
    s = (s or "").replace("'", "").replace("’", "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace(".", " ").replace("-", " ").split())


def _match_player(name, team, players_by_team):
    """Empareja el nombre (abreviado, p.ej. 'R. Jiménez') de un evento de gol
    con un jugador sembrado del mismo equipo: por apellido, desempatando por la
    inicial del nombre. Devuelve el ``Player`` o None."""
    if not team:
        return None
    pool = players_by_team.get(team.id, [])
    if not pool:
        return None
    parts = _norm(name).split()
    if not parts:
        return None
    last = parts[-1]
    if len(last) < 3:
        return None
    cands = [p for p in pool if last in _norm(p.name).split()]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1 and parts[0]:
        ini = parts[0][0]
        narrowed = [p for p in cands if _norm(p.name)[:1] == ini]
        if len(narrowed) == 1:
            return narrowed[0]
    return cands[0] if cands else None

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
            changed += self._sync_head_to_head(provider)
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
        """Refresca la tabla de goleadores. Dos fuentes, por prioridad:

          1. Endpoint agregado ``/players/topscorers`` (a lo sumo cada 30 min):
             la fuente rica (asistencias, PJ, foto oficial). Pero API-Football
             tarda en consolidarlo, así que al inicio del torneo devuelve vacío
             aunque ya se hayan marcado goles.
          2. Los eventos de gol ya guardados en cada ``Match`` (casi en vivo):
             si el endpoint agregado todavía no trae nada, contamos los goles de
             los eventos para que los goleadores aparezcan apenas se juega.

        Reemplaza la tabla completa solo si el contenido cambió, para que el
        broadcast WS no se dispare en vano.
        """
        # Los eventos de los partidos son la fuente PRIMARIA (verdad de campo,
        # casi en vivo): el endpoint agregado de la API al inicio del torneo
        # devuelve un ranking inflado con jugadores de 0 goles, así que solo lo
        # usamos de respaldo si aún no tenemos eventos con goles. En cualquier
        # caso filtramos: a la tabla de GOLEADORES solo entra quien marcó.
        new_rows = self._scorers_from_events() or self._scorers_from_api(provider) or []
        new_rows = [r for r in new_rows if r.goals and r.goals > 0]
        if not new_rows:
            return 0

        # Ordena por goles (luego asistencias y nombre) y re-numera el rank.
        new_rows.sort(key=lambda r: (-r.goals, -r.assists, r.name))
        new_rows = new_rows[:30]
        for i, r in enumerate(new_rows, start=1):
            r.rank = i

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

    def _scorers_from_api(self, provider):
        """Goleadores desde el endpoint agregado. None si no toca (caché de 30
        min vigente), la API no está disponible, o aún devuelve vacío."""
        if not provider.available:
            return None
        now_ts = timezone.now().timestamp()
        last = cache.get(TOPSCORERS_CACHE_KEY)
        if last and (now_ts - last) < TOPSCORERS_INTERVAL_SECONDS:
            return None
        cache.set(TOPSCORERS_CACHE_KEY, now_ts, timeout=None)
        try:
            rows = provider.fetch_top_scorers(limit=30)
        except Exception as exc:  # noqa: BLE001 - no romper el cron por la API
            self.stderr.write(f"  Error trayendo goleadores: {exc}")
            return None
        if not rows:
            return None

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

        out = []
        for row in rows:
            team = by_api_team.get(row.api_team_id)
            out.append(TopScorer(
                rank=0, name=row.name, goals=row.goals, assists=row.assists,
                appearances=row.appearances, photo_url=row.photo_url,
                api_player_id=row.api_player_id, team=team,
                player=local_player(row, team),
            ))
        return out

    def _scorers_from_events(self):
        """Construye la tabla contando los goles de ``Match.events``.

        Excluye autogoles y penales fallados (no cuentan para el goleador).
        Enlaza cada goleador al jugador sembrado (por nombre+equipo) para la
        foto y el ``player_id``; conserva asistencias/PJ/foto de la fila previa
        cuando existan, para no degradarlas entre refrescos del endpoint
        agregado.

        IMPORTANTE: agrupamos por el JUGADOR resuelto, no por el nombre crudo
        del evento. La API escribe al mismo jugador con grafías distintas entre
        partidos (p.ej. "K. Mbappe" y "Kylian Mbappé"); si agrupáramos por el
        texto del evento, sus goles quedarían partidos en filas separadas. Por
        eso resolvemos el ``Player`` ANTES de acumular y usamos su id como clave
        (con fallback al nombre normalizado solo cuando no hay match).
        """
        matches = list(
            Match.objects.filter(
                status__in=[Match.Status.LIVE, Match.Status.FINISHED]
            )
            .exclude(home_team__isnull=True)
            .exclude(away_team__isnull=True)
            .select_related("home_team", "away_team")
        )
        if not matches:
            return []

        players_by_team = {}
        for p in Player.objects.all():
            players_by_team.setdefault(p.team_id, []).append(p)
        prev_by_player = {}
        prev_by_name = {}
        for ts in TopScorer.objects.all():
            if ts.player_id:
                prev_by_player[ts.player_id] = ts
            prev_by_name[_norm(ts.name)] = ts

        # clave -> {name, team, player, goals, assists}
        # clave = ("p", player_id) cuando se resuelve el jugador sembrado;
        #         ("n", nombre_norm, team_id) como respaldo si no hay match.
        tally = {}

        def slot(name, team):
            player = _match_player(name, team, players_by_team)
            if player is not None:
                key = ("p", player.id)
            else:
                key = ("n", _norm(name), team.id if team else None)
            acc = tally.get(key)
            if acc is None:
                acc = {
                    "name": player.name if player else name,
                    "team": team,
                    "player": player,
                    "goals": 0,
                    "assists": 0,
                }
                tally[key] = acc
            return acc

        for m in matches:
            for ev in m.events or []:
                if (ev.get("type") or "") != "goal":
                    continue
                detail = (ev.get("detail") or "").lower()
                if "own" in detail or "missed" in detail:
                    continue
                team = m.home_team if ev.get("side") == "home" else m.away_team
                scorer = (ev.get("player") or "").strip()
                if scorer:
                    slot(scorer, team)["goals"] += 1
                assist = (ev.get("assist") or "").strip()
                if assist:
                    slot(assist, team)["assists"] += 1

        out = []
        for acc in tally.values():
            # Tabla de GOLEADORES: quien solo asistió (0 goles) no entra.
            if acc["goals"] <= 0:
                continue
            player = acc["player"]
            old = (prev_by_player.get(player.id) if player else None) \
                or prev_by_name.get(_norm(acc["name"]))
            photo = (player.photo_url if player else "") or (old.photo_url if old else "")
            out.append(TopScorer(
                rank=0,
                name=(player.name if player else acc["name"]),
                goals=acc["goals"],
                assists=acc["assists"],
                appearances=(old.appearances if old else 0),
                photo_url=photo or "",
                api_player_id=(player.api_player_id if player else None),
                team=acc["team"],
                player=player,
            ))
        return out

    def _sync_head_to_head(self, provider, limit=6):
        """Historial (head-to-head) de los cruces que arrancan en < 48 h.

        Es UNA llamada por cruce y queda congelado (``h2h_fetched_at``): el
        historico no cambia. ``limit`` reparte el trabajo entre corridas; con
        4-6 partidos por dia se cubre todo en la primera pasada.
        """
        if not provider.available:
            return 0
        now = timezone.now()
        targets = list(
            Match.objects.filter(
                status=Match.Status.UPCOMING,
                h2h_fetched_at__isnull=True,
                kickoff__lte=now + timedelta(hours=48),
                home_team__api_team_id__isnull=False,
                away_team__api_team_id__isnull=False,
            ).select_related("home_team", "away_team")[:limit]
        )
        done = 0
        for m in targets:
            try:
                meetings = provider.fetch_head_to_head(
                    m.home_team.api_team_id, m.away_team.api_team_id)
            except Exception as exc:  # noqa: BLE001 - reintenta en otra corrida
                self.stderr.write(f"  Error trayendo H2H de {m.slug}: {exc}")
                continue

            home_id = m.home_team.api_team_id
            wins = {"home": 0, "away": 0, "draw": 0}
            items = []
            for g in meetings:
                hg, ag = g["home_goals"], g["away_goals"]
                if hg is None or ag is None:
                    continue
                winner_id = (
                    g["home_api_team_id"] if hg > ag
                    else g["away_api_team_id"] if ag > hg
                    else None
                )
                if winner_id is None:
                    wins["draw"] += 1
                elif winner_id == home_id:
                    wins["home"] += 1
                else:
                    wins["away"] += 1
                items.append({
                    "date": g["date"],
                    "competition": g["competition"],
                    "home_name": g["home_name"],
                    "away_name": g["away_name"],
                    "home_goals": hg,
                    "away_goals": ag,
                })
            items.sort(key=lambda x: x["date"], reverse=True)
            m.h2h = {
                "summary": {**wins, "total": sum(wins.values())},
                "meetings": items[:5],
            }
            m.h2h_fetched_at = now
            m.save(update_fields=["h2h", "h2h_fetched_at", "updated_at"])
            done += 1
        if done:
            self.stdout.write(f"  Historial (H2H) traido para {done} cruces.")
        return done

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
