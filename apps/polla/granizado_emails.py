"""Correo a los GANADORES del granizado (acertaron el marcador exacto de Colombia).

Avisa al ganador que se llevó un granizado, hasta cuándo puede reclamarlo
(ventana de 24 h del premio), recuerda el próximo partido de Colombia como otra
oportunidad e invita a seguir jugando la Polla. Reutiliza los helpers de
formato y el enlace de baja de ``email_reminders``; el envío real lo hace
``mailer.send_email`` y la orquestación el comando ``polla_granizado_email``.
"""
from __future__ import annotations

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from .email_reminders import (
    COLOMBIA_CODE,
    _first_name,
    _fmt_dt,
    _fmt_time,
    _is_colombia,
    _match_row,
    unsubscribe_url,
)
from .models import GranizadoReward, Match


def claim_url():
    """URL pública de la pestaña Partidos de la Polla, donde se reclama."""
    return f"{settings.SITE_URL.rstrip('/')}/partidos"


# ── Datos compartidos del envío (se calculan una sola vez) ──────────────────
def gather():
    now = timezone.now()
    upcoming = (
        Match.objects.filter(
            status=Match.Status.UPCOMING,
            kickoff__gt=now,
            home_team__isnull=False,
            away_team__isnull=False,
        )
        .select_related("home_team", "away_team")
        .order_by("kickoff")
    )
    next_colombia = next((m for m in upcoming if _is_colombia(m)), None)
    return {"now": now, "next_colombia": next_colombia}


def pending_winners(match_number=None, only_email=None):
    """Granizados por reclamar (no entregados ni vencidos), más recientes primero.

    Filtra por ``match_number`` (un partido concreto) y/o por correo. Excluye
    usuarios inactivos o sin correo. El opt-out se aplica en el comando para
    poder reportar cuántos se omitieron por haberse dado de baja.
    """
    now = timezone.now()
    qs = (
        GranizadoReward.objects.select_related(
            "user", "match", "match__home_team", "match__away_team"
        )
        .filter(redeemed=False, expires_at__gt=now)
        .filter(user__is_active=True)
        .exclude(user__email="")
        .exclude(user__email__isnull=True)
        .order_by("-issued_at")
    )
    if match_number:
        qs = qs.filter(match__number=match_number)
    if only_email:
        qs = qs.filter(user__email__iexact=only_email.strip())
    return qs


def build_context(reward, shared):
    """Contexto personalizado para el correo de un ganador."""
    user = reward.user
    match = reward.match
    home = match.home_team.name if match.home_team else (match.home_placeholder or "?")
    away = match.away_team.name if match.away_team else (match.away_placeholder or "?")

    # Rival (el equipo que no es Colombia) para titulares más amables.
    if match.home_team and match.home_team.code == COLOMBIA_CODE:
        rival = away
    elif match.away_team and match.away_team.code == COLOMBIA_CODE:
        rival = home
    else:
        rival = None

    now = shared["now"]
    secs_left = (reward.expires_at - now).total_seconds()
    hours_left = max(0, int(secs_left // 3600))
    deadline_today = (
        timezone.localtime(reward.expires_at).date() == timezone.localtime(now).date()
    )

    # No anunciar como "próximo" el mismo partido que ya se jugó.
    next_col = shared["next_colombia"]
    if next_col and next_col.id == match.id:
        next_col = None

    return {
        "name": _first_name(user),
        "code": reward.code or "",
        "home": home,
        "away": away,
        "rival": rival,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "has_score": match.home_score is not None and match.away_score is not None,
        "deadline": _fmt_dt(reward.expires_at),
        "deadline_time": _fmt_time(reward.expires_at),
        "deadline_today": deadline_today,
        "hours_left": hours_left,
        "next_colombia": _match_row(next_col) if next_col else None,
        "claim_url": claim_url(),
        "polla_url": settings.POLLA_PUBLIC_URL,
        "unsub_url": unsubscribe_url(user),
        "reply_to": settings.POLLA_EMAIL_REPLY_TO,
        "brand_site": settings.SITE_URL,
        "year": now.year,
    }


def subject_for(ctx):
    # Sin "gratis"/"premio"/"$" en el asunto (gancho de spam). La lista de
    # ganadores es pequeña, así que prioriza llegar a Principal.
    name = ctx["name"]
    core = "reclama tu granizado por acertar el marcador de Colombia"
    if name:
        return f"{name}, {core}"
    return core[0].upper() + core[1:]


def render_winner(ctx):
    """Devuelve (asunto, html, texto) del correo al ganador."""
    subject = subject_for(ctx)
    html = render_to_string("polla/email/granizado_winner.html", ctx)
    text = render_to_string("polla/email/granizado_winner.txt", ctx)
    return subject, html, text
