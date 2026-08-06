"""Pruebas del agente de WhatsApp reproduciendo chats reales.

Los casos vienen de conversaciones que salieron mal en producción: cada test
lleva la fecha del chat que reprodujo. El LLM se sustituye por un doble (no se
prueba qué contesta el modelo, sino cuándo y cuántas veces lo llamamos).
"""

import threading
import time
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings

from .agent import AgentTurn
from .models import WebhookEvent, WhatsAppContact
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


def webhook_payload(text, sequence=1, message_id=None):
    """Un webhook de Kapso con buffering activo (siempre formato batch)."""
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
                "message": {
                    "id": message_id or f"wamid.test{sequence}",
                    "from": PHONE,
                    "type": "text",
                    "text": {"body": text},
                    "kapso": {"direction": "inbound"},
                },
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
