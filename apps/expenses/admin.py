from django.contrib import admin
from .models import ExpenseCategory, OperationalExpense, RecurringExpenseTemplate


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'icon', 'color', 'is_active', 'display_order']
    list_filter = ['is_active', 'is_recurring_default']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['display_order', 'name']


@admin.register(OperationalExpense)
class OperationalExpenseAdmin(admin.ModelAdmin):
    list_display = [
        'expense_number', 'business', 'description', 'category', 'amount',
        'expense_date', 'status', 'payment_method', 'created_by'
    ]
    list_filter = ['business', 'status', 'category', 'payment_method', 'is_recurring', 'expense_date']
    search_fields = ['expense_number', 'description', 'reference_number', 'notes']
    date_hierarchy = 'expense_date'
    readonly_fields = ['expense_number', 'created_at', 'updated_at']
    raw_id_fields = ['created_by', 'recurring_template', 'business']
    ordering = ['-expense_date', '-created_at']


@admin.register(RecurringExpenseTemplate)
class RecurringExpenseTemplateAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'business', 'category', 'amount', 'recurrence_type',
        'is_active', 'next_due_date', 'last_generated_at'
    ]
    list_filter = ['business', 'is_active', 'recurrence_type', 'category']
    search_fields = ['name', 'description']
    readonly_fields = ['last_generated_at', 'created_at', 'updated_at']
    raw_id_fields = ['created_by', 'business']
    ordering = ['name']
