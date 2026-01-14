from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta

from .models import Order, OrderItem, Table
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

    queryset = Order.objects.prefetch_related(
        "items", "items__product_variant__product")
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

        # Filtrar por fecha (hoy, ayer, semana, mes) - usar hora local (America/Bogota)
        date_filter = self.request.query_params.get("date")
        local_now = timezone.localtime()

        if date_filter == "today":
            # Obtener inicio y fin del día en hora local
            start_of_day = local_now.replace(
                hour=0, minute=0, second=0, microsecond=0)
            end_of_day = local_now.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            queryset = queryset.filter(
                created_at__gte=start_of_day, created_at__lte=end_of_day)
        elif date_filter == "yesterday":
            # Obtener inicio y fin de ayer en hora local
            yesterday = local_now - timedelta(days=1)
            start_of_yesterday = yesterday.replace(
                hour=0, minute=0, second=0, microsecond=0)
            end_of_yesterday = yesterday.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            queryset = queryset.filter(
                created_at__gte=start_of_yesterday, created_at__lte=end_of_yesterday)
        elif date_filter == "week":
            week_ago = local_now - timedelta(days=7)
            queryset = queryset.filter(created_at__gte=week_ago)
        elif date_filter == "month":
            month_ago = local_now - timedelta(days=30)
            queryset = queryset.filter(created_at__gte=month_ago)

        # Filtrar solo activos
        active_only = self.request.query_params.get("active")
        if active_only == "true":
            queryset = queryset.filter(
                status__in=[Order.Status.PENDING,
                            Order.Status.PREPARING, Order.Status.READY]
            )

        return queryset

    @action(detail=True, methods=["post"])
    def update_status(self, request, pk=None):
        """Actualizar estado del pedido"""
        order = self.get_object()
        serializer = OrderStatusUpdateSerializer(data=request.data)

        if serializer.is_valid():
            new_status = serializer.validated_data["status"]

            if new_status == Order.Status.DELIVERED:
                # Usar el método del modelo que marca todos los items como entregados
                order.mark_as_delivered()
            else:
                order.status = new_status
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
        unpaid_count = OrderItem.objects.filter(
            order_id=order.pk, is_paid=False).count()
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

            # Crear items individuales (cada uno con quantity=1) para permitir pagos separados
            # Si quantity > 1, se crean múltiples items independientes
            for _ in range(quantity):
                OrderItem.objects.create(
                    order_id=order.pk,
                    product_variant=product_variant,
                    quantity=1,  # Siempre 1 para que cada item sea independiente
                    unit_price=unit_price,
                    subtotal=unit_price,  # Subtotal de un solo item
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
            status__in=[Order.Status.PENDING,
                        Order.Status.PREPARING, Order.Status.READY]
        )
        serializer = OrderListSerializer(active_orders, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def pending_payments(self, request):
        """
        Obtener pedidos con pagos pendientes independientemente de la fecha.
        Incluye pedidos donde is_paid=False o que tienen items sin pagar.
        Excluye pedidos cancelados.
        """
        # Obtener pedidos que no están completamente pagados y no están cancelados
        pending_orders = self.get_queryset().filter(
            is_paid=False
        ).exclude(
            status=Order.Status.CANCELLED
        ).order_by("-created_at")

        # Calcular estadísticas
        total_pending = pending_orders.aggregate(
            total=Sum("total")
        )["total"] or 0

        # Contar items pendientes de pago
        from django.db.models import Count, Q
        orders_with_unpaid_count = pending_orders.annotate(
            unpaid_items=Count(
                'items',
                filter=Q(items__is_paid=False)
            )
        )

        serializer = OrderListSerializer(orders_with_unpaid_count, many=True)

        return Response({
            "orders": serializer.data,
            "total_orders": pending_orders.count(),
            "total_pending": str(total_pending),
        })

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """Estadísticas de pedidos"""
        # Filtro de fecha - usar hora local (America/Bogota)
        date_filter = request.query_params.get("date", "today")
        start_date_param = request.query_params.get("start_date")
        end_date_param = request.query_params.get("end_date")

        # Usar el helper para obtener el rango de fechas
        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Filtrar pedidos por el rango de fechas
        orders = Order.objects.filter(
            created_at__gte=start_date, created_at__lte=end_date)
        order_ids = orders.values_list('id', flat=True)

        total_orders = orders.count()

        # Items pagados de pedidos en el período (excluyendo cancelados)
        paid_items = OrderItem.objects.filter(
            order_id__in=order_ids,
            is_paid=True
        ).exclude(order__status=Order.Status.CANCELLED)

        # Total de ingresos = suma de items pagados
        total_revenue = paid_items.aggregate(
            total=Sum("subtotal"))["total"] or 0

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
        unpaid_total = unpaid_items.aggregate(
            total=Sum("subtotal"))["total"] or 0

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

    def _get_date_range(self, date_filter, start_date_param, end_date_param):
        """Helper para obtener rango de fechas"""
        from datetime import datetime as dt

        # Obtener la fecha/hora local actual
        local_now = timezone.localtime()

        # Si hay parámetros de fecha personalizados o el filtro es "custom", usarlos
        if (start_date_param and end_date_param) or date_filter == "custom":
            if start_date_param and end_date_param:
                try:
                    # Parsear las fechas y convertirlas a timezone-aware
                    start_date = timezone.make_aware(
                        dt.strptime(start_date_param, "%Y-%m-%d")
                    ).replace(hour=0, minute=0, second=0, microsecond=0)
                    end_date = timezone.make_aware(
                        dt.strptime(end_date_param, "%Y-%m-%d")
                    ).replace(hour=23, minute=59, second=59, microsecond=999999)
                    return start_date, end_date
                except (ValueError, TypeError):
                    # Si hay error, usar hoy como fallback
                    start_date = local_now.replace(
                        hour=0, minute=0, second=0, microsecond=0)
                    end_date = local_now.replace(
                        hour=23, minute=59, second=59, microsecond=999999)
                    return start_date, end_date
        elif date_filter == "today":
            # Para "today", usar la fecha de hoy desde las 00:00 hasta las 23:59:59
            # Asegurarse de usar la fecha actual, no la hora actual
            # timezone.localtime() ya devuelve la fecha/hora en la zona horaria configurada (America/Bogota)
            # Obtener la fecha de hoy explícitamente
            today_date = local_now.date()
            # Crear start_date y end_date usando la fecha de hoy
            today_start = timezone.make_aware(
                dt.combine(today_date, dt.min.time())
            )
            # Usar 23:59:59.999999 en lugar de dt.max.time() para evitar problemas
            max_time = dt.time(23, 59, 59, 999999)
            today_end = timezone.make_aware(
                dt.combine(today_date, max_time)
            )
            return today_start, today_end
        elif date_filter == "yesterday":
            yesterday = local_now - timedelta(days=1)
            start_date = yesterday.replace(
                hour=0, minute=0, second=0, microsecond=0)
            end_date = yesterday.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            return start_date, end_date
        elif date_filter == "week":
            start_date = local_now - timedelta(days=7)
            end_date = local_now.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            return start_date, end_date
        elif date_filter == "month":
            start_date = local_now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = local_now.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            return start_date, end_date
        elif date_filter == "last_month":
            first_day_this_month = local_now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            last_day_last_month = first_day_this_month - timedelta(days=1)
            start_date = last_day_last_month.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = last_day_last_month.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            return start_date, end_date
        elif date_filter == "year":
            start_date = local_now.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = local_now.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            return start_date, end_date
        else:
            start_date = local_now.replace(
                hour=0, minute=0, second=0, microsecond=0)
            end_date = local_now.replace(
                hour=23, minute=59, second=59, microsecond=999999)
            return start_date, end_date

    @action(detail=False, methods=["get"])
    def revenue_by_day(self, request):
        """Ingresos por día para gráfica de línea"""
        from django.db.models import F, Q
        from django.db.models.functions import Coalesce
        from datetime import datetime, timedelta

        date_filter = request.query_params.get("date", "today")
        start_date_param = request.query_params.get("start_date")
        end_date_param = request.query_params.get("end_date")

        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Obtener items pagados en el rango de fechas (excluyendo cancelados)
        # Filtrar por la fecha de pago (paid_at si existe, sino order.created_at)
        # Esto asegura que incluimos items pagados en el rango, independientemente de cuándo se creó el pedido
        paid_items = OrderItem.objects.filter(
            is_paid=True
        ).exclude(order__status=Order.Status.CANCELLED).annotate(
            payment_date=Coalesce('paid_at', 'order__created_at')
        ).filter(
            Q(payment_date__gte=start_date) & Q(payment_date__lte=end_date)
        )

        # Agrupar por día usando la fecha de pago
        revenue_by_day = (
            paid_items
            .annotate(
                date=TruncDate('payment_date')
            )
            .values('date')
            .annotate(revenue=Sum('subtotal'))
            .order_by('date')
        )

        # Crear un diccionario con los datos existentes
        revenue_dict = {}
        for item in revenue_by_day:
            date_str = item['date'].strftime('%Y-%m-%d')
            revenue_dict[date_str] = float(item['revenue'] or 0)

        # Generar todos los días del rango, incluso sin datos
        data = []
        current_date = start_date.date()
        end_date_only = end_date.date()

        # Si el filtro es "today", forzar que end_date_only sea el día de hoy
        # Esto asegura que siempre incluya el día actual, incluso si hay problemas de zona horaria
        if date_filter == "today":
            today_date = timezone.localtime().date()
            # Siempre usar la fecha de hoy, no confiar en end_date_only
            end_date_only = today_date

        while current_date <= end_date_only:
            date_str = current_date.strftime('%Y-%m-%d')
            data.append({
                "date": date_str,
                "revenue": revenue_dict.get(date_str, 0.0),
            })
            current_date += timedelta(days=1)

        # Verificación final: si el filtro es "today", asegurarse de que hoy esté en la lista
        if date_filter == "today":
            today_date = timezone.localtime().date()
            today_str = today_date.strftime('%Y-%m-%d')
            # Si hoy no está en la lista, agregarlo
            if not any(item['date'] == today_str for item in data):
                data.append({
                    "date": today_str,
                    "revenue": revenue_dict.get(today_str, 0.0),
                })
                # Ordenar por fecha
                data.sort(key=lambda x: x['date'])

        # Información de depuración para verificar la fecha cuando es "today"
        debug_info = None
        if date_filter == "today":
            debug_info = {
                "server_local_now": timezone.localtime().isoformat(),
                "server_today_date": timezone.localtime().date().isoformat(),
                "calculated_start_date": start_date.isoformat(),
                "calculated_end_date": end_date.isoformat(),
                "end_date_only": end_date_only.isoformat(),
            }

        return Response({
            "data": data,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "debug": debug_info,
        })

    @action(detail=False, methods=["get"])
    def product_stats(self, request):
        """Estadísticas por producto: cantidad vendida e ingresos"""
        date_filter = request.query_params.get("date", "today")
        start_date_param = request.query_params.get("start_date")
        end_date_param = request.query_params.get("end_date")

        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Obtener items pagados en el rango de fechas (excluyendo cancelados)
        # Filtrar por la fecha de pago (paid_at si existe, sino order.created_at)
        # Esto asegura que incluimos items pagados en el rango, independientemente de cuándo se creó el pedido
        from django.db.models import Q
        from django.db.models.functions import Coalesce

        paid_items = OrderItem.objects.filter(
            is_paid=True
        ).exclude(order__status=Order.Status.CANCELLED).annotate(
            payment_date=Coalesce('paid_at', 'order__created_at')
        ).filter(
            Q(payment_date__gte=start_date) & Q(payment_date__lte=end_date)
        )

        # Agrupar por producto y variante
        product_stats = (
            paid_items
            .select_related('product_variant', 'product_variant__product')
            .values(
                'product_variant__product__id',
                'product_variant__product__name',
                'product_variant__id',
                'product_variant__name'
            )
            .annotate(
                quantity_sold=Sum('quantity'),
                revenue=Sum('subtotal'),
                count=Count('id')
            )
            .order_by('-revenue')
        )

        # Formatear datos para el frontend
        data = []
        for item in product_stats:
            product_name = item['product_variant__product__name']
            variant_name = item['product_variant__name']
            display_name = f"{product_name}"
            if variant_name and variant_name != product_name:
                display_name = f"{product_name} - {variant_name}"

            data.append({
                "product_id": item['product_variant__product__id'],
                "variant_id": item['product_variant__id'],
                "product_name": product_name,
                "variant_name": variant_name,
                "display_name": display_name,
                "quantity_sold": item['quantity_sold'] or 0,
                "revenue": float(item['revenue'] or 0),
                "count": item['count'] or 0,
            })

        return Response({
            "data": data,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        })


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
            payment_method = serializer.validated_data.get(
                "payment_method", "")
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
            payment_method = serializer.validated_data.get(
                "payment_method", "")
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


class TableViewSet(viewsets.ViewSet):
    """ViewSet para tracking de visitas a mesas"""

    @action(detail=False, methods=["post"], url_path="register-visit")
    def register_visit(self, request):
        """Registra una visita a una mesa"""
        table_number = request.data.get("table_number")

        if table_number is None or table_number == "":
            return Response(
                {"error": "table_number es requerido"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            table_number = int(table_number)
        except (ValueError, TypeError):
            return Response(
                {"error": "table_number debe ser un número"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buscar o crear la mesa (0 = Barra)
        table_name = "Barra" if table_number == 0 else f"Mesa {table_number}"
        table, created = Table.objects.get_or_create(
            table_number=table_number,
            defaults={
                "table_name": table_name,
                "is_active": True
            }
        )

        # Registrar la visita
        table.register_visit()

        return Response({
            "table_number": table.table_number,
            "table_name": table.table_name,
            "visit_count": table.visit_count
        })

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """Obtiene estadísticas de visitas por mesa"""
        tables = Table.objects.filter(is_active=True).order_by("-visit_count")

        data = [
            {
                "table_number": table.table_number,
                "table_name": table.table_name,
                "visit_count": table.visit_count
            }
            for table in tables
        ]

        return Response({
            "tables": data,
            "total_visits": sum(t.visit_count for t in tables)
        })
