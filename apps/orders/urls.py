from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OrderViewSet, OrderItemViewSet, TableViewSet, PageVisitViewSet

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="order")
router.register(r"order-items", OrderItemViewSet, basename="order-item")
router.register(r"tables", TableViewSet, basename="table")
router.register(r"pages", PageVisitViewSet, basename="page")

urlpatterns = [
    path("", include(router.urls)),
]

