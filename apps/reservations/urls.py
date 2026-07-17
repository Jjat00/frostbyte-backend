from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CustomerReservationViewSet, StaffReservationViewSet

router = DefaultRouter()
# El prefijo admin va primero para que no lo capture el prefijo vacío
router.register(r"admin", StaffReservationViewSet,
                basename="reservation-admin")
router.register(r"", CustomerReservationViewSet, basename="reservation")

urlpatterns = [
    path("", include(router.urls)),
]
