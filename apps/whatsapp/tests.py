"""Pruebas del agente de WhatsApp reproduciendo chats reales.

Los casos vienen de conversaciones que salieron mal en producción: cada test
lleva la fecha del chat que reprodujo. El LLM se sustituye por un doble (no se
prueba qué contesta el modelo, sino cuándo y cuántas veces lo llamamos).
"""

import datetime
import threading
import time
from unittest.mock import Mock, patch

from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
)

from .agent import AgentTurn, _for_whatsapp, build_system_prompt
from .stickers import StickerError, has_transparency, normalize
from .tools import TurnContext, build_tools
from apps.orders.models import Order

from .models import (
    AgentSettings,
    ChatMessage,
    SentMessage,
    Sticker,
    StickerDraft,
    WebhookEvent,
    WhatsAppContact,
)
from .worker import _active, _pending, _process_event_safe

PHONE = "573001112233"
PHONE_NUMBER_ID = "111222333"

# Tiempos de juguete: la lógica es la misma, la espera se mide en milisegundos
FAST = dict(
    WHATSAPP_BATCH_WAIT_SECONDS=0.4,
    WHATSAPP_BATCH_MAX_WAIT_SECONDS=3.0,
    WHATSAPP_AGENT_ENABLED=True,
    KAPSO_PHONE_NUMBER_IDS=[PHONE_NUMBER_ID],
)


def webhook_payload(text, sequence=1, message_id=None, quoted_wamid=None, msg_type="text"):
    """Un webhook de Kapso con buffering activo (siempre formato batch)."""
    message = {
        "id": message_id or f"wamid.test{sequence}",
        "from": PHONE,
        "type": msg_type,
        "text": {"body": text},
        "context": {"id": quoted_wamid, "from": PHONE} if quoted_wamid else None,
        "kapso": {"direction": "inbound"},
    }
    return {
        "type": "whatsapp.message.received",
        "batch": True,
        "batch_info": {
            "size": 1,
            "window_ms": 2000,
            "first_sequence": sequence,
            "last_sequence": sequence,
        },
        "data": [
            {
                "phone_number_id": PHONE_NUMBER_ID,
                "conversation": {"phone_number": PHONE, "contact_name": "Eduardo"},
                "message": message,
            }
        ],
    }


class WorkerAgrupadoTests(TransactionTestCase):
    """Mensajes seguidos del cliente = un turno y una respuesta.

    TransactionTestCase (no TestCase) porque el worker corre en hilos con sus
    propias conexiones: dentro de la transacción única de TestCase no verían
    los datos y close_old_connections cerraría la conexión del test.
    """

    def setUp(self):
        _pending.clear()
        _active.clear()
        self.sent = []
        self.turns = []
        self.discarded = []

        patcher = patch("apps.whatsapp.worker.kapso")
        self.kapso = patcher.start()
        self.addCleanup(patcher.stop)
        self.kapso.send_text.side_effect = lambda pnid, phone, text: self.sent.append(text)

        discard = patch("apps.whatsapp.agent.discard_turn")
        self.discard = discard.start()
        self.addCleanup(discard.stop)
        self.discard.side_effect = lambda contact, ids: self.discarded.append(ids)

    def receive(self, text, sequence=1):
        """Simula la llegada de un webhook y su procesamiento (síncrono)."""
        event = WebhookEvent.objects.create(
            idempotency_key=f"key-{sequence}-{text[:20]}",
            payload=webhook_payload(text, sequence),
            event_type="whatsapp.message.received",
        )
        _process_event_safe(event.pk)
        return event

    def wait_idle(self, timeout=10):
        """Espera a que el loop del contacto termine (cola vacía)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if PHONE not in _active and not _pending.get(PHONE):
                return True
            time.sleep(0.05)
        self.fail("el loop del contacto no terminó a tiempo")

    def fake_turn(self, reply="ok", mutated=False, delay=0.0, on_call=None):
        """Doble del LLM: registra el texto que recibió y tarda `delay`."""

        def _run(contact, text, phone_number_id="", message_id=""):
            self.turns.append(text)
            if on_call:
                on_call()
            if delay:
                time.sleep(delay)
            return AgentTurn(reply=reply, message_ids=("m1", "m2"), mutated=mutated)

        return _run

    @override_settings(**FAST)
    def test_dos_mensajes_seguidos_una_sola_respuesta(self):
        """Chat real del 19/07: 'Hola, quiero...' + 'Buenas' → 2 respuestas.

        Los dos llegaron en lotes distintos de Kapso (9 s de diferencia, más
        que su ventana de buffering) y el agente contestó dos veces casi lo
        mismo. Ahora la cola los junta en un solo turno.
        """
        with patch("apps.whatsapp.agent.run_turn", side_effect=self.fake_turn()):
            self.receive("Hola, quiero hacer un pedido a domicilio", 1)
            time.sleep(0.2)  # el cliente escribe otra vez antes de que expire
            self.receive("Buenas", 3)
            self.wait_idle()

        self.assertEqual(len(self.turns), 1, "el agente debió correr una sola vez")
        self.assertIn("Hola, quiero hacer un pedido a domicilio", self.turns[0])
        self.assertIn("Buenas", self.turns[0])
        self.assertEqual(len(self.sent), 1, "el cliente debió recibir una sola respuesta")

    @override_settings(**FAST)
    def test_mensaje_mientras_el_agente_piensa_descarta_la_respuesta(self):
        """El cliente escribe con el agente ya generando: no se envía a medias."""
        arrived = threading.Event()

        def escribe_durante_el_turno():
            if arrived.is_set():
                return
            arrived.set()
            self.receive("de café porfa", 2)

        with patch(
            "apps.whatsapp.agent.run_turn",
            side_effect=self.fake_turn(delay=0.5, on_call=escribe_durante_el_turno),
        ):
            self.receive("quiero un granizado", 1)
            self.wait_idle()

        self.assertEqual(len(self.turns), 2, "el turno descartado se rehace")
        self.assertIn("de café porfa", self.turns[1])
        self.assertIn("quiero un granizado", self.turns[1], "el lote vuelve entero")
        self.assertEqual(len(self.discarded), 1, "el turno a medias se borra del hilo")
        self.assertEqual(len(self.sent), 1, "una sola respuesta, la completa")

    @override_settings(**FAST)
    def test_turno_que_creo_el_pedido_no_se_descarta(self):
        """Si el turno ya escribió en la BD, descartarlo duplicaría el pedido."""
        arrived = threading.Event()

        def escribe_durante_el_turno():
            if arrived.is_set():
                return
            arrived.set()
            self.receive("gracias!", 2)

        with patch(
            "apps.whatsapp.agent.run_turn",
            side_effect=self.fake_turn(
                mutated=True, delay=0.5, on_call=escribe_durante_el_turno
            ),
        ):
            self.receive("sí, confirmo el pedido", 1)
            self.wait_idle()

        self.assertEqual(len(self.discarded), 0, "un turno con pedido creado no se borra")
        self.assertEqual(len(self.sent), 2, "se envía la confirmación y luego lo nuevo")

    @override_settings(**FAST)
    def test_tope_duro_responde_a_quien_escribe_sin_parar(self):
        """La ventana deslizante no puede posponer la respuesta para siempre."""
        stop = threading.Event()

        def tecleo_constante():
            seq = 100
            while not stop.is_set():
                seq += 1
                self.receive(f"mensaje {seq}", seq)
                time.sleep(0.25)  # siempre antes de que expire la ventana

        with patch("apps.whatsapp.agent.run_turn", side_effect=self.fake_turn()):
            hilo = threading.Thread(target=tecleo_constante, daemon=True)
            hilo.start()
            time.sleep(4.0)  # más que el tope duro
            stop.set()
            hilo.join(timeout=5)
            self.wait_idle()

        self.assertGreaterEqual(len(self.sent), 1, "el tope duro debió disparar el turno")

    @override_settings(**FAST)
    def test_contacto_pausado_por_humano_no_recibe_respuesta(self):
        WhatsAppContact.objects.create(phone=PHONE, human_handoff=True)
        with patch("apps.whatsapp.agent.record_messages") as record:
            with patch("apps.whatsapp.agent.run_turn", side_effect=self.fake_turn()):
                self.receive("hola?", 1)
                time.sleep(0.5)
        self.assertEqual(len(self.sent), 0, "el agente no habla si hay un humano")
        record.assert_called()  # pero el mensaje sí queda en el hilo


class ExtraccionWebhookTests(TestCase):
    """El sobre de Kapso: lote, mensaje suelto y salientes."""

    def test_lote_de_kapso_devuelve_los_mensajes_en_orden(self):
        from .worker import extract_inbound_messages

        payload = webhook_payload("primero", 9)
        payload["data"].append(webhook_payload("segundo", 10)["data"][0])
        payload["batch_info"] = {"size": 2, "first_sequence": 9, "last_sequence": 10}

        messages = extract_inbound_messages(payload)
        self.assertEqual([m["text"] for m in messages], ["primero", "segundo"])
        self.assertEqual(messages[0]["phone"], PHONE)

    def test_los_salientes_no_se_procesan_como_entrantes(self):
        from .worker import extract_inbound_messages

        payload = webhook_payload("respuesta del sistema", 1)
        payload["data"][0]["message"]["kapso"]["direction"] = "outbound"
        self.assertEqual(extract_inbound_messages(payload), [])

    def test_un_mensaje_no_entregado_no_se_lee_como_silencio(self):
        """Chat real del 24/07: WhatsApp entregó el sobre vacío (error 131060).

        Kapso hoy no reenvía estos mensajes por webhook, pero si algún día lo
        hace el agente debe enterarse de que el cliente intentó mandar algo.
        """
        from .worker import extract_inbound_messages

        payload = webhook_payload("", 1, msg_type="unsupported")
        payload["data"][0]["message"].pop("text")

        messages = extract_inbound_messages(payload)
        self.assertEqual(len(messages), 1, "no puede descartarse en silencio")
        self.assertIn("no nos pudo entregar", messages[0]["text"])


class CitasTests(TestCase):
    """Respuestas citando un mensaje (deslizar para responder)."""

    def test_el_texto_citado_llega_al_agente(self):
        from .worker import _message_text, extract_inbound_messages

        ChatMessage.remember(
            "wamid.citado",
            PHONE,
            ChatMessage.Direction.OUTBOUND,
            "Granizado de Blue Berry: Pequeño $8.000, Grande $10.000",
        )
        payload = webhook_payload("ese porfa", 2, quoted_wamid="wamid.citado")

        msg = extract_inbound_messages(payload)[0]
        self.assertEqual(msg["quoted_wamid"], "wamid.citado")
        text = _message_text(msg, PHONE_NUMBER_ID)
        self.assertIn("Granizado de Blue Berry", text, "el agente debe ver qué citó")
        self.assertIn("ese porfa", text)

    def test_una_cita_sin_texto_guardado_igual_se_avisa(self):
        from .worker import _message_text, extract_inbound_messages

        payload = webhook_payload("este mismo", 3, quoted_wamid="wamid.viejisimo")
        text = _message_text(extract_inbound_messages(payload)[0], PHONE_NUMBER_ID)
        self.assertIn("citando un mensaje anterior", text)

    def test_sin_cita_el_texto_va_limpio(self):
        from .worker import _message_text, extract_inbound_messages

        payload = webhook_payload("quiero un granizado", 4)
        text = _message_text(extract_inbound_messages(payload)[0], PHONE_NUMBER_ID)
        self.assertEqual(text, "quiero un granizado")


class PromptTests(TestCase):
    """El prompt se arma con los datos de configuración, sin placeholders sueltos.

    Necesita BD: la zona de cobertura del prompt sale de StoreSettings.
    """

    def test_lleva_el_numero_al_que_remitir_cuando_no_sabe(self):
        with override_settings(WHATSAPP_CONTACT_PHONE="3009998877"):
            prompt = build_system_prompt()
        self.assertIn("3009998877", prompt)
        self.assertNotIn("{", prompt, "quedó un placeholder sin reemplazar")

    def test_la_hora_va_de_ultima_para_no_tirar_el_cache_del_prefijo(self):
        """El proveedor cachea por prefijo: un dato del minuto arriba lo anula todo."""
        prompt = build_system_prompt()
        self.assertIn("FECHA Y HORA ACTUAL", prompt.split("REGLAS DE ORO")[1])
        self.assertLess(
            len(prompt) - prompt.index("FECHA Y HORA ACTUAL"),
            60,
            "detrás de la hora no puede quedar nada del prompt",
        )

    def test_el_tono_elegido_reemplaza_la_personalidad_por_defecto(self):
        """Elegir "serio" no puede dejar dentro al parcero: se contradirían."""
        config = AgentSettings.load()
        config.tone_preset = "serio"
        config.save()
        prompt = build_system_prompt()
        self.assertIn("USTED siempre", prompt)
        self.assertNotIn("parcero del pueblo", prompt)

    def test_el_tono_por_defecto_es_el_parcero_de_siempre(self):
        self.assertIn("parcero del pueblo", build_system_prompt())

    def test_un_tono_que_ya_no_existe_no_deja_al_agente_sin_personalidad(self):
        config = AgentSettings.load()
        AgentSettings.objects.filter(pk=config.pk).update(tone_preset="inventado")
        self.assertIn("QUIÉN ERES", build_system_prompt())

    def test_los_ajustes_de_tono_se_suman_al_tono_elegido(self):
        config = AgentSettings.load()
        config.tone_preset = "cercano"
        config.tone = "No uses emojis."
        config.save()
        prompt = build_system_prompt()
        self.assertIn("cálido y atento", prompt)
        self.assertIn("No uses emojis.", prompt)

    def test_los_botones_no_se_ofrecen_para_elegir_el_pago(self):
        """Los quiso el dueño solo para confirmar; el pago se pregunta escribiendo."""
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        prompt = build_system_prompt(turn=turn)
        self.assertIn("enviar_botones", prompt)
        self.assertIn("NUNCA los uses para el método de pago", prompt)

    def test_la_llave_bre_b_es_el_mismo_nequi(self):
        """Tercer medio de pago del local: la llave es el número del Nequi."""
        prompt = build_system_prompt()
        self.assertIn("Bre-B", prompt)
        self.assertIn("MISMO número del Nequi", prompt)


class CoberturaSinUbicacionTests(TestCase):
    """La ubicación que el cliente sí mandó pero WhatsApp no nos entregó."""

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE)

    def _verificar_cobertura(self):
        from .tools import build_tools

        tools = {t.name: t for t in build_tools(self.contact)}
        return tools["verificar_cobertura"].invoke({})

    def test_sin_ubicacion_y_sin_intentos_pide_la_ubicacion(self):
        with patch("apps.whatsapp.tools.kapso.recent_undelivered", return_value=[]):
            respuesta = self._verificar_cobertura()
        self.assertIn("NO ha compartido su ubicación", respuesta)

    def test_si_el_cliente_intento_mandarla_no_se_le_niega(self):
        """Chat real del 24/07: 'Mírala aquí' → 'no he recibido tu ubicación'.

        El cliente la compartió dos veces y las dos llegaron como mensaje no
        disponible (error 131060), que Kapso no reenvía por webhook. El agente
        respondía lo único que sabía —que no la tenía— y el cliente veía que le
        insistían con lo mismo. Ahora la tool pregunta por esos mensajes.
        """
        with patch(
            "apps.whatsapp.tools.kapso.recent_undelivered", return_value=[1784937117]
        ):
            respuesta = self._verificar_cobertura()
        self.assertIn("SÍ intentó enviarnos algo", respuesta)
        self.assertIn("solicitar_humano", respuesta, "a la segunda, un humano")


class BusquedaDeProductosTests(TestCase):
    """Chat real 2026-08-24: el cliente pregunta por 'salchipapas' (plural) y el
    agente responde que no hay, teniendo cinco publicadas en Frostbyte Food.

    La búsqueda comparaba la palabra del cliente DENTRO del nombre del producto,
    así que 'salchipapas' no encontraba 'Salchipapa Clásica' y la tool contestaba
    'eso no está disponible hoy'.
    """

    def setUp(self):
        from apps.business.models import Business
        from apps.products.models import Category, Product, ProductVariant

        # los dos negocios los crea una migración de datos
        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food", defaults={"name": "Frostbyte Food", "display_order": 2}
        )
        bebidas, _ = Business.objects.get_or_create(
            slug="frostbyte", defaults={"name": "Frostbyte", "display_order": 1}
        )
        self.salchipapas = Category.objects.create(name="Salchipapas", slug="salchipapas", business=food)
        granizados = Category.objects.create(name="Granizados", slug="granizados", business=bebidas)
        for i, nombre in enumerate(
            ["Salchipapa Clásica", "Salchipapa con Queso", "Salchipapa Especial Frostbyte"]
        ):
            producto = Product.objects.create(
                name=nombre,
                category=self.salchipapas,
                business=food,
                description="Papas con las tres salchichas",
            )
            ProductVariant.objects.create(
                product=producto, name="Personal", sku=f"SP-{i}", price=16000
            )
        mora = Product.objects.create(
            name="Granizado de Mora",
            category=granizados,
            business=bebidas,
            description="Granizado de fruta natural",
        )
        ProductVariant.objects.create(product=mora, name="Mediano", sku="GR-1", price=8000)

        contact = WhatsAppContact.objects.create(phone=PHONE)
        self.buscar = {t.name: t for t in build_tools(contact)}["buscar_producto"]

    def _buscar(self, texto):
        return self.buscar.invoke({"texto": texto})

    def test_el_plural_encuentra_las_salchipapas(self):
        resultado = self._buscar("hola tienen salchipapas?")
        self.assertIn("Salchipapa Clásica", resultado)
        self.assertIn("Salchipapa Especial Frostbyte", resultado)

    def test_sin_tildes_encuentra_el_producto(self):
        self.assertIn("Salchipapa Clásica", self._buscar("la salchipapa clasica"))

    def test_el_nombre_exacto_sigue_ganando(self):
        resultado = self._buscar("salchipapa especial")
        primero = [l for l in resultado.splitlines() if l.startswith("  - ")][0]
        self.assertIn("Salchipapa Especial Frostbyte", primero)

    def test_un_generico_del_cliente_llega_a_la_categoria(self):
        self.assertIn("Salchipapa", self._buscar("que hay de comer"))
        self.assertIn("Salchipapa", self._buscar("tienen papas"))

    def test_un_error_de_tecleo_no_niega_el_producto(self):
        self.assertIn("Granizado de Mora", self._buscar("un granisado de mora"))

    def test_lo_que_no_vendemos_se_sigue_negando(self):
        resultado = self._buscar("tienen hamburguesas")
        self.assertIn("Sin coincidencias", resultado)
        self.assertIn("Salchipapas", resultado)  # ofrece las categorías que sí hay

    def test_la_busqueda_no_cruza_negocios_por_error(self):
        self.assertNotIn("Granizado", self._buscar("salchipapas"))

    def test_un_tamano_suelto_no_identifica_producto(self):
        """Chat real 2026-08-20: el cliente escribió "1 grande" junto a una foto
        que no se pudo procesar. Un tamaño no nombra ningún producto: la tool no
        puede devolver medio menú para que el modelo elija uno al azar."""
        resultado = self._buscar("1 grande")
        self.assertIn("Sin coincidencias", resultado)

    def test_el_tamano_sigue_desempatando(self):
        self.assertIn("Salchipapa Clásica", self._buscar("salchipapa clasica personal"))


class PedidoParaRecogerTests(TestCase):
    """Chat real 2026-08-19: con los domicilios pausados un cliente pidió una
    salchipapa "apenas esté lista me avisa, ya voy por ella". El agente no tenía
    cómo tomarlo y la venta se perdió."""

    def setUp(self):
        from apps.business.models import Business
        from apps.orders.models import StoreSettings
        from apps.products.models import Category, Product, ProductVariant

        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food", defaults={"name": "Frostbyte Food"}
        )
        categoria = Category.objects.create(name="Salchipapas", slug="salchipapas", business=food)
        producto = Product.objects.create(
            name="Salchipapa con Queso", category=categoria, business=food, description="Con queso"
        )
        self.variante = ProductVariant.objects.create(
            product=producto, name="Personal", sku="SPQ-1", price=18000
        )
        self.cfg = StoreSettings.load()
        self.cfg.is_open = True
        self.cfg.customer_ordering_enabled = False  # domicilios pausados
        self.cfg.pickup_enabled = True
        self.cfg.delivery_fee = 2000
        self.cfg.save()

        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def _crear(self, **kwargs):
        datos = {
            "items": [{"variante_id": self.variante.id, "cantidad": 1, "notas": ""}],
            "nombre_cliente": "Eduardo",
            "metodo_pago": "cash",
            "paga_con": "exacto",
        }
        datos.update(kwargs)
        return self.tools["crear_pedido"].invoke(datos)

    def test_con_domicilios_pausados_se_puede_encargar_para_recoger(self):
        from apps.orders.models import Order

        resultado = self._crear(para_recoger=True)
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.OrderType.PICKUP)
        self.assertEqual(order.delivery_fee, 0)
        self.assertEqual(order.source, Order.Source.WHATSAPP)

    def test_recoger_no_pide_ubicacion_ni_direccion(self):
        # el contacto no tiene ubicación compartida: a domicilio sería un ERROR
        self.assertIn("PEDIDO CREADO", self._crear(para_recoger=True))
        self.assertIn("ERROR", self._crear(direccion="Carrera 11 #21-17"))

    def test_el_domicilio_pausado_sugiere_recoger(self):
        self.assertIn("PARA RECOGER", self._crear(direccion="Carrera 11 #21-17"))

    def test_recoger_pausado_no_crea_pedido(self):
        self.cfg.pickup_enabled = False
        self.cfg.save()
        self.assertIn("ERROR", self._crear(para_recoger=True))

    def test_para_recoger_no_hace_falta_metodo_de_pago_ni_nada_mas(self):
        """Regla de Jaime (27/08): para recoger solo se confirma el total y se
        crea el pedido; el cliente paga al recogerlo."""
        from apps.orders.models import Order

        resultado = self._crear(para_recoger=True, metodo_pago="", paga_con="")
        self.assertIn("PEDIDO CREADO", resultado)
        self.assertIn("paga al recogerlo", resultado)
        self.assertIn("TOTAL: $18.000 · pago: al recoger en el local", resultado)
        order = Order.objects.get()
        self.assertEqual(order.payment_method, "")
        self.assertEqual(order.order_type, Order.OrderType.PICKUP)

    def test_para_recoger_usa_el_nombre_que_ya_se_conoce(self):
        from apps.orders.models import Order

        self.contact.profile_name = "Milena Irua Studio"
        self.contact.save()
        resultado = self._crear(para_recoger=True, metodo_pago="", nombre_cliente="")
        self.assertIn("PEDIDO CREADO", resultado)
        self.assertEqual(Order.objects.get().customer_name, "Milena Irua Studio")

    def test_sin_ningun_nombre_se_pregunta_solo_eso(self):
        from apps.orders.models import Order

        resultado = self._crear(para_recoger=True, metodo_pago="", nombre_cliente="")
        self.assertIn("ERROR", resultado)
        self.assertIn("nombre", resultado)
        self.assertEqual(Order.objects.count(), 0)

    def test_el_domicilio_sigue_exigiendo_metodo_de_pago(self):
        self.cfg.customer_ordering_enabled = True
        self.cfg.save()
        resultado = self._crear(metodo_pago="", direccion="Carrera 11 #21-17")
        self.assertIn("ERROR", resultado)
        self.assertIn("método de pago", resultado)

    def test_el_prompt_no_pregunta_nada_para_recoger_pero_si_confirma(self):
        prompt = build_system_prompt()
        self.assertIn("NO preguntes método de pago, celular, dirección ni ubicación", prompt)
        self.assertIn("Solo para domicilio: pregunta el método de pago", prompt)
        # Regla de Jaime: items + total, el cliente confirma, y solo ahí se crea
        self.assertIn("muestra items y TOTAL, y espera su", prompt)
        self.assertIn('espera un "sí" explícito', prompt)
        self.assertNotIn("DE INMEDIATO", prompt)

    def test_el_local_cerrado_manda_sobre_los_dos_canales(self):
        self.cfg.is_open = False
        self.cfg.save()
        self.assertIn("CERRADO", self._crear(para_recoger=True))

    def test_la_cotizacion_de_recoger_no_cobra_envio(self):
        cotizacion = self.tools["cotizar_pedido"].invoke(
            {"items": [{"variante_id": self.variante.id, "cantidad": 1}], "para_recoger": True}
        )
        self.assertIn("TOTAL: $18.000", cotizacion)
        self.assertNotIn("Envío:", cotizacion)

    def test_la_cotizacion_avisa_que_el_pedido_no_existe_todavia(self):
        """Chat real 2026-08-27: el agente cotizó un granizado para recoger y le
        dijo a la clienta "te avisaré cuando esté listo" sin crear el pedido."""
        cotizacion = self.tools["cotizar_pedido"].invoke(
            {"items": [{"variante_id": self.variante.id, "cantidad": 1}], "para_recoger": True}
        )
        self.assertIn("NO está creado", cotizacion)
        self.assertIn("PEDIDO CREADO", cotizacion)
        prompt = build_system_prompt()
        self.assertIn("cotizar_pedido NO crea nada", prompt)
        self.assertNotIn("y dile que le avisas cuando esté listo. Todo", prompt)

    def test_el_estado_avisa_que_se_puede_encargar(self):
        estado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("Puedes tomar pedidos A DOMICILIO: NO", estado)
        self.assertIn("Puedes tomar pedidos PARA RECOGER: sí", estado)

    def test_las_tools_nunca_dicen_pausado_al_modelo(self):
        # Jaime (2026-08-27): el cliente no debe leer "pausados" (jerga interna);
        # el modelo calca el texto de las tools, así que la palabra no puede aparecer.
        textos = [
            self.tools["consultar_estado_tienda"].invoke({}),
            self._crear(direccion="Carrera 11 #21-17"),
        ]
        self.cfg.pickup_enabled = False
        self.cfg.save()
        textos.append(self._crear(para_recoger=True))
        for texto in textos:
            self.assertNotIn("pausad", texto.lower())
        self.assertIn("justo en este momento no hay servicio de domicilios", textos[1])

    def test_el_prompt_prohibe_decir_pausado(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("justo en este momento no tenemos servicio de domicilios", SYSTEM_PROMPT)
        self.assertIn("NUNCA digas al cliente que un servicio está \"pausado\"", SYSTEM_PROMPT)


class LocalCerradoTests(TestCase):
    """Chat real 2026-09-01, 21:44: Nancy cerró el local a las 21:42 y el agente
    respondió "no tenemos servicio de domicilios ni de recogida. Pero puedes
    encargar tu pedido y pasar por él al local", sin decir que estaba cerrado y
    contradiciéndose en la misma frase."""

    def setUp(self):
        from apps.orders.models import StoreSettings

        self.cfg = StoreSettings.load()
        self.cfg.is_open = False
        self.cfg.customer_ordering_enabled = False
        self.cfg.pickup_enabled = False
        self.cfg.opening_time = datetime.time(13, 30)
        self.cfg.save()

        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def test_el_estado_dice_cerrado_y_nada_mas(self):
        estado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("LOCAL CERRADO", estado)
        # Ni una palabra de canales: es lo que el modelo calcó para contradecirse
        self.assertNotIn("Puedes tomar pedidos", estado)
        self.assertNotIn("Tarifa de envío", estado)
        self.assertIn("NO le ofrezcas encargar", estado)

    def test_el_estado_dice_cuando_abrimos(self):
        estado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("1:30 p. m.", estado)
        self.assertIn("normalmente", estado)

    def test_con_el_local_abierto_si_se_ofrecen_los_canales(self):
        self.cfg.is_open = True
        self.cfg.pickup_enabled = True
        self.cfg.save()
        estado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("Local ABIERTO", estado)
        self.assertIn("Puedes tomar pedidos PARA RECOGER: sí", estado)
        self.assertNotIn("LOCAL CERRADO", estado)

    def test_abierto_sin_ningun_canal_no_ofrece_el_otro(self):
        self.cfg.is_open = True
        self.cfg.save()  # los dos canales siguen apagados
        estado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("ningún canal recibe pedidos", estado)
        self.assertNotIn("ofrécele encargar", estado)

    def test_crear_pedido_cerrado_no_ofrece_recoger(self):
        error = self.tools["crear_pedido"].invoke(
            {"items": [], "para_recoger": True, "nombre_cliente": "Daniel"}
        )
        self.assertIn("CERRADO", error)
        self.assertIn("no le ofrezcas encargar ni recoger", error)

    def _hint_a_las(self, hora, minuto, cerrado_el=None):
        """El aviso de reapertura tal como se leería a esa hora de hoy.

        `cerrado_el` es el último cambio del interruptor del local (None = nadie
        lo ha tocado desde que existe el dato).
        """
        from apps.orders.models import StoreSettings
        from django.utils import timezone as dj_timezone

        cfg = StoreSettings.load()
        cfg.status_changed_at = cerrado_el
        cfg.save()
        ahora = dj_timezone.localtime().replace(
            hour=hora, minute=minuto, second=0, microsecond=0
        )
        with patch("django.utils.timezone.now", return_value=ahora):
            return cfg.reopening_hint()

    def _hoy_a_las(self, hora, minuto):
        from django.utils import timezone as dj_timezone

        return dj_timezone.localtime().replace(
            hour=hora, minute=minuto, second=0, microsecond=0
        )

    def test_antes_de_la_hora_habitual_dice_hoy(self):
        anoche = self._hoy_a_las(13, 30) - datetime.timedelta(hours=15)
        self.assertIn("hoy normalmente abrimos", self._hint_a_las(12, 0, anoche))

    def test_pasada_la_hora_sin_abrir_dice_que_falta_poco_no_manana(self):
        """Chat real 2026-09-03, 13:34: escribió cuatro minutos después de la
        hora de apertura y el agente lo mandó a volver mañana. El local abre
        todos los días: a esa hora está a punto de abrir."""
        anoche = self._hoy_a_las(13, 30) - datetime.timedelta(hours=15)
        hint = self._hint_a_las(13, 34, anoche)
        self.assertIn("estamos por abrir", hint)
        self.assertNotIn("mañana", hint)
        self.assertNotIn("mañana", self._hint_a_las(14, 0, anoche))

    def test_despues_de_cerrar_la_jornada_si_dice_manana(self):
        cerraron_hoy = self._hoy_a_las(21, 42)
        self.assertIn("mañana normalmente abrimos", self._hint_a_las(22, 10, cerraron_hoy))

    def test_sin_dato_del_interruptor_no_promete_abrir_de_noche(self):
        self.assertIn("estamos por abrir", self._hint_a_las(13, 34))
        self.assertIn("mañana normalmente abrimos", self._hint_a_las(20, 0))

    def test_el_prompt_ordena_local_domicilio_recoger(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("PRIMERO si el local está abierto o cerrado", SYSTEM_PROMPT)
        self.assertIn("Con el local CERRADO se acabó la conversación de pedidos", SYSTEM_PROMPT)
        self.assertIn("normalmente sí se puede pasar a recoger", SYSTEM_PROMPT)


class ArchivoDeConversacionTests(TestCase):
    """La conversación se guarda entera para poder revisar después si hubo venta,
    incluidos los pedidos que el equipo cierra a mano durante una pausa humana."""

    def test_se_distingue_al_humano_del_agente(self):
        ChatMessage.remember("wamid.a", PHONE, ChatMessage.Direction.INBOUND, "hay salchipapas?")
        ChatMessage.remember("wamid.b", PHONE, ChatMessage.Direction.OUTBOUND, "No tenemos")
        ChatMessage.remember(
            "wamid.c", PHONE, ChatMessage.Direction.OUTBOUND, "Si, si hay",
            author=ChatMessage.Author.HUMAN,
        )
        autores = dict(ChatMessage.objects.values_list("wamid", "author"))
        self.assertEqual(autores["wamid.a"], ChatMessage.Author.CUSTOMER)
        self.assertEqual(autores["wamid.b"], ChatMessage.Author.AGENT)
        self.assertEqual(autores["wamid.c"], ChatMessage.Author.HUMAN)

    def test_el_archivo_guarda_lo_que_el_agente_leyo_de_verdad(self):
        ChatMessage.remember(
            "wamid.img", PHONE, ChatMessage.Direction.INBOUND,
            "[El cliente envió una imagen que no se pudo procesar]",
        )
        ChatMessage.enrich("wamid.img", "[El cliente envió una imagen. Contenido: comprobante por $39.000]")
        self.assertIn("39.000", ChatMessage.objects.get(wamid="wamid.img").body)

    def test_el_aviso_de_listo_no_dice_va_en_camino_si_es_para_recoger(self):
        from apps.whatsapp.signals import PICKUP_MESSAGES
        from apps.orders.models import Order

        self.assertIn("pasa por él", PICKUP_MESSAGES[Order.Status.READY].lower())
        self.assertNotIn("va en camino", PICKUP_MESSAGES[Order.Status.READY])


BSUID = "CO.2430294670795328"


def bsuid_payload(text, sequence=1):
    """Webhook real del 27/08: cliente con nombre de usuario, sin número."""
    payload = webhook_payload(text, sequence)
    entry = payload["data"][0]
    entry["message"]["from"] = None
    entry["message"]["from_user_id"] = BSUID
    entry["message"]["username"] = "dayanab1088"
    entry["conversation"] = {
        "phone_number": None,
        "business_scoped_user_id": BSUID,
        "username": "dayanab1088",
        "contact_name": "Dayana",
    }
    return payload


def app_reply_payload(text, wamid="wamid.app1"):
    """Saliente desde la app de WhatsApp Business a un cliente sin número."""
    return {
        "type": "whatsapp.message.sent",
        "data": [
            {
                "phone_number_id": PHONE_NUMBER_ID,
                "message": {
                    "id": wamid,
                    "to": None,
                    "to_user_id": BSUID,
                    "from": "573117814338",
                    "type": "text",
                    "text": {"body": text},
                    "kapso": {"direction": "outbound", "origin": "business_app"},
                },
                "conversation": {"phone_number": None, "business_scoped_user_id": BSUID},
            }
        ],
    }


class ClientesSinNumeroTests(TestCase):
    """Chat real del 27/08: WhatsApp ya no manda el número de los clientes con
    nombre de usuario, solo su business-scoped user ID (BSUID, "CO.243…").

    El agente contestaba al BSUID como si fuera un teléfono (campo "to") y
    Meta devolvía 131026 "Message undeliverable": el cliente nunca lo vio, y
    la respuesta del equipo desde el celular tampoco pausaba al agente.
    """

    def fake_post(self, posted):
        def _post(url, json=None, headers=None, timeout=None):
            posted.append(json)
            response = Mock(status_code=200)
            response.json.return_value = {"messages": [{"id": f"wamid.out{len(posted)}"}]}
            return response

        return _post

    def test_el_entrante_sin_numero_se_identifica_por_bsuid(self):
        from .worker import extract_inbound_messages

        messages = extract_inbound_messages(bsuid_payload("Hola, hoy hay atención?"))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["phone"], "")
        self.assertEqual(messages[0]["wa_user_id"], BSUID)
        self.assertEqual(messages[0]["username"], "dayanab1088")

    def test_al_bsuid_se_le_escribe_en_recipient_no_en_to(self):
        from . import kapso

        posted = []
        with override_settings(KAPSO_API_KEY="clave"):
            with patch("apps.whatsapp.kapso.requests.post", side_effect=self.fake_post(posted)):
                kapso.send_text(PHONE_NUMBER_ID, BSUID, "hola")
                kapso.send_text(PHONE_NUMBER_ID, PHONE, "hola")
                kapso.send_buttons(PHONE_NUMBER_ID, BSUID, "¿Sí?", [("si", "Sí")])

        self.assertEqual(posted[0]["recipient"], BSUID)
        self.assertNotIn("to", posted[0])
        self.assertEqual(posted[1]["to"], PHONE)
        self.assertNotIn("recipient", posted[1])
        self.assertEqual(posted[2]["recipient"], BSUID)
        # El wamid queda registrado como propio: no se confunde con un humano
        self.assertTrue(SentMessage.objects.filter(wamid="wamid.out1", to_phone=BSUID).exists())

    def test_el_saliente_desde_la_app_conserva_el_bsuid(self):
        from .worker import extract_outbound_messages

        out = extract_outbound_messages(app_reply_payload("Buenas tardes si señor"))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["phone"], "")
        self.assertEqual(out[0]["wa_user_id"], BSUID)
        self.assertEqual(out[0]["origin"], "business_app")

    def test_la_respuesta_humana_desde_la_app_pausa_al_agente(self):
        from .worker import _handle_outbound, extract_outbound_messages

        contact = WhatsAppContact.objects.create(phone=BSUID, wa_user_id=BSUID)
        payload = app_reply_payload("Buenas tardes si señor")
        event = WebhookEvent.objects.create(
            idempotency_key="app-1", payload=payload, event_type="whatsapp.message.sent"
        )
        with patch("apps.whatsapp.agent.record_messages"):
            _handle_outbound(event, extract_outbound_messages(payload))

        contact.refresh_from_db()
        self.assertIsNotNone(contact.human_until, "el humano ya está atendiendo")
        self.assertEqual(WhatsAppContact.objects.count(), 1, "no crea un contacto vacío")
        self.assertEqual(event.contact_phone, BSUID)

    def test_el_contacto_se_reconoce_por_bsuid_aunque_deje_de_llegar_el_numero(self):
        from .worker import _find_contact

        known = WhatsAppContact.objects.create(phone=PHONE, wa_user_id=BSUID)
        self.assertEqual(_find_contact(BSUID, BSUID), known)
        self.assertEqual(WhatsAppContact.objects.count(), 1)

    def test_los_pedidos_de_un_cliente_sin_numero_se_emparejan_por_su_celular(self):
        from .tools import _customer_orders

        contact = WhatsAppContact.objects.create(
            phone=BSUID, wa_user_id=BSUID, contact_phone="573001234567"
        )
        order = Order.objects.create(
            source=Order.Source.WHATSAPP, customer_phone="573001234567", customer_name="Dayana"
        )
        self.assertEqual(list(_customer_orders(contact)), [order])

    def test_la_notificacion_del_pedido_sale_por_whatsapp_y_no_por_el_celular(self):
        from .signals import _destination

        contact = WhatsAppContact.objects.create(
            phone=BSUID, wa_user_id=BSUID, contact_phone="573001234567"
        )
        self.assertEqual(_destination("573001234567"), (contact, BSUID))
        normal = WhatsAppContact.objects.create(phone=PHONE)
        self.assertEqual(_destination(PHONE), (normal, PHONE))


class ClienteSinNumeroPideCelularTests(TestCase):
    """Pedido de Jaime (27/08): si WhatsApp no muestra el número del cliente,
    el agente pide un celular de contacto para poder llamarlo si hace falta.
    Solo en domicilios: quien pasa a recoger viene al local."""

    def setUp(self):
        from apps.business.models import Business
        from apps.orders.models import StoreSettings
        from apps.products.models import Category, Product, ProductVariant

        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food", defaults={"name": "Frostbyte Food"}
        )
        categoria = Category.objects.create(name="Salchipapas", slug="salchipapas", business=food)
        producto = Product.objects.create(
            name="Salchipapa con Queso", category=categoria, business=food, description="Con queso"
        )
        self.variante = ProductVariant.objects.create(
            product=producto, name="Personal", sku="SPQ-1", price=18000
        )
        cfg = StoreSettings.load()
        cfg.is_open = True
        cfg.pickup_enabled = True
        cfg.customer_ordering_enabled = True
        cfg.save()

        # Ubicación compartida en el propio local: dentro de la zona seguro
        from django.conf import settings as dj_settings

        self.contact = WhatsAppContact.objects.create(
            phone=BSUID,
            wa_user_id=BSUID,
            last_location_lat=dj_settings.DELIVERY_CENTER_LAT,
            last_location_lng=dj_settings.DELIVERY_CENTER_LNG,
        )
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def _crear(self, **kwargs):
        datos = {
            "items": [{"variante_id": self.variante.id, "cantidad": 1, "notas": ""}],
            "nombre_cliente": "Dayana",
            "metodo_pago": "cash",
            "paga_con": "exacto",
            "direccion": "Carrera 11 #21-17",
        }
        datos.update(kwargs)
        return self.tools["crear_pedido"].invoke(datos)

    def test_sin_celular_no_se_crea_el_domicilio(self):
        resultado = self._crear()
        self.assertIn("ERROR", resultado)
        self.assertIn("celular", resultado)
        self.assertEqual(Order.objects.count(), 0)

    def test_para_recoger_no_se_pide_ningun_numero(self):
        resultado = self._crear(para_recoger=True, direccion="")
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertEqual(order.customer_phone, BSUID, "sin celular, el pedido conserva la identidad")
        # y la notificación de "listo" sigue saliendo por WhatsApp
        from .signals import _destination

        self.assertEqual(_destination(order.customer_phone), (self.contact, BSUID))

    def test_un_celular_mal_dado_se_vuelve_a_pedir(self):
        resultado = self._crear(telefono_contacto="123")
        self.assertIn("ERROR", resultado)
        self.assertIn("10 dígitos", resultado)
        self.assertEqual(Order.objects.count(), 0)

    def test_el_celular_queda_en_el_pedido_y_en_el_contacto(self):
        resultado = self._crear(telefono_contacto="300 123 4567")
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertEqual(order.customer_phone, "573001234567", "el staff ve un número al que llamar")
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.contact_phone, "573001234567")

    def test_el_segundo_domicilio_no_vuelve_a_pedir_el_celular(self):
        self.contact.contact_phone = "573001234567"
        self.contact.save()
        resultado = self._crear()
        self.assertIn("PEDIDO CREADO", resultado)
        self.assertEqual(Order.objects.get().customer_phone, "573001234567")

    def test_el_historial_junta_los_pedidos_con_celular_y_los_de_recoger(self):
        from .tools import _customer_orders

        self._crear(para_recoger=True, direccion="")
        self._crear(telefono_contacto="300 123 4567")
        self.assertEqual(_customer_orders(self.contact).count(), 2)

    def test_el_prompt_le_avisa_al_agente_que_pida_el_celular(self):
        prompt = build_system_prompt(self.contact)
        self.assertIn("NO nos muestra su número", prompt)
        self.assertIn("A DOMICILIO", prompt)
        self.assertIn("NO le pidas ningún número", prompt)
        self.assertNotIn("Ya nos dio", prompt)

        self.contact.contact_phone = "573001234567"
        prompt = build_system_prompt(self.contact)
        self.assertIn("Ya nos dio el 573001234567", prompt)

        normal = WhatsAppContact.objects.create(phone=PHONE)
        self.assertNotIn("NO nos muestra su número", build_system_prompt(normal))


class ParametrosDelModeloTests(TestCase):
    """Cada familia de modelos se llama distinto (2026-09-03, cambio a GPT-5.6 Terra).

    Los modelos que razonan (GPT-5 en adelante) rechazan `temperature` y cobran
    los tokens de razonamiento contra el presupuesto de salida; los clásicos
    (`gpt-4o-mini`) siguen esperando `temperature` y `max_tokens`.
    """

    def test_terra_no_manda_temperature_y_si_esfuerzo_de_razonamiento(self):
        from .llm import chat_model_params

        with override_settings(WHATSAPP_AGENT_REASONING_EFFORT="low"):
            params = chat_model_params("gpt-5.6-terra", temperature=0.3)
        self.assertNotIn("temperature", params, "la API devuelve 400 si se manda")
        self.assertEqual(params["reasoning_effort"], "low")
        self.assertTrue(params["use_responses_api"], "conserva el razonamiento entre tools")

    def test_sin_razonamiento_terra_vuelve_a_aceptar_temperature(self):
        from .llm import chat_model_params

        with override_settings(WHATSAPP_AGENT_REASONING_EFFORT="none"):
            params = chat_model_params("gpt-5.6-terra", temperature=0.3)
        self.assertEqual(params["temperature"], 0.3)
        self.assertEqual(params["reasoning_effort"], "none")

    def test_un_modelo_clasico_se_sigue_llamando_como_antes(self):
        from .llm import chat_model_params, completion_params

        self.assertEqual(chat_model_params("gpt-4o-mini", temperature=0.3), {"temperature": 0.3})
        self.assertEqual(
            completion_params("gpt-4o-mini", temperature=0, max_output_tokens=200),
            {"temperature": 0, "max_tokens": 200},
        )

    def test_la_vision_con_terra_deja_margen_para_los_tokens_de_razonamiento(self):
        from .llm import completion_params

        with override_settings(WHATSAPP_AGENT_REASONING_EFFORT="low"):
            params = completion_params("gpt-5.6-terra", temperature=0, max_output_tokens=200)
        self.assertNotIn("max_tokens", params, "gpt-5 usa max_completion_tokens")
        self.assertGreater(
            params["max_completion_tokens"],
            200,
            "sin margen el modelo gasta el cupo pensando y la descripción llega vacía",
        )

    def test_la_vision_no_arrastra_el_esfuerzo_del_agente(self):
        from .llm import completion_params
        from .media import VISION_REASONING_EFFORT

        with override_settings(WHATSAPP_AGENT_REASONING_EFFORT="high"):
            params = completion_params(
                "gpt-5.6-luna", temperature=0, max_output_tokens=200, effort=VISION_REASONING_EFFORT
            )
        self.assertEqual(params["reasoning_effort"], "low", "leer un comprobante no paga razonamiento alto")

    def test_leer_media_no_usa_el_modelo_caro_del_agente(self):
        from django.conf import settings

        self.assertNotEqual(
            settings.WHATSAPP_VISION_MODEL,
            settings.WHATSAPP_AGENT_MODEL,
            "las imágenes van con un modelo barato; el del agente es para conversar",
        )
        self.assertIn("mini", settings.WHATSAPP_TRANSCRIBE_MODEL)

    def test_gpt_5_chat_no_cuenta_como_modelo_de_razonamiento(self):
        from .llm import is_reasoning_model

        self.assertTrue(is_reasoning_model("gpt-5.6-terra"))
        self.assertFalse(is_reasoning_model("gpt-5-chat-latest"))
        self.assertFalse(is_reasoning_model("gpt-4o-mini"))


class PersonalidadYStickersTests(TestCase):
    """El módulo de configuración de Frosty y lo que puede mandar al chat.

    La regla que se protege aquí es una sola: el prompt y las tools tienen que
    ir juntos. Contarle al modelo que puede mandar stickers y no darle la tool
    (o al revés) es lo que produce promesas que el turno no cumple.
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE)

    def _sticker(self, label="granizado feliz", **kwargs):
        return Sticker.objects.create(
            label=label,
            description=kwargs.pop("description", "para saludar al cliente"),
            data=b"webp-falso",
            byte_size=10,
            **kwargs,
        )

    def test_sin_contexto_de_turno_no_hay_tools_de_envio(self):
        """Las pruebas por shell no tienen por dónde mandar nada."""
        names = {t.name for t in build_tools(self.contact)}
        self.assertNotIn("enviar_sticker", names)
        self.assertNotIn("reaccionar", names)
        self.assertIn("crear_pedido", names, "las tools de siempre siguen ahí")

    def test_el_banco_vacio_no_ofrece_la_tool_ni_aparece_en_el_prompt(self):
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID, message_id="wamid.1")
        names = {t.name for t in build_tools(self.contact, turn)}
        self.assertNotIn("enviar_sticker", names)
        self.assertNotIn("BANCO DE STICKERS", build_system_prompt(self.contact, turn))

    def test_el_banco_lleno_llega_al_prompt_con_su_cuando_usarlo(self):
        self._sticker(description="para celebrar que el pedido quedó listo")
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID, message_id="wamid.1")
        prompt = build_system_prompt(self.contact, turn)
        self.assertIn("granizado feliz", prompt)
        self.assertIn("para celebrar que el pedido quedó listo", prompt)
        self.assertIn("enviar_sticker", {t.name for t in build_tools(self.contact, turn)})

    def test_el_sticker_inactivo_no_existe_para_el_agente(self):
        self._sticker(is_active=False)
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        self.assertNotIn("granizado feliz", build_system_prompt(self.contact, turn))

    def test_apagar_una_capacidad_la_quita_del_prompt_y_de_las_tools(self):
        self._sticker()
        config = AgentSettings.load()
        config.stickers_enabled = False
        config.reactions_enabled = False
        config.save()
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID, message_id="wamid.1")
        prompt = build_system_prompt(self.contact, turn)
        names = {t.name for t in build_tools(self.contact, turn)}
        self.assertNotIn("enviar_sticker", names)
        self.assertNotIn("reaccionar", names)
        self.assertNotIn("granizado feliz", prompt)
        self.assertNotIn("enviar_sticker", prompt)
        self.assertIn("enviar_foto_producto", names, "lo demás sigue encendido")

    def test_sin_message_id_no_puede_reaccionar(self):
        """Una notificación de estado no responde a ningún mensaje del cliente."""
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID, message_id="")
        self.assertNotIn("reaccionar", {t.name for t in build_tools(self.contact, turn)})

    def test_el_nombre_y_el_tono_configurados_mandan_en_el_prompt(self):
        config = AgentSettings.load()
        config.agent_name = "Cubito"
        config.tone = "Trata al cliente de usted."
        config.save()
        prompt = build_system_prompt(self.contact)
        self.assertIn("Cubito", prompt)
        self.assertIn("Trata al cliente de usted.", prompt)
        self.assertNotIn("{", prompt, "quedó un placeholder sin reemplazar")

    def test_mandar_un_sticker_marca_el_turno_como_irreversible(self):
        """Lo que el cliente ya vio no se puede deshacer descartando el turno."""
        self._sticker()
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        with patch("apps.whatsapp.kapso.send_sticker", return_value={"ok": True}) as send:
            salida = tool.invoke({"nombre": "granizado feliz"})
        self.assertTrue(turn.posted, "el cliente ya lo vio: el turno no se puede rehacer")
        self.assertIn("enviado", salida.lower())
        self.assertEqual(send.call_args.args[1], PHONE)
        self.assertEqual(Sticker.objects.get(label="granizado feliz").sent_count, 1)

    def test_pedir_un_sticker_inventado_devuelve_los_que_existen(self):
        """El modelo inventa nombres; darle la lista cuesta menos que un turno perdido."""
        self._sticker()
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        with patch("apps.whatsapp.kapso.send_sticker") as send:
            salida = tool.invoke({"nombre": "gato bailando"})
        send.assert_not_called()
        self.assertFalse(turn.posted)
        self.assertIn("granizado feliz", salida)

    def test_el_sticker_se_encuentra_aunque_el_modelo_cambie_tildes_o_mayusculas(self):
        self._sticker(label="corazón frío")
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        with patch("apps.whatsapp.kapso.send_sticker", return_value={"ok": True}):
            salida = tool.invoke({"nombre": "Corazon Frio"})
        self.assertTrue(turn.posted)
        self.assertIn("enviado", salida.lower())

    def test_si_kapso_falla_el_turno_sigue_siendo_de_texto(self):
        self._sticker()
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        with patch("apps.whatsapp.kapso.send_sticker", return_value=None):
            salida = tool.invoke({"nombre": "granizado feliz"})
        self.assertFalse(turn.posted, "no se envió nada: el turno se puede rehacer")
        self.assertFalse(turn.answered, "sigue debiendo una respuesta de texto")
        self.assertIn("texto", salida.lower())

    def test_los_botones_rechazan_opciones_que_whatsapp_no_acepta(self):
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_botones")
        with patch("apps.whatsapp.kapso.send_buttons") as send:
            una = tool.invoke({"texto": "¿Confirmas?", "opciones": ["Sí"]})
            larga = tool.invoke(
                {"texto": "¿Confirmas?", "opciones": ["Sí, confírmame el pedido ya", "No"]}
            )
        send.assert_not_called()
        self.assertIn("dos opciones", una)
        self.assertIn("20 caracteres", larga)

    def test_los_botones_se_mandan_con_ids_propios(self):
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_botones")
        with patch("apps.whatsapp.kapso.send_buttons", return_value={"ok": True}) as send:
            tool.invoke({"texto": "¿Cómo pagas?", "opciones": ["Efectivo", "Nequi"]})
        self.assertTrue(turn.posted)
        self.assertEqual(
            send.call_args.args[3], [("btn_0", "Efectivo"), ("btn_1", "Nequi")]
        )

    def test_la_reaccion_responde_pero_no_deja_mensaje(self):
        """Prueba real 03/09: un "mil gracias" contestado con ❤️ recibía además
        "Perdón, ¿me lo repites?", porque la reacción no contaba como respuesta.

        Son dos cosas distintas: no pone mensaje en el chat (el turno se puede
        rehacer) pero sí responde (no hace falta texto de relleno).
        """
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID, message_id="wamid.7")
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "reaccionar")
        with patch("apps.whatsapp.kapso.send_reaction", return_value={"ok": True}) as send:
            tool.invoke({"emoji": "❤️"})
        self.assertFalse(turn.posted, "una reacción no es un mensaje")
        self.assertTrue(turn.answered, "pero sí es una respuesta: no se pide repetir")
        self.assertEqual(send.call_args.args[2:], ("wamid.7", "❤️"))

    def test_un_turno_que_solo_reacciona_no_manda_texto_de_relleno(self):
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID, message_id="wamid.7")
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "reaccionar")
        with patch("apps.whatsapp.kapso.send_reaction", return_value={"ok": True}):
            tool.invoke({"emoji": "❤️"})
        self.assertEqual(_for_whatsapp("", already_answered=turn.answered), "")

    def test_la_foto_de_un_producto_sin_imagen_no_se_inventa(self):
        from apps.products.models import Business, Category, Product

        business, _ = Business.objects.get_or_create(name="Frostbyte", defaults={"slug": "frostbyte"})
        category = Category.objects.create(name="Granizados", slug="granizados", business=business)
        Product.objects.create(
            name="Granizado de mango", slug="granizado-mango", category=category, image_url=""
        )
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_foto_producto")
        with patch("apps.whatsapp.kapso.send_image") as send:
            salida = tool.invoke({"producto_slug": "granizado-mango"})
        send.assert_not_called()
        self.assertFalse(turn.posted)
        self.assertIn("no tiene foto", salida)


class RespuestaVaciaTests(TestCase):
    """Cuando el turno ya puso algo en el chat, callarse es la respuesta correcta."""

    def test_sin_texto_y_sin_envio_previo_se_pide_repetir(self):
        self.assertEqual(_for_whatsapp("  "), "Perdón, ¿me lo repites?")

    def test_sin_texto_despues_de_responder_no_se_manda_nada(self):
        self.assertEqual(_for_whatsapp("", already_answered=True), "")

    def test_el_texto_normal_no_cambia(self):
        self.assertEqual(_for_whatsapp("Listo parce", already_answered=True), "Listo parce")


class ConversionDeStickersTests(TestCase):
    """Lo que sube una persona desde el admin tiene que salir válido para WhatsApp."""

    def _png(self, size=(300, 200), color=(255, 0, 0, 255)):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGBA", size, color).save(buffer, format="PNG")
        return buffer.getvalue()

    def test_una_imagen_cualquiera_sale_de_512x512(self):
        from io import BytesIO

        from PIL import Image

        data, animated = normalize(self._png())
        self.assertFalse(animated)
        self.assertLessEqual(len(data), 100 * 1024, "WhatsApp rechaza los fijos de más de 100 KB")
        with Image.open(BytesIO(data)) as out:
            self.assertEqual(out.size, (512, 512))
            self.assertEqual(out.format, "WEBP")

    def test_no_se_deforma_lo_que_no_era_cuadrado(self):
        """El sobrante se rellena transparente en vez de estirar el dibujo."""
        from io import BytesIO

        from PIL import Image

        data, _ = normalize(self._png(size=(400, 100)))
        with Image.open(BytesIO(data)) as out:
            alpha = out.convert("RGBA").getchannel("A")
        self.assertEqual(alpha.getpixel((256, 10)), 0, "arriba debió quedar transparente")

    def test_un_archivo_que_no_es_imagen_da_un_error_legible(self):
        with self.assertRaises(StickerError):
            normalize(b"esto no es una imagen")

    def test_se_detecta_si_falta_la_transparencia(self):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (300, 300), (255, 255, 255)).save(buffer, format="PNG")
        self.assertFalse(has_transparency(buffer.getvalue()))
        self.assertTrue(has_transparency(self._png(color=(255, 0, 0, 0))))


class EndpointDeStickersTests(TestCase):
    """WhatsApp descarga el archivo con un GET anónimo desde los servidores de Meta."""

    def setUp(self):
        self.sticker = Sticker.objects.create(
            label="pulgar arriba", description="para confirmar", data=b"RIFF-webp-falso", byte_size=15
        )

    def test_se_sirve_sin_autenticacion_y_como_webp(self):
        response = self.client.get(f"/api/v1/whatsapp/stickers/{self.sticker.pk}.webp")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertEqual(response.content, b"RIFF-webp-falso")

    def test_un_sticker_desactivado_deja_de_servirse(self):
        Sticker.objects.filter(pk=self.sticker.pk).update(is_active=False)
        response = self.client.get(f"/api/v1/whatsapp/stickers/{self.sticker.pk}.webp")
        self.assertEqual(response.status_code, 404)

    def test_la_url_del_modelo_es_la_que_resuelve_el_router(self):
        """Si dejan de coincidir, WhatsApp recibe un 404 y no manda el sticker."""
        from django.urls import reverse

        self.assertTrue(
            self.sticker.url.endswith(reverse("whatsapp-sticker", args=[self.sticker.pk]))
        )


class FormularioDeStickersTests(TestCase):
    """Subir un sticker desde el admin: es el camino real de quien llena el banco."""

    def _upload(self, size=(300, 200), mode="RGBA", color=(255, 0, 0, 255), fmt="PNG"):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = BytesIO()
        Image.new(mode, size, color[: 3 if mode == "RGB" else 4]).save(buffer, format=fmt)
        return SimpleUploadedFile("sticker.png", buffer.getvalue(), content_type="image/png")

    def _form(self, **overrides):
        from .admin import StickerForm

        data = {
            "label": "granizado feliz",
            "description": "para saludar al cliente",
            "is_active": True,
            "display_order": 0,
        }
        data.update(overrides.pop("data", {}))
        return StickerForm(data=data, files={"archivo": overrides.pop("archivo", self._upload())})

    def test_un_png_cualquiera_queda_guardado_como_webp_valido(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        sticker = form.save()
        self.assertTrue(bytes(sticker.data).startswith(b"RIFF"), "no salió un WebP")
        self.assertEqual(sticker.byte_size, len(bytes(sticker.data)))
        self.assertLessEqual(sticker.byte_size, 100 * 1024)

    def test_sin_imagen_no_se_crea_el_sticker(self):
        from .admin import StickerForm

        form = StickerForm(
            data={"label": "x", "description": "y", "is_active": True, "display_order": 0},
            files={},
        )
        self.assertFalse(form.is_valid())

    def test_el_fondo_opaco_avisa_pero_no_bloquea(self):
        """Un sticker sin transparencia se ve como un cuadro, pero a veces se quiere igual."""
        form = self._form(archivo=self._upload(mode="RGB", color=(255, 255, 255)))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.flat_background, "debió marcar el aviso para el admin")

    def test_un_archivo_roto_da_un_error_de_formulario_y_no_revienta(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .admin import StickerForm

        form = StickerForm(
            data={"label": "x", "description": "y", "is_active": True, "display_order": 0},
            files={"archivo": SimpleUploadedFile("x.png", b"no soy una imagen")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("No se pudo leer la imagen", str(form.errors))

    def test_editar_el_texto_sin_resubir_conserva_la_imagen(self):
        from .admin import StickerForm

        sticker = self._form()
        self.assertTrue(sticker.is_valid(), sticker.errors)
        guardado = sticker.save()
        original = bytes(guardado.data)

        form = StickerForm(
            data={
                "label": "granizado feliz",
                "description": "descripción nueva",
                "is_active": True,
                "display_order": 3,
            },
            files={},
            instance=guardado,
        )
        self.assertTrue(form.is_valid(), form.errors)
        actualizado = form.save()
        self.assertEqual(bytes(actualizado.data), original)
        self.assertEqual(actualizado.description, "descripción nueva")


OWNER_PHONE = "573164277879"


class ModoDuenoTests(TestCase):
    """El dueño escribe desde su WhatsApp para configurar al agente y para probarlo.

    Dos cosas a la vez: manda sus stickers y ajusta el tono, pero sigue siendo
    un cliente más para todo lo que toca dinero.
    """

    def setUp(self):
        self.owner = WhatsAppContact.objects.create(phone=OWNER_PHONE)
        self.cliente = WhatsAppContact.objects.create(phone=PHONE)
        self.turn = TurnContext(phone_number_id=PHONE_NUMBER_ID, message_id="wamid.1")

    def _png(self, size=(300, 300)):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGBA", size, (0, 200, 255, 255)).save(buffer, format="PNG")
        return buffer.getvalue()

    def _tools(self, contact):
        return {t.name: t for t in build_tools(contact, self.turn)}

    # --- quién es el dueño ---

    def test_el_dueno_se_reconoce_aunque_el_numero_llegue_sin_indicativo(self):
        config = AgentSettings.load()
        self.assertTrue(config.is_owner("573164277879"))
        self.assertTrue(config.is_owner("3164277879"))
        self.assertTrue(config.is_owner("+57 316 427 7879"))
        self.assertFalse(config.is_owner(PHONE))
        self.assertFalse(config.is_owner(""))

    def test_quien_oculta_su_numero_nunca_es_el_dueno(self):
        """Un BSUID no tiene dígitos que comparar: no puede heredar el permiso."""
        config = AgentSettings.load()
        config.owner_phones = "573164277879"
        config.save()
        self.assertFalse(config.is_owner("CO.2430294670795328"))

    def test_solo_el_dueno_ve_las_tools_de_configuracion(self):
        propias = {"guardar_sticker", "listar_stickers", "actualizar_sticker",
                   "quitar_sticker", "ajustar_tono"}
        self.assertTrue(propias <= set(self._tools(self.owner)))
        self.assertFalse(propias & set(self._tools(self.cliente)))

    def test_el_dueno_conserva_todas_las_tools_del_cliente(self):
        """Le sirve para probar en real: sus pedidos son pedidos."""
        for name in ("crear_pedido", "cotizar_pedido", "consultar_menu", "verificar_cobertura"):
            self.assertIn(name, self._tools(self.owner))

    def test_el_prompt_del_dueno_solo_sale_para_el(self):
        suyo = build_system_prompt(self.owner, self.turn)
        ajeno = build_system_prompt(self.cliente, self.turn)
        self.assertIn("DUEÑO de Frostbyte", suyo)
        self.assertIn("pedidos DE VERDAD", suyo, "debe seguir tomándole pedidos")
        self.assertNotIn("DUEÑO de Frostbyte", ajeno)
        self.assertNotIn("{", suyo, "quedó un placeholder sin reemplazar")

    def test_sin_numeros_configurados_no_hay_dueno(self):
        config = AgentSettings.load()
        config.owner_phones = ""
        config.save()
        self.assertFalse(config.is_owner(OWNER_PHONE))
        self.assertNotIn("guardar_sticker", self._tools(self.owner))

    # --- guardar un sticker desde el chat ---

    def test_sin_archivo_pendiente_no_inventa_un_sticker(self):
        salida = self._tools(self.owner)["guardar_sticker"].invoke(
            {"nombre": "granizado feliz", "cuando_usarlo": "para saludar"}
        )
        self.assertIn("No tienes ningún archivo", salida)
        self.assertEqual(Sticker.objects.count(), 0)

    def test_una_imagen_del_dueno_se_vuelve_sticker_con_su_momento(self):
        StickerDraft.keep(self.owner, StickerDraft.Kind.IMAGE, self._png(), "image/png")
        salida = self._tools(self.owner)["guardar_sticker"].invoke(
            {"nombre": "granizado feliz", "cuando_usarlo": "para saludar al cliente"}
        )
        sticker = Sticker.objects.get(label="granizado feliz")
        self.assertEqual(sticker.description, "para saludar al cliente")
        self.assertTrue(bytes(sticker.data).startswith(b"RIFF"), "debió quedar en WebP")
        self.assertIn("guardado", salida)
        self.assertFalse(
            StickerDraft.objects.filter(contact=self.owner).exists(),
            "el archivo pendiente se consume al guardarlo",
        )

    def test_guardar_con_un_nombre_que_ya_existe_reemplaza_en_vez_de_duplicar(self):
        StickerDraft.keep(self.owner, StickerDraft.Kind.IMAGE, self._png(), "image/png")
        tools = self._tools(self.owner)
        tools["guardar_sticker"].invoke({"nombre": "saludo", "cuando_usarlo": "para saludar"})
        StickerDraft.keep(self.owner, StickerDraft.Kind.IMAGE, self._png((400, 400)), "image/png")
        salida = self._tools(self.owner)["guardar_sticker"].invoke(
            {"nombre": "Saludo", "cuando_usarlo": "para arrancar la conversación"}
        )
        self.assertEqual(Sticker.objects.filter(label__iexact="saludo").count(), 1)
        self.assertIn("reemplazado", salida)
        self.assertEqual(Sticker.objects.get().description, "para arrancar la conversación")

    def test_un_archivo_nuevo_reemplaza_al_pendiente_anterior(self):
        """Quien manda la foto equivocada manda la buena; vale la última."""
        StickerDraft.keep(self.owner, StickerDraft.Kind.IMAGE, b"vieja", "image/png")
        StickerDraft.keep(self.owner, StickerDraft.Kind.STICKER, self._png(), "image/webp")
        drafts = StickerDraft.objects.filter(contact=self.owner)
        self.assertEqual(drafts.count(), 1)
        self.assertEqual(drafts.first().kind, StickerDraft.Kind.STICKER)

    def test_guardar_sin_nombre_o_sin_momento_pregunta_en_vez_de_guardar(self):
        StickerDraft.keep(self.owner, StickerDraft.Kind.IMAGE, self._png(), "image/png")
        salida = self._tools(self.owner)["guardar_sticker"].invoke(
            {"nombre": "granizado", "cuando_usarlo": "  "}
        )
        self.assertIn("Faltan", salida)
        self.assertEqual(Sticker.objects.count(), 0)
        self.assertTrue(StickerDraft.objects.filter(contact=self.owner).exists())

    def test_un_archivo_ilegible_no_deja_un_sticker_roto_en_el_banco(self):
        StickerDraft.keep(self.owner, StickerDraft.Kind.IMAGE, b"esto no es una imagen")
        salida = self._tools(self.owner)["guardar_sticker"].invoke(
            {"nombre": "x", "cuando_usarlo": "para probar"}
        )
        self.assertIn("No se pudo convertir", salida)
        self.assertEqual(Sticker.objects.count(), 0)

    def test_el_prompt_avisa_del_archivo_pendiente(self):
        self.assertNotIn("ARCHIVO PENDIENTE", build_system_prompt(self.owner, self.turn))
        StickerDraft.keep(self.owner, StickerDraft.Kind.VIDEO, b"x", "video/mp4")
        prompt = build_system_prompt(self.owner, self.turn)
        self.assertIn("ARCHIVO PENDIENTE", prompt)
        self.assertIn("un video", prompt)

    # --- gestionar el banco ---

    def test_listar_actualizar_y_quitar_stickers(self):
        StickerDraft.keep(self.owner, StickerDraft.Kind.IMAGE, self._png(), "image/png")
        tools = self._tools(self.owner)
        tools["guardar_sticker"].invoke({"nombre": "saludo", "cuando_usarlo": "para saludar"})

        self.assertIn("saludo", tools["listar_stickers"].invoke({}))

        tools["actualizar_sticker"].invoke(
            {"nombre": "saludo", "nuevo_nombre": "hola parce", "cuando_usarlo": "al empezar"}
        )
        sticker = Sticker.objects.get()
        self.assertEqual((sticker.label, sticker.description), ("hola parce", "al empezar"))

        tools["quitar_sticker"].invoke({"nombre": "hola parce"})
        sticker.refresh_from_db()
        self.assertFalse(sticker.is_active, "se desactiva, no se borra: es recuperable")
        self.assertIn("[DESACTIVADO]", tools["listar_stickers"].invoke({}))

    def test_actualizar_un_sticker_que_no_existe_no_revienta(self):
        salida = self._tools(self.owner)["actualizar_sticker"].invoke({"nombre": "fantasma"})
        self.assertIn("No existe", salida)

    # --- el tono ---

    def test_el_dueno_cambia_el_tono_y_queda_en_el_prompt_de_los_clientes(self):
        self._tools(self.owner)["ajustar_tono"].invoke(
            {"instrucciones": "Trata a todos de usted."}
        )
        self.assertEqual(AgentSettings.load().tone, "Trata a todos de usted.")
        self.assertIn("Trata a todos de usted.", build_system_prompt(self.cliente, self.turn))

    def test_el_tono_vacio_devuelve_al_agente_a_su_estilo_normal(self):
        config = AgentSettings.load()
        config.tone = "Trata a todos de usted."
        config.save()
        salida = self._tools(self.owner)["ajustar_tono"].invoke({"instrucciones": ""})
        self.assertEqual(AgentSettings.load().tone, "")
        self.assertIn("restablecido", salida)

    def test_configurar_al_agente_marca_el_turno_como_irreversible(self):
        """Rehacer el turno no desharía el sticker guardado ni el tono cambiado."""
        from .agent import MUTATING_TOOLS

        for name in ("guardar_sticker", "actualizar_sticker", "quitar_sticker", "ajustar_tono"):
            self.assertIn(name, MUTATING_TOOLS)


class MediaDelDuenoTests(TestCase):
    """El sticker o el video del dueño se descargan; los del cliente no."""

    def setUp(self):
        self.owner = WhatsAppContact.objects.create(phone=OWNER_PHONE)
        self.cliente = WhatsAppContact.objects.create(phone=PHONE)

    def _msg(self, kind="sticker", caption=""):
        return {
            "text": f"[El cliente envió un(a) {kind} que no puedes ver.]",
            "media": {"kind": kind, "media_id": "mid.1", "caption": caption},
            "message_id": "wamid.1",
        }

    def test_el_sticker_del_dueno_se_guarda_y_no_se_gasta_vision_en_el(self):
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.download_media", return_value=(b"webp", "image/webp")), \
             patch("apps.whatsapp.media.describe_image") as vision:
            texto = _resolve_media(self._msg(), PHONE_NUMBER_ID, self.owner)
        vision.assert_not_called()
        self.assertIn("listo para volverlo sticker", texto)
        draft = StickerDraft.objects.get(contact=self.owner)
        self.assertEqual((draft.kind, bytes(draft.data)), ("sticker", b"webp"))

    def test_el_caption_del_dueno_llega_al_agente(self):
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.download_media", return_value=(b"webp", "image/webp")):
            texto = _resolve_media(self._msg(caption="guárdalo para saludar"), PHONE_NUMBER_ID, self.owner)
        self.assertIn("guárdalo para saludar", texto)

    def test_la_imagen_del_dueno_se_guarda_y_ademas_se_describe(self):
        """Puede ser un sticker por hacer o un comprobante: hacen falta las dos cosas."""
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.download_media", return_value=(b"png", "image/png")), \
             patch("apps.whatsapp.media.describe_image", return_value="un granizado azul"):
            texto = _resolve_media({**self._msg("image")}, PHONE_NUMBER_ID, self.owner)
        self.assertIn("un granizado azul", texto)
        self.assertTrue(StickerDraft.objects.filter(contact=self.owner).exists())

    def test_el_sticker_de_un_cliente_no_se_descarga(self):
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.download_media") as download:
            texto = _resolve_media(self._msg(), PHONE_NUMBER_ID, self.cliente)
        download.assert_not_called()
        self.assertEqual(StickerDraft.objects.count(), 0)
        self.assertIn("no puedes ver", texto)

    def test_si_la_descarga_falla_el_mensaje_sigue_llegando(self):
        """No poder guardar un sticker no puede costar el mensaje que venía con él."""
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.download_media", side_effect=RuntimeError("boom")):
            texto = _resolve_media(self._msg(), PHONE_NUMBER_ID, self.owner)
        self.assertEqual(StickerDraft.objects.count(), 0)
        self.assertIn("no puedes ver", texto)

    def test_un_archivo_gigante_se_descarta(self):
        from .worker import MAX_DRAFT_BYTES, _resolve_media

        grande = b"x" * (MAX_DRAFT_BYTES + 1)
        with patch("apps.whatsapp.media.download_media", return_value=(grande, "video/mp4")):
            _resolve_media(self._msg("video"), PHONE_NUMBER_ID, self.owner)
        self.assertEqual(StickerDraft.objects.count(), 0)

    def test_el_webhook_de_un_sticker_trae_su_media_id(self):
        """Sin esto no hay nada que descargar después."""
        from .worker import extract_inbound_messages

        payload = {
            "type": "whatsapp.message.received",
            "data": [
                {
                    "phone_number_id": PHONE_NUMBER_ID,
                    "conversation": {"phone_number": OWNER_PHONE},
                    "message": {
                        "id": "wamid.9",
                        "from": OWNER_PHONE,
                        "type": "sticker",
                        "sticker": {"id": "mid.9"},
                        "kapso": {"direction": "inbound"},
                    },
                }
            ],
        }
        mensajes = extract_inbound_messages(payload)
        self.assertEqual(mensajes[0]["media"], {"kind": "sticker", "media_id": "mid.9", "caption": ""})


class VideoASlickerTests(TestCase):
    """La conversión de video necesita ffmpeg; sin él hay que decirlo, no fallar raro."""

    def test_sin_ffmpeg_el_error_le_dice_al_dueno_qué_hacer(self):
        with patch("apps.whatsapp.stickers._ffmpeg_binary", return_value=None):
            with self.assertRaises(StickerError) as error:
                from .stickers import from_video

                from_video(b"video")
        self.assertIn("imagen o el GIF", str(error.exception))

    def test_un_video_de_verdad_sale_como_sticker_animado(self):
        """Se genera con el propio ffmpeg y se convierte, de punta a punta."""
        import subprocess
        from io import BytesIO

        from PIL import Image

        from .stickers import MAX_ANIMATED_BYTES, _ffmpeg_binary, from_video

        binary = _ffmpeg_binary()
        if not binary:
            self.skipTest("no hay ffmpeg en este entorno")
        hecho = subprocess.run(
            [binary, "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
             "-pix_fmt", "yuv420p", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", "pipe:1"],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(hecho.returncode, 0, hecho.stderr[-300:])

        data, animated = from_video(hecho.stdout)
        self.assertTrue(animated, "un video de 2 s debe quedar animado")
        self.assertLessEqual(len(data), MAX_ANIMATED_BYTES)
        with Image.open(BytesIO(data)) as out:
            self.assertEqual(out.size, (512, 512))
            self.assertEqual(out.format, "WEBP")
            self.assertGreater(out.n_frames, 1)

    def test_el_video_entra_al_banco_por_la_tool_del_dueno(self):
        import subprocess

        from .stickers import _ffmpeg_binary

        binary = _ffmpeg_binary()
        if not binary:
            self.skipTest("no hay ffmpeg en este entorno")
        owner = WhatsAppContact.objects.create(phone=OWNER_PHONE)
        hecho = subprocess.run(
            [binary, "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
             "-pix_fmt", "yuv420p", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", "pipe:1"],
            capture_output=True,
            timeout=60,
        )
        StickerDraft.keep(owner, StickerDraft.Kind.VIDEO, hecho.stdout, "video/mp4")
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(owner, turn) if t.name == "guardar_sticker")
        salida = tool.invoke({"nombre": "bailecito", "cuando_usarlo": "para celebrar"})
        self.assertIn("guardado", salida)
        self.assertTrue(Sticker.objects.get(label="bailecito").is_animated)


class LimpiezaDeArchivosPendientesTests(TestCase):
    """Un archivo que nadie llegó a nombrar no se queda ocupando megas para siempre."""

    def test_los_archivos_viejos_se_borran_y_los_de_hoy_no(self):
        from datetime import timedelta

        from django.utils import timezone

        from .worker import DRAFT_TTL

        owner = WhatsAppContact.objects.create(phone=OWNER_PHONE)
        otro = WhatsAppContact.objects.create(phone=PHONE)
        viejo = StickerDraft.keep(owner, StickerDraft.Kind.IMAGE, b"x")
        StickerDraft.objects.filter(pk=viejo.pk).update(
            created_at=timezone.now() - DRAFT_TTL - timedelta(minutes=1)
        )
        StickerDraft.keep(otro, StickerDraft.Kind.IMAGE, b"y")

        StickerDraft.objects.filter(created_at__lt=timezone.now() - DRAFT_TTL).delete()
        self.assertEqual([d.contact_id for d in StickerDraft.objects.all()], [otro.pk])


class ModuloDeConfiguracionEnElPanelTests(TestCase):
    """El mismo agente, configurado desde la app en vez del admin de Django.

    Lo que se prueba aquí no es la configuración (ya tiene sus tests) sino la
    puerta: quién puede entrar y qué pasa con un archivo que llega del celular.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        User = get_user_model()
        self.api = APIClient()
        self.admin = User.objects.create(username="dueno", email="d@x.com", role="admin")
        self.empleado = User.objects.create(username="mesero", email="m@x.com", role="employee")

    def _upload(self, mode="RGBA", color=(255, 0, 0, 255), fmt="PNG", name="sticker.png"):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = BytesIO()
        Image.new(mode, (300, 200), color[: 3 if mode == "RGB" else 4]).save(buffer, format=fmt)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_sin_sesion_no_se_ve_la_configuracion(self):
        self.assertIn(
            self.api.get("/api/v1/whatsapp/agent-settings/").status_code, (401, 403)
        )

    def test_un_empleado_no_puede_cambiar_como_habla_el_negocio(self):
        self.api.force_authenticate(self.empleado)
        self.assertEqual(self.api.get("/api/v1/whatsapp/agent-settings/").status_code, 403)
        self.assertEqual(self.api.get("/api/v1/whatsapp/stickers/").status_code, 403)

    def test_el_dueno_lee_y_edita_la_configuracion(self):
        self.api.force_authenticate(self.admin)
        resp = self.api.get("/api/v1/whatsapp/agent-settings/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["agent_name"], "Frosty")

        resp = self.api.patch(
            "/api/v1/whatsapp/agent-settings/",
            {"tone": "trata al cliente de usted", "stickers_enabled": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        config = AgentSettings.load()
        self.assertEqual(config.tone, "trata al cliente de usted")
        self.assertFalse(config.stickers_enabled)

    def test_el_catalogo_de_tonos_viaja_con_la_configuracion(self):
        """La pantalla no repite los textos de los tonos: los recibe de aquí."""
        self.api.force_authenticate(self.admin)
        resp = self.api.get("/api/v1/whatsapp/agent-settings/")
        self.assertEqual(resp.data["tone_preset"], "parcero")
        claves = [preset["key"] for preset in resp.data["tone_presets"]]
        self.assertEqual(claves, ["parcero", "cercano", "serio", "directo"])
        self.assertTrue(all(p["sample"] for p in resp.data["tone_presets"]))
        self.assertNotIn(
            "persona", resp.data["tone_presets"][0], "el prompt no sale a la pantalla"
        )

    def test_el_dueno_cambia_el_tono_desde_la_app(self):
        self.api.force_authenticate(self.admin)
        resp = self.api.patch(
            "/api/v1/whatsapp/agent-settings/", {"tone_preset": "serio"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AgentSettings.load().tone_preset, "serio")

    def test_un_tono_inventado_se_rechaza(self):
        self.api.force_authenticate(self.admin)
        resp = self.api.patch(
            "/api/v1/whatsapp/agent-settings/", {"tone_preset": "pirata"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_el_numero_del_dueno_se_guarda_en_digitos_aunque_se_escriba_bonito(self):
        """En el celular el número sale con espacios y con +; así pegado no lo reconocería."""
        self.api.force_authenticate(self.admin)
        resp = self.api.patch(
            "/api/v1/whatsapp/agent-settings/",
            {"owner_phones": "+57 316 427 7879, 573001112233"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["owner_phones"], "573164277879,573001112233")
        self.assertTrue(AgentSettings.load().is_owner("3164277879"))

    def test_un_numero_incompleto_se_rechaza_con_un_mensaje_util(self):
        self.api.force_authenticate(self.admin)
        resp = self.api.patch(
            "/api/v1/whatsapp/agent-settings/", {"owner_phones": "3164"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("indicativo", str(resp.data))

    def test_subir_una_imagen_desde_el_panel_deja_un_webp_listo_para_whatsapp(self):
        self.api.force_authenticate(self.admin)
        resp = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {
                "label": "Granizado Feliz",
                "description": "para saludar al cliente",
                "archivo": self._upload(),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        sticker = Sticker.objects.get(label="granizado feliz")
        self.assertTrue(bytes(sticker.data).startswith(b"RIFF"), "no salió un WebP")
        self.assertLessEqual(sticker.byte_size, 100 * 1024)
        self.assertTrue(resp.data["preview"].startswith("data:image/webp;base64,"))

    def test_el_fondo_opaco_avisa_en_la_respuesta_pero_guarda_igual(self):
        self.api.force_authenticate(self.admin)
        resp = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {
                "label": "pulgar arriba",
                "description": "para cerrar un acuerdo",
                "archivo": self._upload(mode="RGB", color=(255, 255, 255)),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn("transparente", resp.data["warning"])
        self.assertTrue(Sticker.objects.filter(label="pulgar arriba").exists())

    def test_un_archivo_roto_no_revienta_y_explica_el_problema(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.api.force_authenticate(self.admin)
        resp = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {
                "label": "x",
                "description": "y",
                "archivo": SimpleUploadedFile("x.png", b"no soy una imagen"),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No se pudo leer la imagen", str(resp.data))
        self.assertFalse(Sticker.objects.exists())

    def test_sin_archivo_no_se_crea_el_sticker(self):
        self.api.force_authenticate(self.admin)
        resp = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {"label": "x", "description": "cuando sea"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Sticker.objects.exists())

    def test_editar_el_texto_sin_resubir_conserva_la_imagen(self):
        self.api.force_authenticate(self.admin)
        creado = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {"label": "moto", "description": "cuando sale el pedido", "archivo": self._upload()},
            format="multipart",
        )
        original = bytes(Sticker.objects.get(pk=creado.data["id"]).data)

        resp = self.api.patch(
            f"/api/v1/whatsapp/stickers/{creado.data['id']}/",
            {"description": "cuando el domiciliario ya salió", "is_active": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        sticker = Sticker.objects.get(pk=creado.data["id"])
        self.assertEqual(bytes(sticker.data), original)
        self.assertFalse(sticker.is_active)
        self.assertNotIn("warning", resp.data)

    def test_desactivar_un_sticker_lo_saca_del_banco_que_ve_el_agente(self):
        self.api.force_authenticate(self.admin)
        creado = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {"label": "triste", "description": "cuando algo no se pudo", "archivo": self._upload()},
            format="multipart",
        )
        self.assertEqual(len(Sticker.catalog()), 1)
        self.api.patch(
            f"/api/v1/whatsapp/stickers/{creado.data['id']}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(Sticker.catalog(), [])

    def test_borrar_un_sticker_lo_saca_del_banco(self):
        self.api.force_authenticate(self.admin)
        creado = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {"label": "brindis", "description": "para celebrar", "archivo": self._upload()},
            format="multipart",
        )
        resp = self.api.delete(f"/api/v1/whatsapp/stickers/{creado.data['id']}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Sticker.objects.exists())

    def test_la_lista_llega_completa_sin_paginar(self):
        """El banco es corto y la pantalla lo pinta entero: paginarlo escondería stickers."""
        self.api.force_authenticate(self.admin)
        for i in range(3):
            self.api.post(
                "/api/v1/whatsapp/stickers/",
                {"label": f"s{i}", "description": "cuando sea", "archivo": self._upload()},
                format="multipart",
            )
        resp = self.api.get("/api/v1/whatsapp/stickers/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 3)

    def test_el_endpoint_publico_del_archivo_sigue_abierto_para_meta(self):
        """Los servidores de Meta lo piden sin token: protegerlo rompería el envío."""
        self.api.force_authenticate(self.admin)
        creado = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {"label": "hola", "description": "para saludar", "archivo": self._upload()},
            format="multipart",
        )
        anonimo = self.client.get(f"/api/v1/whatsapp/stickers/{creado.data['id']}.webp")
        self.assertEqual(anonimo.status_code, 200)
        self.assertEqual(anonimo["Content-Type"], "image/webp")

    def test_reemplazar_la_imagen_no_apaga_el_sticker(self):
        """Subir la imagen buena encima es lo que hace quien se equivocó de archivo.

        Va en multipart por el archivo, y DRF lee un booleano ausente en un
        formulario como `False`: sin cuidado, corregir el dibujo dejaba el
        sticker desactivado sin que nadie lo pidiera.
        """
        self.api.force_authenticate(self.admin)
        creado = self.api.post(
            "/api/v1/whatsapp/stickers/",
            {"label": "brindis", "description": "para celebrar", "archivo": self._upload()},
            format="multipart",
        )
        original = bytes(Sticker.objects.get(pk=creado.data["id"]).data)

        resp = self.api.patch(
            f"/api/v1/whatsapp/stickers/{creado.data['id']}/",
            {"archivo": self._upload(color=(0, 128, 255, 255))},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        sticker = Sticker.objects.get(pk=creado.data["id"])
        self.assertNotEqual(bytes(sticker.data), original, "no se reemplazó la imagen")
        self.assertEqual(sticker.byte_size, len(bytes(sticker.data)))
        self.assertTrue(sticker.is_active, "un cambio de imagen no debe apagarlo")
        self.assertEqual(sticker.label, "brindis")
