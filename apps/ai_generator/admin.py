from django.contrib import admin
from django.utils.html import format_html
from .models import AIImageGeneration, AIGenerationUsageQuota


@admin.register(AIImageGeneration)
class AIImageGenerationAdmin(admin.ModelAdmin):
    """Admin para generaciones de imágenes IA"""

    list_display = [
        'id',
        'user_email',
        'status_badge',
        'transparent_background',
        'generation_time_seconds',
        'cost_usd',
        'product_link',
        'created_at',
    ]
    list_filter = [
        'status',
        'transparent_background',
        'openai_model',
        'created_at',
    ]
    search_fields = [
        'id',
        'user__email',
        'user_prompt',
        'product__name',
    ]
    readonly_fields = [
        'id',
        'user',
        'status',
        'full_prompt',
        'error_message',
        'generation_time_seconds',
        'cost_usd',
        'metadata',
        'created_at',
        'updated_at',
        'completed_at',
        'original_image_preview',
        'reference_image_preview',
        'generated_image_preview',
    ]
    fieldsets = (
        ('Información Básica', {
            'fields': ('id', 'user', 'status', 'product')
        }),
        ('Imágenes', {
            'fields': (
                'original_image',
                'original_image_preview',
                'reference_image',
                'reference_image_preview',
                'generated_image',
                'generated_image_preview',
            )
        }),
        ('Parámetros', {
            'fields': (
                'user_prompt',
                'transparent_background',
                'full_prompt',
                'openai_model',
            )
        }),
        ('Resultados', {
            'fields': (
                'generation_time_seconds',
                'cost_usd',
                'metadata',
                'error_message',
            )
        }),
        ('Fechas', {
            'fields': ('created_at', 'updated_at', 'completed_at')
        }),
    )

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'Usuario'

    def status_badge(self, obj):
        colors = {
            'pending': 'orange',
            'processing': 'blue',
            'completed': 'green',
            'failed': 'red',
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Estado'

    def product_link(self, obj):
        if obj.product:
            return format_html(
                '<a href="/admin/products/product/{}/change/">{}</a>',
                obj.product.id,
                obj.product.name
            )
        return '-'
    product_link.short_description = 'Producto'

    def original_image_preview(self, obj):
        if obj.original_image:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px;" />',
                obj.original_image.url
            )
        return '-'
    original_image_preview.short_description = 'Preview Original'

    def reference_image_preview(self, obj):
        if obj.reference_image:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px;" />',
                obj.reference_image.url
            )
        return '-'
    reference_image_preview.short_description = 'Preview Referencia'

    def generated_image_preview(self, obj):
        if obj.generated_image:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px;" />',
                obj.generated_image.url
            )
        return '-'
    generated_image_preview.short_description = 'Preview Generada'


@admin.register(AIGenerationUsageQuota)
class AIGenerationUsageQuotaAdmin(admin.ModelAdmin):
    """Admin para cuotas de uso"""

    list_display = [
        'user_email',
        'usage_display',
        'total_cost_usd',
        'last_reset',
    ]
    list_filter = ['last_reset']
    search_fields = ['user__email']
    readonly_fields = ['user', 'total_cost_usd', 'last_reset']

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'Usuario'

    def usage_display(self, obj):
        percentage = (obj.used_this_month / obj.monthly_limit * 100) if obj.monthly_limit > 0 else 0
        color = 'green' if percentage < 80 else 'orange' if percentage < 100 else 'red'

        return format_html(
            '{} / {} <span style="color: {};">({:.1f}%)</span>',
            obj.used_this_month,
            obj.monthly_limit,
            color,
            percentage
        )
    usage_display.short_description = 'Uso (este mes)'
