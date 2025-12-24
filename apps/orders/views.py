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
    AddItemToOrderSerializer,
    MarkItemPaidSerializer,
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

        # Filtrar por fecha (hoy, semana, mes) - usar hora local (America/Bogota)
        date_filter = self.request.query_params.get("date")
        if date_filter == "today":
            # Obtener inicio y fin del día en hora local
            local_now = timezone.localtime()
            start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = local_now.replace(hour=23, minute=59, second=59, microsecond=999999)
            queryset = queryset.filter(created_at__gte=start_of_day, created_at__lte=end_of_day)
        elif date_filter == "week":
            week_ago = timezone.localtime() - timedelta(days=7)
            queryset = queryset.filter(created_at__gte=week_ago)
        elif date_filter == "month":
            month_ago = timezone.localtime() - timedelta(days=30)
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
        """Marcar pedido como pagado (marca todos los items pendientes)"""
        order = self.get_object()
        payment_method = request.data.get("payment_method", "")

        # Marcar todos los items pendientes como pagados usando update directo
        from django.utils import timezone
        OrderItem.objects.filter(order_id=order.pk, is_paid=False).update(
            is_paid=True,
            payment_method=payment_method,
            paid_at=timezone.now()
        )

        # Verificar si todos los items están pagados
        unpaid_count = OrderItem.objects.filter(order_id=order.pk, is_paid=False).count()
        all_paid = (unpaid_count == 0)

        # Actualizar estado de pago del pedido
        Order.objects.filter(pk=order.pk).update(
            is_paid=all_paid,
            payment_method=payment_method if payment_method else order.payment_method
        )

        # Re-obtener el order para la respuesta
        order = Order.objects.prefetch_related(
            "items", "items__product_variant__product"
        ).get(pk=order.pk)

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

    @action(detail=True, methods=["post"])
    def add_item(self, request, pk=None):
        """Añadir item a una orden existente"""
        order = self.get_object()
        serializer = AddItemToOrderSerializer(data=request.data)

        if serializer.is_valid():
            product_variant = serializer.validated_data["product_variant_id"]
            quantity = serializer.validated_data.get("quantity", 1)
            notes = serializer.validated_data.get("notes", "")
            unit_price = product_variant.price or 0

            # Crear el nuevo item (sin pagar por defecto)
            OrderItem.objects.create(
                order_id=order.pk,
                product_variant=product_variant,
                quantity=quantity,
                unit_price=unit_price,
                subtotal=unit_price * quantity,
                notes=notes,
                is_paid=False,  # El nuevo item no está pagado
            )

            # Recalcular totales directamente en la BD
            from django.db.models import Sum
            items_total = OrderItem.objects.filter(order_id=order.pk).aggregate(
                total=Sum('subtotal')
            )['total'] or 0
            
            # El pedido ya no está completamente pagado porque hay un item nuevo sin pagar
            # Si el pedido estaba entregado, vuelve a pendiente para preparar el nuevo item
            new_status = order.status
            if order.status == Order.Status.DELIVERED:
                new_status = Order.Status.PENDING
            
            Order.objects.filter(pk=order.pk).update(
                subtotal=items_total,
                total=items_total - order.discount,
                is_paid=False,  # Ya no está completamente pagado
                status=new_status,
            )

            # Re-obtener el order completo para la respuesta
            order = Order.objects.prefetch_related(
                "items", "items__product_variant__product"
            ).get(pk=order.pk)

            return Response(OrderDetailSerializer(order).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
        # Filtro de fecha - usar hora local (America/Bogota)
        date_filter = request.query_params.get("date", "today")
        local_now = timezone.localtime()

        if date_filter == "today":
            start_date = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif date_filter == "week":
            start_date = local_now - timedelta(days=7)
        elif date_filter == "month":
            start_date = local_now - timedelta(days=30)
        else:
            start_date = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

        orders = Order.objects.filter(created_at__gte=start_date)
        order_ids = orders.values_list('id', flat=True)

        total_orders = orders.count()
        
        # Items pagados de pedidos en el período (excluyendo cancelados)
        paid_items = OrderItem.objects.filter(
            order_id__in=order_ids,
            is_paid=True
        ).exclude(order__status=Order.Status.CANCELLED)
        
        # Total de ingresos = suma de items pagados
        total_revenue = paid_items.aggregate(total=Sum("subtotal"))["total"] or 0

        # Estadísticas por método de pago basadas en ITEMS
        by_payment_method = {}
        for method_code, method_name in Order.PaymentMethod.choices:
            method_stats = paid_items.filter(payment_method=method_code).aggregate(
                total=Sum("subtotal"), count=Count("id")
            )
            by_payment_method[method_code] = {
                "name": method_name,
                "total": str(method_stats["total"] or 0),
                "count": method_stats["count"] or 0,
            }

        # Items pagados sin método especificado
        no_method_stats = paid_items.filter(payment_method="").aggregate(
            total=Sum("subtotal"), count=Count("id")
        )
        by_payment_method["other"] = {
            "name": "Otro/Sin especificar",
            "total": str(no_method_stats["total"] or 0),
            "count": no_method_stats["count"] or 0,
        }

        # Conteo por estado de pedidos
        pending_count = orders.filter(status=Order.Status.PENDING).count()
        preparing_count = orders.filter(status=Order.Status.PREPARING).count()
        ready_count = orders.filter(status=Order.Status.READY).count()
        delivered_count = orders.filter(status=Order.Status.DELIVERED).count()
        cancelled_count = orders.filter(status=Order.Status.CANCELLED).count()

        # Total pendiente = suma de items NO pagados (excluyendo cancelados)
        unpaid_items = OrderItem.objects.filter(
            order_id__in=order_ids,
            is_paid=False
        ).exclude(order__status=Order.Status.CANCELLED)
        unpaid_total = unpaid_items.aggregate(total=Sum("subtotal"))["total"] or 0

        # Items totales pagados y pendientes
        total_paid_items = paid_items.count()
        total_unpaid_items = unpaid_items.count()

        return Response(
            {
                "period": date_filter,
                "total_orders": total_orders,
                "total_revenue": str(total_revenue),
                "unpaid_total": str(unpaid_total),
                "total_paid_items": total_paid_items,
                "total_unpaid_items": total_unpaid_items,
                "by_status": {
                    "pending": pending_count,
                    "preparing": preparing_count,
                    "ready": ready_count,
                    "delivered": delivered_count,
                    "cancelled": cancelled_count,
                },
                "by_payment_method": by_payment_method,
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

    @action(detail=True, methods=["post"])
    def mark_paid(self, request, pk=None):
        """Marcar un item como pagado"""
        item = self.get_object()
        serializer = MarkItemPaidSerializer(data=request.data)

        if serializer.is_valid():
            payment_method = serializer.validated_data.get("payment_method", "")
            item.mark_as_paid(payment_method)
            
            # Actualizar estado de pago del pedido
            order = item.order
            order.update_payment_status()
            
            # Refrescar el item desde la base de datos
            item.refresh_from_db()
            
            return Response(OrderItemSerializer(item).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def unmark_paid(self, request, pk=None):
        """Desmarcar un item como pagado"""
        item = self.get_object()
        order = item.order
        
        item.is_paid = False
        item.payment_method = ""
        item.paid_at = None
        item.save(update_fields=["is_paid", "payment_method", "paid_at"])
        
        # Actualizar estado de pago del pedido (ya no está todo pagado)
        Order.objects.filter(pk=order.pk).update(is_paid=False)
        
        return Response(OrderItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def change_payment_method(self, request, pk=None):
        """Cambiar el método de pago de un item"""
        item = self.get_object()
        serializer = MarkItemPaidSerializer(data=request.data)

        if serializer.is_valid():
            payment_method = serializer.validated_data.get("payment_method", "")
            item.change_payment_method(payment_method)
            
            # Actualizar estado de pago del pedido
            item.order.update_payment_status()
            
            return Response(OrderItemSerializer(item).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def mark_delivered(self, request, pk=None):
        """Marcar un item como entregado"""
        item = self.get_object()
        item.mark_as_delivered()
        
        # Actualizar estado de entrega del pedido
        item.order.update_delivery_status()
        
        return Response(OrderItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def unmark_delivered(self, request, pk=None):
        """Desmarcar un item como entregado"""
        item = self.get_object()
        order = item.order
        
        item.unmark_as_delivered()
        
        # Si el pedido estaba entregado, volver a preparando
        if order.status == Order.Status.DELIVERED:
            Order.objects.filter(pk=order.pk).update(
                status=Order.Status.PREPARING,
                completed_at=None
            )
        
        return Response(OrderItemSerializer(item).data)
