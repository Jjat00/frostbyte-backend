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
                         Tools → ORM (tools.py): menú activo, estado tienda,
                         historial, crear/modificar/cancelar pedido, handoff
```

- **Pedidos**: se crean como `Order` normales (`source=whatsapp`, `order_type=delivery`),
  así que aparecen en el KDS y flujos del staff sin cambios (se emite `orders_changed`).
- **Notificaciones**: `signals.py` escucha transiciones de estado de pedidos
  `source=whatsapp` y avisa al cliente: preparando → "en preparación",
  **listo → "va en camino" 🛵**, entregado y cancelado. Salen dentro de la
  ventana de 24 h de WhatsApp (gratis, sin templates).
- **Pago**: el agente pregunta efectivo/transferencia/Nequi/Daviplata; con
  efectivo pregunta el billete (queda en `customer_notes` como "Paga en
  efectivo con $X"); con transferencia comparte `WHATSAPP_TRANSFER_INFO`.
- **Dos números**: ambos atienden el catálogo completo. El webhook trae
  `phone_number_id`; el agente responde por el mismo número por el que
  escribió el cliente (guardado en `WhatsAppContact.last_phone_number_id`).
- **Handoff humano**: la tool `solicitar_humano` activa
  `WhatsAppContact.human_handoff` y el agente deja de responder a ese contacto.
  Se reactiva desde el admin de Django (Contactos de WhatsApp).
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
| `OPENAI_API_KEY` | Ya existente (la usa también el generador de imágenes) |

## Puesta en marcha en Kapso

1. Crear cuenta en https://app.kapso.ai y conectar los números
   (plan Free = 1 número; para los 2 números se necesita plan Pro).
2. **Sandbox primero**: WhatsApp → Sandbox, registrar tu número con el código
   de 6 caracteres. El sandbox soporta texto, interactivos y webhooks propios.
3. Crear el webhook apuntando a
   `https://<backend>/api/v1/whatsapp/webhook/` suscrito a
   `whatsapp.message.received`, y copiar el secret a `KAPSO_WEBHOOK_SECRET`.
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

## Límites conocidos (v1)

- El agente no ve imágenes ni escucha audios: pide el contenido por texto y,
  si es un comprobante de pago, avisa que el equipo lo verificará.
- No hay estado "en camino" propio: el aviso 🛵 sale cuando el pedido pasa a
  `ready` (todas las cocinas terminaron). Si algún día se quiere un botón
  "En camino" explícito, hay que añadir el estado a `Order.Status` y al front.
- Los mensajes fuera de la ventana de 24 h requerirían templates aprobados por
  Meta (no implementado; en la práctica el flujo de pedido siempre cae dentro).
- El handoff no notifica al staff activamente: se ve en el inbox de Kapso y en
  el admin de Django.
