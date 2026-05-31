"""Sincroniza resultados/posiciones y recalcula puntajes de la Polla.

Pensado para correr periodicamente (cron de Railway), p.ej. cada 5 minutos
durante el torneo:

    python manage.py polla_sync

Flujo:
  1. Si hay ``API_FOOTBALL_KEY`` configurada, trae el estado de los partidos
     desde API-Football y lo aplica (mapeo por ``api_fixture_id``).
  2. Actualiza la "forma reciente" de cada selección desde sus partidos finales.
  3. Recalcula pronósticos, menciones, misiones y la tabla de posiciones.

Opciones:
  --no-fetch   Solo recalcula (no llama a la API externa).
"""
from django.core.management.base import BaseCommand

from apps.polla.models import Match, Team
from apps.polla.providers import get_provider
from apps.polla.scoring import recompute_all


class Command(BaseCommand):
    help = "Sincroniza resultados y recalcula puntajes de la Polla."

    def add_arguments(self, parser):
        parser.add_argument("--no-fetch", action="store_true",
                            help="No llamar a la API externa; solo recalcular.")

    def handle(self, *args, **options):
        if not options["no_fetch"]:
            self._fetch_live()
        self._update_recent_form()
        recompute_all()
        self.stdout.write(self.style.SUCCESS("Sync de la Polla completado."))

    def _fetch_live(self):
        provider = get_provider()
        if not provider.available:
            self.stdout.write("  Sin API_FOOTBALL_KEY: se omite la traída en vivo.")
            return
        try:
            updates = provider.fetch_fixtures()
        except Exception as exc:  # noqa: BLE001 - no romper el cron por la API
            self.stderr.write(f"  Error trayendo fixtures: {exc}")
            return

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

            m.save(update_fields=fields)
            changed += 1
        self.stdout.write(f"  Partidos actualizados desde API-Football: {changed}")

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
