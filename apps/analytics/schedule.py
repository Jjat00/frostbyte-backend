"""Horario real del local: a que hora se abre y se cierra cada jornada.

Sale de StoreStatusEvent, que guarda cada vez que el staff abre o cierra el
local desde el panel. Ese registro empezo el 2026-10-11: no hay historial
anterior, asi que la grafica arranca vacia y se llena dia a dia.

Una jornada va de 6:00 a. m. a 6:00 a. m. del dia siguiente, para que un
cierre a la 1:30 a. m. cuente en la noche que empezo el dia anterior. Las
horas se devuelven en decimal (13.5 = 1:30 p. m.) y las de despues de
medianoche siguen contando desde 24 (25.5 = 1:30 a. m.), asi la linea de
cierre no salta de arriba abajo en la grafica.
"""

from datetime import timedelta
from statistics import median

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsAdminUser
from apps.orders.models import StoreStatusEvent

ALLOWED_DAYS = (30, 90, 365)
DAY_START_HOUR = 6


def _jornada(moment):
    """(fecha de la jornada, hora decimal) de un instante."""
    local = timezone.localtime(moment)
    hours = local.hour + local.minute / 60
    if local.hour < DAY_START_HOUR:
        hours += 24
    return (local - timedelta(hours=DAY_START_HOUR)).date(), round(hours, 2)


def _usual(values):
    return round(median(values), 2) if values else None


class ScheduleAnalyticsViewSet(viewsets.ViewSet):
    """Apertura y cierre del local por jornada. Solo administradores.

    ?days=30|90|365 fija la ventana (por defecto 30); ?days=0 es todo.
    """

    permission_classes = [IsAdminUser]

    @action(detail=False, methods=["get"])
    def daily(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        if days != 0 and days not in ALLOWED_DAYS:
            days = 30

        events = StoreStatusEvent.objects.all()
        if days:
            events = events.filter(created_at__gte=timezone.now() - timedelta(days=days))

        # De cada jornada vale la primera apertura y el ultimo cierre: si se
        # cerro un rato a media tarde, el local igual trabajo de punta a punta.
        by_day = {}
        for event in events.order_by("created_at"):
            day, hours = _jornada(event.created_at)
            row = by_day.setdefault(day, {"open": None, "close": None})
            if event.is_open:
                if row["open"] is None:
                    row["open"] = hours
            else:
                row["close"] = hours

        data = [
            {"date": day.isoformat(), "weekday": day.weekday(), **row}
            for day, row in sorted(by_day.items())
        ]

        by_weekday = []
        for weekday in range(7):
            rows = [r for r in data if r["weekday"] == weekday]
            by_weekday.append({
                "weekday": weekday,
                "open": _usual([r["open"] for r in rows if r["open"] is not None]),
                "close": _usual([r["close"] for r in rows if r["close"] is not None]),
                "days": len(rows),
            })

        opens = [r["open"] for r in data if r["open"] is not None]
        closes = [r["close"] for r in data if r["close"] is not None]
        first = StoreStatusEvent.objects.order_by("created_at").values_list("created_at", flat=True).first()

        return Response({
            "days": days,
            "data": data,
            "usual": {
                "open": _usual(opens),
                "close": _usual(closes),
                "days_open": len(opens),
                "days_close": len(closes),
            },
            "by_weekday": by_weekday,
            "tracking_since": first,
        })
