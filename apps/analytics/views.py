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
from apps.expenses.models import OperationalExpense, ExpenseCategory, ExpenseKind
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

    def _resolve_month_anchor(self, request):
        """Primer dia del mes a analizar.

        Lee ?year= y ?month= (1-12); sin parametros validos devuelve el
        primer dia del mes en curso. Este es el unico gancho temporal que
        necesita el frontend para ver cualquier mes.
        """
        local_now = timezone.localtime()
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        if year and month:
            try:
                y, m = int(year), int(month)
                if 1 <= m <= 12:
                    return local_now.replace(
                        year=y, month=m, day=1,
                        hour=0, minute=0, second=0, microsecond=0
                    )
            except (ValueError, TypeError):
                pass
        return local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _month_range_for(self, anchor):
        """Rango (start, end) del mes del ancla.

        Si el ancla es el mes en curso, el fin es 'ahora' (no cuenta dias
        futuros); si es un mes pasado, el fin es el ultimo instante del mes.
        """
        start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        local_now = timezone.localtime()
        if start.year == local_now.year and start.month == local_now.month:
            end = local_now.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            next_month = start + relativedelta(months=1)
            end = (next_month - timedelta(days=1)).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
        return start, end

    def _previous_month_range_for(self, anchor):
        """Rango del mes anterior al ancla (mes completo)."""
        return self._month_range_for(anchor - relativedelta(months=1))

    def _year_ago_month_range_for(self, anchor):
        """Rango del mismo mes del ano anterior al ancla (mes completo)."""
        return self._month_range_for(anchor - relativedelta(years=1))

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

    def _expenses_qs(self, start_date, end_date, business_slug=None):
        """Gastos pagados del periodo, sin distinguir tipo.

        Usa expense_date para determinar el período del gasto.
        """
        expenses = OperationalExpense.objects.filter(
            status=OperationalExpense.Status.PAID,
            expense_date__gte=start_date.date(),
            expense_date__lte=end_date.date()
        )
        if business_slug:
            expenses = expenses.filter(business__slug=business_slug)
        return expenses

    def _calculate_operational_expenses(self, start_date, end_date, business_slug=None):
        """Calcula el gasto corriente pagado del periodo (OPEX).

        Excluye la inversion a proposito: comprar una nevera o montar un piso
        consume caja pero no es costo del mes, y meterlo aqui hace ver en
        perdida un mes rentable.
        """
        expenses = self._expenses_qs(start_date, end_date, business_slug).filter(
            kind=ExpenseKind.OPERATIONAL
        )
        return expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    def _calculate_investment(self, start_date, end_date, business_slug=None):
        """Calcula la inversion pagada del periodo (CAPEX).

        No resta del margen operativo; se reporta aparte para poder leer la
        caja que quedo despues de invertir.
        """
        expenses = self._expenses_qs(start_date, end_date, business_slug).filter(
            kind=ExpenseKind.INVESTMENT
        )
        return expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    def _calculate_orders_count(self, start_date, end_date, business_slug=None):
        """Cuenta pedidos distintos con items pagados en el rango.

        Usa el mismo criterio que _calculate_revenue (items pagados,
        excluyendo pedidos cancelados). Por negocio cuenta los pedidos que
        tengan al menos un item pagado de ese negocio.
        """
        paid_items = OrderItem.objects.filter(
            is_paid=True,
            order__created_at__gte=start_date,
            order__created_at__lte=end_date
        ).exclude(order__status=Order.Status.CANCELLED)
        if business_slug:
            paid_items = paid_items.filter(
                product_variant__product__business__slug=business_slug
            )
        return paid_items.values('order_id').distinct().count()

    def _calculate_percentage_change(self, current, previous):
        """Calcula el porcentaje de cambio entre dos valores"""
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round(((current - previous) / previous) * 100, 1)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        KPIs principales: ingresos, gastos, ganancia neta, margen, pedidos y
        ticket promedio. Compara el mes ancla contra el mes anterior y contra
        el mismo mes del ano pasado (interanual).
        """
        business_slug = request.query_params.get('business')
        anchor = self._resolve_month_anchor(request)
        current_start, current_end = self._month_range_for(anchor)
        prev_start, prev_end = self._previous_month_range_for(anchor)
        ya_start, ya_end = self._year_ago_month_range_for(anchor)

        def compute(start, end):
            revenue = self._calculate_revenue(start, end, business_slug)
            inventory = self._calculate_inventory_expenses(start, end, business_slug)
            operational = self._calculate_operational_expenses(start, end, business_slug)
            investment = self._calculate_investment(start, end, business_slug)
            total_expenses = inventory + operational
            profit = revenue - total_expenses
            cash_after_investment = profit - investment
            orders = self._calculate_orders_count(start, end, business_slug)
            avg_ticket = (revenue / orders) if orders > 0 else Decimal('0')
            margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
            return {
                'revenue': revenue,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'investment': investment,
                'total_expenses': total_expenses,
                'net_profit': profit,
                'cash_after_investment': cash_after_investment,
                'orders_count': orders,
                'avg_ticket': avg_ticket,
                'profit_margin': margin,
            }

        cur = compute(current_start, current_end)
        prev = compute(prev_start, prev_end)
        ya = compute(ya_start, ya_end)

        def block(key):
            return {
                'value': float(cur[key]),
                'previous': float(prev[key]),
                'change': self._calculate_percentage_change(cur[key], prev[key]),
                'year_ago': float(ya[key]),
                'change_yoy': self._calculate_percentage_change(cur[key], ya[key]),
            }

        return Response({
            'revenue': block('revenue'),
            'total_expenses': block('total_expenses'),
            'inventory_expenses': block('inventory_expenses'),
            'operational_expenses': block('operational_expenses'),
            'investment': block('investment'),
            'net_profit': block('net_profit'),
            'cash_after_investment': block('cash_after_investment'),
            'orders_count': block('orders_count'),
            'avg_ticket': block('avg_ticket'),
            'profit_margin': {
                'value': cur['profit_margin'],
                'previous': prev['profit_margin'],
                'year_ago': ya['profit_margin'],
            },
            'period': {
                'current_month': current_start.strftime('%B %Y'),
                'previous_month': prev_start.strftime('%B %Y'),
                'year_ago_month': ya_start.strftime('%B %Y'),
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
            investment = float(self._calculate_investment(month_start, month_end, business_slug))
            total_expenses = inventory + operational
            profit = revenue - total_expenses

            data.append({
                'month': month_start.strftime('%Y-%m'),
                'month_label': month_start.strftime('%b %Y'),
                'revenue': revenue,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'investment': investment,
                'total_expenses': total_expenses,
                'profit': profit,
                'cash_after_investment': profit - investment,
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
        anchor = self._resolve_month_anchor(request)
        current_start, current_end = self._month_range_for(anchor)

        # Gastos operativos por categoria
        # Usa expense_date para determinar el período del gasto
        operational_qs = OperationalExpense.objects.filter(
            status=OperationalExpense.Status.PAID,
            expense_date__gte=current_start.date(),
            expense_date__lte=current_end.date()
        )
        if business_slug:
            operational_qs = operational_qs.filter(business__slug=business_slug)
        def by_category(qs):
            return qs.values(
                'category__name', 'category__slug', 'category__color', 'category__icon'
            ).annotate(
                total=Sum('amount'),
                count=Count('id')
            ).order_by('-total')

        # El pie de gastos solo muestra gasto corriente: la inversion va aparte
        # para que los porcentajes no mezclen costo del mes con compra de activos.
        operational_by_category = by_category(
            operational_qs.filter(kind=ExpenseKind.OPERATIONAL)
        )
        investment_by_category = by_category(
            operational_qs.filter(kind=ExpenseKind.INVESTMENT)
        )

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

        investment_categories = []
        for item in investment_by_category:
            investment_categories.append({
                'name': item['category__name'],
                'slug': item['category__slug'],
                'color': item['category__color'],
                'icon': item['category__icon'],
                'total': float(item['total']),
                'count': item['count'],
                'type': 'investment'
            })
        investment_total = sum(c['total'] for c in investment_categories)
        for category in investment_categories:
            category['percentage'] = (
                round((category['total'] / investment_total * 100), 1)
                if investment_total > 0 else 0
            )

        return Response({
            'categories': categories,
            'total': float(total),
            'investment_categories': investment_categories,
            'investment_total': float(investment_total),
            'period': {
                'start': current_start.isoformat(),
                'end': current_end.isoformat(),
            }
        })

    @action(detail=False, methods=['get'])
    def daily_trend(self, request):
        """
        Datos dia a dia del mes ancla (hasta hoy si es el mes en curso).
        """
        business_slug = request.query_params.get('business')
        anchor = self._resolve_month_anchor(request)
        current_start, current_end = self._month_range_for(anchor)

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
            investment = float(self._calculate_investment(day_start, day_end, business_slug))
            total_expenses = inventory + operational
            profit = revenue - total_expenses

            data.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'date_label': current_date.strftime('%d %b'),
                'revenue': revenue,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'investment': investment,
                'total_expenses': total_expenses,
                'profit': profit,
                'cash_after_investment': profit - investment,
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
        Comparacion del mes ancla vs el mes anterior y vs el mismo mes del
        ano pasado. Datos para bar chart comparativo.
        """
        business_slug = request.query_params.get('business')
        anchor = self._resolve_month_anchor(request)
        current_start, current_end = self._month_range_for(anchor)
        prev_start, prev_end = self._previous_month_range_for(anchor)
        ya_start, ya_end = self._year_ago_month_range_for(anchor)

        def month_block(start, end):
            revenue = float(self._calculate_revenue(start, end, business_slug))
            inventory = float(self._calculate_inventory_expenses(start, end, business_slug))
            operational = float(self._calculate_operational_expenses(start, end, business_slug))
            investment = float(self._calculate_investment(start, end, business_slug))
            expenses = inventory + operational
            profit = revenue - expenses
            return {
                'label': start.strftime('%B %Y'),
                'revenue': revenue,
                'expenses': expenses,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'investment': investment,
                'profit': profit,
                'cash_after_investment': profit - investment,
            }

        current = month_block(current_start, current_end)
        previous = month_block(prev_start, prev_end)
        year_ago = month_block(ya_start, ya_end)

        return Response({
            'current_month': current,
            'previous_month': previous,
            'year_ago_month': year_ago,
            'changes': {
                'revenue': self._calculate_percentage_change(current['revenue'], previous['revenue']),
                'expenses': self._calculate_percentage_change(current['expenses'], previous['expenses']),
                'profit': self._calculate_percentage_change(current['profit'], previous['profit']),
                'revenue_yoy': self._calculate_percentage_change(current['revenue'], year_ago['revenue']),
                'expenses_yoy': self._calculate_percentage_change(current['expenses'], year_ago['expenses']),
                'profit_yoy': self._calculate_percentage_change(current['profit'], year_ago['profit']),
                'investment': self._calculate_percentage_change(current['investment'], previous['investment']),
            }
        })

    @action(detail=False, methods=['get'])
    def by_business(self, request):
        """
        Desglose del mes ancla por cada negocio (Frostbyte vs Frostbyte Food)
        + el consolidado. Pensado para el dashboard del dueño.
        Parametros: year + month (mes concreto) tienen prioridad; si no,
        months_offset (0 = mes actual, 1 = mes anterior, ...) por compatibilidad.
        """
        if request.query_params.get('year') and request.query_params.get('month'):
            anchor = self._resolve_month_anchor(request)
        else:
            try:
                months_offset = max(0, min(int(request.query_params.get('months_offset', 0)), 36))
            except (ValueError, TypeError):
                return Response({'error': 'months_offset debe ser un entero >= 0.'}, status=400)
            anchor = timezone.localtime().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ) - relativedelta(months=months_offset)

        current_start, current_end = self._month_range_for(anchor)

        def metrics(business_slug):
            revenue = float(self._calculate_revenue(current_start, current_end, business_slug))
            inventory = float(self._calculate_inventory_expenses(current_start, current_end, business_slug))
            operational = float(self._calculate_operational_expenses(current_start, current_end, business_slug))
            investment = float(self._calculate_investment(current_start, current_end, business_slug))
            expenses = inventory + operational
            profit = revenue - expenses
            margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
            return {
                'revenue': revenue,
                'inventory_expenses': inventory,
                'operational_expenses': operational,
                'investment': investment,
                'total_expenses': expenses,
                'profit': profit,
                'profit_margin': margin,
                'cash_after_investment': profit - investment,
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
