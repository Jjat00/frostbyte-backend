"""Envía el recordatorio de MENCIONES de la Polla (vía Resend).

Avisa que las menciones (campeón, subcampeón, goleador, MVP, guante de oro) se
pueden elegir y cambiar solo hasta que empiecen las eliminatorias. El correo es
deliberadamente plano y personal (texto, sin imágenes ni botones) para caer en
la pestaña Principal de Gmail y no en Promociones.

Ejemplos:
    # Ver a quién le llegaría y una vista previa, SIN enviar nada
    python manage.py polla_menciones_reminder --dry-run

    # Muestra de diseño/entregabilidad a CUALQUIER correo (con la imagen)
    python manage.py polla_menciones_reminder --preview tucorreo@gmail.com

    # Prueba real solo a tu correo (debes ser cliente registrado en la Polla)
    python manage.py polla_menciones_reminder --only tucorreo@gmail.com --yes

    # Envío masivo real (pide confirmación; usa --yes para omitirla)
    python manage.py polla_menciones_reminder

Filtros y opciones:
    --missing-only    solo a quienes les faltan menciones por elegir
    --limit N         tope de destinatarios (seguridad)
    --sleep S         pausa entre envíos (def. 0.6s; respeta el rate limit)
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.polla import email_reminders as er
from apps.polla.mailer import EmailError, send_email

_YES = {"si", "sí", "s", "yes", "y"}


class Command(BaseCommand):
    help = "Envía el recordatorio de menciones de la Polla (con imagen embebida)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="No envía: muestra destinatarios y una vista previa del correo.",
        )
        parser.add_argument(
            "--only", metavar="EMAIL",
            help="Enviar solo al participante con ese correo (prueba real).",
        )
        parser.add_argument(
            "--preview", metavar="EMAIL",
            help="Enviar UN correo de muestra a esa dirección (no tiene que ser "
                 "participante). Usa los datos del primer participante.",
        )
        parser.add_argument(
            "--missing-only", action="store_true",
            help="Solo a quienes les faltan menciones por elegir.",
        )
        parser.add_argument(
            "--exclude", action="append", default=[], metavar="EMAIL",
            help="Excluir este correo del envío (se puede repetir).",
        )
        parser.add_argument(
            "--skip-joined-today", action="store_true",
            help="Excluir a quienes se registraron hoy (date_joined hoy, hora Colombia).",
        )
        parser.add_argument(
            "--skip-zero-points", action="store_true",
            help="Excluir a quienes tienen 0 puntos.",
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

        if opts.get("preview"):
            self._send_preview(opts["preview"])
            return

        shared = er.gather_menciones()
        recipients = list(er.eligible_recipients(only_email=opts.get("only")))
        if opts.get("only") and not recipients:
            raise CommandError(
                f"No hay participante con correo {opts['only']} "
                "(debe ser cliente, estar activo y no haberse dado de baja)."
            )

        exclude_emails = {e.strip().lower() for e in opts["exclude"]}
        skip_today = opts["skip_joined_today"]
        skip_zero = opts["skip_zero_points"]
        today = timezone.localdate()  # hoy en hora de Colombia (TIME_ZONE)

        plan = []
        skipped_email = []
        skipped_today = []
        skipped_zero = 0
        for score in recipients:
            user = score.user
            if (user.email or "").strip().lower() in exclude_emails:
                skipped_email.append(user.email)
                continue
            if skip_today and user.date_joined and timezone.localtime(user.date_joined).date() == today:
                skipped_today.append(user.email)
                continue
            if skip_zero and score.points == 0:
                skipped_zero += 1
                continue
            ctx = er.build_menciones_context(score, shared)
            if opts["missing_only"] and ctx["missing_count"] == 0:
                continue
            plan.append((score, ctx))
            if opts["limit"] and len(plan) >= opts["limit"]:
                break

        if not plan:
            self.stdout.write(self.style.WARNING("No hay destinatarios que cumplan los filtros."))
            return

        self.stdout.write("")
        self.stdout.write(f"Remitente   : {settings.POLLA_EMAIL_FROM}")
        self.stdout.write(f"Reply-To    : {settings.POLLA_EMAIL_REPLY_TO}")
        self.stdout.write(f"Polla URL   : {settings.POLLA_PUBLIC_URL}")
        self.stdout.write(f"Cierre      : {plan[0][1]['deadline_long'] or '(sin fecha)'}")
        if exclude_emails:
            self.stdout.write(
                f"Excluidos (correo): {len(skipped_email)} -> "
                f"{', '.join(skipped_email) or '(no estaban en la lista)'}"
            )
        if skip_today:
            self.stdout.write(f"Excluidos (entraron hoy {today}): {len(skipped_today)}")
            for e in skipped_today[:60]:
                self.stdout.write(f"    · {e}")
        if skip_zero:
            self.stdout.write(f"Excluidos (0 puntos): {skipped_zero}")
        self.stdout.write(f"Destinatarios: {len(plan)}")
        self.stdout.write("")

        if dry:
            self._preview(plan)
            return

        if not opts["yes"]:
            answer = input(
                f"Vas a enviar {len(plan)} correos REALES desde "
                f"{settings.POLLA_EMAIL_FROM}. ¿Continuar? [si/no]: "
            )
            if answer.strip().lower() not in _YES:
                self.stdout.write(self.style.WARNING("Cancelado."))
                return

        self._send(plan, opts["sleep"])

    # ── helpers ──────────────────────────────────────────────────────────
    def _send_preview(self, to_email):
        shared = er.gather_menciones()
        score = er.eligible_recipients().first()
        if not score:
            raise CommandError("No hay ningún participante para tomar de muestra.")
        ctx = er.build_menciones_context(score, shared)
        subject, html, text = er.render_menciones(ctx)
        try:
            msg_id = send_email(
                to=to_email,
                subject=f"[PRUEBA] {subject}",
                html=html,
                text=text,
                tags=[{"name": "campaign", "value": "polla_menciones_preview"}],
            )
        except EmailError as exc:
            raise CommandError(f"No se pudo enviar la muestra: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Muestra enviada a {to_email}  {msg_id}"))

    def _preview(self, plan):
        for score, ctx in plan[:50]:
            self.stdout.write(
                f"  - {score.user.email:<34} {ctx['set_count']}/{ctx['total_awards']} menciones · "
                f"faltan {ctx['missing_count']}"
            )
        if len(plan) > 50:
            self.stdout.write(f"  … y {len(plan) - 50} más")

        subject, _html, text = er.render_menciones(plan[0][1])
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Asunto de ejemplo: {subject}"))
        self.stdout.write("-" * 60)
        self.stdout.write(text)
        self.stdout.write("-" * 60)
        self.stdout.write(self.style.SUCCESS("DRY-RUN: no se envió ningún correo."))

    def _send(self, plan, sleep):
        sent = failed = 0
        total = len(plan)
        for i, (score, ctx) in enumerate(plan, start=1):
            subject, html, text = er.render_menciones(ctx)
            try:
                msg_id = send_email(
                    to=score.user.email,
                    subject=subject,
                    html=html,
                    text=text,
                    tags=[{"name": "campaign", "value": "polla_menciones"}],
                )
                sent += 1
                self.stdout.write(self.style.SUCCESS(f"[{i}/{total}] OK  {score.user.email}  {msg_id}"))
            except EmailError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"[{i}/{total}] ERR {score.user.email}: {exc}"))
            if sleep and i < total:
                time.sleep(sleep)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Enviados: {sent}  ·  Fallidos: {failed}  ·  Total: {total}")
        )
