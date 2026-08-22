from rest_framework import viewsets, filters, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import UserRateThrottle
from apps.accounts.permissions import IsAdminUser, IsStaffMember
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate, ExtractHour, ExtractWeekDay
from django.utils import timezone
from datetime import timedelta

from .models import (
    Order,
    OrderItem,
    Table,
    PageVisit,
    StoreSettings,
    MIN_DELIVERY_RADIUS_KM,
    MAX_DELIVERY_RADIUS_KM,
)
from .consumers import broadcast_orders_update
from .coverage import clean_delivery_area
from .serializers import (
    OrderListSerializer,
    OrderDetailSerializer,
    OrderCreateSerializer,
    OrderUpdateSerializer,
    OrderStatusUpdateSerializer,
    OrderItemSerializer,
    AddItemToOrderSerializer,
    MarkItemPaidSerializer,
    PublicOrderDetailSerializer,
    TableSerializer,
    CustomerOrderCreateSerializer,
    CustomerOrderListSerializer,
    build_table_label,
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

    queryset = Order.objects.select_related("table").prefetch_related(
        "items", "items__product_variant__product", "items__business")
    permission_classes = [IsAuthenticated]
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

    @action(
        detail=False,
        methods=["get", "patch"],
        url_path="store-settings",
        permission_classes=[IsStaffMember],
    )
    def store_settings(self, request):
        """Estado operativo del local para el staff (abierto/cerrado, domicilios).

        GET  -> estado actual.
        PATCH-> actualiza `is_open`, `customer_ordering_enabled` y/o
                `delivery_radius_km`. Al cambiar `is_open` se registra quién y
                cuándo; el radio solo lo puede cambiar un admin.
        """
        cfg = StoreSettings.load()

        if request.method == "PATCH":
            update_fields = []

            if "is_open" in request.data:
                new_value = bool(request.data.get("is_open"))
                if new_value != cfg.is_open:
                    cfg.is_open = new_value
                    cfg.status_changed_at = timezone.now()
                    cfg.status_changed_by = request.user
                    update_fields += ["is_open", "status_changed_at", "status_changed_by"]

            if "customer_ordering_enabled" in request.data:
                cfg.customer_ordering_enabled = bool(request.data.get("customer_ordering_enabled"))
                update_fields.append("customer_ordering_enabled")

            # La zona define hasta dónde vendemos: decisión de negocio,
            # reservada al admin, se exprese como radio o como polígono.
            if {"delivery_radius_km", "delivery_area"} & set(request.data):
                if not request.user.is_admin:
                    return Response(
                        {"detail": "Solo un administrador puede cambiar la zona de domicilios."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

            if "delivery_radius_km" in request.data:
                try:
                    radius = Decimal(str(request.data.get("delivery_radius_km"))).quantize(
                        Decimal("0.01"))
                except (InvalidOperation, TypeError, ValueError):
                    return Response(
                        {"delivery_radius_km": "Ingresa el radio en kilómetros (ej: 1.5)."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not (MIN_DELIVERY_RADIUS_KM <= radius <= MAX_DELIVERY_RADIUS_KM):
                    return Response(
                        {"delivery_radius_km": (
                            f"El radio debe estar entre {float(MIN_DELIVERY_RADIUS_KM):g} y "
                            f"{float(MAX_DELIVERY_RADIUS_KM):g} km."
                        )},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if radius != cfg.delivery_radius_km:
                    cfg.delivery_radius_km = radius
                    update_fields.append("delivery_radius_km")

            if "delivery_area" in request.data:
                # Lista vacía = borrar el polígono y volver al círculo
                try:
                    area = clean_delivery_area(request.data.get("delivery_area"))
                except ValueError as exc:
                    return Response(
                        {"delivery_area": str(exc)},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if area != cfg.delivery_area:
                    cfg.delivery_area = area
                    update_fields.append("delivery_area")

            if update_fields:
                cfg.save(update_fields=update_fields + ["updated_at"])

        return Response({
            "is_open": cfg.is_open,
            "customer_ordering_enabled": cfg.customer_ordering_enabled,
            "can_order": cfg.is_open and cfg.customer_ordering_enabled,
            "delivery_fee": str(cfg.delivery_fee),
            "delivery_radius_km": float(cfg.delivery_radius_km),
            "delivery_area": cfg.delivery_area or [],
            "status_changed_at": cfg.status_changed_at,
        })

    def perform_create(self, serializer):
        """Crear pedido y notificar via WebSocket"""
        instance = serializer.save()
        broadcast_orders_update()
        return instance

    def perform_update(self, serializer):
        """Recalcular totales cuando se actualiza el descuento"""
        instance = serializer.save()
        if "discount" in serializer.validated_data:
            instance.calculate_totals()
            instance.save(update_fields=["subtotal", "total", "updated_at"])
        broadcast_orders_update()

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

        # Los no-admin (meseros/cocina) solo pueden listar pedidos de hoy y
        # ayer, sin importar el filtro de fecha que pidan por query param.
        user = self.request.user
        if self.action == "list" and not getattr(user, "is_admin", False):
            start_of_yesterday = (local_now - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            queryset = queryset.filter(created_at__gte=start_of_yesterday)

        # Filtrar solo activos
        active_only = self.request.query_params.get("active")
        if active_only == "true":
            queryset = queryset.filter(
                status__in=[Order.Status.PENDING,
                            Order.Status.PREPARING, Order.Status.READY]
            )

        # Filtrar por negocio: pedidos que tengan al menos un item de ese
        # negocio (un pedido puede cruzar negocios, por eso .distinct()).
        business_slug = self.request.query_params.get("business")
        if business_slug:
            queryset = queryset.filter(
                items__business__slug=business_slug
            ).distinct()

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

            broadcast_orders_update()
            return Response(OrderDetailSerializer(order).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def mark_paid(self, request, pk=None):
        """Marcar pedido como pagado (marca todos los items pendientes)"""
        order = self.get_object()
        payment_method = request.data.get("payment_method", "")

        if payment_method and payment_method not in Order.ACTIVE_PAYMENT_METHODS:
            metodos = ", ".join(
                label for _, label in Order.active_payment_choices())
            return Response(
                {"payment_method": f"Método de pago no disponible. Usa: {metodos}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Las dos escrituras van en una transacción: si el request muere en
        # medio (timeout, daphne matando la conexión), no puede quedar el
        # pedido con los items pagados y la orden marcada como no pagada.
        from django.utils import timezone
        with transaction.atomic():
            # Marcar todos los items pendientes como pagados usando update directo
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

        broadcast_orders_update()
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
        broadcast_orders_update()
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

            # Items y totales van juntos: un pedido con items nuevos y el
            # total viejo cobra de menos.
            with transaction.atomic():
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

            broadcast_orders_update()
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
    def kitchen(self, request):
        """Vista de cocina (KDS) filtrada por negocio.

        Devuelve los pedidos activos pero, dentro de cada uno, SOLO los items
        del negocio indicado (?business=<slug>) que aún no fueron entregados.
        Así la cocina de Frostbyte Food ve únicamente sus platos y la barra de
        Frostbyte solo sus bebidas, aunque sea el mismo pedido/mesa.
        """
        business_slug = request.query_params.get("business")

        orders = self.get_queryset().filter(
            status__in=[Order.Status.PENDING,
                        Order.Status.PREPARING, Order.Status.READY]
        ).order_by("created_at")

        data = []
        for order in orders:
            items = list(order.items.all())
            if business_slug:
                items = [i for i in items
                         if i.business and i.business.slug == business_slug]
            # En la cocina no interesan los items ya entregados.
            items = [i for i in items if not i.is_delivered]
            if not items:
                continue

            # Ordenar: primero los que faltan, luego los listos.
            prep_order = {
                OrderItem.PrepStatus.PENDING: 0,
                OrderItem.PrepStatus.PREPARING: 1,
                OrderItem.PrepStatus.READY: 2,
            }
            items.sort(key=lambda i: prep_order.get(i.prep_status, 0))

            data.append({
                "id": order.id,
                "order_number": order.order_number,
                "table_number": order.table_number,
                "table_label": build_table_label(order),
                "order_type": order.order_type,
                "source": order.source,
                "payment_method": order.payment_method,
                "is_paid": order.is_paid,
                "customer_name": order.customer_name,
                "customer_notes": order.customer_notes,
                "status": order.status,
                "created_at": order.created_at,
                "items": OrderItemSerializer(items, many=True).data,
            })

        return Response(data)

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
        # Filtro por negocio (los KPIs del Home/Pedidos respetan el negocio activo)
        business_slug = request.query_params.get("business")

        # Usar el helper para obtener el rango de fechas
        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Filtrar pedidos por el rango de fechas (para conteo de pedidos y estados)
        orders = Order.objects.filter(
            created_at__gte=start_date, created_at__lte=end_date)
        if business_slug:
            # Pedidos que involucran a este negocio (pueden cruzar negocios)
            orders = orders.filter(items__business__slug=business_slug).distinct()

        total_orders = orders.count()

        # Items pagados de pedidos creados en el período.
        # La venta se atribuye a la fecha de creación del pedido, no a la de pago,
        # para que un pedido tomado a las 11pm y pagado pasada medianoche
        # cuente en el día en que se generó.
        paid_items = OrderItem.objects.filter(
            is_paid=True,
            order__created_at__gte=start_date,
            order__created_at__lte=end_date
        ).exclude(order__status=Order.Status.CANCELLED)
        if business_slug:
            paid_items = paid_items.filter(business__slug=business_slug)

        # Total de ingresos = suma de items pagados - descuentos de pedidos
        items_revenue = paid_items.aggregate(
            total=Sum("subtotal"))["total"] or 0

        # Restar descuentos de pedidos completamente pagados en el período.
        paid_order_ids = paid_items.values_list(
            'order_id', flat=True).distinct()
        if business_slug:
            # Prorratear el descuento de cada pedido por la parte de este negocio.
            from decimal import Decimal
            total_discounts = Decimal('0')
            disc_orders = Order.objects.filter(
                id__in=list(paid_order_ids), is_paid=True, discount__gt=0
            )
            for o in disc_orders:
                order_paid = OrderItem.objects.filter(
                    order_id=o.id, is_paid=True
                ).aggregate(t=Sum('subtotal'))['t'] or Decimal('0')
                biz_paid = OrderItem.objects.filter(
                    order_id=o.id, is_paid=True, business__slug=business_slug
                ).aggregate(t=Sum('subtotal'))['t'] or Decimal('0')
                if order_paid > 0:
                    total_discounts += (o.discount * biz_paid / order_paid)
        else:
            total_discounts = Order.objects.filter(
                id__in=paid_order_ids,
                is_paid=True,
                discount__gt=0
            ).aggregate(total=Sum('discount'))['total'] or 0

        total_revenue = items_revenue - total_discounts

        # Estadísticas por método de pago basadas en ITEMS
        # Prorratear descuentos proporcionalmente por método de pago
        by_payment_method = {}
        for method_code, method_name in Order.PaymentMethod.choices:
            method_stats = paid_items.filter(payment_method=method_code).aggregate(
                total=Sum("subtotal"), count=Count("id")
            )
            method_total = method_stats["total"] or 0
            # Prorratear descuento proporcionalmente
            if items_revenue > 0 and total_discounts > 0:
                method_total = method_total - (total_discounts * method_total / items_revenue)
            by_payment_method[method_code] = {
                "name": method_name,
                "total": str(round(method_total, 2)),
                "count": method_stats["count"] or 0,
            }

        # Items pagados sin método especificado
        no_method_stats = paid_items.filter(payment_method="").aggregate(
            total=Sum("subtotal"), count=Count("id")
        )
        other_total = no_method_stats["total"] or 0
        if items_revenue > 0 and total_discounts > 0:
            other_total = other_total - (total_discounts * other_total / items_revenue)
        by_payment_method["other"] = {
            "name": "Otro/Sin especificar",
            "total": str(round(other_total, 2)),
            "count": no_method_stats["count"] or 0,
        }

        # Conteo por estado de pedidos
        pending_count = orders.filter(status=Order.Status.PENDING).count()
        preparing_count = orders.filter(status=Order.Status.PREPARING).count()
        ready_count = orders.filter(status=Order.Status.READY).count()
        delivered_count = orders.filter(status=Order.Status.DELIVERED).count()
        cancelled_count = orders.filter(status=Order.Status.CANCELLED).count()

        # Total pendiente = suma de items NO pagados de pedidos del período
        order_ids = list(orders.values_list('id', flat=True))
        unpaid_items = OrderItem.objects.filter(
            order_id__in=order_ids,
            is_paid=False
        ).exclude(order__status=Order.Status.CANCELLED)
        if business_slug:
            unpaid_items = unpaid_items.filter(business__slug=business_slug)
        unpaid_total = unpaid_items.aggregate(
            total=Sum("subtotal"))["total"] or 0

        # Items totales pagados y pendientes
        total_paid_items = paid_items.count()
        total_unpaid_items = unpaid_items.count()

        # Domicilios: la tarifa de envío es del domiciliario, NO ingreso del
        # local, por eso va aparte y no se suma a total_revenue
        delivery_orders = orders.filter(
            order_type=Order.OrderType.DELIVERY
        ).exclude(status=Order.Status.CANCELLED)
        delivery_paid = delivery_orders.filter(is_paid=True).aggregate(
            total=Sum("delivery_fee"), count=Count("id"))

        return Response(
            {
                "period": date_filter,
                "total_orders": total_orders,
                "total_revenue": str(total_revenue),
                "unpaid_total": str(unpaid_total),
                "total_paid_items": total_paid_items,
                "total_unpaid_items": total_unpaid_items,
                "delivery": {
                    "orders_count": delivery_orders.count(),
                    "paid_count": delivery_paid["count"] or 0,
                    "fees_total": str(delivery_paid["total"] or 0),
                },
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
        from datetime import datetime as dt, time
        import zoneinfo

        # Usar la zona horaria de Colombia explícitamente
        tz = zoneinfo.ZoneInfo('America/Bogota')

        # Obtener la fecha/hora local actual en la zona horaria de Colombia
        local_now = timezone.localtime(timezone=tz)

        # Si hay parámetros de fecha personalizados o el filtro es "custom", usarlos
        if (start_date_param and end_date_param) or date_filter == "custom":
            if start_date_param and end_date_param:
                try:
                    # Parsear las fechas y convertirlas a timezone-aware en zona horaria de Colombia
                    start_date = dt.strptime(start_date_param, "%Y-%m-%d").replace(
                        hour=0, minute=0, second=0, microsecond=0, tzinfo=tz
                    )
                    end_date = dt.strptime(end_date_param, "%Y-%m-%d").replace(
                        hour=23, minute=59, second=59, microsecond=999999, tzinfo=tz
                    )
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
            # Usar la zona horaria de Colombia explícitamente
            today_date = local_now.date()
            # Crear start_date y end_date usando la fecha de hoy con zona horaria explícita
            today_start = dt.combine(today_date, time.min).replace(tzinfo=tz)
            max_time = time(23, 59, 59, 999999)
            today_end = dt.combine(today_date, max_time).replace(tzinfo=tz)
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
        from datetime import datetime, timedelta
        import zoneinfo

        date_filter = request.query_params.get("date", "today")
        start_date_param = request.query_params.get("start_date")
        end_date_param = request.query_params.get("end_date")
        business_slug = request.query_params.get("business")

        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Items pagados de pedidos creados en el rango (atribución por fecha de pedido).
        paid_items = OrderItem.objects.filter(
            is_paid=True,
            order__created_at__gte=start_date,
            order__created_at__lte=end_date
        ).exclude(order__status=Order.Status.CANCELLED)
        if business_slug:
            paid_items = paid_items.filter(business__slug=business_slug)

        # Obtener la zona horaria configurada
        tz = zoneinfo.ZoneInfo('America/Bogota')

        # Agrupar por día usando la fecha de creación del pedido en la zona horaria correcta
        revenue_by_day = (
            paid_items
            .annotate(
                date=TruncDate('order__created_at', tzinfo=tz)
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

        # Restar descuentos de pedidos completamente pagados, asociados al día del último pago
        discounted_orders = Order.objects.filter(
            id__in=paid_items.values_list('order_id', flat=True).distinct(),
            is_paid=True,
            discount__gt=0
        ).exclude(status=Order.Status.CANCELLED)

        for order in discounted_orders:
            last_paid_at = OrderItem.objects.filter(
                order_id=order.pk, is_paid=True
            ).order_by('-paid_at').values_list('paid_at', flat=True).first()
            if last_paid_at:
                date_str = last_paid_at.astimezone(tz).strftime('%Y-%m-%d')
                if date_str in revenue_dict:
                    # Prorratear el descuento por la parte del negocio activo.
                    discount = float(order.discount)
                    if business_slug:
                        order_paid = OrderItem.objects.filter(
                            order_id=order.pk, is_paid=True
                        ).aggregate(t=Sum('subtotal'))['t'] or 0
                        biz_paid = OrderItem.objects.filter(
                            order_id=order.pk, is_paid=True, business__slug=business_slug
                        ).aggregate(t=Sum('subtotal'))['t'] or 0
                        discount = discount * (float(biz_paid) / float(order_paid)) if order_paid else 0
                    revenue_dict[date_str] -= discount

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
        business_slug = request.query_params.get("business")

        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Items pagados de pedidos creados en el rango (atribución por fecha de pedido).
        paid_items = OrderItem.objects.filter(
            is_paid=True,
            order__created_at__gte=start_date,
            order__created_at__lte=end_date
        ).exclude(order__status=Order.Status.CANCELLED)
        if business_slug:
            paid_items = paid_items.filter(business__slug=business_slug)

        # Agrupar por producto y variante
        product_stats = (
            paid_items
            .select_related('product_variant', 'product_variant__product')
            .values(
                'product_variant__product__id',
                'product_variant__product__name',
                'product_variant__id',
                'product_variant__name',
                'product_variant__price'
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

            quantity_sold = item['quantity_sold'] or 0
            revenue = float(item['revenue'] or 0)

            data.append({
                "product_id": item['product_variant__product__id'],
                "variant_id": item['product_variant__id'],
                "product_name": product_name,
                "variant_name": variant_name,
                "display_name": display_name,
                "variant_price": float(item['product_variant__price'] or 0),
                "avg_unit_price": round(revenue / quantity_sold, 2) if quantity_sold else 0,
                "quantity_sold": quantity_sold,
                "revenue": revenue,
                "count": item['count'] or 0,
            })

        return Response({
            "data": data,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        })

    @action(detail=False, methods=["get"])
    def sales_by_hour(self, request):
        """Ventas agrupadas por hora del día para identificar horarios pico"""
        import zoneinfo

        date_filter = request.query_params.get("date", "month")
        start_date_param = request.query_params.get("start_date")
        end_date_param = request.query_params.get("end_date")

        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Obtener la zona horaria de Colombia
        tz = zoneinfo.ZoneInfo('America/Bogota')

        # Items pagados de pedidos creados en el rango (atribución por fecha de pedido).
        paid_items = OrderItem.objects.filter(
            is_paid=True,
            order__created_at__gte=start_date,
            order__created_at__lte=end_date
        ).exclude(order__status=Order.Status.CANCELLED)

        # Agrupar por hora del día (hora en que se creó el pedido)
        sales_by_hour = (
            paid_items
            .annotate(hour=ExtractHour('order__created_at', tzinfo=tz))
            .values('hour')
            .annotate(
                revenue=Sum('subtotal'),
                count=Count('id')
            )
            .order_by('hour')
        )

        # Crear diccionario con los datos existentes
        hour_dict = {}
        for item in sales_by_hour:
            hour_dict[item['hour']] = {
                'revenue': float(item['revenue'] or 0),
                'count': item['count'] or 0
            }

        # Restar descuentos asociados a la hora de creación del pedido
        discounted_orders = Order.objects.filter(
            id__in=paid_items.values_list('order_id', flat=True).distinct(),
            is_paid=True,
            discount__gt=0
        ).exclude(status=Order.Status.CANCELLED)

        for order in discounted_orders:
            hour = order.created_at.astimezone(tz).hour
            if hour in hour_dict:
                hour_dict[hour]['revenue'] -= float(order.discount)

        # Generar todas las horas del día (0-23)
        data = []
        for hour in range(24):
            hour_data = hour_dict.get(hour, {'revenue': 0, 'count': 0})
            # Formatear hora para mostrar (ej: "08:00", "14:00")
            hour_label = f"{hour:02d}:00"
            data.append({
                "hour": hour,
                "hour_label": hour_label,
                "revenue": hour_data['revenue'],
                "count": hour_data['count']
            })

        return Response({
            "data": data,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        })

    @action(detail=False, methods=["get"])
    def sales_by_weekday(self, request):
        """Ventas agrupadas por día de la semana"""
        import zoneinfo

        date_filter = request.query_params.get("date", "month")
        start_date_param = request.query_params.get("start_date")
        end_date_param = request.query_params.get("end_date")

        start_date, end_date = self._get_date_range(
            date_filter, start_date_param, end_date_param)

        # Obtener la zona horaria de Colombia
        tz = zoneinfo.ZoneInfo('America/Bogota')

        # Items pagados de pedidos creados en el rango (atribución por fecha de pedido).
        paid_items = OrderItem.objects.filter(
            is_paid=True,
            order__created_at__gte=start_date,
            order__created_at__lte=end_date
        ).exclude(order__status=Order.Status.CANCELLED)

        # Agrupar por día de la semana en que se creó el pedido (1=Domingo, 2=Lunes, ..., 7=Sábado en Django)
        sales_by_weekday = (
            paid_items
            .annotate(weekday=ExtractWeekDay('order__created_at', tzinfo=tz))
            .values('weekday')
            .annotate(
                revenue=Sum('subtotal'),
                count=Count('id')
            )
            .order_by('weekday')
        )

        # Crear diccionario con los datos existentes
        weekday_dict = {}
        for item in sales_by_weekday:
            weekday_dict[item['weekday']] = {
                'revenue': float(item['revenue'] or 0),
                'count': item['count'] or 0
            }

        # Restar descuentos asociados al día de la semana en que se creó el pedido
        discounted_orders = Order.objects.filter(
            id__in=paid_items.values_list('order_id', flat=True).distinct(),
            is_paid=True,
            discount__gt=0
        ).exclude(status=Order.Status.CANCELLED)

        for order in discounted_orders:
            # ExtractWeekDay de Django: 1=Domingo, 2=Lunes, ..., 7=Sábado
            weekday = order.created_at.astimezone(tz).isoweekday()
            # isoweekday: 1=Lunes...7=Domingo → convertir a Django format
            django_weekday = 1 if weekday == 7 else weekday + 1
            if django_weekday in weekday_dict:
                weekday_dict[django_weekday]['revenue'] -= float(order.discount)

        # Nombres de los días en español (Django: 1=Domingo, 2=Lunes, ..., 7=Sábado)
        weekday_names = {
            1: "Domingo",
            2: "Lunes",
            3: "Martes",
            4: "Miércoles",
            5: "Jueves",
            6: "Viernes",
            7: "Sábado"
        }

        # Ordenar empezando por Lunes (2) y terminando en Domingo (1)
        weekday_order = [2, 3, 4, 5, 6, 7, 1]

        data = []
        for weekday in weekday_order:
            weekday_data = weekday_dict.get(weekday, {'revenue': 0, 'count': 0})
            data.append({
                "weekday": weekday,
                "weekday_name": weekday_names[weekday],
                "revenue": weekday_data['revenue'],
                "count": weekday_data['count']
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
    permission_classes = [IsAuthenticated]

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
            with transaction.atomic():
                item.mark_as_paid(payment_method)

                # Actualizar estado de pago del pedido
                order = item.order
                order.update_payment_status()

            # Refrescar el item desde la base de datos
            item.refresh_from_db()

            broadcast_orders_update()
            return Response(OrderItemSerializer(item).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def unmark_paid(self, request, pk=None):
        """Desmarcar un item como pagado"""
        item = self.get_object()
        order = item.order

        with transaction.atomic():
            item.is_paid = False
            item.payment_method = ""
            item.paid_at = None
            item.save(update_fields=["is_paid", "payment_method", "paid_at"])

            # Actualizar estado de pago del pedido (ya no está todo pagado)
            Order.objects.filter(pk=order.pk).update(is_paid=False)

        broadcast_orders_update()
        return Response(OrderItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def change_payment_method(self, request, pk=None):
        """Cambiar el método de pago de un item"""
        item = self.get_object()
        serializer = MarkItemPaidSerializer(data=request.data)

        if serializer.is_valid():
            payment_method = serializer.validated_data.get(
                "payment_method", "")
            with transaction.atomic():
                item.change_payment_method(payment_method)

                # Actualizar estado de pago del pedido
                item.order.update_payment_status()

            broadcast_orders_update()
            return Response(OrderItemSerializer(item).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def mark_delivered(self, request, pk=None):
        """Marcar un item como entregado"""
        item = self.get_object()
        with transaction.atomic():
            item.mark_as_delivered()

            # Actualizar estado de entrega del pedido
            item.order.update_delivery_status()

        broadcast_orders_update()
        return Response(OrderItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def unmark_delivered(self, request, pk=None):
        """Desmarcar un item como entregado"""
        item = self.get_object()
        order = item.order

        with transaction.atomic():
            item.unmark_as_delivered()

            # Si el pedido estaba entregado, volver a preparando
            if order.status == Order.Status.DELIVERED:
                Order.objects.filter(pk=order.pk).update(
                    status=Order.Status.PREPARING,
                    completed_at=None
                )

        broadcast_orders_update()
        return Response(OrderItemSerializer(item).data)

    @action(detail=True, methods=["post"], url_path="prep-status")
    def set_prep_status(self, request, pk=None):
        """Cambiar el estado de preparación de un item (lo usa el KDS).

        La cocina marca SOLO sus items; el estado del pedido se recalcula a
        partir del avance de todos los items (sync_status_from_items).
        """
        item = self.get_object()
        new_status = request.data.get("prep_status")

        valid = {c[0] for c in OrderItem.PrepStatus.choices}
        if new_status not in valid:
            return Response(
                {"error": f"prep_status inválido. Opciones: {sorted(valid)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            item.prep_status = new_status
            item.save(update_fields=["prep_status"])

            # Recalcular el estado del pedido según el avance de las cocinas.
            item.order.sync_status_from_items()

        broadcast_orders_update()
        return Response(OrderItemSerializer(item).data)


class TableViewSet(viewsets.ModelViewSet):
    """
    CRUD de mesas y barras.

    Lectura pública (alimenta los selectores de mesa); creación, edición y
    desactivación restringidas a staff. La eliminación es lógica (is_active=False)
    para preservar el historial de pedidos que referencian la mesa.
    """
    serializer_class = TableSerializer
    # Sin paginación: los consumidores (selectores de mesa y juegos) esperan
    # una lista plana, no la respuesta paginada de DRF.
    pagination_class = None

    def get_queryset(self):
        qs = Table.objects.all().order_by("floor", "table_number")
        include_inactive = str(
            self.request.query_params.get("include_inactive", "")
        ).lower()
        if self.action == "list" and include_inactive not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return qs

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "stats"):
            return [IsAdminUser()]
        return [AllowAny()]

    def destroy(self, request, *args, **kwargs):
        """Baja lógica: desactiva la mesa en vez de borrarla."""
        table = self.get_object()
        table.is_active = False
        table.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="register-visit")
    def register_visit(self, request):
        """Registra una visita a una mesa (carta pública por QR)."""
        table_number = request.data.get("table_number")
        floor = request.data.get("floor", 2)

        if table_number is None or table_number == "":
            return Response(
                {"error": "table_number es requerido"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            table_number = int(table_number)
            floor = int(floor)
        except (ValueError, TypeError):
            return Response(
                {"error": "table_number y floor deben ser números"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buscar o crear la mesa (0 = Barra) dentro del piso
        table_name = "Barra" if table_number == 0 else f"Mesa {table_number}"
        table, created = Table.objects.get_or_create(
            table_number=table_number,
            floor=floor,
            defaults={
                "table_name": table_name,
                "is_active": True
            }
        )

        # Registrar la visita
        table.register_visit()

        return Response({
            "table_number": table.table_number,
            "floor": table.floor,
            "table_name": table.table_name,
            "label": table.label,
            "visit_count": table.visit_count
        })

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """Obtiene estadísticas de visitas por mesa"""
        tables = Table.objects.filter(is_active=True).order_by("-visit_count")

        data = [
            {
                "table_number": table.table_number,
                "floor": table.floor,
                "table_name": table.table_name,
                "label": table.label,
                "visit_count": table.visit_count
            }
            for table in tables
        ]

        return Response({
            "tables": data,
            "total_visits": sum(t.visit_count for t in tables)
        })


class PageVisitViewSet(viewsets.ViewSet):
    """ViewSet para tracking de visitas a páginas"""
    permission_classes = [AllowAny]

    # Mapeo de rutas a nombres descriptivos (solo rutas que existen)
    PAGE_NAMES = {
        "/": "Carta Pública",
        "/game": "Juegos",
    }

    @action(detail=False, methods=["post"], url_path="register-visit")
    def register_visit(self, request):
        """Registra una visita a una página"""
        path = request.data.get("path")

        if not path:
            return Response(
                {"error": "path es requerido"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Normalizar la ruta (asegurar que empiece con /)
        if not path.startswith("/"):
            path = "/" + path

        # Determinar el nombre de la página
        page_name = self.PAGE_NAMES.get(path, f"Página {path}")

        # Buscar o crear la página
        page, created = PageVisit.objects.get_or_create(
            path=path,
            defaults={
                "page_name": page_name,
                "is_active": True
            }
        )

        # Registrar la visita
        page.register_visit()

        return Response({
            "path": page.path,
            "page_name": page.page_name,
            "visit_count": page.visit_count
        })

    @action(detail=False, methods=["get"], url_path="stats", permission_classes=[IsAdminUser])
    def stats(self, request):
        """Obtiene estadísticas de visitas por página"""
        pages = PageVisit.objects.filter(is_active=True).order_by("-visit_count")

        data = [
            {
                "path": page.path,
                "page_name": page.page_name,
                "visit_count": page.visit_count
            }
            for page in pages
        ]

        return Response({
            "pages": data,
            "total_visits": sum(p.visit_count for p in pages)
        })


class PublicOrderViewSet(viewsets.ViewSet):
    """
    ViewSet público para que clientes consulten su pedido.
    No requiere autenticación.
    """
    permission_classes = [AllowAny]

    @action(detail=False, methods=["post"])
    def verify(self, request):
        """Verificar pedido con código de acceso"""
        access_code = request.data.get("access_code", "").strip().upper()

        if not access_code:
            return Response(
                {"error": "access_code es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Visible mientras esté activo O entregado pero sin pagar
        order = Order.objects.prefetch_related(
            "items", "items__product_variant__product", "items__product_variant__product__category"
        ).filter(
            access_code=access_code,
        ).filter(
            Q(status__in=[Order.Status.PENDING, Order.Status.PREPARING, Order.Status.READY])
            | Q(status=Order.Status.DELIVERED, is_paid=False)
        ).exclude(
            status=Order.Status.CANCELLED,
        ).first()

        if not order:
            return Response(
                {"error": "Código incorrecto o pedido no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PublicOrderDetailSerializer(order)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="popular-products")
    def popular_products(self, request):
        """Retorna top 6 productos más pedidos en los últimos 7 días"""
        seven_days_ago = timezone.now() - timedelta(days=7)

        top_products = (
            OrderItem.objects.filter(
                order__created_at__gte=seven_days_ago,
            )
            .exclude(order__status=Order.Status.CANCELLED)
            .values(
                "product_variant__product__id",
                "product_variant__product__name",
                "product_variant__product__image_url",
                "product_variant__product__category__name",
                "product_variant__name",
                "product_variant__price",
            )
            .annotate(total_ordered=Count("id"))
            .order_by("-total_ordered")[:6]
        )

        data = [
            {
                "product_name": item["product_variant__product__name"],
                "variant_name": item["product_variant__name"],
                "image_url": item["product_variant__product__image_url"],
                "category": item["product_variant__product__category__name"],
                "price": str(item["product_variant__price"]),
            }
            for item in top_products
        ]

        return Response(data)


class CustomerOrderThrottle(UserRateThrottle):
    """Límite anti-spam para la creación de pedidos del cliente."""
    scope = "customer_orders"
    rate = "30/hour"


class CustomerOrderViewSet(mixins.CreateModelMixin,
                           mixins.ListModelMixin,
                           mixins.RetrieveModelMixin,
                           viewsets.GenericViewSet):
    """
    Pedidos a domicilio del cliente autenticado con Google.

    create: Crea un pedido a domicilio propio (entra directo a la cola como PENDING).
    list: 'Mis pedidos' del cliente autenticado.
    retrieve: Detalle/seguimiento de un pedido propio.
    config: Configuración pública del local (tarifa de envío, domicilios activos).
    """
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        # Solo limitar la creación de pedidos, no las consultas
        if self.action == "create":
            return [CustomerOrderThrottle()]
        return []

    def get_queryset(self):
        return (
            Order.objects
            .filter(user=self.request.user)
            .prefetch_related(
                "items",
                "items__product_variant__product",
                "items__product_variant__product__category",
            )
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return CustomerOrderCreateSerializer
        if self.action == "list":
            return CustomerOrderListSerializer
        return OrderDetailSerializer

    def perform_create(self, serializer):
        cfg = StoreSettings.load()
        if not cfg.is_open:
            raise ValidationError(
                "El local está cerrado en este momento. No es posible hacer pedidos.")
        if not cfg.customer_ordering_enabled:
            raise ValidationError(
                "Los pedidos a domicilio están deshabilitados por el momento.")

        # El servidor decide la tarifa de envío; nunca se confía en el cliente
        instance = serializer.save(
            user=self.request.user, delivery_fee=cfg.delivery_fee)
        broadcast_orders_update()
        return instance

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def config(self, request):
        """Configuración pública: estado del local, tarifa, zona y disponibilidad."""
        cfg = StoreSettings.load()
        return Response({
            "is_open": cfg.is_open,
            "customer_ordering_enabled": cfg.customer_ordering_enabled,
            "can_order": cfg.is_open and cfg.customer_ordering_enabled,
            "delivery_fee": str(cfg.delivery_fee),
            # Con esto el checkout dibuja la zona y valida el pin: si hay
            # polígono manda él, y si viene vacío se usa el círculo.
            "delivery_radius_km": float(cfg.delivery_radius_km),
            "delivery_area": cfg.delivery_area or [],
        })
