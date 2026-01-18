from django.db import models
from django.conf import settings


class ExpenseCategory(models.Model):
    """Categorias de gastos operativos"""

    name = models.CharField(max_length=100, verbose_name="Nombre")
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True, verbose_name="Descripcion")
    icon = models.CharField(
        max_length=50,
        default='MoreHorizontal',
        verbose_name="Icono",
        help_text="Nombre del icono de Lucide React"
    )
    color = models.CharField(
        max_length=20,
        default='gray',
        verbose_name="Color",
        help_text="Color para UI (ej: blue, green, red)"
    )
    is_recurring_default = models.BooleanField(
        default=False,
        verbose_name="Es recurrente por defecto",
        help_text="Indica si esta categoria tipicamente tiene gastos recurrentes"
    )
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoria de gasto"
        verbose_name_plural = "Categorias de gastos"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class OperationalExpense(models.Model):
    """Gasto operativo individual"""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        PAID = 'paid', 'Pagado'
        CANCELLED = 'cancelled', 'Cancelado'

    class PaymentMethod(models.TextChoices):
        CASH = 'cash', 'Efectivo'
        TRANSFER = 'transfer', 'Transferencia'
        CARD = 'card', 'Tarjeta'
        CHECK = 'check', 'Cheque'
        NEQUI = 'nequi', 'Nequi'
        DAVIPLATA = 'daviplata', 'Daviplata'
        OTHER = 'other', 'Otro'

    class RecurrencePeriod(models.TextChoices):
        NONE = 'none', 'No recurrente'
        DAILY = 'daily', 'Diario'
        WEEKLY = 'weekly', 'Semanal'
        BIWEEKLY = 'biweekly', 'Quincenal'
        MONTHLY = 'monthly', 'Mensual'
        QUARTERLY = 'quarterly', 'Trimestral'
        YEARLY = 'yearly', 'Anual'

    expense_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        verbose_name="Numero de gasto"
    )
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name='expenses',
        verbose_name="Categoria"
    )
    description = models.CharField(
        max_length=500,
        verbose_name="Descripcion",
        help_text="Descripcion del gasto"
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Monto",
        help_text="Monto en COP"
    )
    expense_date = models.DateField(
        verbose_name="Fecha del gasto",
        help_text="Fecha en que se realizo o se debe realizar el gasto"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Estado"
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        blank=True,
        verbose_name="Metodo de pago"
    )
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de pago"
    )
    reference_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Numero de referencia",
        help_text="Numero de factura, recibo, etc."
    )
    receipt_url = models.URLField(
        blank=True,
        verbose_name="URL del recibo",
        help_text="Enlace a imagen del recibo o factura"
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Notas adicionales"
    )
    is_recurring = models.BooleanField(
        default=False,
        verbose_name="Es recurrente"
    )
    recurrence_period = models.CharField(
        max_length=20,
        choices=RecurrencePeriod.choices,
        default=RecurrencePeriod.NONE,
        verbose_name="Periodo de recurrencia"
    )
    recurring_template = models.ForeignKey(
        'RecurringExpenseTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='generated_expenses',
        verbose_name="Plantilla recurrente"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_expenses',
        verbose_name="Creado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Gasto operativo"
        verbose_name_plural = "Gastos operativos"
        ordering = ["-expense_date", "-created_at"]
        indexes = [
            models.Index(fields=['expense_date']),
            models.Index(fields=['status']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return f"{self.expense_number} - {self.description} (${self.amount})"

    def save(self, *args, **kwargs):
        if not self.expense_number:
            import uuid
            from django.utils import timezone
            date_str = timezone.now().strftime("%Y%m%d")
            self.expense_number = f"EXP-{date_str}-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    def mark_as_paid(self, payment_method=None):
        """Marcar gasto como pagado"""
        from django.utils import timezone
        self.status = self.Status.PAID
        self.paid_at = timezone.now()
        if payment_method:
            self.payment_method = payment_method
        self.save()

    def mark_as_cancelled(self):
        """Cancelar gasto"""
        self.status = self.Status.CANCELLED
        self.save()


class RecurringExpenseTemplate(models.Model):
    """Plantilla para gastos recurrentes"""

    class RecurrenceType(models.TextChoices):
        DAILY = 'daily', 'Diario'
        WEEKLY = 'weekly', 'Semanal'
        BIWEEKLY = 'biweekly', 'Quincenal'
        MONTHLY = 'monthly', 'Mensual'
        QUARTERLY = 'quarterly', 'Trimestral'
        YEARLY = 'yearly', 'Anual'

    name = models.CharField(
        max_length=200,
        verbose_name="Nombre",
        help_text="Nombre descriptivo del gasto recurrente"
    )
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name='recurring_templates',
        verbose_name="Categoria"
    )
    description = models.CharField(
        max_length=500,
        verbose_name="Descripcion"
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Monto estimado"
    )
    recurrence_type = models.CharField(
        max_length=20,
        choices=RecurrenceType.choices,
        default=RecurrenceType.MONTHLY,
        verbose_name="Tipo de recurrencia"
    )
    day_of_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Dia del mes",
        help_text="Para gastos mensuales (1-31)"
    )
    day_of_week = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Dia de la semana",
        help_text="Para gastos semanales (0=Lunes, 6=Domingo)"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activo"
    )
    last_generated_at = models.DateField(
        null=True,
        blank=True,
        verbose_name="Ultima generacion"
    )
    next_due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Proxima fecha"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='recurring_expense_templates',
        verbose_name="Creado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plantilla de gasto recurrente"
        verbose_name_plural = "Plantillas de gastos recurrentes"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} - ${self.amount} ({self.get_recurrence_type_display()})"

    def generate_expense(self, expense_date=None):
        """Genera un gasto a partir de esta plantilla"""
        from django.utils import timezone

        if expense_date is None:
            expense_date = timezone.now().date()

        expense = OperationalExpense.objects.create(
            category=self.category,
            description=self.description,
            amount=self.amount,
            expense_date=expense_date,
            is_recurring=True,
            recurrence_period=self.recurrence_type,
            recurring_template=self,
            created_by=self.created_by,
        )

        self.last_generated_at = expense_date
        self.calculate_next_due_date()
        self.save()

        return expense

    def calculate_next_due_date(self):
        """Calcula la proxima fecha de vencimiento"""
        from datetime import timedelta, date
        from django.utils import timezone

        base_date = self.last_generated_at or timezone.now().date()

        if self.recurrence_type == self.RecurrenceType.DAILY:
            self.next_due_date = base_date + timedelta(days=1)
        elif self.recurrence_type == self.RecurrenceType.WEEKLY:
            self.next_due_date = base_date + timedelta(weeks=1)
        elif self.recurrence_type == self.RecurrenceType.BIWEEKLY:
            self.next_due_date = base_date + timedelta(weeks=2)
        elif self.recurrence_type == self.RecurrenceType.MONTHLY:
            month = base_date.month
            year = base_date.year
            if month == 12:
                month = 1
                year += 1
            else:
                month += 1
            day = min(self.day_of_month or base_date.day, 28)
            self.next_due_date = date(year, month, day)
        elif self.recurrence_type == self.RecurrenceType.QUARTERLY:
            month = base_date.month + 3
            year = base_date.year
            if month > 12:
                month -= 12
                year += 1
            self.next_due_date = date(year, month, base_date.day)
        elif self.recurrence_type == self.RecurrenceType.YEARLY:
            self.next_due_date = date(base_date.year + 1, base_date.month, base_date.day)
