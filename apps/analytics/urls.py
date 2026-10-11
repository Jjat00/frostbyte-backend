from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .channels import ChannelAnalyticsViewSet
from .schedule import ScheduleAnalyticsViewSet
from .views import FinancialAnalyticsViewSet

router = DefaultRouter()
router.register('financial', FinancialAnalyticsViewSet, basename='financial-analytics')
router.register('channels', ChannelAnalyticsViewSet, basename='channel-analytics')
router.register('schedule', ScheduleAnalyticsViewSet, basename='schedule-analytics')

urlpatterns = [
    path('', include(router.urls)),
]
