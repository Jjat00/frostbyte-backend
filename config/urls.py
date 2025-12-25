"""
URL configuration for config project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    # Auth & Users
    path('api/v1/', include('apps.accounts.urls')),
    # Products
    path('api/v1/', include('apps.products.urls')),
    # Inventory
    path('api/v1/inventory/', include('apps.inventory.urls')),
    # Orders
    path('api/v1/', include('apps.orders.urls')),
    # Music
    path('api/v1/', include('apps.music.urls')),
]
