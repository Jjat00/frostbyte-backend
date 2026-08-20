from rest_framework import serializers
from apps.business.models import Business
from .models import (
    ExpenseCategory,
    ExpenseKind,
    OperationalExpense,
    RecurringExpenseTemplate,
)


class ExpenseCategorySerializer(serializers.ModelSerializer):
    """Serializer para categorias de gastos"""
    expenses_count = serializers.IntegerField(read_only=True)
    total_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)

    class Meta:
        model = ExpenseCategory
        fields = [
            'id', 'name', 'slug', 'description', 'icon', 'color',
            'kind', 'kind_display',
            'is_recurring_default', 'is_active', 'display_order',
            'expenses_count', 'total_amount', 'created_at', 'updated_at'
        ]
        read_only_fields = ['slug', 'created_at', 'updated_at']


class OperationalExpenseListSerializer(serializers.ModelSerializer):
    """Serializer para listado de gastos"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_icon = serializers.CharField(source='category.icon', read_only=True)
    category_color = serializers.CharField(source='category.color', read_only=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payment_method_display = serializers.CharField(
        source='get_payment_method_display', read_only=True
    )
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    business_name = serializers.CharField(source='business.name', read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = OperationalExpense
        fields = [
            'id', 'expense_number', 'business', 'business_name', 'category', 'category_name',
            'category_icon', 'category_color', 'category_slug',
            'kind', 'kind_display',
            'description', 'amount', 'expense_date', 'status',
            'status_display', 'payment_method', 'payment_method_display',
            'is_recurring', 'created_by_name', 'created_at'
        ]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None


class OperationalExpenseDetailSerializer(serializers.ModelSerializer):
    """Serializer para detalle de gasto"""
    category = ExpenseCategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=ExpenseCategory.objects.filter(is_active=True),
        source='category',
        write_only=True
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payment_method_display = serializers.CharField(
        source='get_payment_method_display', read_only=True
    )
    recurrence_period_display = serializers.CharField(
        source='get_recurrence_period_display', read_only=True
    )
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    business_name = serializers.CharField(source='business.name', read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = OperationalExpense
        fields = '__all__'
        # business es de solo lectura aqui: se fija al crear y no se cambia en
        # actualizaciones (asi un PUT del front actual sin business no rompe).
        read_only_fields = ['expense_number', 'business', 'created_by', 'created_at', 'updated_at']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None

    def update(self, instance, validated_data):
        from django.utils import timezone
        # Si se cambia el estado a 'paid' y no tenía paid_at, establecerlo
        new_status = validated_data.get('status')
        if new_status == OperationalExpense.Status.PAID and not instance.paid_at:
            validated_data['paid_at'] = timezone.now()
        return super().update(instance, validated_data)


class OperationalExpenseCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear gastos"""
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=ExpenseCategory.objects.filter(is_active=True),
        source='category'
    )
    business = serializers.PrimaryKeyRelatedField(
        queryset=Business.objects.all(), required=False
    )
    payment_method = serializers.ChoiceField(
        choices=OperationalExpense.PaymentMethod.choices,
        required=False,
        allow_blank=True
    )
    reference_number = serializers.CharField(required=False, allow_blank=True)
    receipt_url = serializers.URLField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    recurrence_period = serializers.ChoiceField(
        choices=OperationalExpense.RecurrencePeriod.choices,
        required=False
    )
    kind = serializers.ChoiceField(
        choices=ExpenseKind.choices,
        required=False,
        help_text="Si no se envia, se hereda de la categoria elegida"
    )

    class Meta:
        model = OperationalExpense
        fields = [
            'business', 'category_id', 'kind', 'description', 'amount', 'expense_date',
            'status', 'payment_method', 'reference_number', 'receipt_url',
            'notes', 'is_recurring', 'recurrence_period'
        ]

    def create(self, validated_data):
        from django.utils import timezone
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            validated_data['created_by'] = request.user
        # Compatibilidad: si no se especifica negocio, usar el principal.
        business = validated_data.get('business') or Business.get_main()
        if business is None:
            raise serializers.ValidationError(
                {'business': 'No hay negocio principal configurado; especifica business.'}
            )
        validated_data['business'] = business
        # Si no se indica el tipo, se hereda de la categoria: las categorias de
        # inversion (equipos, mobiliario, montaje) crean inversion por defecto.
        if not validated_data.get('kind'):
            validated_data['kind'] = validated_data['category'].kind
        # Si se crea con estado 'paid', establecer paid_at automáticamente
        if validated_data.get('status') == OperationalExpense.Status.PAID:
            validated_data['paid_at'] = timezone.now()
        return super().create(validated_data)


class RecurringExpenseTemplateSerializer(serializers.ModelSerializer):
    """Serializer para plantillas de gastos recurrentes"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_icon = serializers.CharField(source='category.icon', read_only=True)
    category_color = serializers.CharField(source='category.color', read_only=True)
    recurrence_type_display = serializers.CharField(
        source='get_recurrence_type_display', read_only=True
    )
    created_by_name = serializers.SerializerMethodField()
    business_name = serializers.CharField(source='business.name', read_only=True)
    kind = serializers.CharField(source='category.kind', read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=ExpenseCategory.objects.filter(is_active=True),
        source='category',
        write_only=True,
        required=False
    )
    business = serializers.PrimaryKeyRelatedField(
        queryset=Business.objects.all(), required=False
    )

    class Meta:
        model = RecurringExpenseTemplate
        fields = '__all__'
        read_only_fields = ['created_by', 'last_generated_at', 'created_at', 'updated_at']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            validated_data['created_by'] = request.user
        # Compatibilidad: si no se especifica negocio, usar el principal.
        business = validated_data.get('business') or Business.get_main()
        if business is None:
            raise serializers.ValidationError(
                {'business': 'No hay negocio principal configurado; especifica business.'}
            )
        validated_data['business'] = business
        return super().create(validated_data)


class MarkExpensePaidSerializer(serializers.Serializer):
    """Serializer para marcar gasto como pagado"""
    payment_method = serializers.ChoiceField(
        choices=OperationalExpense.PaymentMethod.choices,
        required=False,
        allow_blank=True
    )
