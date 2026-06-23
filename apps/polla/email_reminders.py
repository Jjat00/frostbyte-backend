"""Construcción de los correos recordatorio de la Polla.

Arma un contexto personalizado por participante (puntaje, posición, partidos
sin pronosticar, próximo partido de Colombia con el gancho del granizado y
misiones pendientes) y renderiza las plantillas. El envío real lo hace
``mailer.send_email`` y la orquestación el comando ``polla_email_reminder``.

El enlace de baja usa ``django.core.signing`` (firmado con SECRET_KEY): no hace
falta guardar tokens en la BD ni que caduquen. La vista de baja vive en
``email_views.email_unsubscribe``.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Match, Mission, Prediction, UserMission, UserScore

User = get_user_model()

COLOMBIA_CODE = "COL"
UNSUB_SALT = "polla-email-unsub"
MAX_MATCHES_SHOWN = 4
MAX_MISSIONS_SHOWN = 5
MAX_TODAY_SHOWN = 8

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                 "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ── Helpers ────────────────────────────────────────────────────────────────
def _first_name(user):
    """Primer nombre para el saludo; cadena vacía si no se conoce."""
    full = (user.get_full_name() or "").strip()
    if full:
        return full.split()[0]
    if (user.first_name or "").strip():
        return user.first_name.strip().split()[0]
    base = (user.username or "").split("@")[0].strip()
    # Evita saludar con algo tipo "user_8f3a" o un correo: mejor sin nombre.
    return "" if (not base or "_" in base or base.isdigit()) else base


def _fmt_dt(dt):
    """Fecha y hora en español, hora de Colombia (p.ej. 'Sáb 14 jun · 5:00 p. m.')."""
    loc = timezone.localtime(dt)
    dia = _DIAS[loc.weekday()].capitalize()[:3]
    return f"{dia} {loc.day} {_MESES[loc.month - 1]} · {_fmt_time(dt)}"


def _fmt_time(dt):
    """Solo la hora en español, hora de Colombia (p.ej. '5:00 p. m.')."""
    loc = timezone.localtime(dt)
    hora = loc.strftime("%I:%M %p").lstrip("0")
    return hora.replace("AM", "a. m.").replace("PM", "p. m.")


def _fmt_today(dt):
    """Etiqueta de la fecha de hoy en español (p.ej. 'martes 23 de junio')."""
    loc = timezone.localtime(dt)
    return f"{_DIAS[loc.weekday()]} {loc.day} de {_MESES_LARGOS[loc.month - 1]}"


def _is_colombia(match):
    for team in (match.home_team, match.away_team):
        if team and team.code == COLOMBIA_CODE:
            return True
    return False


def _match_row(match):
    return {
        "home": match.home_team.name if match.home_team else (match.home_placeholder or "?"),
        "home_code": match.home_team.code if match.home_team else "",
        "away": match.away_team.name if match.away_team else (match.away_placeholder or "?"),
        "away_code": match.away_team.code if match.away_team else "",
        "when": _fmt_dt(match.kickoff),
        "time": _fmt_time(match.kickoff),
        "is_colombia": _is_colombia(match),
        "stage_label": match.get_stage_display(),
    }


def unsubscribe_token(user):
    return signing.dumps({"uid": user.id}, salt=UNSUB_SALT)


def unsubscribe_url(user):
    base = settings.BACKEND_PUBLIC_URL.rstrip("/")
    return f"{base}/api/v1/polla/email/unsubscribe/{unsubscribe_token(user)}/"


# ── Datos compartidos del envío (se calculan una sola vez) ──────────────────
def gather():
    """Datos comunes a todos los correos del envío (una sola lectura)."""
    now = timezone.now()
    upcoming = list(
        Match.objects.filter(
            status=Match.Status.UPCOMING,
            kickoff__gt=now,
            home_team__isnull=False,
            away_team__isnull=False,
        )
        .select_related("home_team", "away_team")
        .order_by("kickoff")
    )
    colombia_upcoming = [m for m in upcoming if _is_colombia(m)]
    # Partidos que se juegan HOY (en hora de Colombia), para recordarlos a todos
    # aunque ya estén pronosticados.
    today = timezone.localtime(now).date()
    today_matches = [m for m in upcoming if timezone.localtime(m.kickoff).date() == today]
    # Misiones que se completan pronosticando (la de invitar es social aparte).
    missions = list(Mission.objects.exclude(kind=Mission.Kind.INVITE))
    total = UserScore.objects.count()
    return {
        "now": now,
        "upcoming": upcoming,
        "colombia_upcoming": colombia_upcoming,
        "today": today,
        "today_matches": today_matches,
        "missions": missions,
        "total": total,
    }


def eligible_recipients(only_email=None):
    """Clientes activos, con correo y suscritos, ordenados por posición."""
    qs = (
        UserScore.objects.select_related("user")
        .filter(user__role=User.Role.CUSTOMER, user__is_active=True, user__email_opt_out=False)
        .exclude(user__email="")
        .exclude(user__email__isnull=True)
        .order_by("position", "user__email")
    )
    if only_email:
        qs = qs.filter(user__email__iexact=only_email.strip())
    return qs


def build_context(score, shared):
    """Contexto personalizado para el correo de un participante."""
    user = score.user
    predicted_ids = set(
        Prediction.objects.filter(user=user).values_list("match_id", flat=True)
    )
    pending = [m for m in shared["upcoming"] if m.id not in predicted_ids]

    colombia_upcoming = shared["colombia_upcoming"]
    next_colombia = colombia_upcoming[0] if colombia_upcoming else None
    colombia_pending = any(m.id not in predicted_ids for m in colombia_upcoming)
    # Partidos de hoy (compartidos), con marca de si este usuario ya los pronosticó.
    today = shared["today"]
    today_rows = []
    today_pending_count = 0
    for m in shared["today_matches"][:MAX_TODAY_SHOWN]:
        row = _match_row(m)
        row["is_predicted"] = m.id in predicted_ids
        if not row["is_predicted"]:
            today_pending_count += 1
        today_rows.append(row)
    # ¿El próximo partido de Colombia es HOY? (misma fecha en hora de Colombia
    # respecto al momento del envío). Hace que el correo diga "hoy juega Colombia".
    colombia_today = bool(
        next_colombia and timezone.localtime(next_colombia.kickoff).date() == today
    )
    colombia_today_pending = bool(
        colombia_today and next_colombia.id not in predicted_ids
    )

    done_mission_ids = set(
        UserMission.objects.filter(user=user, done=True).values_list("mission_id", flat=True)
    )
    pending_missions = [m for m in shared["missions"] if m.id not in done_mission_ids]

    return {
        "name": _first_name(user),
        "points": score.points,
        "position": score.position or 0,
        "total": shared["total"],
        "exact_hits": score.exact_hits,
        "predicted": score.predicted,
        "pending": [_match_row(m) for m in pending[:MAX_MATCHES_SHOWN]],
        "pending_count": len(pending),
        "pending_extra": max(0, len(pending) - MAX_MATCHES_SHOWN),
        "today_matches": today_rows,
        "today_count": len(today_rows),
        "today_pending_count": today_pending_count,
        "today_label": _fmt_today(shared["now"]),
        "next_colombia": _match_row(next_colombia) if next_colombia else None,
        "colombia_pending": colombia_pending,
        "colombia_today": colombia_today,
        "colombia_today_pending": colombia_today_pending,
        "pending_missions": [m.title for m in pending_missions[:MAX_MISSIONS_SHOWN]],
        "pending_missions_count": len(pending_missions),
        "polla_url": settings.POLLA_PUBLIC_URL,
        "unsub_url": unsubscribe_url(user),
        "reply_to": settings.POLLA_EMAIL_REPLY_TO,
        "brand_site": settings.SITE_URL,
        "year": shared["now"].year,
    }


def subject_for(ctx):
    # Asuntos personales y sin palabras "gancho" (gratis, premio, $) para evitar
    # que Gmail lo mande a Promociones; suben la chance de llegar a Principal.
    name = ctx["name"]
    if ctx["colombia_today"]:
        core = (
            "hoy juega Colombia, no te quedes sin pronóstico"
            if ctx["colombia_today_pending"]
            else "hoy juega Colombia"
        )
    elif ctx["colombia_pending"]:
        core = "no olvides tu pronóstico del partido de Colombia"
    elif ctx["pending_count"] > 0:
        n = ctx["pending_count"]
        core = f"te faltan {n} pronóstico{'s' if n != 1 else ''} del Mundial"
    else:
        core = "tu resumen de la Polla Mundialista"
    if name:
        return f"{name}, {core}"
    return core[0].upper() + core[1:]


def render_reminder(ctx):
    """Devuelve (asunto, html, texto) del correo recordatorio."""
    subject = subject_for(ctx)
    html = render_to_string("polla/email/reminder.html", ctx)
    text = render_to_string("polla/email/reminder.txt", ctx)
    return subject, html, text


def unsub_headers(ctx):
    """Cabeceras de baja (one-click, RFC 8058) para entregabilidad."""
    return {
        "List-Unsubscribe": (
            f"<{ctx['unsub_url']}>, <mailto:{ctx['reply_to']}?subject=baja%20polla>"
        ),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
