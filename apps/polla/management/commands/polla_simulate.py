"""Simula LOCALMENTE el cierre de la fase de grupos para previsualizar la llave.

Dos modos:

  * JUGABLE (por defecto): cierra los partidos de grupos que aun no se jugaron
    (respeta los ya finalizados), siembra los DIECISEISAVOS con los clasificados
    reales y los deja ABIERTOS. La eliminacion queda jugable: eliges quien avanza
    en cada cruce y tus picks van proyectando octavos -> final. Octavos en
    adelante NO traen equipos: dependen de tus pronosticos (asi es el producto).

  * ESPECTADOR (--full): ademas juega todas las rondas hasta el campeon. Sirve
    para ver "como se veria" un torneo terminado, pero deja todo finalizado (no
    se puede pronosticar). Usalo solo para mirar, no para jugar.

Pausa el loop de tiempo real mientras dure (crea ``.polla_sync_paused``) para
que ``polla_sync`` no pise la simulacion con los resultados reales. Todo es
REVERSIBLE: guarda un snapshot del estado original y ``--reset`` lo restaura y
reanuda el sync. Solo para la BD local (guarda anti-produccion).

    python manage.py polla_simulate            # abre la eliminacion JUGABLE
    python manage.py polla_simulate --full      # simula el torneo entero (mirar)
    python manage.py polla_simulate --reset      # restaura el estado real
    python manage.py polla_simulate --seed 7     # otra combinacion (reproducible)
"""
import json
import random
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.polla import bracket as bracketmod
from apps.polla.models import Match
from apps.polla.scoring import recompute_all

SNAP = Path(settings.BASE_DIR) / ".polla_sim_snapshot.json"
PAUSE = Path(settings.BASE_DIR) / ".polla_sync_paused"
FIELDS = ["status", "home_team_id", "away_team_id", "home_score", "away_score", "winner_team_id"]
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "db", "frostbyte-db", "postgres"}


class Command(BaseCommand):
    help = (
        "Simula localmente el cierre de grupos para previsualizar la eliminacion. "
        "Por defecto la deja JUGABLE; --full juega el torneo entero. Reversible "
        "con --reset. Solo BD local."
    )

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true",
                            help="Modo espectador: juega R32..Final (deja todo finalizado).")
        parser.add_argument("--reset", action="store_true",
                            help="Restaura el estado original y reanuda el sync.")
        parser.add_argument("--seed", type=int, default=2026,
                            help="Semilla del RNG (misma semilla => mismos marcadores).")
        parser.add_argument("--force", action="store_true",
                            help="Corre aunque la BD no parezca local (usar con cuidado).")

    # ── guardas ────────────────────────────────────────────────────────────
    def _guard_local(self, force):
        host = (settings.DATABASES["default"].get("HOST") or "").lower()
        if host not in _LOCAL_HOSTS and not force:
            raise CommandError(
                f"La BD no parece local (HOST={host!r}). Aborto para no tocar "
                f"produccion. Si de verdad queres correrlo, usa --force."
            )

    def _snapshot(self, matches):
        """Guarda el estado original UNA sola vez (no pisa un snapshot previo)."""
        if SNAP.exists():
            self.stdout.write("Snapshot previo encontrado; conservo el estado original.")
            return
        data = {str(m.number): {f: getattr(m, f) for f in FIELDS} for m in matches}
        SNAP.write_text(json.dumps(data))
        self.stdout.write(f"Snapshot guardado ({len(data)} partidos) -> {SNAP.name}")

    # ── entrada ──────────────────────────────────────────────────────────────
    @transaction.atomic
    def handle(self, *args, **opts):
        self._guard_local(opts["force"])
        if opts["reset"]:
            return self._reset()

        rng = random.Random(opts["seed"])
        group_pending = list(
            Match.objects.filter(stage=Match.Stage.GROUP).exclude(status=Match.Status.FINISHED)
        )
        knockout = list(Match.objects.exclude(stage=Match.Stage.GROUP))
        self._snapshot(group_pending + knockout)

        # pausa el sync para que el loop de tiempo real no pise la simulacion
        PAUSE.write_text("simulacion local activa")

        # 1) cerrar los grupos pendientes (los ya finished se respetan)
        closed = 0
        for m in group_pending:
            if m.home_team_id is None or m.away_team_id is None:
                continue
            m.home_score = rng.randint(0, 3)
            m.away_score = rng.randint(0, 3)
            m.status = Match.Status.FINISHED
            m.save(update_fields=["home_score", "away_score", "status"])
            closed += 1
        self.stdout.write(f"Grupos cerrados (simulados): {closed}")

        # 2) dejar la eliminacion LIMPIA y abierta: sin equipos ni resultados, en
        #    estado proximo (jugable). Asi se borra cualquier simulacion previa.
        Match.objects.exclude(stage=Match.Stage.GROUP).update(
            status=Match.Status.UPCOMING, home_team=None, away_team=None,
            home_score=None, away_score=None, winner_team=None,
        )

        # 3) sembrar SOLO los dieciseisavos con los clasificados reales
        seeded = bracketmod.advance_real_bracket()
        self.stdout.write(
            f"Dieciseisavos sembrados y abiertos: {seeded} cruces. is_open={bracketmod.is_open()}"
        )

        # 4) opcional: jugar el torneo entero (modo espectador)
        if opts["full"]:
            self._sim_knockout(rng)
            self.stdout.write(self.style.WARNING(
                "Modo --full: todo quedo FINALIZADO; no se puede pronosticar (solo mirar)."
            ))

        recompute_all()
        self._print_bracket()
        self.stdout.write(self.style.SUCCESS(
            "Listo. Reinicia el backend (daphne) UNA vez para cargar la pausa del "
            "loop y recarga el front. Revertir: python manage.py polla_simulate --reset"
        ))

    # ── simulacion de la fase eliminatoria (modo espectador) ─────────────────
    def _sim_knockout(self, rng):
        for stage in bracketmod.ROUND_ORDER:
            bracketmod.advance_real_bracket()  # propaga ganadores de la ronda previa
            ms = Match.objects.filter(
                stage=stage, home_team__isnull=False, away_team__isnull=False
            )
            for m in ms:
                if m.status == Match.Status.FINISHED:
                    continue
                hs, as_ = rng.randint(0, 3), rng.randint(0, 3)
                m.home_score, m.away_score = hs, as_
                if hs == as_:  # "penales": ganador determinista
                    m.winner_team = m.home_team if rng.random() < 0.5 else m.away_team
                m.status = Match.Status.FINISHED
                m.save(update_fields=["home_score", "away_score", "winner_team", "status"])
            bracketmod.advance_real_bracket()
        self.stdout.write("Eliminatorias simuladas hasta la final.")

    # ── reset ────────────────────────────────────────────────────────────────
    def _reset(self):
        PAUSE.unlink(missing_ok=True)  # reanuda el sync
        if not SNAP.exists():
            raise CommandError("No hay snapshot que restaurar (.polla_sim_snapshot.json).")
        data = json.loads(SNAP.read_text())
        nums = [int(k) for k in data]
        by_num = {m.number: m for m in Match.objects.filter(number__in=nums)}
        upd = []
        for num, fields in data.items():
            m = by_num.get(int(num))
            if m is None:
                continue
            for f, v in fields.items():
                setattr(m, f, v)
            upd.append(m)
        Match.objects.bulk_update(upd, FIELDS, batch_size=200)
        SNAP.unlink()
        recompute_all()
        self.stdout.write(self.style.SUCCESS(
            f"Restaurados {len(upd)} partidos al estado original. Sync reanudado."
        ))

    # ── reporte ──────────────────────────────────────────────────────────────
    def _print_bracket(self):
        label = {
            Match.Stage.R32: "Dieciseisavos", Match.Stage.R16: "Octavos",
            Match.Stage.QF: "Cuartos", Match.Stage.SF: "Semifinales",
            Match.Stage.THIRD: "Tercer puesto", Match.Stage.FINAL: "Final",
        }
        for stage in bracketmod.ROUND_ORDER:
            ms = Match.objects.filter(stage=stage).select_related(
                "home_team", "away_team", "winner_team").order_by("number")
            if not ms:
                continue
            self.stdout.write(self.style.HTTP_INFO(f"\n{label.get(stage, stage)}:"))
            for m in ms:
                h = m.home_team.name if m.home_team else (m.home_placeholder or "?")
                a = m.away_team.name if m.away_team else (m.away_placeholder or "?")
                if m.is_finished:
                    w = bracketmod.real_winner(m)
                    wn = f"  -> {w.name}" if w else ""
                    self.stdout.write(f"  #{m.number}: {h} {m.home_score}-{m.away_score} {a}{wn}")
                else:
                    self.stdout.write(f"  #{m.number}: {h} vs {a}")
