from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta

from .models import Order, OrderItem
from .serializers import (
    OrderListSerializer,
    OrderDetailSerializer,
    OrderCreateSerializer,
    OrderUpdateSerializer,
    OrderStatusUpdateSerializer,
    OrderItemSerializer,
)


class OrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gestionar pedidos.

    list: Listar pedidos
    retrieve: Detalle de un pedido
    create: Crear nuevo pedido
    update: Actualizar pedido
    destroy: Eliminar pedido
    """

    queryset = Order.objects.prefetch_related("items", "items__product_variant__product")
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["order_number", "customer_name", "customer_phone"]
    ordering_fields = ["created_at", "total", "status"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return OrderListSerializer
        elif self.action == "create":
            return OrderCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return OrderUpdateSerializer
        elif self.action == "update_status":
            return OrderStatusUpdateSerializer
        return OrderDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por estado
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filtrar por fecha (hoy, semana, mes)
        date_filter = self.request.query_params.get("date")
        if date_filter == "today":
            today = timezone.now().date()
            queryset = queryset.filter(created_at__date=today)
        elif date_filter == "week":
            week_ago = timezone.now() - timedelta(days=7)
            queryset = queryset.filter(created_at__gte=week_ago)
        elif date_filter == "month":
            month_ago = timezone.now() - timedelta(days=30)
            queryset = queryset.filter(created_at__gte=month_ago)

        # Filtrar solo activos
        active_only = self.request.query_params.get("active")
        if active_only == "true":
            queryset = queryset.filter(
                status__in=[Order.Status.PENDING, Order.Status.PREPARING, Order.Status.READY]
            )

        return queryset

    @action(detail=True, methods=["post"])
    def update_status(self, request, pk=None):
        """Actualizar estado del pedido"""
        order = self.get_object()
        serializer = OrderStatusUpdateSerializer(data=request.data)

        if serializer.is_valid():
            new_status = serializer.validated_data["status"]
            order.status = new_status

            if new_status == Order.Status.DELIVERED:
                order.completed_at = timezone.now()

            order.save()
            return Response(OrderDetailSerializer(order).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def mark_paid(self, request, pk=None):
        """Marcar pedido como pagado"""
        order = self.get_object()
        payment_method = request.data.get("payment_method", "")

        order.is_paid = True
        if payment_method:
            order.payment_method = payment_method
        order.save()

        return Response(OrderDetailSerializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancelar pedido"""
        order = self.get_object()

        if order.status == Order.Status.DELIVERED:
            return Response(
                {"error": "No se puede cancelar un pedido ya entregado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.mark_as_cancelled()
        return Response(OrderDetailSerializer(order).data)

    @action(detail=False, methods=["get"])
    def active(self, request):
        """Obtener pedidos activos (pendientes, preparando, listos)"""
        active_orders = self.get_queryset().filter(
            status__in=[Order.Status.PENDING, Order.Status.PREPARING, Order.Status.READY]
        )
        serializer = OrderListSerializer(active_orders, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """Estadísticas de pedidos"""
        # Filtro de fecha
        date_filter = request.query_params.get("date", "today")

        if date_filter == "today":
            start_date = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        elif date_filter == "week":
            start_date = timezone.now() - timedelta(days=7)
        elif date_filter == "month":
            start_date = timezone.now() - timedelta(days=30)
        else:
            start_date = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        orders = Order.objects.filter(created_at__gte=start_date)

        total_orders = orders.count()
        total_revenue = orders.filter(
            status=Order.Status.DELIVERED, is_paid=True
        ).aggregate(total=Sum("total"))["total"] or 0

        pending_count = orders.filter(status=Order.Status.PENDING).count()
        preparing_count = orders.filter(status=Order.Status.PREPARING).count()
        ready_count = orders.filter(status=Order.Status.READY).count()
        delivered_count = orders.filter(status=Order.Status.DELIVERED).count()
        cancelled_count = orders.filter(status=Order.Status.CANCELLED).count()

        return Response(
            {
                "period": date_filter,
                "total_orders": total_orders,
                "total_revenue": str(total_revenue),
                "by_status": {
                    "pending": pending_count,
                    "preparing": preparing_count,
                    "ready": ready_count,
                    "delivered": delivered_count,
                    "cancelled": cancelled_count,
                },
            }
        )


class OrderItemViewSet(viewsets.ModelViewSet):
    """ViewSet para items de pedido"""

    queryset = OrderItem.objects.select_related(
        "order", "product_variant", "product_variant__product"
    )
    serializer_class = OrderItemSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por pedido
        order_id = self.request.query_params.get("order")
        if order_id:
            queryset = queryset.filter(order_id=order_id)

        return queryset
