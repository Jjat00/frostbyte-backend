"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from apps.games.routing import websocket_urlpatterns

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

# Import routing after Django is set up

# Permitir todos los orígenes para WebSockets
# Nota: AllowedHostsOriginValidator puede bloquear conexiones si el frontend
# está en un dominio diferente al backend. Como ya tenemos CORS configurado
# y ALLOWED_HOSTS = ['*'], permitimos todos los orígenes para WebSockets también.
websocket_middleware = AuthMiddlewareStack(
    URLRouter(
        websocket_urlpatterns
    )
)

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": websocket_middleware,
})
