#!/bin/bash
# Script para iniciar el servidor Django con soporte WebSocket usando Daphne

cd "$(dirname "$0")"
source .venv/bin/activate

echo "🚀 Iniciando servidor Django con soporte WebSocket..."
echo "📡 Usando Daphne (ASGI server)"
echo ""
echo "El servidor estará disponible en: http://localhost:8000"
echo "WebSockets estarán disponibles en: ws://localhost:8000/ws/game/room/{id}/"
echo ""
echo "Para detener el servidor, presiona Ctrl+C"
echo ""

daphne -b 0.0.0.0 -p 8000 --application-close-timeout 120 --request-timeout 120 config.asgi:application

