from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.accounts.permissions import IsAdminUser
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta

from .models import ExpenseCategory, OperationalExpense, RecurringExpenseTemplate
from .serializers import (
    ExpenseCategorySerializer,
    OperationalExpenseListSerializer,
    OperationalExpenseDetailSerializer,
    OperationalExpenseCreateSerializer,
    RecurringExpenseTemplateSerializer,
    MarkExpensePaidSerializer,
)


class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    """ViewSet para categorias de gastos"""
    queryset = ExpenseCategory.objects.filter(is_active=True)
    serializer_class = ExpenseCategorySerializer
    permission_classes = [IsAdminUser]
    lookup_field = 'slug'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering = ['display_order', 'name']

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.annotate(
            expenses_count=Count('expenses'),
            total_amount=Sum(
                'expenses__amount',
                filter=Q(expenses__status='paid')
            )
        )
        return queryset

    def destroy(self, request, *args, **kwargs):
        """Soft delete"""
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OperationalExpenseViewSet(viewsets.ModelViewSet):
    """ViewSet para gastos operativos"""
    queryset = OperationalExpense.objects.select_related('category', 'created_by')
    permission_classes = [IsAdminUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['expense_number', 'description', 'reference_number']
    ordering = ['-expense_date', '-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return OperationalExpenseListSerializer
        elif self.action == 'create':
            return OperationalExpenseCreateSerializer
        return OperationalExpenseDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filtrar por categoria
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category__slug=category)

        # Filtrar por estado
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filtrar por rango de fechas personalizado
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date and end_date:
            queryset = queryset.filter(expense_date__range=[start_date, end_date])

        # Filtrar por periodo predefinido
        date_filter = self.request.query_params.get('date')
        if date_filter:
            queryset = self._filter_by_date(queryset, date_filter)

        return queryset

    def _filter_by_date(self, queryset, date_filter):
        """Filtrar por periodo predefinido"""
        local_now = timezone.localtime()
        today = local_now.date()

        if date_filter == 'today':
            queryset = queryset.filter(expense_date=today)
        elif date_filter == 'week':
            week_ago = today - timedelta(days=7)
            queryset = queryset.filter(expense_date__gte=week_ago)
        elif date_filter == 'month':
            month_start = today.replace(day=1)
            queryset = queryset.filter(expense_date__gte=month_start)
        elif date_filter == 'last_month':
            month_start = today.replace(day=1)
            last_month_end = month_start - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            queryset = queryset.filter(
                expense_date__gte=last_month_start,
                expense_date__lte=last_month_end
            )
        elif date_filter == 'year':
            year_start = today.replace(month=1, day=1)
            queryset = queryset.filter(expense_date__gte=year_start)

        return queryset

    @action(detail=True, methods=['post'])
    def mark_paid(self, request, pk=None):
        """Marcar gasto como pagado"""
        expense = self.get_object()
        serializer = MarkExpensePaidSerializer(data=request.data)

        if serializer.is_valid():
            payment_method = serializer.validated_data.get('payment_method', '')
            expense.mark_as_paid(payment_method)
            return Response(OperationalExpenseDetailSerializer(expense).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancelar gasto"""
        expense = self.get_object()
        expense.mark_as_cancelled()
        return Response(OperationalExpenseDetailSerializer(expense).data)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Estadisticas de gastos"""
        date_filter = request.query_params.get('date', 'month')

        # Aplicar filtro de fecha
        expenses = self._filter_by_date(self.get_queryset(), date_filter)

        # Totales
        total_paid = expenses.filter(status='paid').aggregate(
            total=Sum('amount')
        )['total'] or 0

        total_pending = expenses.filter(status='pending').aggregate(
            total=Sum('amount')
        )['total'] or 0

        # Por categoria
        by_category = expenses.filter(status='paid').values(
            'category__name', 'category__slug', 'category__color', 'category__icon'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Por metodo de pago
        by_payment = expenses.filter(status='paid').exclude(
            payment_method=''
        ).values('payment_method').annotate(
            total=Sum('amount'),
            count=Count('id')
        )

        # Formatear metodos de pago
        payment_methods_formatted = {}
        for item in by_payment:
            method = item['payment_method']
            display = dict(OperationalExpense.PaymentMethod.choices).get(method, method)
            payment_methods_formatted[method] = {
                'name': display,
                'total': str(item['total']),
                'count': item['count']
            }

        return Response({
            'period': date_filter,
            'total_paid': str(total_paid),
            'total_pending': str(total_pending),
            'expenses_count': expenses.count(),
            'paid_count': expenses.filter(status='paid').count(),
            'pending_count': expenses.filter(status='pending').count(),
            'cancelled_count': expenses.filter(status='cancelled').count(),
            'by_category': list(by_category),
            'by_payment_method': payment_methods_formatted,
        })

    @action(detail=False, methods=['get'])
    def pending(self, request):
        """Obtener gastos pendientes de pago"""
        pending = self.get_queryset().filter(status='pending').order_by('expense_date')
        total = pending.aggregate(total=Sum('amount'))['total'] or 0
        serializer = OperationalExpenseListSerializer(pending, many=True)
        return Response({
            'expenses': serializer.data,
            'total': str(total),
            'count': pending.count()
        })

    @action(detail=False, methods=['get'])
    def by_day(self, request):
        """Gastos agrupados por dia"""
        date_filter = request.query_params.get('date', 'month')
        expenses = self._filter_by_date(
            self.get_queryset().filter(status='paid'),
            date_filter
        )

        by_day = expenses.values('expense_date').annotate(
            total=Sum('amount')
        ).order_by('expense_date')

        return Response({
            'data': list(by_day)
        })


class RecurringExpenseTemplateViewSet(viewsets.ModelViewSet):
    """ViewSet para plantillas de gastos recurrentes"""
    queryset = RecurringExpenseTemplate.objects.select_related('category', 'created_by')
    serializer_class = RecurringExpenseTemplateSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        queryset = super().get_queryset()
        # Filtrar solo activos por defecto
        active_only = self.request.query_params.get('active', 'true')
        if active_only.lower() == 'true':
            queryset = queryset.filter(is_active=True)
        return queryset

    @action(detail=True, methods=['post'])
    def generate(self, request, pk=None):
        """Generar gasto desde plantilla"""
        template = self.get_object()
        expense_date_str = request.data.get('expense_date')
        expense_date = None

        if expense_date_str:
            from datetime import datetime
            try:
                expense_date = datetime.strptime(expense_date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {'error': 'Formato de fecha invalido. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        expense = template.generate_expense(expense_date)
        return Response(
            OperationalExpenseDetailSerializer(expense).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=['post'])
    def generate_all_due(self, request):
        """Generar todos los gastos que estan vencidos"""
        today = timezone.now().date()

        due_templates = RecurringExpenseTemplate.objects.filter(
            is_active=True,
            next_due_date__lte=today
        )

        generated = []
        for template in due_templates:
            expense = template.generate_expense()
            generated.append(expense)

        return Response({
            'generated_count': len(generated),
            'expenses': OperationalExpenseListSerializer(generated, many=True).data
        })

    @action(detail=False, methods=['get'])
    def due_soon(self, request):
        """Obtener plantillas con gastos proximos a vencer"""
        days = int(request.query_params.get('days', 7))
        today = timezone.now().date()
        end_date = today + timedelta(days=days)

        due_templates = self.get_queryset().filter(
            is_active=True,
            next_due_date__lte=end_date
        ).order_by('next_due_date')

        serializer = self.get_serializer(due_templates, many=True)
        return Response({
            'templates': serializer.data,
            'count': due_templates.count()
        })
