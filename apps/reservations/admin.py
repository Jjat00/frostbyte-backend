from django.contrib import admin

from .models import Reservation, ReservationSettings
from apps.search import PlainSearchAdminMixin


@admin.register(Reservation)
class ReservationAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    list_display = (
        "id", "reservation_type", "customer_name", "customer_phone",
        "party_size", "floor", "date", "start_time", "status", "source",
    )
    list_filter = ("reservation_type", "status", "source", "floor", "date")
    search_fields = ("customer_name", "customer_phone")
    date_hierarchy = "date"
    ordering = ("-date", "start_time")


@admin.register(ReservationSettings)
class ReservationSettingsAdmin(PlainSearchAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        # Singleton: solo se edita la fila existente
        return not ReservationSettings.objects.exists()
