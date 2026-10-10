from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .channels import ChannelAnalyticsViewSet
from .views import FinancialAnalyticsViewSet

router = DefaultRouter()
router.register('financial', FinancialAnalyticsViewSet, basename='financial-analytics')
router.register('channels', ChannelAnalyticsViewSet, basename='channel-analytics')

urlpatterns = [
    path('', include(router.urls)),
]
