from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import FinancialAnalyticsViewSet

router = DefaultRouter()
router.register('financial', FinancialAnalyticsViewSet, basename='financial-analytics')

urlpatterns = [
    path('', include(router.urls)),
]
