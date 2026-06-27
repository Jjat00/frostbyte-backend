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

from .models import (
    Award,
    AwardPick,
    Match,
    Mission,
    Prediction,
    Tournament,
    UserMission,
    UserScore,
)

User = get_user_model()

COLOMBIA_CODE = "COL"
UNSUB_SALT = "polla-email-unsub"
MAX_MATCHES_SHOWN = 4
MAX_MISSIONS_SHOWN = 5

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


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


_MESES_LARGO = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fmt_dt_long(dt):
    """Fecha y hora legible en español, hora de Colombia.

    P.ej. 'domingo 28 de junio a las 2:00 p. m.'.
    """
    loc = timezone.localtime(dt)
    dia = _DIAS[loc.weekday()]
    return f"{dia} {loc.day} de {_MESES_LARGO[loc.month - 1]} a las {_fmt_time(dt)}"


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
    # Misiones que se completan pronosticando (la de invitar es social aparte).
    missions = list(Mission.objects.exclude(kind=Mission.Kind.INVITE))
    total = UserScore.objects.count()
    return {
        "now": now,
        "upcoming": upcoming,
        "colombia_upcoming": colombia_upcoming,
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
    # ¿El próximo partido de Colombia es HOY? (misma fecha en hora de Colombia
    # respecto al momento del envío). Hace que el correo diga "hoy juega Colombia".
    today = timezone.localtime(shared["now"]).date()
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


# ── Recordatorio de MENCIONES (campeón, goleador, etc.) ─────────────────────
def gather_menciones():
    """Datos comunes del envío de menciones (una sola lectura).

    El cierre de menciones coincide con el arranque de las eliminatorias: usa
    ``Tournament.awards_lock_at`` y, si está vacío, el pitazo del primer cruce
    de eliminación.
    """
    now = timezone.now()
    tournament = Tournament.get_active()
    deadline = tournament.awards_lock_at if tournament else None
    if deadline is None:
        first_elim = (
            Match.objects.exclude(stage=Match.Stage.GROUP)
            .order_by("kickoff")
            .first()
        )
        deadline = first_elim.kickoff if first_elim else None

    awards = list(Award.objects.order_by("display_order"))
    return {
        "now": now,
        "deadline": deadline,
        "awards": awards,
        "total_awards": len(awards),
        "awards_points": sum(a.points for a in awards),
    }


def build_menciones_context(score, shared):
    """Contexto personalizado del correo de menciones para un participante."""
    user = score.user
    set_count = (
        AwardPick.objects.filter(user=user)
        .filter(team__isnull=False)
        .count()
        + AwardPick.objects.filter(user=user, team__isnull=True, player__isnull=False).count()
    )
    total = shared["total_awards"]
    deadline = shared["deadline"]
    return {
        "name": _first_name(user),
        "points": score.points,
        "position": score.position or 0,
        "awards": [{"title": a.title, "points": a.points} for a in shared["awards"]],
        "total_awards": total,
        "awards_points": shared["awards_points"],
        "set_count": set_count,
        "missing_count": max(0, total - set_count),
        "deadline_long": _fmt_dt_long(deadline) if deadline else "",
        "polla_url": settings.POLLA_PUBLIC_URL,
        "unsub_url": unsubscribe_url(user),
        "reply_to": settings.POLLA_EMAIL_REPLY_TO,
        "brand_site": settings.SITE_URL,
        "year": shared["now"].year,
    }


def menciones_subject_for(ctx):
    # Asunto personal y sin palabras "gancho" para no caer en Promociones.
    name = ctx["name"]
    if ctx["set_count"] == 0:
        core = "aún puedes elegir tus menciones del Mundial"
    elif ctx["missing_count"] > 0:
        core = "te faltan menciones por elegir en el Mundial"
    else:
        core = "puedes ajustar tus menciones antes de las eliminatorias"
    if name:
        return f"{name}, {core}"
    return core[0].upper() + core[1:]


def render_menciones(ctx):
    """Devuelve (asunto, html, texto) del correo de menciones."""
    subject = menciones_subject_for(ctx)
    html = render_to_string("polla/email/menciones.html", ctx)
    text = render_to_string("polla/email/menciones.txt", ctx)
    return subject, html, text
