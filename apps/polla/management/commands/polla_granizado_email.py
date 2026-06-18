"""Avisa por correo a los GANADORES del granizado (marcador exacto de Colombia).

El correo felicita al ganador, le dice hasta cuándo puede reclamar su granizado
(la ventana de 24 h del premio), recuerda el próximo partido de Colombia como
otra oportunidad e invita a seguir jugando la Polla. Solo se envía a premios
PENDIENTES (no entregados y no vencidos): a un ganador que ya reclamó no se le
escribe.

Como el premio caduca pronto, conviene enviarlo el mismo día del partido.

Ejemplos:
    # Ver a quién le llegaría y una vista previa, SIN enviar nada
    python manage.py polla_granizado_email --dry-run

    # Muestra de diseño/entregabilidad a CUALQUIER correo (aunque no sea ganador)
    python manage.py polla_granizado_email --preview tucorreo@gmail.com

    # Prueba real solo a un ganador
    python manage.py polla_granizado_email --only ganador@gmail.com --yes

    # Solo los ganadores de un partido concreto (N.º de partido)
    python manage.py polla_granizado_email --match 50

    # Envío real a todos los ganadores con premio por reclamar (pide confirmación)
    python manage.py polla_granizado_email

Opciones:
    --match N         solo ganadores del partido con ese N.º
    --ignore-opt-out  enviar también a quien se dio de baja (es un premio que ganó)
    --limit N         tope de destinatarios (seguridad)
    --sleep S         pausa entre envíos (def. 0.6s; respeta el rate limit)
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.polla import granizado_emails as ge
from apps.polla.mailer import EmailError, send_email

_YES = {"si", "sí", "s", "yes", "y"}


class Command(BaseCommand):
    help = "Avisa por correo a los ganadores del granizado (marcador exacto de Colombia)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="No envía: muestra destinatarios y una vista previa del correo.",
        )
        parser.add_argument(
            "--only", metavar="EMAIL",
            help="Enviar solo al ganador con ese correo (prueba real).",
        )
        parser.add_argument(
            "--preview", metavar="EMAIL",
            help="Enviar UN correo de muestra a esa dirección (no tiene que ser "
                 "ganador). Usa los datos del primer ganador pendiente, o un "
                 "ejemplo si no hay ninguno; sirve para revisar diseño y spam.",
        )
        parser.add_argument(
            "--match", type=int, default=0, metavar="N",
            help="Solo ganadores del partido con ese N.º de partido.",
        )
        parser.add_argument(
            "--ignore-opt-out", action="store_true",
            help="Enviar también a ganadores que se dieron de baja de los correos.",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Máximo de destinatarios (0 = sin tope).",
        )
        parser.add_argument(
            "--sleep", type=float, default=0.6,
            help="Pausa en segundos entre envíos (rate limit de Resend).",
        )
        parser.add_argument(
            "--yes", action="store_true",
            help="No pedir confirmación interactiva antes del envío real.",
        )

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        if not dry and not settings.RESEND_API_KEY:
            raise CommandError(
                "RESEND_API_KEY no está configurada. Defínela en el entorno o usa --dry-run."
            )

        shared = ge.gather()

        if opts.get("preview"):
            self._send_preview(opts["preview"], shared)
            return

        match_number = opts["match"] or None
        winners = list(ge.pending_winners(match_number=match_number, only_email=opts.get("only")))
        if opts.get("only") and not winners:
            raise CommandError(
                f"No hay ganador con premio por reclamar y correo {opts['only']} "
                "(debe tener un granizado pendiente, no vencido ni entregado)."
            )

        plan = []
        skipped_optout = 0
        for reward in winners:
            if not opts["ignore_opt_out"] and reward.user.email_opt_out:
                skipped_optout += 1
                continue
            ctx = ge.build_context(reward, shared)
            plan.append((reward, ctx))
            if opts["limit"] and len(plan) >= opts["limit"]:
                break

        if not plan:
            msg = "No hay ganadores con premio por reclamar."
            if skipped_optout:
                msg += f" ({skipped_optout} omitido(s) por baja; usa --ignore-opt-out para incluirlos.)"
            self.stdout.write(self.style.WARNING(msg))
            return

        self.stdout.write("")
        self.stdout.write(f"Remitente   : {settings.POLLA_EMAIL_FROM}")
        self.stdout.write(f"Reply-To    : {settings.POLLA_EMAIL_REPLY_TO}")
        self.stdout.write(f"Reclamo URL : {ge.claim_url()}")
        self.stdout.write(f"Ganadores   : {len(plan)}")
        if skipped_optout:
            self.stdout.write(
                self.style.WARNING(f"Omitidos por baja: {skipped_optout} (usa --ignore-opt-out para incluirlos)")
            )
        self.stdout.write("")

        if dry:
            self._preview(plan)
            return

        if not opts["yes"]:
            answer = input(
                f"Vas a enviar {len(plan)} correos REALES a ganadores desde "
                f"{settings.POLLA_EMAIL_FROM}. ¿Continuar? [si/no]: "
            )
            if answer.strip().lower() not in _YES:
                self.stdout.write(self.style.WARNING("Cancelado."))
                return

        self._send(plan, opts["sleep"])

    # ── helpers ──────────────────────────────────────────────────────────
    def _send_preview(self, to_email, shared):
        """Envía un único correo de muestra a to_email (datos reales o de ejemplo)."""
        reward = ge.pending_winners().first()
        if reward:
            ctx = ge.build_context(reward, shared)
        else:
            ctx = self._sample_context(shared)
        subject, html, text = ge.render_winner(ctx)
        try:
            msg_id = send_email(
                to=to_email,
                subject=f"[PRUEBA] {subject}",
                html=html,
                text=text,
                tags=[{"name": "campaign", "value": "polla_granizado_preview"}],
            )
        except EmailError as exc:
            raise CommandError(f"No se pudo enviar la muestra: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Muestra enviada a {to_email}  {msg_id}"))

    def _sample_context(self, shared):
        """Contexto de ejemplo para previsualizar el diseño sin datos reales."""
        from apps.polla.email_reminders import _fmt_dt, _fmt_time, _match_row

        next_col = shared["next_colombia"]
        return {
            "name": "Jaime",
            "code": "GRZ-DEMO",
            "home": "Uzbekistán",
            "away": "Colombia",
            "rival": "Uzbekistán",
            "home_score": 1,
            "away_score": 2,
            "has_score": True,
            "deadline": _fmt_dt(shared["now"]),
            "deadline_time": _fmt_time(shared["now"]),
            "deadline_today": True,
            "hours_left": 12,
            "next_colombia": _match_row(next_col) if next_col else None,
            "claim_url": ge.claim_url(),
            "polla_url": settings.POLLA_PUBLIC_URL,
            "unsub_url": settings.POLLA_PUBLIC_URL,
            "reply_to": settings.POLLA_EMAIL_REPLY_TO,
            "brand_site": settings.SITE_URL,
            "year": shared["now"].year,
        }

    def _preview(self, plan):
        for reward, ctx in plan[:50]:
            score = f"{ctx['home_score']}-{ctx['away_score']}" if ctx["has_score"] else "?"
            self.stdout.write(
                f"  - {reward.user.email:<34} {ctx['code'] or '(sin código)':<10} · "
                f"{ctx['home']} {score} {ctx['away']} · vence {ctx['deadline']}"
            )
        if len(plan) > 50:
            self.stdout.write(f"  … y {len(plan) - 50} más")

        subject, _html, text = ge.render_winner(plan[0][1])
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Asunto de ejemplo: {subject}"))
        self.stdout.write("-" * 60)
        self.stdout.write(text)
        self.stdout.write("-" * 60)
        self.stdout.write(self.style.SUCCESS("DRY-RUN: no se envió ningún correo."))

    def _send(self, plan, sleep):
        # Lista pequeña (solo quienes clavaron el marcador): sin cabecera
        # List-Unsubscribe (señal de envío masivo), igual que el recordatorio.
        # El enlace de baja queda visible en el pie del correo.
        sent = failed = 0
        total = len(plan)
        for i, (reward, ctx) in enumerate(plan, start=1):
            subject, html, text = ge.render_winner(ctx)
            try:
                msg_id = send_email(
                    to=reward.user.email,
                    subject=subject,
                    html=html,
                    text=text,
                    tags=[{"name": "campaign", "value": "polla_granizado_winner"}],
                )
                sent += 1
                self.stdout.write(self.style.SUCCESS(f"[{i}/{total}] OK  {reward.user.email}  {msg_id}"))
            except EmailError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"[{i}/{total}] ERR {reward.user.email}: {exc}"))
            if sleep and i < total:
                time.sleep(sleep)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Enviados: {sent}  ·  Fallidos: {failed}  ·  Total: {total}")
        )
