"""Cuántas tarjetas de campaña se han generado, y con qué proveedor.

Todo sale de `CardGeneration`, una fila por intento. Dos cosas que conviene
tener presentes al leer los números:

1. **Un pedido puede dejar dos filas.** Si Gemini falla y OpenAI responde, el
   intento fallido también queda registrado: `total` cuenta intentos y
   `generated` cuenta tarjetas entregadas, que es el número que importa.
2. **El día es la fecha local**, no UTC: el conteo diario se agrupa con la zona
   horaria del proyecto para que una tarjeta de las 8 p. m. no aparezca al día
   siguiente.
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import CardGeneration

DEFAULT_DAYS = 30
MAX_DAYS = 365


def _parse_days(raw):
    """Los últimos N días, o None para toda la historia."""
    if raw in (None, "", "all"):
        return None
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(days, MAX_DAYS))


def build_card_stats(days_param=None):
    days = _parse_days(days_param)
    now = timezone.localtime()
    rows = CardGeneration.objects.all()
    if days:
        rows = rows.filter(created_at__gte=now - timedelta(days=days))

    generated = Q(status=CardGeneration.OK)
    totals = rows.aggregate(
        total=Count("id"),
        generated=Count("id", filter=generated),
    )
    window = CardGeneration.objects.aggregate(
        today=Count("id", filter=generated & Q(created_at__gte=now.replace(
            hour=0, minute=0, second=0, microsecond=0))),
        last_7_days=Count("id", filter=generated & Q(created_at__gte=now - timedelta(days=7))),
        last_30_days=Count("id", filter=generated & Q(created_at__gte=now - timedelta(days=30))),
        all_time=Count("id", filter=generated),
    )

    counts = _provider_counts(rows)
    by_provider = [
        {
            "provider": provider,
            "label": label,
            "generated": counts.get(provider, {}).get("generated", 0),
            "failed": counts.get(provider, {}).get("failed", 0),
            "as_fallback": counts.get(provider, {}).get("as_fallback", 0),
        }
        for provider, label in CardGeneration.PROVIDER_CHOICES
    ]

    daily = [
        {"date": row["day"].isoformat(), "generated": row["generated"]}
        for row in rows.filter(status=CardGeneration.OK)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(generated=Count("id"))
        .order_by("day")
        if row["day"]
    ]

    return {
        "days": days,
        "total_attempts": totals["total"],
        "generated": totals["generated"],
        "failed": totals["total"] - totals["generated"],
        "by_provider": by_provider,
        "daily": daily,
        **window,
    }


def _provider_counts(rows):
    """Generadas, fallidas y entradas como respaldo, en una sola consulta."""
    return {
        row["provider"]: row
        for row in rows.values("provider").annotate(
            generated=Count("id", filter=Q(status=CardGeneration.OK)),
            failed=Count("id", filter=Q(status=CardGeneration.FAILED)),
            as_fallback=Count("id", filter=Q(was_fallback=True)),
        )
    }
