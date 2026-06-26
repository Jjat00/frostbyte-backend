from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.accounts.permissions import IsAdminUser
from django.db.models import Sum, Count, Case, When, Value, DecimalField, F
from django.db.models.functions import TruncMonth, TruncDate
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from dateutil.relativedelta import relativedelta

from apps.orders.models import Order, OrderItem
from apps.inventory.models import PurchaseOrder
from apps.expenses.models import OperationalExpense, ExpenseCategory
from apps.business.models import Business


class FinancialAnalyticsViewSet(viewsets.ViewSet):
    """
    ViewSet para estadisticas financieras del dashboard ejecutivo.
    Solo accesible por administradores.

    Todos los endpoints aceptan ?business=<slug> para ver un negocio
    concreto (Frostbyte / Frostbyte Food). Sin ese parametro devuelven el
    consolidado de todos los negocios.

    Los ingresos por negocio se derivan de la cadena
    OrderItem -> product_variant -> product -> business, sin tocar el modelo
    de pedidos. El descuento de un pedido (que es global) se prorratea por la
    proporcion del subtotal de cada negocio, de modo que la suma de los
    negocios siempre cuadra con el consolidado.
    """
    permission_classes = [IsAdminUser]

    def _get_date_range(self, months=12):
        """Helper para obtener rango de fechas"""
        local_now = timezone.localtime()
        end_date = local_now.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        start_date = (local_now - relativedelta(months=months)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return start_date, end_date

    def _get_current_month_range(self):
        """Obtiene el rango del mes actual"""
        local_now = timezone.localtime()
        start_of_month = local_now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end_of_month = local_now.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        return start_of_month, end_of_month

    def _get_previous_month_range(self):
        """Obtiene el rango del mes anterior"""
        local_now = timezone.localtime()
        first_day_this_month = local_now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        last_day_last_month = first_day_this_month - timedelta(days=1)
        start_of_last_month = last_day_last_month.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end_of_last_month = last_day_last_month.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        return start_of_last_month, end_of_last_month

    def _calculate_revenue(self, start_date, end_date, business_slug=None):
        """Calcula ingresos de items pagados excluyendo ordenes canceladas,
        restando descuentos de ordenes completamente pagadas.

        Atribuye la venta a la fecha de creación del pedido (no a la de pago)
        para que un pedido tomado a las 11pm y pagado pasada medianoche cuente
        en el día en que se generó.

        Si se pasa business_slug, solo cuenta los items de ese negocio y
        prorratea el descuento de cada pedido segun la proporcion del subtotal
        del negocio.
        """
        paid_items = OrderItem.objects.filter(
            is_paid=True,
            order__created_at__gte=start_date,
            order__created_at__lte=end_date
        ).exclude(order__status=Order.Status.CANCELLED)

        if not business_slug:
            items_revenue = paid_items.aggregate(total=Sum('subtotal'))['total'] or Decimal('0')
            # Restar descuentos de ordenes completamente pagadas con descuento
            paid_order_ids = paid_items.values_list('order_id', flat=True).distinct()
            total_discounts = Order.objects.filter(
                id__in=paid_order_ids,
                is_paid=True,
                discount__gt=0
            ).aggregate(total=Sum('discount'))['total'] or Decimal('0')
            return items_revenue - total_discounts

        # --- Por negocio ---
        business_items = paid_items.filter(
            product_variant__product__business__slug=business_slug
        )
        items_revenue = business_items.aggregate(total=Sum('subtotal'))['total'] or Decimal('0')

        # Prorratear el descuento de cada pedido con descuento que tenga items
        # de este negocio: descuento * (subtotal_negocio / subtotal_total_pedido).
        # Se resuelve en 2 queries (ordenes con descuento + totales por orden con
        # agregacion condicional) en lugar de 2 por orden.
        orders = Order.objects.filter(
            id__in=business_items.values_list('order_id', flat=True).distinct(),
            is_paid=True,
            discount__gt=0,
        )
        discount_map = {o.id: o.discount for o in orders}

        prorated_discount = Decimal('0')
        if discount_map:
            totals = (
                paid_items.filter(order_id__in=discount_map.keys())
                .values('order_id')
                .annotate(
                    order_total=Sum('subtotal'),
                    biz_total=Sum(
                        Case(
                            When(
                                product_variant__product__business__slug=business_slug,
                                then=F('subtotal'),
                            ),
                            default=Value(Decimal('0')),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        )
                    ),
                )
            )
            for row in totals:
                order_total = row['order_total'] or Decimal('0')
                biz_total = row['biz_total'] or Decimal('0')
                if order_total > 0:
                    prorated_discount += (discount_map[row['order_id']] * biz_total / order_total)

        return items_revenue - prorated_discount

    def _calculate_inventory_expenses(self, start_date, end_date, business_slug=None):
        """Calcula gastos de inventario (ordenes de compra completadas).
        Usa created_at para determinar el período del gasto.
        """
        purchases = PurchaseOrder.objects.filter(
            status=PurchaseOrder.Status.PURCHASED,
            created_at__gte=start_date,
            created_at__lte=end_date
        )
        if business_slug:
            purchases = purchases.filter(business__slug=business_slug)
        return purchases.aggregate(total=Sum('actual_total'))['total'] or Decimal('0')

    def _calculate_operational_expenses(self, start_date, end_date, business_slug=None):
        """Calcula gastos operativos pagados.
        Usa expense_date para determinar el período del gasto.
        """
        expenses = OperationalExpense.objects.filter(
            status=OperationalExpense.Status.PAID,
            expense_date__gte=start_date.date(),
            expense_date__lte=end_date.date()
        )
        if business_slug:
            expenses = expenses.filter(business__slug=business_slug)
        return expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    def _calculate_percentage_change(self, current, previous):
        """Calcula el porcentaje de cambio entre dos valores"""
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round(((current - previous) / previous) * 100, 1)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        KPIs principales: ingresos, gastos, ganancia neta, margen
        """
        business_slug = request.query_params.get('business')
        # Rango del mes actual
        current_start, current_end = self._get_current_month_range()
        # Rango del mes anterior
        prev_start, prev_end = self._get_previous_month_range()

        # Calculos mes actual
        current_revenue = self._calculate_revenue(current_start, current_end, business_slug)
        current_inventory = self._calculate_inventory_expenses(current_start, current_end, business_slug)
        current_operational = self._calculate_operational_expenses(current_start, current_end, business_slug)
        current_total_expenses = current_inventory + current_operational
        current_profit = current_revenue - current_total_expenses
        current_margin = round((current_profit / current_revenue * 100), 1) if current_revenue > 0 else 0

        # Calculos mes anterior
        prev_revenue = self._calculate_revenue(prev_start, prev_end, business_slug)
        prev_inventory = self._calculate_inventory_expenses(prev_start, prev_end, business_slug)
        prev_operational = self._calculate_operational_expenses(prev_start, prev_end, business_slug)
        prev_total_expenses = prev_inventory + prev_operational
        prev_profit = prev_revenue - prev_total_expenses

        return Response({
            'revenue': {
                'value': float(current_revenue),
                'previous': float(prev_revenue),
                'change': self._calculate_percentage_change(current_revenue, prev_revenue),
            },
            'total_expenses': {
                'value': float(current_total_expenses),
                'previous': float(prev_total_expenses),
                'change': self._calculate_percentage_change(current_total_expenses, prev_total_expenses),
            },
            'inventory_expenses': {
                'value': float(current_inventory),
                'previous': float(prev_inventory),
                'change': self._calculate_percentage_change(current_inventory, prev_inventory),
            },
            'operational_expenses': {
                'value': float(current_operational),
                'previous': float(prev_operational),
                'change': self._calculate_percentage_change(current_operational, prev_operational),
            },
            'net_profit': {
                'value': float(current_profit),
                'previous': float(prev_profit),
                'change': self._calculate_percentage_change(current_profit, prev_profit),
            },
            'profit_margin': {
                'value': current_margin,
                'previous': round((prev_profit / prev_revenue * 100), 1) if prev_revenue > 0 else 0,
            },
            'period': {
                'current_month': current_start.strftime('%B %Y'),
                'previous_month': prev_start.strftime('%B %Y'),
            }
        })

    @action(detail=False, methods=['get'])
    def monthly_trend(self, request):
        """
        Datos mes a mes para grafica de lineas.
        Parametros: months (default 12)
        """
        try:
            months = max(1, min(int(request.query_params.get('months', 12)), 36))
        except (ValueError, TypeError):
            return Response({'error': 'months debe ser un entero entre 1 y 36.'}, status=400)
        business_slug = request.query_params.get('business')
        start_date, end_date = self._get_date_range(months)

        data = []
        current_date = start_date

        while current_date <= end_date:
            month_start = current_date.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            next_month = month_start + relativedelta(months=1)
            month_end = (next_month - timedelta(days=1)).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )

            # Limitar al mes actual si es el ultimo mes
            if month_end > end_date:
                month_end = end_date

            revenue = float(self._calculate_revenue(month_start, month_end, business_slug))
            inventory = float(self._calculate_inventory_expenses(month_start, month_end, business_slug))
            operational = float(self._calculate_operational_expenses(month_start, month_end, business_slug))
            total_expenses = inventory + operational
            profit = revenue - total_expenses

            data.append({
                'month': month_start.strftime('%Y-%m'),
                'month_label': month_start.strftime('%b %Y'),
                'revenue': revenue,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'total_expenses': total_expenses,
                'profit': profit,
            })

            current_date = next_month

        return Response({
            'data': data,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
        })

    @action(detail=False, methods=['get'])
    def expenses_breakdown(self, request):
        """
        Gastos por categoria para pie chart.
        Incluye gastos operativos por categoria y gastos de inventario.
        """
        business_slug = request.query_params.get('business')
        current_start, current_end = self._get_current_month_range()

        # Gastos operativos por categoria
        # Usa expense_date para determinar el período del gasto
        operational_qs = OperationalExpense.objects.filter(
            status=OperationalExpense.Status.PAID,
            expense_date__gte=current_start.date(),
            expense_date__lte=current_end.date()
        )
        if business_slug:
            operational_qs = operational_qs.filter(business__slug=business_slug)
        operational_by_category = operational_qs.values(
            'category__name', 'category__slug', 'category__color', 'category__icon'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Gastos de inventario
        inventory_total = self._calculate_inventory_expenses(current_start, current_end, business_slug)

        categories = []
        for item in operational_by_category:
            categories.append({
                'name': item['category__name'],
                'slug': item['category__slug'],
                'color': item['category__color'],
                'icon': item['category__icon'],
                'total': float(item['total']),
                'count': item['count'],
                'type': 'operational'
            })

        # Agregar inventario como categoria adicional
        if inventory_total > 0:
            inventory_count_qs = PurchaseOrder.objects.filter(
                status=PurchaseOrder.Status.PURCHASED,
                created_at__gte=current_start,
                created_at__lte=current_end
            )
            if business_slug:
                inventory_count_qs = inventory_count_qs.filter(business__slug=business_slug)
            categories.append({
                'name': 'Inventario',
                'slug': 'inventario',
                'color': 'blue',
                'icon': 'Package',
                'total': float(inventory_total),
                'count': inventory_count_qs.count(),
                'type': 'inventory'
            })

        # Calcular porcentajes
        total = sum(c['total'] for c in categories)
        for category in categories:
            category['percentage'] = round((category['total'] / total * 100), 1) if total > 0 else 0

        return Response({
            'categories': categories,
            'total': float(total),
            'period': {
                'start': current_start.isoformat(),
                'end': current_end.isoformat(),
            }
        })

    @action(detail=False, methods=['get'])
    def daily_trend(self, request):
        """
        Datos dia a dia para el mes actual.
        """
        business_slug = request.query_params.get('business')
        current_start, current_end = self._get_current_month_range()

        data = []
        current_date = current_start

        while current_date <= current_end:
            day_start = current_date.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = current_date.replace(
                hour=23, minute=59, second=59, microsecond=999999
            )

            revenue = float(self._calculate_revenue(day_start, day_end, business_slug))
            inventory = float(self._calculate_inventory_expenses(day_start, day_end, business_slug))
            operational = float(self._calculate_operational_expenses(day_start, day_end, business_slug))
            total_expenses = inventory + operational
            profit = revenue - total_expenses

            data.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'date_label': current_date.strftime('%d %b'),
                'revenue': revenue,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'total_expenses': total_expenses,
                'profit': profit,
            })

            current_date += timedelta(days=1)

        return Response({
            'data': data,
            'period': {
                'start': current_start.isoformat(),
                'end': current_end.isoformat(),
            }
        })

    @action(detail=False, methods=['get'])
    def comparison(self, request):
        """
        Comparacion este mes vs mes anterior.
        Datos para bar chart comparativo.
        """
        business_slug = request.query_params.get('business')
        current_start, current_end = self._get_current_month_range()
        prev_start, prev_end = self._get_previous_month_range()

        # Este mes
        current_revenue = float(self._calculate_revenue(current_start, current_end, business_slug))
        current_inventory = float(self._calculate_inventory_expenses(current_start, current_end, business_slug))
        current_operational = float(self._calculate_operational_expenses(current_start, current_end, business_slug))
        current_expenses = current_inventory + current_operational
        current_profit = current_revenue - current_expenses

        # Mes anterior
        prev_revenue = float(self._calculate_revenue(prev_start, prev_end, business_slug))
        prev_inventory = float(self._calculate_inventory_expenses(prev_start, prev_end, business_slug))
        prev_operational = float(self._calculate_operational_expenses(prev_start, prev_end, business_slug))
        prev_expenses = prev_inventory + prev_operational
        prev_profit = prev_revenue - prev_expenses

        return Response({
            'current_month': {
                'label': current_start.strftime('%B %Y'),
                'revenue': current_revenue,
                'expenses': current_expenses,
                'inventory_expenses': current_inventory,
                'operational_expenses': current_operational,
                'profit': current_profit,
            },
            'previous_month': {
                'label': prev_start.strftime('%B %Y'),
                'revenue': prev_revenue,
                'expenses': prev_expenses,
                'inventory_expenses': prev_inventory,
                'operational_expenses': prev_operational,
                'profit': prev_profit,
            },
            'changes': {
                'revenue': self._calculate_percentage_change(current_revenue, prev_revenue),
                'expenses': self._calculate_percentage_change(current_expenses, prev_expenses),
                'profit': self._calculate_percentage_change(current_profit, prev_profit),
            }
        })

    @action(detail=False, methods=['get'])
    def by_business(self, request):
        """
        Desglose del mes actual por cada negocio (Frostbyte vs Frostbyte Food)
        + el consolidado. Pensado para el dashboard del dueño.
        Parametro opcional: months_offset (0 = mes actual, 1 = mes anterior, ...).
        """
        try:
            months_offset = max(0, min(int(request.query_params.get('months_offset', 0)), 36))
        except (ValueError, TypeError):
            return Response({'error': 'months_offset debe ser un entero >= 0.'}, status=400)

        if months_offset == 0:
            current_start, current_end = self._get_current_month_range()
        else:
            target = timezone.localtime() - relativedelta(months=months_offset)
            current_start = target.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            next_month = current_start + relativedelta(months=1)
            current_end = (next_month - timedelta(days=1)).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )

        def metrics(business_slug):
            revenue = float(self._calculate_revenue(current_start, current_end, business_slug))
            inventory = float(self._calculate_inventory_expenses(current_start, current_end, business_slug))
            operational = float(self._calculate_operational_expenses(current_start, current_end, business_slug))
            expenses = inventory + operational
            profit = revenue - expenses
            margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
            return {
                'revenue': revenue,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'total_expenses': expenses,
                'profit': profit,
                'profit_margin': margin,
            }

        businesses = []
        for business in Business.objects.filter(is_active=True).order_by('display_order', 'name'):
            businesses.append({
                'id': business.id,
                'name': business.name,
                'slug': business.slug,
                'floor': business.floor,
                'color': business.color,
                **metrics(business.slug),
            })

        return Response({
            'period': {
                'label': current_start.strftime('%B %Y'),
                'start': current_start.isoformat(),
                'end': current_end.isoformat(),
            },
            'consolidated': metrics(None),
            'businesses': businesses,
        })
