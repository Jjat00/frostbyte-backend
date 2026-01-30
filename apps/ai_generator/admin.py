from django.contrib import admin
from django.utils.html import format_html
from .models import AIImageGeneration


@admin.register(AIImageGeneration)
class AIImageGenerationAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['id', 'user__email']
    readonly_fields = ['id', 'status', 'error_message', 'created_at', 'preview']

    def preview(self, obj):
        html = ""
        if obj.original_image:
            html += f'<img src="{obj.original_image.url}" style="max-width:150px;margin:5px;" />'
        if obj.generated_image:
            html += f'<img src="{obj.generated_image.url}" style="max-width:150px;margin:5px;" />'
        return format_html(html) if html else "-"
    preview.short_description = "Preview"
