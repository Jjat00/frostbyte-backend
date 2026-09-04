# Frostbyte Backend

API REST para gestión de negocios de bebidas preparadas (granizados, frappés, cócteles), bares y restaurantes. Construida con Django y Django REST Framework. Incluye autenticación JWT, WebSockets para juegos en tiempo real y generación de imágenes con IA.

## Stack Tecnológico

| Tecnología | Versión | Descripción |
|------------|---------|-------------|
| Django | 6.0.1 | Framework web |
| Django REST Framework | 3.16.1 | API REST |
| SimpleJWT | 5.5.1 | Autenticación JWT |
| Django Channels | 4.3.2 | WebSocket support |
| PostgreSQL | - | Base de datos |
| Redis | - | Channel layers (producción) |
| OpenAI | 2.15.0 | Generación de imágenes (GPT Image 1.5) |
| Google GenAI | 1.68.0 | Generación de imágenes (Gemini Pro/Flash) |
| rembg | 2.0.73 | Remoción de fondo con IA (para Gemini) |
| Cloudflare R2 | - | Almacenamiento de archivos |
| Spotipy | 2.26+ | SDK Python para Spotify Web API |
| YouTube Data API v3 | - | Búsqueda y datos de videos de YouTube |
| Gunicorn + Daphne | - | Servidores de producción |

## Arquitectura

```
frostbyte-backend/
├── apps/
│   ├── accounts/       # Autenticación y usuarios
│   ├── products/       # Catálogo de productos
│   ├── inventory/      # Control de inventario
│   ├── orders/         # Gestión de pedidos
│   ├── expenses/       # Gastos operacionales
│   ├── games/          # Salas de juego WebSocket
│   ├── analytics/      # Reportes y dashboards
│   ├── ai_generator/   # Generación de imágenes IA
│   ├── music/          # Solicitudes de música (Spotify + configuración)
│   ├── youtube/        # Solicitudes de video / reproductor TV
│   ├── feedback/       # Feedback de clientes
│   └── motivational/   # Frases motivacionales
├── config/
│   ├── settings.py     # Configuración Django
│   ├── urls.py         # Rutas principales
│   ├── asgi.py         # Config ASGI (WebSocket)
│   └── wsgi.py         # Config WSGI
└── media/              # Archivos subidos
```

## Features de IA Integrados

Frostbyte incluye múltiples capacidades de IA usando **OpenAI API**, distribuidas en vistas públicas y privadas:

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
| **Generador de Imágenes** | `POST /ai/generations/` | Gemini Pro Image / GPT Image 1.5 | Genera imágenes profesionales de productos a partir de foto + prompt (modelo seleccionable) |
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
- Modelo de usuario personalizado con roles
- JWT con access token (12h) y refresh token (7 días)
- Rotación automática de tokens
- Endpoints: login, logout, refresh, me, change-password

### Productos (`/apps/products/`)
- Categorías, productos y variantes
- Generación automática de SKU
- Gestión de imágenes
- Estados: activo, inactivo, agotado

### Pedidos (`/apps/orders/`)
- Ciclo de vida: Pendiente → Preparando → Listo → Entregado
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

### Juegos (`/apps/games/`)
- "Duelo Frostbyte" - Juego de reacción multijugador
- WebSocket con Django Channels
- Salas con códigos únicos
- Tracking de tiempos de reacción

### Generador IA (`/apps/ai_generator/`)
**Generación Profesional de Imágenes de Productos con IA (Gemini / OpenAI)**
- **Modelos disponibles** (seleccionables desde el frontend):
  - `gemini-3-pro-image-preview` - Google Gemini Pro Image (default, mejor calidad)
  - `gemini-3.1-flash-image-preview` - Google Gemini Flash Image (rápido)
  - `gpt-image-1.5` - OpenAI GPT Image 1.5 (transparencia nativa)
- **Flujo**:
  1. Usuario carga imagen original (foto básica/celular)
  2. Opcionalmente agrega imagen de referencia para aplicar estilo
  3. Selecciona modelo de IA (Gemini Pro por defecto)
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
| Auth | `/auth/login/` | Login con JWT |
| Auth | `/auth/refresh/` | Refrescar token |
| Auth | `/auth/me/` | Usuario actual |
| Products | `/categories/` | CRUD categorías |
| Products | `/products/` | CRUD productos |
| Products | `/variants/` | CRUD variantes |
| Orders | `/orders/` | CRUD pedidos |
| Orders | `/tables/` | Mesas del restaurante |
| Inventory | `/inventory/materials/` | Materias primas |
| Inventory | `/inventory/recipes/` | Recetas |
| Expenses | `/expenses/` | Gastos operacionales |
| Games | `/games/rooms/` | Salas de juego |
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
| Analytics | `/analytics/` | Reportes |

### WebSocket
```
ws://localhost:8000/ws/games/rooms/{room_id}/
ws://localhost:8000/ws/music/
ws://localhost:8000/ws/youtube/
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

- Servidor ASGI: Daphne (WebSocket) + Gunicorn (HTTP)
- Archivos estáticos: WhiteNoise
- Base de datos: PostgreSQL
- Channel layers: Redis
- Deployment: Railway

### Configuración Railway

El proyecto incluye `railway.toml` para deployment automático.

## Autenticación

### JWT Tokens
- Access token: 12 horas
- Refresh token: 7 días
- Rotación automática habilitada
- Blacklist después de rotación

### Roles
- `admin`: Acceso completo
- `employee`: Acceso limitado a operaciones

## Testing

```bash
python manage.py test
```

## Estructura de Modelos

### Principales
- `User` - Usuario con rol (admin/employee)
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
