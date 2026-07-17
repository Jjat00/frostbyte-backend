"""
URL configuration for config project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    # Auth & Users
    path('api/v1/', include('apps.accounts.urls')),
    # Business (negocios: Frostbyte, Frostbyte Food)
    path('api/v1/', include('apps.business.urls')),
    # Products
    path('api/v1/', include('apps.products.urls')),
    # Inventory
    path('api/v1/inventory/', include('apps.inventory.urls')),
    # Orders
    path('api/v1/', include('apps.orders.urls')),
    # Music
    path('api/v1/', include('apps.music.urls')),
    # Games
    path('api/v1/', include('apps.games.urls')),
    # Motivational
    path('api/v1/motivational/', include('apps.motivational.urls')),
    # Feedback
    path('api/v1/', include('apps.feedback.urls')),
    # Expenses
    path('api/v1/expenses/', include('apps.expenses.urls')),
    # Analytics
    path('api/v1/analytics/', include('apps.analytics.urls')),
    # AI Generator
    path('api/v1/ai/', include('apps.ai_generator.urls')),
    # Impostor Game
    path('api/v1/impostor/', include('apps.impostor.urls')),
    # Recetarios
    path('api/v1/recetarios/', include('apps.recetarios.urls')),
    # YouTube
    path('api/v1/', include('apps.youtube.urls')),
    # Polla Mundialista 2026
    path('api/v1/polla/', include('apps.polla.urls')),
    # Agente de pedidos por WhatsApp (webhook de Kapso)
    path('api/v1/whatsapp/', include('apps.whatsapp.urls')),
    # Reservas (mesas, grupos y Sala VIP)
    path('api/v1/reservations/', include('apps.reservations.urls')),
]

# Servir archivos media en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)
