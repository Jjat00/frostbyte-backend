# Agente de pedidos por WhatsApp (Kapso + LangGraph)

Agente de IA que toma pedidos a domicilio por WhatsApp de principio a fin, para
que la cocina solo cocine. Vive en `apps/whatsapp`.

## Arquitectura

```
Cliente WhatsApp
      │
      ▼
   Kapso  ──webhook──►  POST /api/v1/whatsapp/webhook/   (firma HMAC + idempotencia)
      ▲                        │  responde 200 en <1s
      │                        ▼
      │                ThreadPoolExecutor (worker.py, lock por teléfono)
      │                        │
      │                        ▼
      └──API mensajes──  Agente LangChain/LangGraph (agent.py)
                               │   modelo: OPENAI (WHATSAPP_AGENT_MODEL)
                               │   memoria: PostgresSaver, thread por contacto+día
                               ▼
                         Tools → ORM (tools.py): menú activo, búsqueda
                         aproximada de productos, estado tienda, historial,
                         cotizar pedido (totales exactos), verificar cobertura,
                         crear/modificar/cancelar pedido, handoff
```

- **Pedidos**: se crean como `Order` normales (`source=whatsapp`, `order_type=delivery`),
  así que aparecen en el KDS y flujos del staff sin cambios (se emite `orders_changed`).
- **Notificaciones**: `signals.py` escucha transiciones de estado de pedidos
  `source=whatsapp` y avisa al cliente: preparando → "en preparación",
  **listo → "va en camino" 🛵**, entregado y cancelado. Salen dentro de la
  ventana de 24 h de WhatsApp (gratis, sin templates).
- **Totales**: el LLM tiene prohibido sumar por su cuenta; antes de mostrar el
  resumen llama la tool `cotizar_pedido`, que calcula items + envío en el
  backend y devuelve las cifras exactas. También debe preguntar la variante
  cuando el producto tiene más de un tamaño (nunca asumirla).
- **Disponibilidad**: antes de decir que algo "no está disponible" el agente
  debe llamar `buscar_producto` (búsqueda por palabras contra el menú activo):
  los clientes no usan nombres exactos ("salchipapa especial" = "Salchipapa
  Especial Frostbyte"). Y nunca vuelca el menú completo al chat (omitiría
  productos): para "qué hay" responde categorías + link a la carta, y solo
  lista completa una categoría concreta si se la piden.
- **Cobertura (ubicación obligatoria)**: solo se entrega dentro de
  `DELIVERY_RADIUS_KM` (default 1.5 km) alrededor del local
  (`DELIVERY_CENTER_LAT/LNG`, compartidos con el checkout web). La ubicación de
  WhatsApp es OBLIGATORIA para crear el pedido: el geocoding por dirección no
  es fiable en Cumbal (las abreviaturas caen en otros municipios y las veredas
  caen al centro del pueblo), así que la única fuente válida es el GPS
  compartido. El worker guarda las coordenadas en
  `WhatsAppContact.last_location_*` cuando llega el mensaje de ubicación y
  `verificar_cobertura`/`crear_pedido` las leen de ahí: el LLM nunca maneja
  coordenadas (no puede inventarlas, como sí hizo con el billete). Si la
  ubicación guardada es de un día anterior, el agente confirma que la entrega
  es en el mismo punto; si el cliente no puede compartir su ubicación, escala
  con `solicitar_humano`.
- **Efectivo**: solo se registra el billete que el cliente DIJO (o
  `paga_con='exacto'` si paga completo); prohibido inventarlo. El dato llega
  al domiciliario en `customer_notes` y el agente no habla de vueltas.
- **Pago**: el agente pregunta efectivo/transferencia/Nequi/Daviplata; con
  efectivo pregunta el billete (queda en `customer_notes` como "Paga en
  efectivo con $X"); con transferencia comparte `WHATSAPP_TRANSFER_INFO`.
  Los pedidos por transferencia/Nequi/Daviplata nacen con `is_paid=False` y el
  staff ve un chip ámbar **"Pago por verificar"** (activos, detalle, historial
  y KDS) hasta que alguien confirme la plata en la app del banco y cobre el
  pedido. El comprobante (imagen) solo sirve para cruzar monto/hora: la
  verificación real siempre es el movimiento en la cuenta.
- **Dos números**: ambos atienden el catálogo completo. El webhook trae
  `phone_number_id`; el agente responde por el mismo número por el que
  escribió el cliente (guardado en `WhatsAppContact.last_phone_number_id`).
- **Handoff humano**: la tool `solicitar_humano` activa
  `WhatsAppContact.human_handoff` y el agente deja de responder a ese contacto.
  Se reactiva desde el admin de Django (Contactos de WhatsApp).
- **Intervención humana (auto-pausa)**: si alguien del equipo le responde al
  cliente desde el inbox de Kapso o la app de WhatsApp Business, el agente se
  pausa solo para ese contacto (`human_until`, ventana deslizante de
  `WHATSAPP_HUMAN_PAUSE_MINUTES`, default 30 min, renovada con cada mensaje
  humano) y se reanuda automáticamente al expirar. Cómo detecta al humano: el
  backend registra el wamid de todo lo que envía (`SentMessage`); un evento
  `whatsapp.message.sent` con wamid desconocido (u `origin=business_app`) es
  un humano. Mientras dura la pausa, tanto los mensajes del humano (como
  respuestas del asistente) como los del cliente quedan guardados en el hilo
  de LangGraph (`record_messages`), así el agente retoma con el contexto
  completo. Requiere suscribir el webhook también a `whatsapp.message.sent`.
- **Memoria**: la conversación del día persiste en Postgres (tablas de
  LangGraph, se crean solas). Lo duradero (nombre, dirección habitual,
  preferencias) vive en `WhatsAppContact` y el historial real en `Order`.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `KAPSO_API_KEY` | API key del panel de Kapso (Settings → API Keys) |
| `KAPSO_WEBHOOK_SECRET` | Secret definido al crear el webhook en Kapso |
| `KAPSO_PHONE_NUMBER_IDS` | `phone_number_id` de los números propios, separados por coma |
| `WHATSAPP_AGENT_ENABLED` | `False` apaga al agente (los webhooks se registran igual) |
| `WHATSAPP_AGENT_MODEL` | Modelo de OpenAI (default `gpt-4o-mini`) |
| `WHATSAPP_TRANSFER_INFO` | Datos de pago por transferencia que comparte el agente |
| `WHATSAPP_HUMAN_PAUSE_MINUTES` | Minutos de pausa del agente tras cada mensaje de un humano del equipo (default 30) |
| `DELIVERY_CENTER_LAT` / `DELIVERY_CENTER_LNG` | Coordenadas del local, centro de la zona de domicilios (default Cra. 8 #18-13, Cumbal) |
| `DELIVERY_RADIUS_KM` | Radio máximo de entrega en km (default 1.5); si se cambia, actualizar también `src/lib/deliveryArea.js` del frontend |
| `OPENAI_API_KEY` | Ya existente (la usa también el generador de imágenes) |

## Puesta en marcha en Kapso

1. Crear cuenta en https://app.kapso.ai y conectar los números
   (plan Free = 1 número; para los 2 números se necesita plan Pro).
2. **Sandbox primero**: WhatsApp → Sandbox, registrar tu número con el código
   de 6 caracteres. El sandbox soporta texto, interactivos y webhooks propios.
3. Crear el webhook apuntando a
   `https://<backend>/api/v1/whatsapp/webhook/` suscrito a
   `whatsapp.message.received` **y** `whatsapp.message.sent` (este último
   permite detectar cuando un humano del equipo interviene y pausar al
   agente), y copiar el secret a `KAPSO_WEBHOOK_SECRET`.
4. Recomendado: activar el *message buffering* de Kapso (3–5 s) para que los
   mensajes partidos del cliente lleguen agrupados.
5. Copiar los `phone_number_id` a `KAPSO_PHONE_NUMBER_IDS`.
6. En producción: activar domicilios (`is_open` + `customer_ordering_enabled`
   desde `/home`), configurar las env vars en Railway y redeploy.

## Pruebas locales

Sin exponer el server, se puede conversar con el agente directamente:

```bash
source .venv/bin/activate
python manage.py shell -c "
from apps.whatsapp.models import WhatsAppContact
from apps.whatsapp.agent import run_agent
c, _ = WhatsAppContact.objects.get_or_create(phone='573001112233')
print(run_agent(c, 'Hola, ¿qué tienen de menú?'))
"
```

Para probar el webhook con Kapso real en local se necesita un túnel
(ej. `cloudflared tunnel --url http://localhost:18000`).

## Media (audios, imágenes y ubicación)

- **Notas de voz**: se descargan vía Kapso (`GET /meta/whatsapp/v24.0/{media_id}`
  → `download_url` firmado) y se transcriben con OpenAI
  (`gpt-4o-mini-transcribe`). El agente recibe la transcripción como texto.
- **Imágenes**: se descargan igual y se describen con el modelo de visión
  (`WHATSAPP_AGENT_MODEL`). Si es un comprobante de pago, la descripción extrae
  monto, fecha, remitente y referencia. El caption del cliente se conserva.
- **Ubicación compartida**: el worker guarda lat/lng en el contacto
  (`last_location_*`) y el agente solo recibe el aviso de que quedó
  registrada; `crear_pedido` copia esas coordenadas al pedido
  (`delivery_lat/lng`) y el staff ve el botón "Cómo llegar" (Google Maps) en
  la tarjeta del pedido. Es obligatoria para el domicilio (ver Cobertura).
- Si la descarga o el modelo fallan, el agente recibe un texto de respaldo y
  pide el contenido por escrito. Videos, documentos y stickers no se procesan.
- Todo el procesado ocurre en `apps/whatsapp/media.py` + `worker.py`, con el
  indicador "escribiendo…" ya activo.

## Límites conocidos (v1)

- No hay estado "en camino" propio: el aviso 🛵 sale cuando el pedido pasa a
  `ready` (todas las cocinas terminaron). Si algún día se quiere un botón
  "En camino" explícito, hay que añadir el estado a `Order.Status` y al front.
- Los mensajes fuera de la ventana de 24 h requerirían templates aprobados por
  Meta (no implementado; en la práctica el flujo de pedido siempre cae dentro).
- El handoff no notifica al staff activamente: se ve en el inbox de Kapso y en
  el admin de Django.
