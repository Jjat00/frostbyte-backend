from django.contrib import admin

from apps.search import PlainSearchAdminMixin

from .models import Contest, ContestEntry


@admin.register(Contest)
class ContestAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = (
        "title", "event_date", "entry_fee", "is_published",
        "registrations_open",
    )
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title",)


@admin.register(ContestEntry)
class ContestEntryAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = (
        "number", "full_name", "instagram_handle", "contest", "paid",
        "follows_instagram", "status",
    )
    list_filter = ("contest", "status", "paid", "follows_instagram")
    search_fields = ("full_name", "phone", "instagram_handle")
    raw_id_fields = ("user", "paid_by", "instagram_checked_by")
