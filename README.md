# Frostbyte Backend

API REST para gestión de negocios de bebidas preparadas (granizados, frappés, cócteles), bares y restaurantes. Construida con Django y Django REST Framework. Incluye autenticación JWT (staff) y con Google (clientes), pedidos en el local y a domicilio, **Frosty** (agente de IA que toma pedidos por WhatsApp), reservas, varios negocios bajo un mismo panel, WebSockets en tiempo real y generación de imágenes con IA.

## Stack Tecnológico

| Tecnología | Versión | Descripción |
|------------|---------|-------------|
| Django | 6.0.1 | Framework web |
| Django REST Framework | 3.16.1 | API REST |
| SimpleJWT | 5.5.1 | Autenticación JWT |
| Django Channels | 4.3.2 | WebSocket support |
| PostgreSQL | - | Base de datos |
| Redis | - | Channel layers (producción) |
| LangChain + LangGraph | 1.3 / 1.2 | Agente de WhatsApp (Frosty) con memoria en Postgres |
| Anthropic (`langchain-anthropic`) | 1.7.5 | Modelo principal de Frosty (Claude Sonnet 5.5) |
| Kapso | - | Puente con la API de WhatsApp (webhook + envío de mensajes) |
| OpenAI | 2.45.0 | Generación de imágenes (GPT Image 1.5 / 2), respaldo de Frosty, visión, transcripción y textos cortos |
| Google GenAI | 1.68.0 | Generación de imágenes (Gemini Pro/Flash) |
| rembg | 2.0.73 | Remoción de fondo con IA (para Gemini) |
| Cloudflare R2 | - | Almacenamiento de archivos |
| Spotipy | 2.26+ | SDK Python para Spotify Web API |
| YouTube Data API v3 | - | Búsqueda y datos de videos de YouTube |
| Daphne | 4.2.1 | Servidor ASGI de producción (HTTP + WebSocket) |

## Arquitectura

```
frostbyte-backend/
├── apps/
│   ├── accounts/       # Autenticación (staff con JWT, clientes con Google)
│   ├── business/       # Negocios dentro de Frostbyte (bebidas, Frostbyte Food)
│   ├── products/       # Catálogo de productos
│   ├── inventory/      # Control de inventario
│   ├── orders/         # Pedidos en el local, para recoger y a domicilio
│   ├── expenses/       # Gastos operacionales
│   ├── games/          # Salas de juego WebSocket (Duelo Frostbyte)
│   ├── impostor/       # Juego del impostor (palabras por categoría)
│   ├── analytics/      # Finanzas, canales y clientes, horario real del local
│   ├── ai_generator/   # Generación de imágenes IA
│   ├── music/          # Solicitudes de música (Spotify + configuración)
│   ├── youtube/        # Solicitudes de video / reproductor TV
│   ├── feedback/       # Feedback de clientes
│   ├── motivational/   # Frases, recomendaciones y tarjetas de celebración
│   ├── recetarios/     # Guías de preparación paso a paso
│   ├── whatsapp/       # Frosty: agente de pedidos por WhatsApp (Kapso + LangGraph)
│   ├── reservations/   # Reservas de mesas, grupos y Sala VIP
│   ├── contests/       # Concursos con inscripción paga (disfraces de Halloween)
│   └── polla/          # Solo modelos: datos históricos de la Polla del Mundial 2026
├── config/
│   ├── settings.py     # Configuración Django
│   ├── urls.py         # Rutas principales
│   ├── asgi.py         # Config ASGI (WebSocket)
│   ├── liveness.py     # Vigía de vida: reinicia el proceso si se congela
│   └── wsgi.py         # Config WSGI
└── media/              # Archivos subidos
```

## Features de IA Integrados

Frostbyte incluye múltiples capacidades de IA (OpenAI, Google Gemini y Anthropic), distribuidas en vistas públicas, privadas y en el agente de WhatsApp:

### Backend - APIs de IA

#### Públicas (Sin Autenticación)
| Feature | Endpoint | Modelo | Descripción |
|---------|----------|--------|-------------|
| **Recomendación por Mood** | `POST /motivational/recommend-mood/` | GPT-4o-mini | Recomienda bebidas según el estado de ánimo del usuario ("tengo calor y quiero algo fuerte") |
| **Recomendación por Quiz** | `POST /motivational/recommend-quiz/` | GPT-4o-mini | Recomienda bebidas basado en preferencias de temperatura, sabor y alcohol |
| **Frase Motivacional Diaria** | `GET /motivational/phrase/` | GPT-4o-mini | Genera frase motivacional o dato histórico basado en la fecha (cacheada 30 min) |
| **Transcripción de Audio** | `POST /motivational/transcribe/` | Whisper | Transcribe archivos de audio a texto en español |

#### Privadas (Requiere Autenticación)
| Feature | Endpoint | Modelo | Descripción |
|---------|----------|--------|-------------|
| **Generador de Imágenes** | `POST /ai/generations/` | GPT Image 1.5 / GPT Image 2 / Gemini Pro / Gemini Flash | Genera imágenes profesionales de productos a partir de foto + prompt (modelo seleccionable) |
| **Sugerencia de Descripción** | `POST /ai/suggest-description/` | GPT-4o-mini | Genera descripciones cortas y atractivas para productos |
| **Galería de Generaciones** | `GET /ai/generations/` | - | Historial de imágenes generadas por el usuario |
| **Persistencia a R2** | `POST /ai/generations/{id}/save_to_r2/` | - | Guarda imágenes generadas en Cloudflare R2 |
| **Asignar a Producto** | `POST /ai/generations/{id}/save_to_product/` | - | Asigna imagen generada como foto principal del producto |

### Frontend - Interfaces de IA

#### Públicas (Vistas Digitales del Menú)
- **Quiz Recomendador**: Interfaz interactiva en menú público para seleccionar preferencias
- **Generador de Frases**: Widget que muestra frase motivacional actualizada diariamente
- **Transcripción de Voz**: Opción para dictar estado de ánimo en lugar de escribir

#### Privadas (Panel de Administración)
- **AIImageGeneratorPage** (`/productos/generador-ia`):
  - Carga imagen original + imagen de referencia (opcional)
  - Editor de prompt con sugerencias preestablecidas
  - Toggle para fondo transparente
  - Galería con historial de generaciones
  - Descarga, guardado a R2 y asignación a productos
  - Muestra prompt utilizado y metadatos de generación

---

## Módulos Principales

### Autenticación (`/apps/accounts/`)
- Modelo de usuario personalizado con roles (admin, employee, customer)
- Clientes con cuenta de Google (`/auth/google/`) para pedir a domicilio, inscribirse en concursos y reservar
- JWT con access token (12h) y refresh token (90 días, ventana deslizante)
- Rotación automática de tokens
- Endpoints: login, logout, refresh, me, change-password

### Productos (`/apps/products/`)
- Categorías, productos y variantes
- Generación automática de SKU
- Gestión de imágenes
- Estados: activo, inactivo, agotado

### Negocios (`/apps/business/`)
- Cada negocio (Frostbyte bebidas en el piso 2, Frostbyte Food en el piso 3) tiene su propio catálogo, inventario y gastos
- Lo anterior a la dimensión multi-negocio pertenece a `frostbyte`, el negocio por defecto

### Pedidos (`/apps/orders/`)
- Ciclo de vida: Pendiente → Preparando → Listo → Entregado
- Canales: en el local (por mesa y piso), para recoger y a domicilio (cliente con Google o por WhatsApp)
- Domicilios: zona de cobertura dibujada como polígono, interruptor fechado para prender o apagar el servicio
- Pedidos pendientes de pago (deudas) y métodos de pago por pedido
- Métodos de pago: Efectivo, tarjeta, transferencia, Nequi, Daviplata
- Items con notas (alergias, preferencias)
- Tracking por mesa

### Inventario (`/apps/inventory/`)
- Materias primas con control de stock
- Recetas vinculadas a variantes
- Órdenes de compra a proveedores
- Alertas de stock bajo

### Gastos (`/apps/expenses/`)
- Gastos operacionales diarios
- Gastos recurrentes (diario, semanal, mensual)
- Categorías con iconos
- Límites presupuestarios

### Música (`/apps/music/` + `/apps/youtube/`)
**Sistema dual de solicitudes musicales. El admin elige la fuente activa (Spotify o YouTube, default YouTube) desde `MusicSettings`; los clientes sólo ven la que esté activa.**

#### Spotify (`/apps/music/`)
- **Solicitudes de clientes**: Buscan y piden canciones desde el menú digital
- **Cola automática**: Las canciones se agregan automáticamente a la cola de Spotify del local
- **Sincronización en tiempo real**: Hilo background sincroniza estados cada 5s con Spotify
- **Controles de playback**: play, pause, skip, volumen desde el panel admin
- **OAuth2**: Flujo Authorization Code con Spotify, tokens con auto-refresh
- **Cola completa**: El admin ve toda la cola de Spotify, con indicador de cuáles son solicitudes
- **Letras sincronizadas**: LRCLib, matching por artista + nombre + duración
- **Dependencia**: `spotipy` · **Requiere**: Cuenta Spotify Premium

#### YouTube (`/apps/youtube/`)
- **Solicitudes y cola**: Los clientes buscan videos y los agregan; se reproducen en secuencia
- **Pantalla TV (`/youtube-tv`)**: Página pública que se abre en un navegador conectado a la TV del local. Usa la IFrame Player API para reproducir los videos a pantalla completa
- **Auto-Mix**: Cuando la cola está vacía, la TV carga automáticamente un Mix de YouTube (`RD<videoId>`) basado en el último video reproducido, de modo que **siempre haya algo sonando**
- **TVState**: La pantalla reporta vía WebSocket qué video está sonando (incluso videos del Mix que no están en la DB). La vista pública "PIDE TU VIDEO" refleja en tiempo real lo que suena en la TV
- **Recomendaciones**: Endpoint `/recommendations` que devuelve videos similares usando el último video como semilla (o trending de música si no hay historial). Se cachean para ahorrar cuota
- **Controles**: play, pause, resume, skip desde el panel admin (se propagan a la TV vía WebSocket)
- **Cache inteligente**: Búsquedas cacheadas 24h y trending 48h (Redis en prod, LocMem en dev). Si la cuota se agota, sirve el cache "stale" por 7 días como fallback
- **Tracking de cuota**: Cada llamada a la API suma su costo (100 para `search.list`, 1 para `videos.list`). Endpoint `/quota-status` expone el consumo del día. Reseteo diario a medianoche Pacífico (03:00 Colombia)
- **MusicSettings**: Singleton `{ source: 'spotify' | 'youtube' }` expuesto en `/music-settings/` (GET público, PATCH admin). El admin toggle desde el panel cambia qué componente ven los clientes

#### Estadísticas de música (`/music-stats/`)
- **Qué responde**: géneros, comparativa entre pisos, hora de la noche, día de la semana, tops de canciones y artistas, y evolución de géneros mes a mes. Solo staff autenticado. Parámetros: `days` (o `all`), `start`/`end` en ISO y `floor`
- **La noche, no la fecha**: lo pedido antes de las 6 a. m. cuenta para la noche anterior (`stats.OPERATING_DAY_CUTOFF_HOUR`), si no cada noche aparecería partida en dos
- **Los géneros NO vienen de Spotify**: la API devuelve `genres: []` para todos los artistas de esta aplicación y los endpoints por lotes responden 403. El género lo pone el modelo de lenguaje una sola vez por artista y se cachea en `ArtistGenre` (taxonomía cerrada en `apps/music/genres.py`)
  - `manage.py classify_artist_genres` clasifica solo los artistas que faltan (`--force` para rehacer; nunca pisa lo corregido a mano en el admin)
  - `manage.py load_artist_genres` carga los ~1.000 ya clasificados desde `fixtures/artist_genres.json` sin gastar llamadas; `--dump` regenera el archivo tras clasificar artistas nuevos
- **Front**: `/musica/estadisticas` (pestaña del panel de música)

### Frosty, agente de WhatsApp (`/apps/whatsapp/`)
- Toma pedidos a domicilio y para recoger de principio a fin; los pedidos son `Order` normales (`source=whatsapp`) y llegan al KDS
- Modelo principal **Claude Sonnet 5.5** (`WHATSAPP_AGENT_MODEL`, esfuerzo `high`) con respaldo **GPT-5.6 Terra** (`WHATSAPP_AGENT_FALLBACK_MODEL`); visión, resumen y transcripción con modelos de OpenAI configurables
- Una respuesta por ráfaga de mensajes, memoria por contacto con resumen de conversaciones largas, totales calculados por la tool `cotizar_pedido` (el modelo nunca suma)
- Avisa al cliente los cambios de estado del pedido, retoma conversaciones que quedaron esperando y cede la conversación al equipo cuando hace falta
- Bandeja de chats de solo lectura para el personal (`/whatsapp/conversations/`), stickers y tonos del agente configurables
- Detalle: [docs/whatsapp-agent.md](docs/whatsapp-agent.md)

### Reservas (`/apps/reservations/`)
- Mesas (gratis), grupos grandes (anticipo abonable) y Sala VIP del piso 3 (anticipo + consumo mínimo, por turnos)
- Panel del staff y avisos en tiempo real por `ws/reservations/`

### Concursos (`/apps/contests/`)
- Inscripción paga con cuenta de Google; el staff marca pago, cédula y seguimiento en Instagram desde la barra
- Primer concurso: disfraces de Halloween 2026. Cuenta visitas únicas a la página del concurso

### Analytics (`/apps/analytics/`)
- `financial`: ventas, gastos y margen
- `channels`: canales, domicilios y registros con Google
- `schedule`: horario real de apertura y cierre del local por jornada

### Juegos (`/apps/games/` + `/apps/impostor/`)
- "Impostor": sesiones con palabras por categoría (generadas con GPT-4o-mini)
- "Duelo Frostbyte" - Juego de reacción multijugador
- WebSocket con Django Channels
- Salas con códigos únicos
- Tracking de tiempos de reacción

### Generador IA (`/apps/ai_generator/`)
**Generación Profesional de Imágenes de Productos con IA (Gemini / OpenAI)**
- **Modelos disponibles** (seleccionables desde el frontend):
  - `gpt-image-1.5` - OpenAI GPT Image 1.5 (default, transparencia nativa)
  - `gpt-image-2` - OpenAI GPT Image 2 (sin fondo transparente)
  - `gemini-3-pro-image-preview` - Google Gemini Pro Image
  - `gemini-3.1-flash-image-preview` - Google Gemini Flash Image (rápido)
- **Flujo**:
  1. Usuario carga imagen original (foto básica/celular)
  2. Opcionalmente agrega imagen de referencia para aplicar estilo
  3. Selecciona modelo de IA (GPT Image 1.5 por defecto)
  4. Escribe prompt detallado o usa plantillas preestablecidas
  5. IA procesa y genera imagen profesional
  6. Opción de fondo transparente (OpenAI: nativo, Gemini: rembg post-procesado)
- **Almacenamiento**:
  - Temporal: Archivos en servidor (se eliminan en 24h)
  - Persistente: Cloudflare R2 para imágenes aprobadas
- **Integración**: Asignación directa como imagen del producto en catálogo
- **Soporte**: Prompt builder con sugerencias por categoría de bebida
- **Sugerencias Automáticas**: Endpoint para generar descripciones de productos con GPT-4o-mini

### Motivacional (`/apps/motivational/`)
**Recomendaciones Personalizadas y Frases Inspiradoras con OpenAI**
- **Frases Motivacionales**:
  - Generadas diariamente con GPT-4o-mini
  - Incluyen datos históricos/curiosidades según la fecha
  - Cacheadas por 30 minutos
  - Tono juvenil, casual y colombiano
- **Recomendaciones por Mood** (Público):
  - Usuario describe su estado de ánimo ("tengo frío y quiero algo dulce")
  - IA recomienda bebida más adecuada
  - Respuesta personalizada con razón en español colombiano
- **Recomendaciones por Quiz** (Público):
  - Quiz interactivo: temperatura, sabor, alcohol
  - IA analiza preferencias y recomienda producto
  - Logging de todas las recomendaciones para analytics
- **Transcripción de Audio** (Público):
  - Soporta entrada por voz usando Whisper
  - Convierte audio a texto en español
  - Integración con recomendaciones por mood
- **Logging de Recomendaciones**: Almacena todas las interacciones para análisis

## API Endpoints

Base URL: `http://localhost:8000/api/v1/`

| Módulo | Endpoint | Descripción |
|--------|----------|-------------|
| Auth | `/auth/login/` | Login con JWT (staff) |
| Auth | `/auth/google/` | Login de clientes con Google |
| Auth | `/auth/refresh/` | Refrescar token |
| Auth | `/auth/me/` | Usuario actual |
| Business | `/businesses/` | Negocios |
| Products | `/categories/` | CRUD categorías |
| Products | `/products/` | CRUD productos |
| Products | `/variants/` | CRUD variantes |
| Orders | `/orders/` | CRUD pedidos |
| Orders | `/tables/` | Mesas del restaurante |
| Orders | `/public-orders/` · `/my-orders/` | Pedidos del cliente con Google |
| Inventory | `/inventory/materials/` | Materias primas |
| Inventory | `/inventory/recipes/` | Recetas |
| Expenses | `/expenses/` | Gastos operacionales |
| Games | `/games/rooms/` | Salas de juego |
| Impostor | `/impostor/categories/` · `/impostor/sessions/` | Juego del impostor |
| Recetarios | `/recetarios/recipes/` | Recetarios de preparación |
| WhatsApp | `/whatsapp/webhook/` | Webhook de Kapso (firma HMAC) |
| WhatsApp | `/whatsapp/conversations/` | Bandeja de chats (solo lectura, staff) |
| WhatsApp | `/whatsapp/agent-settings/` · `/whatsapp/agent-tones/` · `/whatsapp/stickers/` | Configuración de Frosty |
| Reservations | `/reservations/` · `/reservations/admin/` | Reservas del cliente y panel del staff |
| Contests | `/contests/current/` · `/contests/admin/` | Concurso vigente, inscripción y panel de barra |
| Music | `/song-requests/` | CRUD solicitudes de canciones |
| Music | `/song-requests/search/` | Buscar canciones en Spotify |
| Music | `/song-requests/now_playing/` | Canción reproduciéndose |
| Music | `/song-requests/queue_status/` | Cola de Spotify completa |
| Music | `/song-requests/spotify_status/` | Estado de conexión Spotify |
| Music | `/song-requests/player_pause/` | Pausar reproducción |
| Music | `/song-requests/player_resume/` | Reanudar reproducción |
| Music | `/song-requests/player_next/` | Siguiente canción |
| Music | `/song-requests/player_previous/` | Canción anterior |
| Music | `/song-requests/player_play_track/` | Reproducir canción específica |
| Music | `/song-requests/player_volume/` | Ajustar volumen |
| Music | `/song-requests/lyrics/` | Letras sincronizadas via LRCLib |
| Spotify Auth | `/spotify/auth/` | Iniciar OAuth con Spotify |
| Spotify Auth | `/spotify/callback/` | Callback OAuth Spotify |
| Spotify Auth | `/spotify/disconnect/` | Desconectar Spotify |
| Music Settings | `/music-settings/` | Fuente activa (Spotify/YouTube). GET público, PATCH admin |
| YouTube | `/video-requests/` | CRUD solicitudes de video |
| YouTube | `/video-requests/search/` | Buscar en YouTube (cacheado 24h) |
| YouTube | `/video-requests/recommendations/` | Recomendaciones basadas en historial o trending |
| YouTube | `/video-requests/now-playing/` | Video actual (VideoRequest PLAYING o TVState) |
| YouTube | `/video-requests/last-played/` | Último video completado (semilla para el Mix) |
| YouTube | `/video-requests/queue/` | Cola pendiente |
| YouTube | `/video-requests/quota-status/` | Estado de cuota YouTube API (admin) |
| YouTube | `/video-requests/player/play/` | Reproducir video específico |
| YouTube | `/video-requests/player/next/` | Saltar al siguiente (o limpiar para Mix) |
| YouTube | `/video-requests/player/pause/` | Pausar reproducción en TV |
| YouTube | `/video-requests/player/resume/` | Reanudar reproducción en TV |
| **AI - Imágenes** | **`POST /ai/generations/`** | **Generar imagen con IA (Gemini Pro / Flash / GPT Image 1.5)** |
| **AI - Imágenes** | **`GET /ai/generations/`** | **Historial de generaciones** |
| **AI - Imágenes** | **`POST /ai/generations/{id}/save_to_r2/`** | **Persistir imagen en Cloudflare R2** |
| **AI - Imágenes** | **`POST /ai/generations/{id}/save_to_product/`** | **Asignar imagen a producto** |
| **AI - Imágenes** | **`POST /ai/suggest-description/`** | **Sugerir descripción con IA (GPT-4o-mini)** |
| **AI - Motivacional** | **`GET /motivational/phrase/`** | **Frase motivacional diaria (GPT-4o-mini)** |
| **AI - Motivacional** | **`POST /motivational/recommend-mood/`** | **Recomendar por estado de ánimo (GPT-4o-mini)** |
| **AI - Motivacional** | **`POST /motivational/recommend-quiz/`** | **Recomendar por preferencias (GPT-4o-mini)** |
| **AI - Motivacional** | **`POST /motivational/transcribe/`** | **Transcribir audio a texto (Whisper)** |
| Analytics | `/analytics/financial/` · `/analytics/channels/` · `/analytics/schedule/` | Finanzas, canales y clientes, horario del local |

Fuera de `/api/v1/`: `GET /healthz/` (salud del proceso, no toca la base).

### WebSocket
```
ws://localhost:8000/ws/game/room/{room_id}/
ws://localhost:8000/ws/games/admin/
ws://localhost:8000/ws/orders/
ws://localhost:8000/ws/music/
ws://localhost:8000/ws/youtube/
ws://localhost:8000/ws/reservations/
```

**Mensajes en `/ws/youtube/`:**
- Entrantes (desde clientes):
  - `{type: 'ping'}` → responde `pong`
  - `{type: 'tv_playing', video_id, title, channel_name, is_mix}` - La TV reporta qué está sonando
  - `{type: 'video_ended', video_id}` - La TV notifica fin de video
- Salientes (broadcast a todos):
  - `{type: 'youtube_changed'}` - Invalida queries en todos los clientes
  - `{type: 'play_video', video_id, title}` - Instrucción a la TV de reproducir un video
  - `{type: 'player_control', action}` - pause/resume

## Instalación

### Requisitos Previos
- Python 3.12+
- PostgreSQL
- Redis (opcional, para producción)

### Setup Local

```bash
# Crear entorno virtual
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Editar .env con tus configuraciones

# Ejecutar migraciones
python manage.py migrate

# Crear superusuario
python manage.py createsuperuser

# Iniciar servidor de desarrollo
python manage.py runserver
```

### Base de Datos con Docker

```bash
docker compose -f docker-compose.dev.yml up -d db
```

## Configuración

Ver [.env.example](.env.example) para todas las variables de entorno disponibles.

### Variables Principales

| Variable | Descripción | Requerida | Uso |
|----------|-------------|-----------|-----|
| `DATABASE_URL` | URL de conexión PostgreSQL | Sí | Base de datos |
| `SECRET_KEY` | Clave secreta Django | Sí | Seguridad |
| `DEBUG` | Modo debug | No | Desarrollo |
| **`OPENAI_API_KEY`** | **API key de OpenAI** | **Para IA** | **Generación de imágenes (GPT Image 1.5), frases motivacionales, recomendaciones, transcripción de audio y sugerencias de descripción** |
| `ANTHROPIC_API_KEY` | API key de Anthropic | Para Frosty | Modelo principal del agente de WhatsApp |
| `KAPSO_API_KEY` · `KAPSO_WEBHOOK_SECRET` · `KAPSO_PHONE_NUMBER_IDS` | Credenciales de Kapso y números propios | Para Frosty | Webhook y envío de mensajes de WhatsApp |
| `WHATSAPP_AGENT_*` · `WHATSAPP_*` | Modelo, respaldo, esfuerzo, esperas, rescates y teléfonos del agente | No (tienen default) | Ajustes de Frosty |
| `GOOGLE_CLIENT_ID` | Client ID de Google | Para clientes | Login de clientes con Google |
| `DELIVERY_CENTER_LAT` · `DELIVERY_CENTER_LNG` · `DELIVERY_RADIUS_KM` | Centro y radio de respaldo de domicilios | No | Cobertura de domicilios |
| **`GEMINI_API_KEY`** | **API key de Google Gemini** | **Para IA** | **Generación de imágenes con Gemini (proveedor por defecto)** |
| `R2_*` | Credenciales Cloudflare R2 | Para almacenamiento | Persistencia de imágenes generadas |
| `REDIS_URL` | URL de Redis | Producción | Channel layers y caché |
| `SPOTIFY_CLIENT_ID` | Client ID de Spotify Developer | Para música | Integración Spotify |
| `SPOTIFY_CLIENT_SECRET` | Client Secret de Spotify Developer | Para música | Integración Spotify |
| `SPOTIFY_REDIRECT_URI` | URL de callback OAuth Spotify | Para música | Debe coincidir con Spotify Dashboard |
| `FRONTEND_URL` | URL del frontend | Para música | Redirección post-OAuth |
| **`YOUTUBE_API_KEY`** | **API key de YouTube Data API v3** | **Para módulo YouTube** | **Búsqueda, trending, recomendaciones** |
| `YOUTUBE_QUOTA_LIMIT` | Límite diario de unidades (default 10000) | No | Tracking de cuota en admin |

### Configuración de IA

Para habilitar todas las funcionalidades de IA en Frostbyte:

1. **Obtener API Keys**:
   - OpenAI: `https://platform.openai.com/api-keys`
   - Google Gemini: `https://aistudio.google.com/apikey`

2. **Configurar Variables de Entorno**:
   ```bash
   OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx
   GEMINI_API_KEY=AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
   ```

3. **Modelos Utilizados**:
   - `gemini-3-pro-image-preview`: Generación de imágenes (default, mejor calidad)
   - `gemini-3.1-flash-image-preview`: Generación de imágenes (rápido)
   - `gpt-image-1.5`: Generación de imágenes (transparencia nativa)
   - `gpt-4o-mini`: Recomendaciones, frases motivacionales, descripciones
   - `whisper-1`: Transcripción de audio

4. **Límites y Costos**:
   - Generación con Gemini Pro: ~$0.04 por imagen
   - Generación con Gemini Flash: ~$0.02 por imagen
   - Generación con OpenAI GPT Image 1.5: ~$0.04 por imagen
   - Recomendaciones/frases: ~$0.0001 por request (GPT-4o-mini)
   - Transcripción: ~$0.006 por minuto
   - Las frases motivacionales se cachean 30 minutos para economizar

### Configuración de YouTube Data API v3

Paso a paso para obtener la API key:

1. **Crear/seleccionar proyecto en Google Cloud Console**
   - Ir a https://console.cloud.google.com/
   - Selector de proyectos arriba → "New Project" (o usar uno existente)

2. **Habilitar YouTube Data API v3**
   - Menú lateral → **APIs & Services → Library**
   - Buscar "YouTube Data API v3" → Click en el resultado → **Enable**

3. **Crear API Key**
   - **APIs & Services → Credentials**
   - **+ CREATE CREDENTIALS → API key**
   - Copiar la clave generada (formato `AIzaSy...`)

4. **Restringir la API key (recomendado)**
   - Click en la key recién creada → "Edit API key"
   - **API restrictions**: seleccionar **Restrict key** → marcar **YouTube Data API v3** → Save

5. **Agregar al `.env`**
   ```bash
   YOUTUBE_API_KEY=AIzaSy...
   ```

**Cuota y costos**:
- Free tier: **10.000 unidades/día** (se renueva a medianoche hora Pacífico = 03:00 Colombia)
- `search.list` = **100 unidades** por llamada (la más costosa)
- `videos.list` = **1 unidad** por llamada (trending, detalles, duraciones)
- Una búsqueda en la app = 101 unidades (search + detalles de duración)
- **Optimizaciones implementadas**:
  - Cache de búsquedas 24h y trending 48h en Redis/LocMem
  - Cache "stale" por 7 días como fallback si se excede la cuota
  - Debounce de 1s y mínimo 3 caracteres en el frontend
  - Las recomendaciones comparten cache con búsquedas (usando canal como query)
- **Pedir aumento de cuota** (gratis): Google Cloud Console → APIs & Services → YouTube Data API v3 → Quotas → Request quota increase (llenar formulario, se aprueba en 1-3 días)

## Desarrollo

### Generar requirements.txt

```bash
source .venv/bin/activate && uv pip compile pyproject.toml -o requirements.txt
```

### Crear Migraciones

```bash
python manage.py makemigrations
python manage.py migrate
```

### Panel Admin

Acceder a `http://localhost:8000/admin/` con las credenciales del superusuario.

## Producción

- Servidor ASGI: Daphne para HTTP y WebSocket (Gunicorn está en las dependencias pero no se usa en el arranque)
- Archivos estáticos: WhiteNoise
- Base de datos: PostgreSQL
- Channel layers: Redis
- Deployment: Railway

### Configuración Railway

El proyecto incluye `railway.toml` (Nixpacks) y Railway despliega solo con cada push a `main`. El arranque corre `migrate`, los seeds `seed_cocktail_histories` y `seed_food`, `collectstatic` y luego Daphne.

- **Vigía de vida** (`config/liveness.py`): un hilo se pide `/healthz/` por el mismo camino que cualquier request; si falla varias veces seguidas, vuelca la pila al log y termina el proceso. Railway lo levanta de nuevo (`restartPolicyType = "ON_FAILURE"`), porque por sí solo no detecta un proceso vivo pero congelado.

## Autenticación

### JWT Tokens
- Access token: 12 horas
- Refresh token: 7 días
- Rotación automática habilitada
- Blacklist después de rotación

### Roles
- `admin`: Acceso completo
- `employee`: Acceso limitado a operaciones
- `customer`: Cliente con cuenta de Google (pedidos, reservas, concursos)

## Testing

```bash
python manage.py test
```

## Estructura de Modelos

### Principales
- `User` - Usuario con rol (admin/employee/customer)
- `Business` - Negocio (catálogo, inventario y gastos propios)
- `Category` - Categorías de productos
- `Product` - Productos del menú
- `ProductVariant` - Variantes con precios
- `Order` - Pedidos con estado
- `OrderItem` - Items de pedido
- `RawMaterial` - Materias primas
- `Recipe` - Recetas de productos
- `OperationalExpense` - Gastos
- `GameRoom` - Salas de juego
- `AIImageGeneration` - Imágenes generadas
- `SongRequest` - Solicitudes de canciones Spotify (estados: pending, queued, playing, completed, cancelled, failed)
- `SpotifyToken` - Token OAuth de Spotify (singleton, auto-refresh)
- `MusicSettings` - Singleton con la fuente activa (spotify | youtube, default youtube)
- `VideoRequest` - Solicitudes de videos YouTube (estados: pending, queued, playing, completed, cancelled)
- `TVState` - Singleton con el estado actual de la pantalla TV (incluye videos del Mix)
- `WhatsAppContact` - Contacto del agente de WhatsApp con sus preferencias
- `Reservation` - Reservas de mesas, grupos y Sala VIP
- `Contest` / `ContestEntry` - Concursos e inscripciones
