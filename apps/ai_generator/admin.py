from django.contrib import admin
from django.utils.html import format_html
from .models import AIImageGeneration, AIGenerationUsageQuota


@admin.register(AIImageGeneration)
class AIImageGenerationAdmin(admin.ModelAdmin):
    """Admin mínimo para generaciones (uso interno)."""

    list_display = ['id', 'user_email', 'status_badge', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['id', 'user__email', 'user_prompt']
    readonly_fields = [
        'id', 'user', 'status', 'full_prompt', 'error_message',
        'generation_time_seconds', 'cost_usd', 'created_at', 'updated_at', 'completed_at',
        'original_image_preview', 'reference_image_preview', 'generated_image_preview',
    ]
    fieldsets = (
        (None, {'fields': ('id', 'user', 'status')}),
        ('Imágenes', {
            'fields': (
                'original_image', 'original_image_preview',
                'reference_image', 'reference_image_preview',
                'generated_image', 'generated_image_preview',
            ),
        }),
        ('Parámetros', {'fields': ('user_prompt',
         'transparent_background', 'full_prompt')}),
        ('Resultado', {'fields': ('error_message',
         'generation_time_seconds', 'cost_usd')}),
        ('Fechas', {'fields': ('created_at', 'updated_at', 'completed_at')}),
    )

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'Usuario'

    def status_badge(self, obj):
        colors = {'pending': 'orange', 'processing': 'blue',
                  'completed': 'green', 'failed': 'red'}
        c = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:4px;">{}</span>',
            c, obj.get_status_display(),
        )
    status_badge.short_description = 'Estado'

    def original_image_preview(self, obj):
        if obj.original_image:
            return format_html('<img src="{}" style="max-width:180px;max-height:180px;" />', obj.original_image.url)
        return '-'
    original_image_preview.short_description = 'Original'

    def reference_image_preview(self, obj):
        if obj.reference_image:
            return format_html('<img src="{}" style="max-width:180px;max-height:180px;" />', obj.reference_image.url)
        return '-'
    reference_image_preview.short_description = 'Referencia'

    def generated_image_preview(self, obj):
        if obj.generated_image:
            return format_html('<img src="{}" style="max-width:180px;max-height:180px;" />', obj.generated_image.url)
        return '-'
    generated_image_preview.short_description = 'Generada'


@admin.register(AIGenerationUsageQuota)
class AIGenerationUsageQuotaAdmin(admin.ModelAdmin):
    """Cuotas (modelo conservado, sin uso en la app)."""

    list_display = ['user', 'used_this_month', 'monthly_limit', 'last_reset']
    readonly_fields = ['user', 'used_this_month',
                       'monthly_limit', 'total_cost_usd', 'last_reset']
