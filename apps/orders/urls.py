from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OrderViewSet,
    OrderItemViewSet,
    TableViewSet,
    PageVisitViewSet,
    PublicOrderViewSet,
    CustomerOrderViewSet,
)

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="order")
router.register(r"order-items", OrderItemViewSet, basename="order-item")
router.register(r"tables", TableViewSet, basename="table")
router.register(r"pages", PageVisitViewSet, basename="page")
router.register(r"public-orders", PublicOrderViewSet, basename="public-order")
router.register(r"my-orders", CustomerOrderViewSet, basename="my-order")

urlpatterns = [
    path("", include(router.urls)),
]

