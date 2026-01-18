from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ExpenseCategoryViewSet,
    OperationalExpenseViewSet,
    RecurringExpenseTemplateViewSet,
)

router = DefaultRouter()
router.register(r'categories', ExpenseCategoryViewSet, basename='expense-category')
router.register(r'recurring', RecurringExpenseTemplateViewSet, basename='recurring-expense')
router.register(r'', OperationalExpenseViewSet, basename='expense')

urlpatterns = [
    path('', include(router.urls)),
]
