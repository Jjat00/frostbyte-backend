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

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

# Import routing after Django is set up
# IMPORTANTE: Esta importación DEBE estar DESPUÉS de get_asgi_application()
# porque routing.py importa consumers.py, que importa models.py, que necesita Django configurado
from apps.games.routing import websocket_urlpatterns as games_ws_urlpatterns
from apps.orders.routing import websocket_urlpatterns as orders_ws_urlpatterns
from apps.music.routing import websocket_urlpatterns as music_ws_urlpatterns
from apps.youtube.routing import websocket_urlpatterns as youtube_ws_urlpatterns
from apps.polla.routing import websocket_urlpatterns as polla_ws_urlpatterns
from apps.reservations.routing import websocket_urlpatterns as reservations_ws_urlpatterns

# Combinar todas las rutas WebSocket
websocket_urlpatterns = games_ws_urlpatterns + orders_ws_urlpatterns + music_ws_urlpatterns + youtube_ws_urlpatterns + polla_ws_urlpatterns + reservations_ws_urlpatterns

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

# Loop de tiempo real de la Polla. APAGADO por defecto desde que termino el
# Mundial 2026: sin partidos nuevos, sondear solo gastaria cuota de API-Football.
# La llamada se deja puesta porque el loop se enciende con POLLA_REALTIME_LOOP=1
# (sin ella, no bastaria la variable). Se arranca aqui a proposito: este modulo
# solo lo importa el servidor ASGI (daphne), no los comandos de manage.py, asi
# que el loop corre unicamente en el proceso web y no en migrate/collectstatic.
from apps.polla.realtime_loop import start_realtime_loop  # noqa: E402

start_realtime_loop()
