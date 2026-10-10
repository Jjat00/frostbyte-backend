"""Estadisticas de canales: domicilios, pedidos por WhatsApp y por la app,
y clientes que entraron con Google.

Son de adopcion, no financieras: cuentan pedidos por su origen (Order.source)
y por su tipo (Order.order_type), y lo vendido es el total de los pedidos no
cancelados, sin mirar si ya se cobraron. El dinero cobrado de verdad vive en
FinancialAnalyticsViewSet.

Los registros con Google estan inflados por la Polla Mundialista (jun-jul
2026: unas 124 de las primeras 152 cuentas). Por eso cada cifra de usuarios
separa a quien jugo la Polla de quien llego despues.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.permissions import IsAdminUser
from apps.orders.models import Order
from apps.polla.models import AwardPick, BracketPick, Prediction

ALLOWED_DAYS = (7, 30, 90, 365)
CHANNELS = (Order.Source.STAFF, Order.Source.CUSTOMER, Order.Source.WHATSAPP)
NOT_CANCELLED = ~Q(status=Order.Status.CANCELLED)


def _played_polla():
    """Expresion: el usuario dejo al menos una apuesta en la Polla."""
    return (
        Exists(Prediction.objects.filter(user=OuterRef("pk")))
        | Exists(BracketPick.objects.filter(user=OuterRef("pk")))
        | Exists(AwardPick.objects.filter(user=OuterRef("pk")))
    )


def _google_users():
    return User.objects.filter(provider=User.Provider.GOOGLE).annotate(
        played_polla=_played_polla(),
        app_orders=Count(
            "orders",
            filter=Q(orders__source=Order.Source.CUSTOMER) & ~Q(orders__status=Order.Status.CANCELLED),
        ),
    )


def _orders_block(qs):
    """Pedidos, cancelados, vendido y ticket promedio de un queryset."""
    agg = qs.aggregate(
        orders=Count("id", filter=NOT_CANCELLED),
        cancelled=Count("id", filter=Q(status=Order.Status.CANCELLED)),
        revenue=Sum("total", filter=NOT_CANCELLED),
        fees=Sum("delivery_fee", filter=NOT_CANCELLED),
    )
    revenue = float(agg["revenue"] or 0)
    orders = agg["orders"]
    return {
        "orders": orders,
        "cancelled": agg["cancelled"],
        "revenue": revenue,
        "avg_ticket": round(revenue / orders) if orders else 0,
        "delivery_fees": float(agg["fees"] or 0),
    }


def _change(current, previous):
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


class ChannelAnalyticsViewSet(viewsets.ViewSet):
    """Canales de venta y clientes. Solo administradores.

    ?days=7|30|90|365 fija la ventana (por defecto 30); ?days=0 es todo el
    historico, sin comparacion contra un periodo anterior.
    """

    permission_classes = [IsAdminUser]

    def _window(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        if days != 0 and days not in ALLOWED_DAYS:
            days = 30
        now = timezone.now()
        if days == 0:
            return days, None, now, None
        start = now - timedelta(days=days)
        return days, start, now, start - timedelta(days=days)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        days, start, end, prev_start = self._window(request)

        orders = Order.objects.filter(created_at__lte=end)
        if start:
            orders = orders.filter(created_at__gte=start)
        previous = (
            Order.objects.filter(created_at__gte=prev_start, created_at__lt=start)
            if start else None
        )

        channels = []
        for source in CHANNELS:
            block = _orders_block(orders.filter(source=source))
            if previous is not None:
                prev_orders = previous.filter(source=source).filter(NOT_CANCELLED).count()
                block["previous_orders"] = prev_orders
                block["change"] = _change(block["orders"], prev_orders)
            block["source"] = source
            block["label"] = Order.Source(source).label
            channels.append(block)

        # Clientes distintos: en la app por cuenta, en WhatsApp por telefono
        # (o BSUID cuando el cliente oculta su numero).
        live = orders.filter(NOT_CANCELLED)
        unique_customers = {
            Order.Source.CUSTOMER: live.filter(source=Order.Source.CUSTOMER, user__isnull=False)
            .values("user").distinct().count(),
            Order.Source.WHATSAPP: live.filter(source=Order.Source.WHATSAPP).exclude(customer_phone="")
            .values("customer_phone").distinct().count(),
        }
        for block in channels:
            block["unique_customers"] = unique_customers.get(block["source"])

        delivery_qs = orders.filter(order_type=Order.OrderType.DELIVERY)
        delivery = _orders_block(delivery_qs)
        delivery["by_source"] = {
            row["source"]: row["n"]
            for row in delivery_qs.filter(NOT_CANCELLED).values("source").annotate(n=Count("id"))
        }
        if previous is not None:
            prev_delivery = previous.filter(order_type=Order.OrderType.DELIVERY).filter(NOT_CANCELLED).count()
            delivery["previous_orders"] = prev_delivery
            delivery["change"] = _change(delivery["orders"], prev_delivery)

        # Tipo de pedido de los canales en linea (app + WhatsApp)
        online_types = {
            row["order_type"]: row["n"]
            for row in live.exclude(source=Order.Source.STAFF)
            .values("order_type").annotate(n=Count("id"))
        }

        google = _google_users()
        new_google = google.filter(date_joined__gte=start) if start else google
        google_block = {
            "total": google.count(),
            "polla": google.filter(played_polla=True).count(),
            "with_orders": google.filter(app_orders__gt=0).count(),
            "new": new_google.count(),
            "new_without_polla": new_google.filter(played_polla=False).count(),
            "new_with_orders": new_google.filter(app_orders__gt=0).count(),
        }
        if start:
            google_block["previous_new"] = User.objects.filter(
                provider=User.Provider.GOOGLE, date_joined__gte=prev_start, date_joined__lt=start,
            ).count()

        return Response({
            "days": days,
            "channels": channels,
            "delivery": delivery,
            "online_order_types": online_types,
            "google_users": google_block,
        })

    @action(detail=False, methods=["get"])
    def monthly(self, request):
        """Pedidos por canal, domicilios y registros con Google, mes a mes."""
        try:
            months = min(max(int(request.query_params.get("months", 12)), 1), 24)
        except (TypeError, ValueError):
            months = 12
        local_now = timezone.localtime()
        start = (local_now - relativedelta(months=months - 1)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        rows = (
            Order.objects.filter(created_at__gte=start).filter(NOT_CANCELLED)
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(
                staff=Count("id", filter=Q(source=Order.Source.STAFF)),
                customer=Count("id", filter=Q(source=Order.Source.CUSTOMER)),
                whatsapp=Count("id", filter=Q(source=Order.Source.WHATSAPP)),
                delivery=Count("id", filter=Q(order_type=Order.OrderType.DELIVERY)),
            )
        )
        by_month = {r["month"].strftime("%Y-%m"): r for r in rows}

        signups = (
            User.objects.filter(provider=User.Provider.GOOGLE, date_joined__gte=start)
            .annotate(month=TruncMonth("date_joined"), played_polla=_played_polla())
            .values("month")
            .annotate(
                google=Count("id"),
                google_polla=Count("id", filter=Q(played_polla=True)),
            )
        )
        signups_by_month = {r["month"].strftime("%Y-%m"): r for r in signups}

        data = []
        for i in range(months):
            key = (start + relativedelta(months=i)).strftime("%Y-%m")
            r = by_month.get(key, {})
            s = signups_by_month.get(key, {})
            google_total = s.get("google", 0)
            google_polla = s.get("google_polla", 0)
            data.append({
                "month": key,
                "staff": r.get("staff", 0),
                "customer": r.get("customer", 0),
                "whatsapp": r.get("whatsapp", 0),
                "delivery": r.get("delivery", 0),
                "google_users": google_total,
                "google_users_polla": google_polla,
                "google_users_other": google_total - google_polla,
            })
        return Response({"data": data})

    @action(detail=False, methods=["get"])
    def google_users(self, request):
        """Ultimos registros con Google y si llegaron a pedir por la app."""
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 200)
        except (TypeError, ValueError):
            limit = 50
        users = (
            _google_users()
            .annotate(last_order_at=Max(
                "orders__created_at", filter=Q(orders__source=Order.Source.CUSTOMER),
            ))
            .order_by("-date_joined")[:limit]
        )
        return Response({
            "results": [
                {
                    "id": u.id,
                    "name": u.get_full_name() or u.username,
                    "email": u.email,
                    "avatar_url": u.avatar_url,
                    "date_joined": u.date_joined,
                    "played_polla": u.played_polla,
                    "app_orders": u.app_orders,
                    "last_order_at": u.last_order_at,
                }
                for u in users
            ]
        })
