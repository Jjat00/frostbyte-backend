"""Envía el correo recordatorio de la Polla a los participantes (vía Resend).

El correo incluye, personalizado por participante: puntaje y posición actuales,
partidos sin pronosticar, el próximo partido de Colombia con el gancho del
granizado gratis, las misiones pendientes y un botón directo a la Polla.

Ejemplos:
    # Ver a quién le llegaría y una vista previa, SIN enviar nada
    python manage.py polla_email_reminder --dry-run

    # Muestra de diseño/entregabilidad a CUALQUIER correo (aunque no sea participante)
    python manage.py polla_email_reminder --preview tucorreo@gmail.com

    # Prueba real solo a tu correo (debes ser cliente registrado en la Polla)
    python manage.py polla_email_reminder --only tucorreo@gmail.com --yes

    # A todos los que les falta algún pronóstico y YA tienen puntos (excluye 0 pts)
    python manage.py polla_email_reminder --only-pending --min-points 1

    # Envío masivo real (pide confirmación; usa --yes para omitirla)
    python manage.py polla_email_reminder

Filtros y opciones:
    --watch           copy del partido de HOY: invita a verlo en Frostbyte y a
                      dejar el marcador antes del pitazo
    --semis           copy de semifinales: "mañana empiezan las semifinales,
                      aún no hay nada escrito" con los puntos en juego
    --only-pending    solo a quienes tienen partidos sin pronosticar
    --min-points N    excluir a quienes tengan menos de N puntos (1 = omite los de 0)
    --colombia-only   solo a quienes no han pronosticado el próximo Colombia
    --exclude EMAIL   excluir ese correo del envío (repetible)
    --limit N         tope de destinatarios (seguridad)
    --sleep S         pausa entre envíos (def. 0.6s; respeta el rate limit)

Nota: "partidos sin pronosticar" solo cuenta los que ya tienen ambos equipos
definidos y aún no arrancan; en eliminatorias se van habilitando conforme se
juegan, así que los cruces futuros con cupos por definir no se cuentan.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.polla import email_reminders as er
from apps.polla.mailer import EmailError, send_email

_YES = {"si", "sí", "s", "yes", "y"}


class Command(BaseCommand):
    help = "Envía correos recordatorio de la Polla (pronósticos, puntaje, misiones, promo Colombia)."

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
                 "participante). Usa los datos del primer participante para revisar "
                 "diseño y entregabilidad/spam.",
        )
        parser.add_argument(
            "--watch", action="store_true",
            help="Copy del partido de hoy: invita a verlo en Frostbyte.",
        )
        parser.add_argument(
            "--semis", action="store_true",
            help="Copy de semifinales: mañana empiezan, aún no hay nada escrito.",
        )
        parser.add_argument(
            "--only-pending", action="store_true",
            help="Solo a quienes tienen partidos sin pronosticar.",
        )
        parser.add_argument(
            "--min-points", type=int, default=0,
            help="Excluir a quienes tengan menos de N puntos (1 = omite a los de 0 puntos).",
        )
        parser.add_argument(
            "--colombia-only", action="store_true",
            help="Solo a quienes no han pronosticado el próximo partido de Colombia.",
        )
        parser.add_argument(
            "--exclude", metavar="EMAIL", action="append", default=[],
            help="Excluir ese correo del envío. Se puede repetir.",
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
            self._send_preview(opts["preview"], semis=opts["semis"], watch=opts["watch"])
            return

        shared = er.gather()
        recipients = list(er.eligible_recipients(only_email=opts.get("only")))
        if opts.get("only") and not recipients:
            raise CommandError(
                f"No hay participante con correo {opts['only']} "
                "(debe ser cliente, estar activo y no haberse dado de baja)."
            )

        # Construir contextos y aplicar los filtros que dependen del usuario.
        excluded = {e.strip().lower() for e in opts["exclude"]}
        plan = []
        for score in recipients:
            if score.user.email.lower() in excluded:
                continue
            if opts["min_points"] and score.points < opts["min_points"]:
                continue
            ctx = er.build_context(score, shared, semis=opts["semis"], watch=opts["watch"])
            if opts["only_pending"] and ctx["pending_count"] == 0:
                continue
            if opts["colombia_only"] and not ctx["colombia_pending"]:
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
    def _send_preview(self, to_email, semis=False, watch=False):
        """Envía un único correo de muestra (datos del 1er participante) a to_email."""
        shared = er.gather()
        score = er.eligible_recipients().first()
        if not score:
            raise CommandError("No hay ningún participante para tomar de muestra.")
        ctx = er.build_context(score, shared, semis=semis, watch=watch)
        subject, html, text = er.render_reminder(ctx)
        try:
            msg_id = send_email(
                to=to_email,
                subject=f"[PRUEBA] {subject}",
                html=html,
                text=text,
                tags=[{"name": "campaign", "value": "polla_reminder_preview"}],
            )
        except EmailError as exc:
            raise CommandError(f"No se pudo enviar la muestra: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Muestra enviada a {to_email}  {msg_id}"))

    def _preview(self, plan):
        for score, ctx in plan[:50]:
            self.stdout.write(
                f"  - {score.user.email:<34} {ctx['points']:>4} pts · #{ctx['position'] or '-'} · "
                f"{ctx['pending_count']} sin pronosticar · {ctx['pending_missions_count']} misiones"
            )
        if len(plan) > 50:
            self.stdout.write(f"  … y {len(plan) - 50} más")

        subject, _html, text = er.render_reminder(plan[0][1])
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Asunto de ejemplo: {subject}"))
        self.stdout.write("-" * 60)
        self.stdout.write(text)
        self.stdout.write("-" * 60)
        self.stdout.write(self.style.SUCCESS("DRY-RUN: no se envió ningún correo."))

    def _send(self, plan, sleep):
        # No mandamos la cabecera List-Unsubscribe (señal de envío masivo que
        # empuja a la pestaña Promociones). La lista es pequeña (< 5k/día, no la
        # exigen las reglas de Gmail) y el enlace de baja queda visible en el pie
        # del correo, que sigue funcionando vía GET. Si algún día se envía a >5k
        # destinatarios, conviene reactivar `er.unsub_headers(ctx)`.
        sent = failed = 0
        total = len(plan)
        for i, (score, ctx) in enumerate(plan, start=1):
            subject, html, text = er.render_reminder(ctx)
            try:
                msg_id = send_email(
                    to=score.user.email,
                    subject=subject,
                    html=html,
                    text=text,
                    tags=[{"name": "campaign", "value": "polla_reminder"}],
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
