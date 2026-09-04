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
                               │   modelo: OPENAI (WHATSAPP_AGENT_MODEL,
                               │   default gpt-5.6-terra con razonamiento bajo)
                               │   memoria: PostgresSaver, thread por contacto+día
                               ▼
                         Tools → ORM (tools.py): menú activo, búsqueda
                         aproximada de productos, estado tienda, historial,
                         cotizar pedido (totales exactos), verificar cobertura,
                         crear/modificar/cancelar pedido, handoff
```

- **Una respuesta por ráfaga**: los clientes escriben en varios mensajes
  ("Buenas" / "quiero pedir"), y responder a cada uno da respuestas duplicadas.
  Cada contacto tiene una cola con un solo loop (`worker.py`): espera
  `WHATSAPP_BATCH_WAIT_SECONDS` desde el ÚLTIMO mensaje (cada mensaje nuevo
  reinicia la cuenta, con tope duro `WHATSAPP_BATCH_MAX_WAIT_SECONDS` para
  quien escribe sin parar) y recién ahí llama al agente, con todo junto. Si el
  cliente escribe **mientras el agente ya está generando**, esa respuesta nació
  incompleta: se descarta y se borra del hilo (`discard_turn`, con
  `RemoveMessage`), y el turno se rehace entero. Excepción: un turno que ya
  llamó a una tool que escribe en la BD (`MUTATING_TOOLS`: crear/modificar/
  cancelar pedido, guardar preferencia, pedir humano) **no se descarta**, o el
  agente olvidaría un pedido que ya existe y lo crearía dos veces; se envía y
  lo nuevo va al siguiente turno. Tope de 2 descartes seguidos.
  El *message buffering* de Kapso hace lo mismo aguas arriba, pero su ventana
  termina al entregar el webhook: por eso se le deja corta (2 s) y la espera de
  verdad la controla el backend.
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
- **Cobertura (ubicación obligatoria)**: solo se entrega dentro de la zona
  configurada en el dashboard. Si hay un polígono dibujado
  (`StoreSettings.delivery_area`) manda él; si no, el círculo de
  `StoreSettings.delivery_radius_km` (default 1.5 km) alrededor del local
  (`DELIVERY_CENTER_LAT/LNG`, compartidos con el checkout web). El agente lee
  la zona vigente en cada mensaje, así que cambiarla desde la UI afecta al
  instante lo que responde. La ubicación de
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
- **Lo que no sabe se remite, no se improvisa**: ante una pregunta que ninguna
  tool cubre (eventos, reservas de mesa, festivos, empleo…) el agente dice que
  no está seguro y comparte `WHATSAPP_CONTACT_PHONE` para que el cliente llame
  o escriba. No cuenta como "no sé" lo que sí tiene tool (menú, precios,
  horario, cobertura, pedidos): un producto que `buscar_producto` no encuentra
  es un producto que no vendemos. Es la salida barata frente a
  `solicitar_humano`, que pausa al agente y se reserva para quien pide una
  persona, quejas serias o pedidos bloqueados.
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

## El modelo

El agente corre sobre **`gpt-5.6-terra`** (OpenAI, familia GPT-5.6), un modelo
que razona antes de responder. Eso cambia cómo se lo llama y `llm.py` arma los
parámetros según el modelo configurado, así que volver a un modelo clásico
(`WHATSAPP_AGENT_MODEL=gpt-4o-mini`) no exige tocar código:

- **No acepta `temperature`** salvo con `WHATSAPP_AGENT_REASONING_EFFORT=none`;
  mandarla devuelve HTTP 400. Con un modelo clásico se sigue enviando 0.3.
- **Responses API** en vez de Chat Completions: es la que conserva el
  razonamiento entre las llamadas a tools de un mismo turno (un pedido encadena
  varias) en lugar de tirarlo en cada ida y vuelta.
- **El presupuesto de salida incluye los tokens de razonamiento**, así que la
  descripción de imágenes pide `max_completion_tokens` con margen; con el tope
  viejo de 200 el modelo gastaba el cupo pensando y devolvía texto vacío.
- **Costo**: $2 por millón de tokens de entrada ($0.20 si vienen del caché) y
  $12 de salida, contra $0.15/$0.60 de `gpt-4o-mini`. El system prompt es fijo y
  largo, así que el caché de OpenAI absorbe buena parte de la entrada.

**Leer media no usa el modelo del agente.** Transcribir un audio y describir una
imagen es trabajo mecánico y va con modelos baratos propios: `gpt-4o-mini-transcribe`
($0.003 por minuto, el más barato de la línea de transcripción) y `gpt-5.6-luna`
($0.20/$1.20 por millón, ~10x más barato que Terra y con la visión de la familia
GPT-5.6, que es lo que importa para leer el monto de un comprobante). La visión
usa esfuerzo de razonamiento `low` fijo (`media.VISION_REASONING_EFFORT`), sin
seguir al del agente: el cliente está esperando en el chat.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `KAPSO_API_KEY` | API key del panel de Kapso (Settings → API Keys) |
| `KAPSO_WEBHOOK_SECRET` | Secret definido al crear el webhook en Kapso |
| `KAPSO_PHONE_NUMBER_IDS` | `phone_number_id` de los números propios, separados por coma |
| `WHATSAPP_AGENT_ENABLED` | `False` apaga al agente (los webhooks se registran igual) |
| `WHATSAPP_AGENT_MODEL` | Modelo de OpenAI (default `gpt-5.6-terra`) |
| `WHATSAPP_AGENT_REASONING_EFFORT` | Cuánto razona antes de responder: `none`, `low` (default), `medium`, `high`, `xhigh`, `max`. Solo aplica a modelos de razonamiento (GPT-5 en adelante); más esfuerzo = mejor criterio pero más lento y más caro |
| `WHATSAPP_VISION_MODEL` | Modelo que describe las imágenes del cliente (default `gpt-5.6-luna`) |
| `WHATSAPP_TRANSCRIBE_MODEL` | Modelo que transcribe las notas de voz (default `gpt-4o-mini-transcribe`) |
| `WHATSAPP_TRANSFER_INFO` | Datos de pago por transferencia que comparte el agente |
| `WHATSAPP_CONTACT_PHONE` | Número al que remite cuando no sabe algo (default `3164277879`) |
| `WHATSAPP_HUMAN_PAUSE_MINUTES` | Minutos de pausa del agente tras cada mensaje de un humano del equipo (default 30) |
| `WHATSAPP_BATCH_WAIT_SECONDS` | Segundos de silencio del cliente antes de responder; agrupa mensajes seguidos (default 10) |
| `WHATSAPP_BATCH_MAX_WAIT_SECONDS` | Tope duro de espera desde el primer mensaje sin responder (default 40) |
| `DELIVERY_CENTER_LAT` / `DELIVERY_CENTER_LNG` | Coordenadas del local, centro de la zona de domicilios (default Cra. 8 #18-13, Cumbal) |
| `DELIVERY_RADIUS_KM` | Solo semilla/respaldo del radio máximo de entrega (default 1.5). El valor vigente lo edita el admin en el dashboard (chip "Radio" en /home) y vive en `StoreSettings.delivery_radius_km` |
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
4. Activar el *message buffering* de Kapso con ventana **corta (2 s)**: entrega
   rápido y el agrupado real lo hace el backend (`WHATSAPP_BATCH_WAIT_SECONDS`),
   que además cubre al cliente que escribe mientras el agente responde. Con
   buffering activo TODA entrega usa formato batch, incluso de un solo mensaje.
5. Copiar los `phone_number_id` a `KAPSO_PHONE_NUMBER_IDS`.
6. En producción: activar domicilios (`is_open` + `customer_ordering_enabled`
   desde `/home`), configurar las env vars en Railway y redeploy.

## Pruebas

```bash
python manage.py test apps.whatsapp
```

Reproduce chats reales que salieron mal en producción (cada test lleva la fecha
del chat original). El LLM se sustituye por un doble: no se prueba qué contesta
el modelo, sino **cuándo y cuántas veces** lo llamamos y qué se le manda — que
es donde estaban los fallos. Al añadir un caso nuevo, partir del chat real.

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
  (`WHATSAPP_TRANSCRIBE_MODEL`). El agente recibe la transcripción como texto.
- **Imágenes**: se descargan igual y se describen con el modelo de visión
  (`WHATSAPP_VISION_MODEL`). Si es un comprobante de pago, la descripción extrae
  monto, fecha, remitente y referencia. El caption del cliente se conserva.
- **Ubicación compartida**: el worker guarda lat/lng en el contacto
  (`last_location_*`) y el agente solo recibe el aviso de que quedó
  registrada; `crear_pedido` copia esas coordenadas al pedido
  (`delivery_lat/lng`) y el staff ve el botón "Cómo llegar" (Google Maps) en
  la tarjeta del pedido. Es obligatoria para el domicilio (ver Cobertura).
- **Respuestas citadas** (el cliente desliza un mensaje para responderlo):
  WhatsApp manda solo el id del citado (`context.id`), así que el texto sale de
  `ChatMessage`, que guarda lo que dicen cliente, agente y humanos del equipo y
  se limpia sola a los 7 días. El agente lo recibe como
  `[El cliente responde citando este mensaje tuyo: "..."]` delante del mensaje;
  si la cita es más vieja que la retención, al menos se le avisa que hay una.
- **Mensajes que WhatsApp no nos entrega**: llegan como tipo `unsupported` con
  el error **131060** ("This message is unavailable"). El contenido nunca sale
  del teléfono del cliente; pasa sobre todo con ubicaciones enviadas desde un
  dispositivo vinculado y con números en coexistencia (app de WhatsApp Business
  + Cloud API, que es el caso de Frostbyte). Kapso los registra en la
  conversación —se ven en su panel— pero **no los reenvía por webhook**: su
  lista de tipos no los incluye. Por eso `verificar_cobertura`, cuando no hay
  ubicación registrada, pregunta por ellos con `kapso.recent_undelivered()`
  (API platform de Kapso, última hora) y, si el cliente lo intentó, le dice al
  agente que reconozca que no llegó y pida reenviarla desde el celular en vez
  de repetir la misma instrucción (chat real del 24/07).
- Si la descarga o el modelo fallan, el agente recibe un texto de respaldo y
  pide el contenido por escrito. Videos, documentos y stickers no se procesan.
- Todo el procesado ocurre en `apps/whatsapp/media.py` + `worker.py`, con el
  indicador "escribiendo…" ya activo.

## Personalidad y módulo de configuración

El agente se llama **Frosty** y habla como un parcero del pueblo: corto,
chistoso y directo, tuteando y con muletillas de Nariño. El humor tiene un
techo escrito en el prompt: al llegar a cifras, dirección y confirmación el
dato va limpio, y si el cliente está molesto o reclamando el chiste se acaba.
El resto del prompt (reglas de oro, flujo del pedido, cobertura, pagos) no
cambió.

**La voz se recuerda al final.** El bloque QUIÉN ERES abre el prompt y detrás
vienen páginas de reglas operativas; el modelo salía cumpliendo las reglas y
hablando como un formulario ("¿Qué deseas pedir?", chat del 03/09). Por eso el
prompt cierra —después de todo lo demás, antes de lo que cambia cada turno— con
un bloque **TU VOZ** que repite la personalidad y añade la **frase de muestra**
del tono (`AgentTone.sample`, que hasta ahora solo se veía en el panel): un
ejemplo corto calibra el registro mejor que otro párrafo describiéndolo. Los
ajustes de estilo del negocio van después, porque mandan sobre la personalidad.

Lo configurable vive en dos sitios que leen la misma fila (`AgentSettings`,
singleton): el módulo **Agente de WhatsApp** del panel (`/agente-whatsapp`,
solo admin) y **WhatsApp → Configuración del agente** en el admin de Django. El
módulo del panel existe porque es donde está el dueño —el celular—, y el cambio
que quiere hacer suele ser de un toque:

| Campo | Para qué |
| --- | --- |
| Nombre del agente | Con qué nombre se presenta (por defecto `Frosty`). |
| Tono y personalidad | Texto libre que se añade al final del prompt y manda sobre el estilo por defecto (ej. "trata al cliente de usted", "sin emojis"). |
| Puede mandar stickers / reaccionar / fotos / botones | Cuatro interruptores. |

Cada interruptor quita **a la vez** la tool y el trozo de prompt que la
explica (`build_tools` y `build_system_prompt` leen la misma configuración):
describirle al modelo algo que no puede llamar produce promesas que el turno no
cumple, y el cliente lo lee como que el bot está roto.

Las reglas del pedido **no** son configurables a propósito: tienen tests
detrás, y volverlas editables convertiría un descuido de redacción en un pedido
mal tomado.

La API del módulo (`apps/whatsapp/api.py`, toda con `IsAdminUser`):

| Endpoint | Qué hace |
| --- | --- |
| `GET/PATCH /api/v1/whatsapp/agent-settings/` | La fila de configuración. Los números del dueño se guardan normalizados a dígitos: en el celular se teclean con `+`, espacios y guiones, y así pegados `is_owner` no los reconocería. |
| `GET/POST /api/v1/whatsapp/stickers/` | El banco entero (activos e inactivos), cada uno con su miniatura en `preview` como data URI. Sin paginar: el banco es corto y la pantalla lo pinta completo. |
| `PATCH/DELETE /api/v1/whatsapp/stickers/<id>/` | Texto, interruptor o la imagen misma. Sin `archivo` la imagen guardada se conserva. |

La subida va en multipart porque lleva el archivo, y ahí DRF lee un booleano
**ausente** como `False` (asume checkbox HTML). Por eso `is_active` usa
`OptionalBooleanField`: sin él, crear un sticker o corregirle el dibujo lo
dejaba desactivado —guardado, invisible para el agente y sin decir por qué—.

La miniatura viaja como data URI y no como enlace a `stickers/<id>.webp`: esa
URL solo sirve los **activos** (justo los que no hay que revisar) y en local
apunta al backend de producción, donde el sticker recién subido no existe.

### Cuánto nos demoramos

"¿Y en cuánto llega?" es de las primeras preguntas del cliente y el agente no
tenía con qué responderla: caía en la regla de *lo que no sé lo remito* y le
pasaba el número de contacto. Ahora sale de `StoreSettings.eta_min_minutes` /
`eta_max_minutes` (10 y 20 por defecto, editables en el admin de Django junto
al horario y al interruptor de recoger), que `eta_label()` convierte en "de 10
a 20 minutos" —o "unos 15 minutos" si los dos extremos son iguales— y entra en
la regla 9 del prompt.

Va en el prompt y no en una tool: es una línea de texto, la pregunta llega en
cualquier momento de la conversación y una tool más sería un turno más para
decir una frase. El prompt se reconstruye en cada turno, así que cambiar el
rango tiene efecto en el mensaje siguiente. La regla le prohíbe expresamente
prometer una hora exacta: la estimación es del local, el minuto es del cliente
que lo reclama.

### Lo que puede mandar además de texto

| Tool | Cuándo la usa |
| --- | --- |
| `enviar_sticker` | Un gesto, cuando el momento lo pide y el dado del turno lo permite (ver *El pulso*). No sale en el momento: queda apuntado y el worker lo manda al final del turno, detrás del texto **si lo hay** — puede ir solo. |
| `enviar_foto_producto` | El cliente pregunta cómo es algo: manda la foto real (`Product.image_url`). |
| `enviar_botones` | Solo respuestas cerradas: confirmar el pedido y elegir el pago. Lo que toque el cliente vuelve como texto (`interactive.button_reply`, ya soportado). |
| `reaccionar` | Emoji sobre el mensaje del cliente. No genera mensaje ni notificación. |

La foto y los botones escriben en WhatsApp en el momento en que el modelo las
llama, no al final del turno, y eso deja dos cosas distintas en `TurnContext`:

- **`posted`** — quedó un mensaje en el chat (foto, botones). El turno ya no se
  puede descartar y rehacer, igual que uno que tocó la base de datos.
- **`answered`** — el turno ya respondió, aunque no haya sido un mensaje. Una
  reacción sola cuenta: sin esto, un "mil gracias" contestado con ❤️ recibía
  además un "Perdón, ¿me lo repites?" (visto en prueba el 03/09).

Cuando el turno respondió sin texto, el worker no manda nada.

### Varios mensajes en un turno

Una persona por WhatsApp manda lo que contesta y lo que pregunta en dos
mensajes, y remata con el sticker; el agente mandaba siempre uno solo y con el
sticker por delante. `worker._deliver` entrega el turno en ese orden:

1. **El texto**, en uno o dos mensajes. El modelo los separa con una línea que
   tenga solo `---` y `agent._split_messages` la parte. El tope de dos vive en
   el código (`MAX_REPLIES`): en el prompt sería una sugerencia, y lo que pase
   de ahí se pega al último mensaje en vez de empapelar el chat.
2. **El sticker**, si eligió uno. `enviar_sticker` ya no lo manda: lo apunta en
   `turn.sticker` y `stickers.deliver()` lo entrega cuando el texto ya salió
   —el modelo llama a las tools *antes* de escribir, así que mandarlo ahí lo
   ponía siempre delante—. Ahí mismo se suma `sent_count` y se apunta en la
   memoria corta del contacto: un sticker que Kapso rechaza no gasta el cupo
   del día ni el enfriamiento.

**El texto no es obligatorio**: el turno puede ser solo el sticker, y el prompt
lo dice con sus casos (el cliente mandó uno, hizo un chiste, se despidió, dio
las gracias). Eso ya lo permitía `answered`, que es lo que evita el "Perdón,
¿me lo repites?" cuando el modelo calla a propósito; lo que faltaba era que el
prompt no le pidiera escribir siempre algo detrás.

Entre mensaje y mensaje hay `MESSAGE_GAP_SECONDS`: pegados en el mismo segundo
se leen como una ráfaga de bot.

### El banco de stickers

Se gestiona desde el panel (`/agente-whatsapp` → pestaña Stickers), desde
**WhatsApp → Stickers** en el admin de Django, o mandándoselos al agente por
WhatsApp desde el número del dueño. Cada sticker tiene un nombre y un
**"cuándo usarlo"**, que es lo único que el agente lee para elegirlo: describe
el momento, no el dibujo ("para celebrar que el pedido quedó listo", no "un
vaso azul con ojos"). Los activos se renderizan en el prompt.

- Se sube **cualquier imagen** (PNG, JPG, WebP, GIF): `stickers.normalize()` la
  convierte a WebP 512x512 dentro del límite de Meta (100 KB fijo, 500 KB
  animado), rellenando con transparencia en vez de deformarla. Una animación
  que no entra pierde frames antes que calidad; si aun así no cabe, se guarda
  el primer frame.
- **Usa imágenes con fondo transparente**. Sin él se ve un cuadro pegado sobre
  el fondo del chat, no un sticker. El admin lo avisa al guardar, pero no lo
  bloquea.
- Los bytes se guardan **en la base de datos**, no en disco: el sistema de
  archivos de Railway se borra en cada despliegue. WhatsApp los descarga de
  `GET /api/v1/whatsapp/stickers/<id>.webp`, público y sin autenticación
  porque quien lo pide son los servidores de Meta.
- Esa URL se arma con `BACKEND_PUBLIC_URL`: en local apunta a producción, así
  que **los stickers solo se pueden probar de verdad contra el backend
  desplegado**.

Banco sugerido para arrancar (nombre — cuándo usarlo):

| Nombre | Cuándo usarlo |
| --- | --- |
| granizado feliz | para saludar o arrancar la conversación con buena energía |
| pulgar arriba | para confirmar algo que ya quedó claro o cerrar un acuerdo |
| moto en camino | cuando el pedido acaba de salir para donde el cliente |
| corazón frío | cuando el cliente agradece o dice algo lindo del local |
| carita triste | cuando algo no se pudo: fuera de zona, agotado, local cerrado |
| chef listo | cuando el pedido para recoger ya está listo |
| copa brindando | para celebrar un pedido grande o una ocasión especial |
| esperando | cuando el cliente pregunta por un pedido que aún está en cocina |

## El dueño configura al agente por chat

Los números de **Configuración del agente → Números del dueño** (`AgentSettings.owner_phones`,
por defecto el 316 427 7879) reciben trato aparte: el agente sabe que habla con
quien lo creó, se suelta, puede contarle cómo funciona por dentro y le acepta
órdenes de configuración. **Le sigue tomando pedidos de verdad**, con las mismas
reglas y sin atajos, que es lo que le permite probarlo en real.

El reconocimiento compara los **últimos 10 dígitos** (el mismo celular llega
unas veces con indicativo y otras sin él). Un contacto identificado por BSUID
—alguien que oculta su número— nunca es dueño: no hay dígitos que comparar, así
que el permiso no se puede heredar ocultando el número.

Cinco tools que solo existen para él: `guardar_sticker`, `listar_stickers`,
`actualizar_sticker`, `quitar_sticker` y `ajustar_tono`. Ninguna toca dinero:
los pedidos, los precios y los estados siguen gestionándose con las mismas
tools que para cualquier cliente. Las cuatro que escriben están en
`MUTATING_TOOLS`, así que un turno que configuró algo ya no se descarta.

### Mandarle un sticker por WhatsApp

1. El dueño manda una **imagen, un sticker o un video corto** (con o sin
   caption). El worker lo descarga y lo guarda como `StickerDraft` —uno por
   contacto, el nuevo reemplaza al anterior— y le avisa al agente en el prompt
   que tiene un archivo pendiente.
2. El dueño dice cómo llamarlo y en qué momento usarlo. Con las dos cosas, el
   agente llama `guardar_sticker`, que normaliza el archivo y lo mete al banco;
   guardar con un nombre que ya existe **reemplaza** ese sticker.

La descarga ocurre al recibir el mensaje y no dentro de la tool porque el
`download_url` de Kapso caduca a los pocos minutos: cuando el agente decidiera
guardarlo, el enlace ya no serviría. Nunca tumba el turno — no poder guardar un
sticker no puede costar el mensaje que venía con él. Tope de 8 MB y los
borradores sin usar se limpian a las 6 horas (`DRAFT_TTL`).

Un **sticker o un video del dueño no se pasan por visión** (no hay nada que
leerle que él no esté viendo, y cuesta). Una **imagen sí** se describe además de
guardarse: puede ser un sticker por hacer o un comprobante de pago. Para
cualquier otro contacto, stickers y videos siguen siendo "algo que no puedes
ver": el banco es curado por el negocio, no capturado de los chats.

### Videos → stickers animados

`stickers.from_video()` recorta a **3 segundos a 12 fps**, saca los cuadros con
ffmpeg y los ensambla con el mismo código que los GIF, para que un sticker
animado se vea igual venga de donde venga. Si no cabe en 500 KB pierde frames
antes que calidad, y en el peor caso queda el primer cuadro como sticker fijo
(el agente se lo dice).

ffmpeg se busca primero en el sistema y si no está se usa el binario que trae
**`imageio-ffmpeg`** (dependencia nueva): Railway construye con nixpacks + pip,
así que el paquete de Python evita depender de la imagen del build. Sin ninguno
de los dos, el error le pide al dueño mandar la imagen o el GIF.

### Cambiar el tono desde el chat

`ajustar_tono` escribe `AgentSettings.tone`, el mismo campo del admin: manda
sobre el estilo por defecto y aplica **a todos los clientes** desde la siguiente
conversación. El prompt le exige al agente decir con qué texto exacto se va a
quedar y esperar el visto bueno antes de guardar; con el texto vacío se vuelve
al tono normal.

## Límites conocidos (v1)

- La cola de mensajes vive en memoria del proceso: un redeploy justo dentro de
  la ventana de espera (≤ `WHATSAPP_BATCH_MAX_WAIT_SECONDS`) pierde ese turno y
  el `WebhookEvent` queda en `pending` (visible en el admin). Antes el riesgo
  existía igual pero era más corto (solo el turno en vuelo). Si molesta, habría
  que reencolar los `pending` al arrancar, con cuidado de no duplicar el
  procesado si algún día corren varios workers.

- Un mensaje con error 131060 es **irrecuperable**: no hay forma de leer esa
  ubicación, ni por API ni por webhook. Lo único posible es detectar que existió
  y pedir que la reenvíe. Si se vuelve frecuente, vale reportárselo a Kapso
  (podrían entregar los `unsupported` por webhook) y revisar si la coexistencia
  con la app de WhatsApp Business lo empeora.
- La detección anterior solo corre dentro de `verificar_cobertura`: si el agente
  responde sin llamar a la tool, sigue sin enterarse. El prompt se lo exige
  antes de decir que no le llegó la ubicación.
- No hay estado "en camino" propio: el aviso 🛵 sale cuando el pedido pasa a
  `ready` (todas las cocinas terminaron). Si algún día se quiere un botón
  "En camino" explícito, hay que añadir el estado a `Order.Status` y al front.
- Los mensajes fuera de la ventana de 24 h requerirían templates aprobados por
  Meta (no implementado; en la práctica el flujo de pedido siempre cae dentro).
- El handoff no notifica al staff activamente: se ve en el inbox de Kapso y en
  el admin de Django.
- El banco de stickers nace vacío: hasta que alguien suba el primero (por el
  admin o mandándoselo por WhatsApp), el agente no ve la tool ni la sección del
  prompt, y responde solo con texto.
- Ser dueño se decide por el número de WhatsApp, sin segundo factor: quien
  controle esa línea puede cambiar el tono y los stickers del agente. Por eso
  esas tools no tocan pedidos, precios ni estados.
- Los stickers que manda el **cliente** siguen sin procesarse (llegan como
  "algo que no puedes ver"). El banco es curado por el negocio a propósito:
  reenviar lo que llega de un chat sería publicar contenido sin revisar.
