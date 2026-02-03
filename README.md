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
| OpenAI | 2.15.0 | Generación de imágenes |
| Cloudflare R2 | - | Almacenamiento de archivos |
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
│   ├── music/          # Solicitudes de música
│   ├── feedback/       # Feedback de clientes
│   └── motivational/   # Frases motivacionales
├── config/
│   ├── settings.py     # Configuración Django
│   ├── urls.py         # Rutas principales
│   ├── asgi.py         # Config ASGI (WebSocket)
│   └── wsgi.py         # Config WSGI
└── media/              # Archivos subidos
```

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

### Juegos (`/apps/games/`)
- "Duelo Frostbyte" - Juego de reacción multijugador
- WebSocket con Django Channels
- Salas con códigos únicos
- Tracking de tiempos de reacción

### Generador IA (`/apps/ai_generator/`)
- Integración con OpenAI GPT Image 1.5
- Límites diarios/mensuales
- Almacenamiento en Cloudflare R2
- Corrección de transparencia

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
| AI | `/ai/images/generate/` | Generar imagen |
| AI | `/ai/images/usage/` | Uso del servicio |
| Analytics | `/analytics/` | Reportes |

### WebSocket
```
ws://localhost:8000/ws/games/rooms/{room_id}/
```

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

| Variable | Descripción | Requerida |
|----------|-------------|-----------|
| `DATABASE_URL` | URL de conexión PostgreSQL | Sí |
| `SECRET_KEY` | Clave secreta Django | Sí |
| `DEBUG` | Modo debug | No |
| `OPENAI_API_KEY` | API key de OpenAI | Para AI |
| `R2_*` | Credenciales Cloudflare R2 | Para storage |
| `REDIS_URL` | URL de Redis | Producción |

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
