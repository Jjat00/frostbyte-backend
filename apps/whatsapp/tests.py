"""Pruebas del agente de WhatsApp reproduciendo chats reales.

Los casos vienen de conversaciones que salieron mal en producción: cada test
lleva la fecha del chat que reprodujo. El LLM se sustituye por un doble (no se
prueba qué contesta el modelo, sino cuándo y cuántas veces lo llamamos).
"""

import threading
import time
from unittest.mock import patch

from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)

from .agent import AgentTurn, build_system_prompt
from .models import ChatMessage, WebhookEvent, WhatsAppContact
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

        def _run(contact, text):
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


class PromptTests(SimpleTestCase):
    """El prompt se arma con los datos de configuración, sin placeholders sueltos."""

    def test_lleva_el_numero_al_que_remitir_cuando_no_sabe(self):
        with override_settings(WHATSAPP_CONTACT_PHONE="3009998877"):
            prompt = build_system_prompt()
        self.assertIn("3009998877", prompt)
        self.assertNotIn("{", prompt, "quedó un placeholder sin reemplazar")


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
