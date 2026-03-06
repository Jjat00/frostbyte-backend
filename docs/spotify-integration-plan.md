# Plan: Sistema de Peticion de Canciones con Spotify - Frostbyte

## Objetivo
Los clientes piden canciones y estas se agregan automaticamente a la cola de reproduccion de Spotify del local, eliminando la necesidad de que el empleado busque y ponga canciones manualmente.

## Arquitectura

```
CLIENTE (Publico)          BACKEND (Django)           SPOTIFY API

1. Busca cancion --------> 2. GET /v1/search -------> Spotify Search
   (resultados)  <--------    (resultados)   <-------

3. Selecciona    --------> 4. Crea SongRequest
   cancion                 5. POST /v1/me/player/queue -> Add to Queue
                  <-------- 6. WebSocket notifica

ADMIN (Dashboard)

A. Conecta Spotify ------> OAuth2 Authorization Code -> Spotify Auth
B. Ve cola         <------- Queue status
C. Controles       ------> Skip/pause              -> Player Controls
```

## Fases

### Fase 1: Autenticacion Spotify (Backend)

**Archivos a crear/modificar:**
- `apps/music/models.py` - Agregar modelo SpotifyToken
- `apps/music/services/spotify_auth.py` - Servicio OAuth2
- `apps/music/services/spotify_client.py` - Cliente Spotify (search, queue, player)
- `apps/music/views.py` - Endpoints OAuth + search + queue
- `apps/music/serializers.py` - Serializers nuevos
- `apps/music/urls.py` - Rutas nuevas

**Flujo OAuth:**
1. Admin -> "Conectar Spotify" -> Backend genera URL de autorizacion
2. Redirige a Spotify -> Usuario autoriza
3. Spotify redirige a callback con code
4. Backend intercambia code por access_token + refresh_token
5. Tokens se guardan en DB
6. Refresh automatico cuando expira el access_token

**Scopes:**
- user-modify-playback-state (agregar a cola, skip, pause)
- user-read-playback-state (ver que esta sonando)
- user-read-currently-playing (cancion actual)

**Dependencia:** spotipy (SDK oficial Python para Spotify)

### Fase 2: Busqueda y Cola (Backend)

**Endpoints nuevos:**
- GET /api/v1/song-requests/spotify/search/?q=... - Buscar en Spotify
- POST /api/v1/song-requests/ - Crear request + encolar automaticamente
- GET /api/v1/song-requests/now-playing/ - Cancion actual
- GET /api/v1/song-requests/queue-status/ - Estado de la cola

**Modelo SongRequest actualizado:**
- Agregar campo spotify_track_uri
- Agregar campo spotify_track_image
- Agregar campo spotify_track_duration_ms
- Agregar campo requested_by_device

### Fase 3: Interfaz del Cliente (Frontend)

**Archivos nuevos/modificados:**
- src/pages/music/SongSearch.jsx - Buscador con resultados de Spotify
- src/pages/music/NowPlaying.jsx - Cancion sonando + cola
- src/services/music.service.js - Metodos API para Spotify
- src/hooks/useSpotifySearch.js - Hook de busqueda con debounce

**UX:**
1. Cliente entra a seccion "Musica"
2. Barra de busqueda (debounce 500ms)
3. Resultados: portada, nombre, artista, duracion
4. Boton "Pedir cancion" -> agrega a cola
5. Vista de cola actual + now playing

### Fase 4: Panel Admin (Frontend)

**Archivos nuevos:**
- src/pages/admin/SpotifySettings.jsx - Conectar/desconectar Spotify
- Modificar dashboard de musica con controles de reproduccion

**Funcionalidades:**
- Boton conectar/desconectar Spotify (OAuth)
- Estado de conexion
- Controles: skip, pause/play
- Moderacion de cola
- Toggle on/off peticiones

## Variables de entorno nuevas
```
SPOTIFY_CLIENT_ID=xxx
SPOTIFY_CLIENT_SECRET=xxx
SPOTIFY_REDIRECT_URI=http://localhost:8000/api/v1/music/spotify/callback/
```

## Dependencias nuevas
- Backend: spotipy
