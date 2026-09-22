"""Pruebas del agente de WhatsApp reproduciendo chats reales.

Los casos vienen de conversaciones que salieron mal en producción: cada test
lleva la fecha del chat que reprodujo. El LLM se sustituye por un doble (no se
prueba qué contesta el modelo, sino cuándo y cuántas veces lo llamamos).
"""

import datetime
import os
import threading
import time
import unittest
from io import StringIO
from unittest.mock import Mock, patch

from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
)

from django.utils import timezone

from decimal import Decimal

from . import missing
from . import mood
from . import worker
from . import banned
from . import agent as agent_mod
from .agent import AgentTurn, _for_whatsapp, _split_messages, build_system_prompt
from .mood import StickerUrge
from . import stickers as wa_stickers
from .stickers import StickerError, has_transparency, normalize
from .tools import TurnContext, build_tools
from apps.orders.models import Order

from .models import (
    STICKER_MEMORY,
    AgentSettings,
    AgentTone,
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


def webhook_payload(
    text, sequence=1, message_id=None, quoted_wamid=None, msg_type="text", media_id=None
):
    """Un webhook de Kapso con buffering activo (siempre formato batch)."""
    message = {
        "id": message_id or f"wamid.test{sequence}",
        "from": PHONE,
        "type": msg_type,
        "text": {"body": text},
        "context": {"id": quoted_wamid, "from": PHONE} if quoted_wamid else None,
        "kapso": {"direction": "inbound"},
    }
    if media_id:
        message[msg_type] = {"id": media_id}
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

        def _run(
            contact,
            text,
            phone_number_id="",
            message_id="",
            customer_sticker=False,
            silence_ok=False,
        ):
            self.turns.append(text)
            if on_call:
                on_call()
            if delay:
                time.sleep(delay)
            return AgentTurn(
                replies=(reply,) if reply else (), message_ids=("m1", "m2"), mutated=mutated
            )

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
        """Elegir "serio" no puede dejar dentro al de la chispa: se contradirían."""
        config = AgentSettings.load()
        config.tone_preset = "serio"
        config.save()
        prompt = build_system_prompt()
        self.assertIn("USTED siempre", prompt)
        self.assertNotIn("amigo del pueblo", prompt)

    def test_el_tono_por_defecto_es_el_de_siempre(self):
        self.assertIn("amigo del pueblo", build_system_prompt())

    def test_ningun_tono_de_fabrica_le_enseña_a_decir_parce(self):
        """Pedido de Jaime (08/09): «parce» no se dice en todo el país, y en
        Cumbal marca a un forastero. El tono sigue siendo colombiano; la jerga
        que lo delataba como paisa se fue del texto de fábrica."""
        from .tones import SEED_TONES

        for tono in SEED_TONES:
            for campo in ("name", "description", "sample", "persona"):
                self.assertNotIn("parce", tono[campo].lower(), f"{tono['key']}.{campo}")

    def test_el_prompt_le_prohibe_la_jerga_de_una_sola_region(self):
        self.assertIn("Nada de jerga que sea de una sola región", build_system_prompt())

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
        self.assertIn("créalo sin ubicación", respuesta, "a la segunda, el pedido igual")

    def test_sin_ubicacion_la_tool_no_frena_el_pedido(self):
        """Chat real 06/09 (Estefa): la ubicación no llegó y el chat se murió.

        La tool decía "sin ella no se puede crear el pedido", así que el agente
        se quedaba esperando un mensaje que WhatsApp nunca iba a entregarle.
        """
        with patch("apps.whatsapp.tools.kapso.recent_undelivered", return_value=[]):
            respuesta = self._verificar_cobertura()
        self.assertNotIn("no se puede crear el pedido", respuesta)
        self.assertIn("el pedido se crea igual", respuesta)


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

    def test_lo_agotado_no_se_niega_como_si_no_existiera(self):
        """Chat real 2026-09-14: la clienta mandó una foto de la carta con las
        salchipapas y el agente contestó "por ahora no tenemos salchipapas
        disponibles en la carta". Ese día la categoría estaba apagada porque se
        acabó la comida, y ella lo leyó como que no vendemos eso. Se fue."""
        self.salchipapas.is_active = False
        self.salchipapas.save()
        resultado = self._buscar("una salchipapa con queso")
        self.assertIn("HOY no está disponible", resultado)
        self.assertIn("NUNCA que no lo vendemos", resultado)
        self.assertIn("Salchipapa con Queso", resultado)

    def test_lo_agotado_ofrece_lo_que_si_hay_hoy(self):
        self.salchipapas.is_active = False
        self.salchipapas.save()
        self.assertIn("Granizado de Mora", self._buscar("salchipapa"))

    def test_un_typo_que_las_letras_no_alcanzan_lo_resuelve_el_modelo(self):
        """Chat real 2026-09-12: "Tiene pesera??" -> "No, pesera tampoco
        tenemos". La Pecera granizada estaba activa a $30.000 y se perdió la
        venta: "pesera" y "pecera" se parecen 0,833 contra un umbral de 0,85.
        La tool no adivina por él; le entrega lo que hay y el modelo decide."""
        resultado = self._buscar("tiene pesera")
        self.assertIn("Sin coincidencias literales", resultado)
        self.assertIn("Granizado de Mora", resultado)  # el catálogo de hoy, para que elija
        self.assertIn("búscalo otra vez con ESE nombre", resultado)

    def test_el_catalogo_que_se_le_da_al_modelo_no_lleva_precios(self):
        """Es material para que decida, no para volcarlo al chat."""
        resultado = self._buscar("tienen hamburguesas")
        self.assertNotIn("$", resultado)
        self.assertNotIn("[variante_id=", resultado)


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

    def test_recoger_no_se_puede_apagar(self):
        """Jaime (15/09): "siempre que el local esté abierto es obvio que pueden
        pasar a recoger". El interruptor sobraba; lo que se apaga es el
        domicilio, que es el que cuesta un domiciliario."""
        self.assertIn("PEDIDO CREADO", self._crear(para_recoger=True))
        from apps.orders.models import StoreSettings

        self.assertFalse(hasattr(StoreSettings.load(), "pickup_enabled"))

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

    def test_el_domicilio_sin_metodo_de_pago_se_crea_y_lo_deja_pendiente(self):
        """El pago se pregunta, pero no vale un pedido: lo cuadra el equipo."""
        from apps.orders.models import Order

        self.cfg.customer_ordering_enabled = True
        self.cfg.save()
        resultado = self._crear(metodo_pago="", direccion="Carrera 11 #21-17")
        self.assertIn("PEDIDO CREADO", resultado)
        self.assertIn("el método de pago", resultado)
        order = Order.objects.get()
        self.assertEqual(order.payment_method, "")
        self.assertIn("el método de pago", order.customer_notes)

    def test_el_prompt_no_pregunta_nada_para_recoger_pero_si_confirma(self):
        prompt = build_system_prompt()
        self.assertIn("NO preguntes método de pago, celular, dirección ni ubicación", prompt)
        self.assertIn("Solo para domicilio: pregunta el método de pago", prompt)
        # Regla de Jaime: items + total, el cliente confirma, y solo ahí se crea
        self.assertIn("muestra items y TOTAL, y espera su", prompt)
        self.assertIn("confirma y espera su respuesta", prompt)
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
        textos.append(self._crear(para_recoger=True))
        for texto in textos:
            self.assertNotIn("pausad", texto.lower())
        self.assertIn("justo en este momento no hay servicio de domicilios", textos[1])

    def test_el_prompt_prohibe_decir_pausado(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("justo en este momento no tenemos servicio de domicilios", SYSTEM_PROMPT)
        self.assertIn("NUNCA digas al cliente que un servicio está \"pausado\"", SYSTEM_PROMPT)


class PreguntasDeMasTests(TestCase):
    """Chats reales del 18-09 revisados por Jaime: el agente gastaba turnos
    preguntando lo que el cliente acababa de decir.

    A las 19:32 Natt pidió "un granizado especial con alcohol" y, tras elegir
    "para uno por favor", el agente contestó "¿Te preparo uno?" y el chat quedó
    ocho minutos parado. A las 18:56 Alexander dijo "de blue berry el tamaño grande" y
    recibió "¿Te preparo uno grande?"; a las 21:57 Johana pidió "de 14 con un
    poco de Tajín" y recibió "¿Te preparo uno?" en vez de la pregunta que
    faltaba (domicilio o recogida). El 06-09 Anyi dijo "Ok" a la cotización y el
    agente le pidió "¿me confirmas con un sí?"; el 18-09 Sofía mandó el
    comprobante Nequi por los $26.000 exactos y aun así le preguntaron
    "¿confirmas que creemos el pedido?".
    """

    def test_la_cantidad_dicha_no_se_vuelve_a_preguntar(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("La cantidad es otra cosa: NO la preguntes si ya está dicha", SYSTEM_PROMPT)
        self.assertIn("un pedido sin número es de UNO", SYSTEM_PROMPT)
        self.assertNotIn("Confirma también la cantidad", SYSTEM_PROMPT)

    def test_el_prompt_manda_mirar_el_chat_antes_de_preguntar(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("ANTES DE CADA PREGUNTA MIRA LO QUE YA TE DIJO", SYSTEM_PROMPT)
        self.assertIn("ni disfrazado de confirmación", SYSTEM_PROMPT)

    def test_un_ok_ya_es_un_si(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("Vale CUALQUIER afirmación clara", SYSTEM_PROMPT)
        self.assertIn('un "ok" ya es un sí', SYSTEM_PROMPT)

    def test_el_comprobante_del_total_confirma_el_pedido(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("manda el comprobante del pago por el total que", SYSTEM_PROMPT)
        self.assertIn("creas el pedido, no le preguntas si lo", SYSTEM_PROMPT)

    def test_sigue_haciendo_falta_una_confirmacion(self):
        """Aflojar qué cuenta como sí no es crear el pedido sin que confirme."""
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("Lo único que no confirma es el silencio", SYSTEM_PROMPT)
        self.assertIn("un pedido existe SOLO cuando crear_pedido responde", SYSTEM_PROMPT)


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
        self.cfg.save()
        estado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("Local ABIERTO", estado)
        self.assertIn("Puedes tomar pedidos PARA RECOGER: sí", estado)
        self.assertNotIn("LOCAL CERRADO", estado)

    def test_abierto_sin_domicilios_sigue_ofreciendo_recoger(self):
        """Abierto es abierto: el cliente puede venir por su pedido."""
        self.cfg.is_open = True
        self.cfg.save()  # los domicilios siguen apagados
        estado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("Puedes tomar pedidos PARA RECOGER: sí", estado)
        self.assertIn("ofrécele encargar", estado)
        self.assertNotIn("ningún canal", estado)

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

    def test_el_prompt_ordena_local_y_despues_domicilio(self):
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertIn("PRIMERO si el local está abierto o cerrado", SYSTEM_PROMPT)
        self.assertIn("Con el local CERRADO se acabó la conversación de pedidos", SYSTEM_PROMPT)
        self.assertIn("Con el local ABIERTO siempre se puede pasar a recoger", SYSTEM_PROMPT)

    def test_el_prompt_no_conoce_un_recoger_apagado(self):
        """Ya no hay tal estado: si estamos abiertos, se puede recoger."""
        from apps.whatsapp.agent import SYSTEM_PROMPT

        self.assertNotIn("no estamos recibiendo pedidos para recoger", SYSTEM_PROMPT)


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


def app_reply_payload(text, wamid="wamid.app1", sent_at=None):
    """Saliente desde la app de WhatsApp Business a un cliente sin número.

    `sent_at` es cuándo se escribió (datetime); por defecto, ahora mismo.
    """
    momento = sent_at or timezone.now()
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
                    "timestamp": str(int(momento.timestamp())),
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
        # TESTING=False a propósito: esta prueba es justamente sobre el payload
        # que sale, con requests parcheado (ver kapso._post_message).
        with override_settings(KAPSO_API_KEY="clave", TESTING=False):
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
    Solo en domicilios: quien pasa a recoger viene al local.

    Se pide, no se exige (08/09): si el cliente no lo da —o dice que está
    pendiente del chat— el pedido entra igual y el celular queda como
    pendiente. Ningún dato vale un pedido."""

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

    def test_sin_celular_el_domicilio_se_crea_con_el_celular_pendiente(self):
        """El celular se pide, pero no a cambio del pedido: lo pide el equipo."""
        resultado = self._crear()
        self.assertIn("PEDIDO CREADO", resultado)
        self.assertIn("el celular de contacto", resultado)
        order = Order.objects.get()
        self.assertEqual(order.customer_phone, BSUID, "sin celular queda su identidad")
        self.assertIn("el celular de contacto", order.customer_notes)

    def test_con_celular_no_queda_ningun_pendiente(self):
        resultado = self._crear(telefono_contacto="3001234567")
        self.assertIn("PEDIDO CREADO", resultado)
        self.assertNotIn("pendientes", resultado)
        order = Order.objects.get()
        self.assertEqual(order.customer_phone, "573001234567")
        self.assertFalse(order.customer_notes.startswith(missing.PREFIX))

    def test_el_prompt_le_pide_el_celular_pero_no_a_cambio_del_pedido(self):
        prompt = build_system_prompt(self.contact)
        self.assertIn("pídele un celular de contacto de 10 dígitos", prompt)
        self.assertIn("crea el pedido igual", prompt)
        self.assertNotIn("sin ese celular no se crea el domicilio", prompt)

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


class DomicilioConUbicacionSinDireccionTests(TestCase):
    """Chat real 2026-09-04 (Lina): compartió su ubicación y el agente le
    respondió "Ubicación lista 🙌 ¿Me pasas la dirección escrita exacta?".

    Con las coordenadas ya registradas el domiciliario llega con el mapa: la
    dirección escrita era una pregunta de más en el camino a la venta. Ahora es
    opcional y solo se guarda si el cliente la da por su cuenta.
    """

    def setUp(self):
        from apps.business.models import Business
        from apps.orders.models import StoreSettings
        from apps.products.models import Category, Product, ProductVariant
        from django.conf import settings as dj_settings

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
        cfg.customer_ordering_enabled = True
        cfg.delivery_fee = 2000
        cfg.save()

        # Ubicación compartida en el propio local: dentro de la zona seguro
        self.contact = WhatsAppContact.objects.create(
            phone=PHONE,
            last_location_lat=dj_settings.DELIVERY_CENTER_LAT,
            last_location_lng=dj_settings.DELIVERY_CENTER_LNG,
            last_location_at=timezone.now(),
        )
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def _crear(self, **kwargs):
        datos = {
            "items": [{"variante_id": self.variante.id, "cantidad": 1, "notas": ""}],
            "nombre_cliente": "Lina Erazo",
            "metodo_pago": "cash",
            "paga_con": "100000",
        }
        datos.update(kwargs)
        return self.tools["crear_pedido"].invoke(datos)

    def test_con_ubicacion_el_domicilio_se_crea_sin_direccion_escrita(self):
        resultado = self._crear()
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertEqual(order.order_type, Order.OrderType.DELIVERY)
        self.assertEqual(order.delivery_address, "")
        self.assertIsNotNone(order.delivery_lat, "el domiciliario llega con el mapa")
        self.assertIsNotNone(order.delivery_lng)

    def test_la_direccion_que_el_cliente_escribe_por_su_cuenta_se_guarda(self):
        self.assertIn("PEDIDO CREADO", self._crear(direccion="Transversal 4 #13-80"))
        self.assertEqual(Order.objects.get().delivery_address, "Transversal 4 #13-80")

    def test_un_pedido_sin_direccion_no_borra_la_habitual(self):
        self.contact.default_address = "Transversal 4 #13-80"
        self.contact.default_reference = "Asadero de cuyes"
        self.contact.save()
        self.assertIn("PEDIDO CREADO", self._crear())
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.default_address, "Transversal 4 #13-80")
        self.assertEqual(self.contact.default_reference, "Asadero de cuyes")

    def test_sin_ubicacion_el_pedido_se_crea_igual_y_queda_anotada(self):
        """Chat real 06/09 (Estefa): la ubicación se perdió y el pedido también.

        WhatsApp no nos entregó el mensaje (error 131060) y la tool rechazaba
        el pedido, así que la conversación se quedó parada en la ubicación. Un
        dato que falta cuesta una pregunta; el pedido que no existe, la venta.
        """
        self.contact.last_location_lat = None
        self.contact.last_location_lng = None
        self.contact.save()
        resultado = self._crear()
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertIsNone(order.delivery_lat)
        self.assertTrue(order.customer_notes.startswith(missing.PREFIX))
        self.assertIn(missing.LOCATION, order.customer_notes)

    def test_el_prompt_ya_no_le_manda_pedir_la_direccion_escrita(self):
        prompt = build_system_prompt(self.contact)
        self.assertIn("NO le pidas la dirección escrita", prompt)
        self.assertNotIn("dirección escrita EXACTA", prompt)


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

    def test_cuanto_nos_demoramos_sale_de_la_configuracion_de_la_tienda(self):
        """La demora cambia con el local, no con un despliegue."""
        from apps.orders.models import StoreSettings

        self.assertIn("de 10 a 20 minutos", build_system_prompt(self.contact))
        cfg = StoreSettings.load()
        cfg.eta_min_minutes = 25
        cfg.eta_max_minutes = 40
        cfg.save()
        prompt = build_system_prompt(self.contact)
        self.assertIn("de 25 a 40 minutos", prompt)
        self.assertNotIn("de 10 a 20 minutos", prompt)

    def test_la_voz_se_recuerda_al_final_del_prompt_con_una_muestra(self):
        """Entre QUIÉN ERES y el mensaje hay páginas de reglas: sin recordatorio se pierde."""
        AgentTone.seed_catalog()
        prompt = build_system_prompt(self.contact)
        persona = AgentSettings.load().persona()
        self.assertEqual(prompt.count(persona), 2, "la personalidad se dice al empezar y al final")
        self.assertIn("TU VOZ", prompt)
        self.assertIn(AgentTone.objects.get(key="parcero").sample, prompt)
        self.assertLess(
            prompt.index("CÓMO ESCRIBES"), prompt.index("TU VOZ"), "el recordatorio va de últimas"
        )

    def test_un_tono_sin_frase_de_muestra_no_deja_hueco_en_el_prompt(self):
        AgentTone.objects.create(key="mudo", name="Mudo", persona="QUIÉN ERES: alguien.", sample="")
        config = AgentSettings.load()
        config.tone_preset = "mudo"
        config.save()
        prompt = build_system_prompt(self.contact)
        self.assertNotIn("Así suena un saludo tuyo", prompt)

    def test_el_sticker_queda_pendiente_y_sale_detras_del_texto(self):
        """El modelo elige el sticker antes de escribir: mandarlo ya lo pondría delante."""
        self._sticker()
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        with patch("apps.whatsapp.kapso.send_sticker") as send:
            salida = tool.invoke({"nombre": "granizado feliz"})
        send.assert_not_called()
        self.assertEqual(turn.sticker.label, "granizado feliz")
        self.assertTrue(turn.answered)
        self.assertFalse(turn.posted, "todavía no ha salido nada: el turno se puede rehacer")
        self.assertIn("al final de este turno", salida)
        self.assertEqual(Sticker.objects.get(label="granizado feliz").sent_count, 0)

    def test_el_sticker_se_entrega_despues_del_texto_y_se_apunta(self):
        """La memoria corta cuenta lo que el cliente vio, no lo que el modelo pidió."""
        sticker = self._sticker()
        with patch("apps.whatsapp.kapso.send_sticker", return_value={"ok": True}) as send:
            self.assertTrue(wa_stickers.deliver(self.contact, sticker, PHONE_NUMBER_ID))
        self.assertEqual(send.call_args.args[1], PHONE)
        self.assertEqual(Sticker.objects.get(label="granizado feliz").sent_count, 1)
        self.contact.refresh_from_db()
        self.assertEqual(
            [label for label, _ in self.contact.stickers_today()], ["granizado feliz"]
        )

    def test_si_kapso_rechaza_el_sticker_no_cuenta_como_enviado(self):
        """Un sticker que no llegó no puede gastar el cupo del día ni el enfriamiento."""
        sticker = self._sticker()
        with patch("apps.whatsapp.kapso.send_sticker", return_value=None):
            self.assertFalse(wa_stickers.deliver(self.contact, sticker, PHONE_NUMBER_ID))
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.stickers_today(), [])
        self.assertEqual(Sticker.objects.get(label="granizado feliz").sent_count, 0)

    def test_pedir_un_sticker_inventado_devuelve_los_que_existen(self):
        """El modelo inventa nombres; darle la lista cuesta menos que un turno perdido."""
        self._sticker()
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        with patch("apps.whatsapp.kapso.send_sticker") as send:
            salida = tool.invoke({"nombre": "gato bailando"})
        send.assert_not_called()
        self.assertIsNone(turn.sticker)
        self.assertIn("granizado feliz", salida)

    def test_el_sticker_se_encuentra_aunque_el_modelo_cambie_tildes_o_mayusculas(self):
        self._sticker(label="corazón frío")
        turn = TurnContext(phone_number_id=PHONE_NUMBER_ID)
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        tool.invoke({"nombre": "Corazon Frio"})
        self.assertEqual(turn.sticker.label, "corazón frío")

    def test_el_turno_sin_sticker_no_manda_aunque_el_modelo_lo_pida(self):
        """El 'a veces no' es del sistema: si dependiera del prompt sería un 'casi nunca no'."""
        self._sticker()
        turn = TurnContext(
            phone_number_id=PHONE_NUMBER_ID, sticker_urge=StickerUrge(False, mood.NO_URGE)
        )
        tool = next(t for t in build_tools(self.contact, turn) if t.name == "enviar_sticker")
        with patch("apps.whatsapp.kapso.send_sticker") as send:
            salida = tool.invoke({"nombre": "granizado feliz"})
        send.assert_not_called()
        self.assertIsNone(turn.sticker)
        self.assertIn("texto", salida.lower())
        self.assertEqual(self.contact.stickers_today(), [])

    def test_el_pulso_del_turno_llega_al_prompt_y_nombra_el_ultimo(self):
        self._sticker()
        rato = timezone.now() - datetime.timedelta(minutes=mood.COOLDOWN_MINUTES + 1)
        self.contact.sticker_log = [{"label": "granizado feliz", "at": rato.isoformat()}]
        self.contact.save(update_fields=["sticker_log"])
        turn = TurnContext(
            phone_number_id=PHONE_NUMBER_ID,
            message_id="wamid.1",
            sticker_urge=mood.sticker_urge(self.contact, roll=0.0),
        )
        prompt = build_system_prompt(self.contact, turn)
        self.assertIn("STICKERS EN ESTE TURNO", prompt)
        self.assertIn("«granizado feliz»", prompt, "sin esto repetiría el mismo")

    def test_sin_banco_no_hay_nota_de_stickers_en_el_prompt(self):
        """Nada que mandar, nada que contarle: es prompt que se paga en cada turno."""
        turn = TurnContext(
            phone_number_id=PHONE_NUMBER_ID,
            message_id="wamid.1",
            sticker_urge=mood.sticker_urge(self.contact, roll=0.0),
        )
        self.assertNotIn("STICKERS EN ESTE TURNO", build_system_prompt(self.contact, turn))

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


class MensajesSeguidosTests(TestCase):
    """Una persona manda lo que contesta y lo que pregunta en dos mensajes."""

    def test_un_mensaje_normal_sigue_siendo_uno(self):
        self.assertEqual(_split_messages("Listo parce, ¿algo más?"), ("Listo parce, ¿algo más?",))

    def test_la_linea_de_guiones_parte_la_respuesta_en_dos(self):
        self.assertEqual(
            _split_messages("Uf, esa está buena.\n---\n¿Personal o para 2?"),
            ("Uf, esa está buena.", "¿Personal o para 2?"),
        )

    def test_pasarse_de_dos_no_empapela_el_chat(self):
        """El tope vive aquí: en el prompt sería una sugerencia."""
        self.assertEqual(
            _split_messages("Uno\n---\nDos\n---\nTres\n---\nCuatro"),
            ("Uno", "Dos\nTres\nCuatro"),
        )

    def test_sin_texto_no_hay_mensajes(self):
        self.assertEqual(_split_messages(""), ())


class EntregaDelTurnoTests(TestCase):
    """El orden en que sale lo del turno: primero el texto, el sticker de remate."""

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.sticker = Sticker.objects.create(
            label="perro feliz", description="para celebrar", data=b"webp", byte_size=4
        )

    def _deliver(self, turn):
        orden = []
        with patch("apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0), patch(
            "apps.whatsapp.kapso.send_text",
            side_effect=lambda pid, to, text: orden.append(("texto", text)),
        ), patch(
            "apps.whatsapp.kapso.send_sticker",
            side_effect=lambda pid, to, url: orden.append(("sticker", url)) or {"ok": True},
        ):
            worker._deliver(self.contact, PHONE_NUMBER_ID, turn)
        return orden

    def test_el_sticker_sale_despues_del_texto(self):
        turn = AgentTurn(
            replies=("Listo, ya queda.",),
            message_ids=(),
            mutated=False,
            sticker=self.sticker,
        )
        orden = self._deliver(turn)
        self.assertEqual([kind for kind, _ in orden], ["texto", "sticker"])

    def test_los_dos_mensajes_salen_en_orden(self):
        turn = AgentTurn(replies=("Uno", "Dos"), message_ids=(), mutated=False)
        self.assertEqual(self._deliver(turn), [("texto", "Uno"), ("texto", "Dos")])

    def test_un_turno_de_solo_sticker_no_manda_texto_vacio(self):
        """El gesto puede ser la respuesta entera: sin texto no se inventa ninguno."""
        turn = AgentTurn(replies=(), message_ids=(), mutated=False, sticker=self.sticker)
        self.assertEqual([kind for kind, _ in self._deliver(turn)], ["sticker"])


class PulsoDeStickersTests(TestCase):
    """Cuándo le dan ganas de mandar uno (mood.py).

    Lo que se protege aquí es que el sticker siga siendo un gesto: que a veces
    no toque, que no lleguen dos seguidos y que una conversación no acabe
    empapelada. El dado se fija con `roll` para que la prueba no dependa de la
    suerte.
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE)

    def test_el_dado_decide_el_turno(self):
        self.assertTrue(mood.sticker_urge(self.contact, roll=0.0).allowed)
        self.assertFalse(mood.sticker_urge(self.contact, roll=0.99).allowed)

    def test_dos_seguidos_no(self):
        """Un sticker detrás de otro no es cercanía, es ruido."""
        self.contact.remember_sticker("granizado feliz")
        self.assertFalse(mood.sticker_urge(self.contact, roll=0.0).allowed)

    def test_pasado_el_enfriamiento_vuelve_a_ser_posible(self):
        self.contact.remember_sticker("granizado feliz")
        antes = timezone.now() - datetime.timedelta(minutes=mood.COOLDOWN_MINUTES + 1)
        self.contact.sticker_log = [{"label": "granizado feliz", "at": antes.isoformat()}]
        self.contact.save(update_fields=["sticker_log"])
        urge = mood.sticker_urge(self.contact, roll=0.0)
        self.assertTrue(urge.allowed)
        self.assertIn("granizado feliz", urge.note, "tiene que saber cuál para no repetirlo")

    def test_el_tope_del_dia_apaga_los_stickers_pase_lo_que_pase(self):
        viejo = timezone.now() - datetime.timedelta(hours=1)
        self.contact.sticker_log = [
            {"label": f"sticker {i}", "at": viejo.isoformat()}
            for i in range(len(mood.CHANCE_BY_COUNT))
        ]
        self.contact.save(update_fields=["sticker_log"])
        self.assertFalse(mood.sticker_urge(self.contact, roll=0.0).allowed)
        self.assertFalse(
            mood.sticker_urge(self.contact, answering_sticker=True, roll=0.0).allowed,
            "ni siquiera devolviendo el gesto: el tope del día manda",
        )

    def test_responder_al_sticker_del_cliente_es_lo_natural(self):
        """El mismo dado que dice que no en un turno normal dice que sí aquí."""
        roll = (mood.CHANCE_BY_COUNT[0] + mood.CHANCE_ANSWERING_STICKER) / 2
        self.assertFalse(mood.sticker_urge(self.contact, roll=roll).allowed)
        self.assertTrue(
            mood.sticker_urge(self.contact, answering_sticker=True, roll=roll).allowed
        )

    def test_los_de_ayer_no_cuentan_hoy(self):
        """El hilo del agente también se renueva a diario."""
        ayer = timezone.now() - datetime.timedelta(days=1)
        self.contact.sticker_log = [{"label": "granizado feliz", "at": ayer.isoformat()}]
        self.contact.save(update_fields=["sticker_log"])
        self.assertEqual(self.contact.stickers_today(), [])
        self.assertIn("todavía no le has mandado", mood.sticker_urge(self.contact, roll=0.0).note)

    def test_la_memoria_no_crece_sin_fin(self):
        for i in range(12):
            self.contact.remember_sticker(f"sticker {i}")
        self.assertEqual(len(self.contact.sticker_log), STICKER_MEMORY)
        self.assertEqual(self.contact.sticker_log[0]["label"], "sticker 11")

    def test_una_fecha_ilegible_no_tumba_el_turno(self):
        """El log es JSON suelto: lo que no se entienda se ignora, no revienta."""
        self.contact.sticker_log = [{"label": "raro", "at": "no es una fecha"}, "basura"]
        self.contact.save(update_fields=["sticker_log"])
        self.assertEqual(self.contact.stickers_today(), [])


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

    def test_el_sticker_de_un_cliente_se_lee_pero_no_se_guarda(self):
        """El banco de stickers es del dueño; el del cliente solo se mira."""
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.describe_sticker", return_value="saluda contento") as vision:
            texto = _resolve_media(self._msg(), PHONE_NUMBER_ID, self.cliente)
        vision.assert_called_once()
        self.assertEqual(StickerDraft.objects.count(), 0)
        self.assertIn("saluda contento", texto)

    def test_el_sticker_de_un_cliente_que_no_se_entiende_no_deja_texto(self):
        """Sin texto no hay turno: al gesto que no viste no se le contesta."""
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.describe_sticker", return_value=""):
            self.assertEqual(_resolve_media(self._msg(), PHONE_NUMBER_ID, self.cliente), "")

    def test_si_la_descarga_falla_el_mensaje_sigue_llegando(self):
        """No poder guardar un sticker no puede costar el mensaje que venía con él.

        Con el dueño no vale callarse: está configurando al agente y espera
        respuesta, aunque el archivo se haya perdido por el camino.
        """
        from .worker import _resolve_media

        with patch("apps.whatsapp.media.download_media", side_effect=RuntimeError("boom")):
            texto = _resolve_media(self._msg(), PHONE_NUMBER_ID, self.owner)
        self.assertEqual(StickerDraft.objects.count(), 0)
        self.assertIn("sticker", texto)

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
        """La pantalla no repite los textos de los tonos: los recibe de aquí.

        Desde que los tonos se editan, el texto del prompt también viaja: es
        justo lo que el dueño abre a cambiar.
        """
        self.api.force_authenticate(self.admin)
        resp = self.api.get("/api/v1/whatsapp/agent-settings/")
        self.assertEqual(resp.data["tone_preset"], "parcero")
        claves = [preset["key"] for preset in resp.data["tone_presets"]]
        self.assertEqual(claves, ["parcero", "cercano", "serio", "directo"])
        self.assertTrue(all(p["sample"] for p in resp.data["tone_presets"]))
        self.assertIn("QUIÉN ERES", resp.data["tone_presets"][0]["persona"])
        self.assertTrue(all(p["is_builtin"] for p in resp.data["tone_presets"]))

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

    # ---- El catálogo de tonos ----

    def _crear_tono(self, **campos):
        datos = {
            "name": "Poeta",
            "description": "Habla bonito, con calma.",
            "sample": "Buenas, ¿qué se le antoja a esta hora?",
            "persona": (
                "QUIÉN ERES: el que atiende con calma y buenas palabras. Tuteas, no metes "
                "prisa y si el cliente está molesto, primero lo escuchas."
            ),
        }
        datos.update(campos)
        return self.api.post("/api/v1/whatsapp/agent-tones/", datos, format="json")

    def test_un_empleado_no_toca_el_catalogo_de_tonos(self):
        """Cómo habla el negocio lo decide el dueño, no quien tiene el turno abierto."""
        self.api.force_authenticate(self.empleado)
        self.assertEqual(self.api.get("/api/v1/whatsapp/agent-tones/").status_code, 403)
        self.assertEqual(self._crear_tono().status_code, 403)

    def test_el_dueno_afina_un_tono_y_el_agente_lo_habla(self):
        """Editar la personalidad es el punto entero: tiene que llegar al prompt."""
        self.api.force_authenticate(self.admin)
        parcero = AgentTone.objects.get(key="parcero")
        resp = self.api.patch(
            f"/api/v1/whatsapp/agent-tones/{parcero.pk}/",
            {"persona": "QUIÉN ERES: el más frentero del pueblo, y jamás dices la palabra bacano."},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("el más frentero del pueblo", build_system_prompt())
        self.assertTrue(resp.data["is_modified"])

    def test_un_tono_nuevo_se_crea_y_se_puede_elegir(self):
        self.assertIn(self._crear_tono().status_code, (401, 403), "sin sesión no se crea")

        self.api.force_authenticate(self.admin)
        resp = self._crear_tono()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["key"], "poeta", "la clave sale del nombre")
        self.assertFalse(resp.data["is_builtin"])

        elegido = self.api.patch(
            "/api/v1/whatsapp/agent-settings/", {"tone_preset": "poeta"}, format="json"
        )
        self.assertEqual(elegido.status_code, 200, elegido.data)
        self.assertIn("con calma y buenas palabras", build_system_prompt())

    def test_una_personalidad_de_dos_palabras_se_rechaza(self):
        """Reemplaza el bloque entero: dejarla en nada deja al agente sin nadie que ser."""
        self.api.force_authenticate(self.admin)
        resp = self._crear_tono(persona="sé amable")
        self.assertEqual(resp.status_code, 400)

    def test_dos_tonos_con_el_mismo_nombre_no_chocan(self):
        self.api.force_authenticate(self.admin)
        self.assertEqual(self._crear_tono().data["key"], "poeta")
        self.assertEqual(self._crear_tono().data["key"], "poeta-2")

    def test_no_se_borra_el_tono_con_el_que_esta_hablando(self):
        """Borrarlo dejaría al agente hablando con la personalidad de otro."""
        self.api.force_authenticate(self.admin)
        parcero = AgentTone.objects.get(key=AgentSettings.load().tone_preset)
        resp = self.api.delete(f"/api/v1/whatsapp/agent-tones/{parcero.pk}/")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(AgentTone.objects.filter(pk=parcero.pk).exists())

    def test_un_tono_que_no_esta_en_uso_se_borra(self):
        self.api.force_authenticate(self.admin)
        serio = AgentTone.objects.get(key="serio")
        resp = self.api.delete(f"/api/v1/whatsapp/agent-tones/{serio.pk}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(AgentTone.objects.filter(pk=serio.pk).exists())

    def test_no_se_borra_el_ultimo_tono_que_queda(self):
        """Sin catálogo no hay nada que elegir: la pantalla quedaría muerta."""
        self.api.force_authenticate(self.admin)
        AgentTone.objects.exclude(key="parcero").delete()
        AgentSettings.objects.filter(pk=1).update(tone_preset="ninguno")
        unico = AgentTone.objects.get(key="parcero")
        resp = self.api.delete(f"/api/v1/whatsapp/agent-tones/{unico.pk}/")
        self.assertEqual(resp.status_code, 400)

    def test_un_tono_de_fabrica_editado_vuelve_a_su_texto_original(self):
        self.api.force_authenticate(self.admin)
        serio = AgentTone.objects.get(key="serio")
        self.api.patch(
            f"/api/v1/whatsapp/agent-tones/{serio.pk}/",
            {"persona": "QUIÉN ERES: alguien completamente distinto al que venía de fábrica."},
            format="json",
        )
        resp = self.api.post(f"/api/v1/whatsapp/agent-tones/{serio.pk}/restore/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("USTED siempre", AgentTone.objects.get(pk=serio.pk).persona)
        self.assertFalse(resp.data["is_modified"])

    def test_un_tono_propio_no_tiene_original_al_que_volver(self):
        self.api.force_authenticate(self.admin)
        creado = self._crear_tono()
        resp = self.api.post(f"/api/v1/whatsapp/agent-tones/{creado.data['id']}/restore/")
        self.assertEqual(resp.status_code, 400)

    def test_guardar_el_nombre_o_el_tono_no_apaga_los_interruptores(self):
        """Los interruptores se guardan solos; el resto de la pantalla no los toca.

        Un PATCH con los campos de texto no menciona los booleanos, y si el
        serializer los tratara como ausentes-igual-a-falso, cambiar el nombre
        del agente le apagaría los stickers sin que nadie lo pidiera.
        """
        self.api.force_authenticate(self.admin)
        self.api.patch(
            "/api/v1/whatsapp/agent-settings/",
            {"reactions_enabled": False},
            format="json",
        )
        resp = self.api.patch(
            "/api/v1/whatsapp/agent-settings/",
            {"agent_name": "Frosty", "tone_preset": "serio", "tone": "", "owner_phones": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        config = AgentSettings.load()
        self.assertTrue(config.stickers_enabled, "el que estaba encendido sigue encendido")
        self.assertFalse(config.reactions_enabled, "el que estaba apagado sigue apagado")
        self.assertTrue(config.product_photos_enabled)
        self.assertTrue(config.quick_replies_enabled)


class PalabrasVetadasTests(TestCase):
    """Lo que el negocio prohíbe decir no sale, aunque el modelo lo escriba.

    Chat real del 05/09: con «parce» y «pana» prohibidas en el panel, Frosty
    siguió diciendo «parce». No es desobediencia del modelo: su personalidad
    se la pide, el saludo de muestra la usaba y sus propios mensajes de antes
    —que sigue leyendo del hilo del día— también. Contra eso, una línea en el
    prompt no alcanza: la palabra se quita del mensaje antes de enviarlo.
    """

    def test_la_palabra_prohibida_no_sale_aunque_el_modelo_la_escriba(self):
        salida = _for_whatsapp("Qué más parce, ¿lo de siempre?", banned_words={"parce"})
        self.assertEqual(salida, "Qué más, ¿lo de siempre?")

    def test_el_vocativo_no_deja_la_coma_colgando(self):
        salida = _for_whatsapp("Parce, ya te sale el pedido", banned_words={"parce"})
        self.assertEqual(salida, "Ya te sale el pedido")

    def test_da_igual_como_la_escriba(self):
        """Mayúsculas y tildes son la misma palabra: si no, la prohibición se esquiva sola."""
        salida = _for_whatsapp("PARCE y parcé son el mismo parce", banned_words={"parce"})
        self.assertNotIn("arce", salida.lower())

    def test_no_se_lleva_por_delante_una_palabra_que_la_contiene(self):
        """Prohibir «pana» no puede dejar sin «panadería» ni sin «panela»."""
        salida = _for_whatsapp("Tenemos panela y pan de la panadería", banned_words={"pana"})
        self.assertEqual(salida, "Tenemos panela y pan de la panadería")

    def test_sin_nada_prohibido_el_texto_sale_intacto(self):
        texto = "Listo, tu pedido va en camino."
        self.assertEqual(_for_whatsapp(texto), texto)

    def test_lo_prohibido_en_los_ajustes_de_tono_tambien_cuenta(self):
        """Ahí lo escribió el dueño antes de que existiera el campo de palabras."""
        config = AgentSettings.load()
        config.tone = 'Sin emojis, y nunca digas «pana».'
        config.save()
        self.assertEqual(config.forbidden_words(), {"pana"})

    def test_una_instruccion_sin_comillas_no_prohibe_de_su_cuenta(self):
        """«no uses palabras raras» no puede acabar borrando «raras» del pedido."""
        config = AgentSettings.load()
        config.tone = "No uses palabras raras ni tecnicismos."
        config.save()
        self.assertEqual(config.forbidden_words(), set())

    def test_el_campo_del_panel_manda_igual(self):
        config = AgentSettings.load()
        config.banned_words = "parce, pana"
        config.save()
        self.assertEqual(config.forbidden_words(), {"parce", "pana"})

    def test_el_saludo_de_muestra_no_le_ensena_la_palabra_prohibida(self):
        """Darle de ejemplo justo lo que se le prohibió es pedirle que falle."""
        config = AgentSettings.load()
        config.tone_preset = "parcero"
        config.banned_words = "parce"
        config.save()
        prompt = build_system_prompt()
        self.assertIn("Así suena un saludo tuyo", prompt)
        self.assertNotIn("parce,", prompt.split("Así suena un saludo tuyo")[1])

    def test_el_prompt_dice_que_la_lista_manda_sobre_la_personalidad(self):
        config = AgentSettings.load()
        config.banned_words = "parce"
        config.save()
        prompt = build_system_prompt()
        self.assertIn("PALABRAS PROHIBIDAS", prompt)
        self.assertIn("«parce»", prompt)

    def test_las_palabras_prohibidas_van_despues_de_los_ajustes_de_tono(self):
        """Contradicen a la personalidad a propósito: van de últimas de lo estable."""
        config = AgentSettings.load()
        config.tone = "Trata al cliente de usted."
        config.banned_words = "parce"
        config.save()
        prompt = build_system_prompt()
        self.assertLess(prompt.index("AJUSTES DE ESTILO"), prompt.index("PALABRAS PROHIBIDAS"))
        self.assertLess(prompt.index("PALABRAS PROHIBIDAS"), prompt.index("FECHA Y HORA"))

    def test_una_frase_entera_no_se_guarda_como_palabra(self):
        """Quitarle una frase a un mensaje lo deja cojo; se avisa en vez de guardarla."""
        from .serializers import AgentSettingsSerializer

        serializer = AgentSettingsSerializer(
            AgentSettings.load(), data={"banned_words": "nunca digas parce"}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("banned_words", serializer.errors)

    def test_el_limpiador_deja_la_frase_en_pie(self):
        self.assertEqual(banned.clean("Hágale pues parce", {"parce"}), "Hágale pues")
        self.assertEqual(banned.clean("Bien parce, ¿y usted?", {"parce"}), "Bien, ¿y usted?")
        self.assertEqual(banned.clean("Un texto cualquiera", {"parce"}), "Un texto cualquiera")


class StickerDelClienteTests(TransactionTestCase):
    """Un sticker es un gesto: si no se puede leer, no se responde nada.

    Chat real del 05/09: el cliente mandó un sticker y Frosty contestó
    "Perdón, ¿me lo repites?". El modelo había hecho lo correcto —callarse
    ante un gesto que no vio—, y el relleno de _for_whatsapp convirtió ese
    silencio en una pregunta absurda: no se le pide a nadie que repita un
    sticker.
    """

    def setUp(self):
        _pending.clear()
        _active.clear()
        self.sent = []
        self.turns = []

        patcher = patch("apps.whatsapp.worker.kapso")
        self.kapso = patcher.start()
        self.addCleanup(patcher.stop)
        self.kapso.send_text.side_effect = lambda pnid, phone, text: self.sent.append(text)

        # El hilo del agente vive en Postgres (LangGraph): en las pruebas no se
        # toca, solo se comprueba que se le anota lo que llegó
        recall = patch("apps.whatsapp.agent.record_messages")
        self.record = recall.start()
        self.addCleanup(recall.stop)

        vision = patch("apps.whatsapp.worker.wa_media.describe_sticker")
        self.describe = vision.start()
        self.addCleanup(vision.stop)

    def receive(self, text, sequence=1, msg_type="text", media_id=None):
        event = WebhookEvent.objects.create(
            idempotency_key=f"sticker-{sequence}",
            payload=webhook_payload(
                text, sequence, msg_type=msg_type, media_id=media_id
            ),
            event_type="whatsapp.message.received",
        )
        _process_event_safe(event.pk)
        return event

    def wait_idle(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if PHONE not in _active and not _pending.get(PHONE):
                return True
            time.sleep(0.05)
        self.fail("el loop del contacto no terminó a tiempo")

    def fake_turn(self, reply="ok"):
        def _run(
            contact,
            text,
            phone_number_id="",
            message_id="",
            customer_sticker=False,
            silence_ok=False,
        ):
            self.turns.append(text)
            return AgentTurn(
                replies=(reply,) if reply else (), message_ids=("m1",), mutated=False
            )

        return _run

    @override_settings(**FAST, OPENAI_API_KEY="test")
    def test_un_sticker_que_no_se_entiende_no_recibe_respuesta(self):
        self.describe.return_value = ""
        with patch("apps.whatsapp.agent.run_turn", side_effect=self.fake_turn()):
            self.receive("", msg_type="sticker", media_id="media-1")
            self.wait_idle()
        self.assertEqual(self.sent, [], "a un gesto que no viste no se le contesta")
        self.assertEqual(self.turns, [], "ni siquiera se llama al modelo")

    @override_settings(**FAST, OPENAI_API_KEY="test")
    def test_el_sticker_ilegible_queda_anotado_en_el_hilo(self):
        """Que no se responda no significa que la conversación no lo sepa."""
        self.describe.return_value = ""
        with patch("apps.whatsapp.agent.run_turn", side_effect=self.fake_turn()):
            self.receive("", msg_type="sticker", media_id="media-1")
            self.wait_idle()
        self.assertTrue(self.record.called)

    @override_settings(**FAST, OPENAI_API_KEY="test")
    def test_un_sticker_que_si_se_entiende_llega_al_agente_como_gesto(self):
        self.describe.return_value = "Un bebé con cara de confusión; pregunta qué pasó."
        with patch("apps.whatsapp.agent.run_turn", side_effect=self.fake_turn()):
            self.receive("", msg_type="sticker", media_id="media-1")
            self.wait_idle()
        self.assertEqual(len(self.turns), 1)
        self.assertIn("cara de confusión", self.turns[0])
        self.assertIn("Es un gesto, no una pregunta", self.turns[0])

    @override_settings(**FAST, OPENAI_API_KEY="test")
    def test_un_sticker_junto_a_una_pregunta_no_calla_al_agente(self):
        """Lo que se ignora es el gesto, no lo que el cliente escribió con él."""
        self.describe.return_value = ""
        with patch("apps.whatsapp.agent.run_turn", side_effect=self.fake_turn()):
            self.receive("", sequence=1, msg_type="sticker", media_id="media-1")
            self.receive("¿cuánto se demora?", sequence=2)
            self.wait_idle()
        self.assertEqual(len(self.turns), 1)
        self.assertIn("¿cuánto se demora?", self.turns[0])
        self.assertNotIn("\n\n", self.turns[0], "el sticker no deja renglones en blanco")
        self.assertEqual(self.sent, ["ok"])

    def test_el_silencio_ante_un_gesto_no_se_rellena(self):
        self.assertEqual(_for_whatsapp("", silence_ok=True), "")

    def test_ante_una_pregunta_el_silencio_si_se_rellena(self):
        """Callarse cuando el cliente preguntó algo sigue siendo un fallo."""
        self.assertEqual(_for_whatsapp(""), "Perdón, ¿me lo repites?")


class AvisosDeEstadoTests(TestCase):
    """Los avisos automáticos de estado hablan con la misma boca que el agente.

    Chat real del 06/09: con «parce» prohibida en el panel, el cliente recibió
    "¡Listo parce! Tu pedido ya está en la cocina". No lo escribió el modelo:
    es una plantilla nuestra, y el filtro solo miraba lo que escribía el modelo.
    Para el negocio la palabra seguía viva justo donde la vio.
    """

    def setUp(self):
        config = AgentSettings.load()
        config.banned_words = "parce, pana"
        config.save()
        self.order = Order.objects.create(
            source=Order.Source.WHATSAPP,
            order_type=Order.OrderType.DELIVERY,
            customer_name="Anyi",
            customer_phone=PHONE,
            payment_method=Order.PaymentMethod.NEQUI,
        )

    def _aviso(self, status):
        """El texto que le llega al cliente con el pedido en ese estado."""
        from .signals import message_for

        self.order.status = status
        return message_for(self.order)

    def test_el_aviso_de_la_cocina_no_dice_la_palabra_prohibida(self):
        aviso = self._aviso(Order.Status.PREPARING)
        self.assertNotIn("parce", aviso.lower())
        self.assertIn("ya está en la cocina", aviso, "el aviso sigue diciendo lo suyo")
        self.assertTrue(aviso.startswith("👨‍🍳 ¡Listo!"), aviso)

    def test_ninguna_plantilla_dice_la_palabra_que_el_negocio_prohibió(self):
        """El filtro es el guardarraíl; la plantilla ya no la trae de fábrica."""
        from .signals import PICKUP_MESSAGES, STATUS_MESSAGES

        for plantilla in list(STATUS_MESSAGES.values()) + list(PICKUP_MESSAGES.values()):
            self.assertNotIn("parce", plantilla.lower(), plantilla)

    def test_la_palabra_prohibida_sale_de_cualquier_parte_del_aviso(self):
        """No solo del saludo: también de la línea de pago, que es nuestra."""
        config = AgentSettings.load()
        config.banned_words = "porfa"
        config.save()
        self.order.payment_method = Order.PaymentMethod.CASH
        aviso = self._aviso(Order.Status.READY)
        self.assertNotIn("porfa", aviso.lower())
        self.assertIn("Ten listico el efectivo", aviso)

    def test_sin_nada_prohibido_el_aviso_va_tal_cual(self):
        config = AgentSettings.load()
        config.banned_words = ""
        config.tone = ""
        config.save()
        self.assertEqual(
            self._aviso(Order.Status.PREPARING),
            f"👨‍🍳 ¡Listo! Tu pedido {self.order.order_number} ya está en la cocina.",
        )

    def test_al_pedido_sin_metodo_de_pago_no_se_le_pide_comprobante(self):
        """Se creó sin pago elegido (ver missing.py): no hay nada que mandar."""
        self.order.payment_method = ""
        aviso = self._aviso(Order.Status.READY)
        self.assertNotIn("comprobante", aviso)
        self.assertIn("cuadramos el pago", aviso)

    def test_un_estado_sin_aviso_no_manda_nada(self):
        self.assertEqual(self._aviso(Order.Status.PENDING), "")

    def _comprobante(self, body, wamid="wamid.img", minutos=0):
        """Archiva una imagen entrante como la dejó la visión."""
        msg = ChatMessage.objects.create(
            wamid=wamid,
            phone=PHONE,
            direction=ChatMessage.Direction.INBOUND,
            body=body,
        )
        if minutos:
            ChatMessage.objects.filter(pk=msg.pk).update(
                created_at=msg.created_at - datetime.timedelta(minutes=minutos)
            )
        return msg

    def test_el_comprobante_ya_recibido_no_se_vuelve_a_pedir(self):
        """Chat real del 18-09 (Natt, +57 318 797 7295): pagó por Nequi a las
        19:43 y el agente le contestó "recibimos el comprobante por $22.000";
        a las 19:54 el aviso de salida le pidió el comprobante otra vez."""
        from .signals import PROOF_MARKER

        self._comprobante(
            f"[El cliente envió una imagen. Contenido: {PROOF_MARKER} Nequi por $22.000,00 "
            "a Jaimen Aza el 18 de septiembre de 2026 a las 07:42 p. m. Referencia M25077102.]"
        )
        aviso = self._aviso(Order.Status.READY)
        self.assertNotIn("mándanos el comprobante", aviso)
        self.assertIn("Ya tenemos tu comprobante", aviso)

    def test_sin_comprobante_el_aviso_sigue_pidiéndolo(self):
        """Aflojarlo para quien ya pagó no puede callarlo para quien no."""
        aviso = self._aviso(Order.Status.READY)
        self.assertIn("Si todavía no has pagado, mándanos el comprobante.", aviso)

    def test_una_foto_cualquiera_no_cuenta_como_comprobante(self):
        self._comprobante(
            "[El cliente envió una imagen. Contenido: Fotografía de una calle lluviosa "
            "con motos parqueadas frente a un local.]"
        )
        self.assertIn("mándanos el comprobante", self._aviso(Order.Status.READY))

    def test_la_imagen_que_no_se_pudo_leer_tampoco_cuenta(self):
        """Su texto de respaldo nombra el comprobante en minúsculas, y el
        marcador va en mayúsculas justo para no confundirse con él."""
        self._comprobante(
            "[El cliente envió un(a) imagen que no puedes ver. Si esperabas un comprobante "
            "de pago, dile que el equipo lo verificará.]"
        )
        self.assertIn("mándanos el comprobante", self._aviso(Order.Status.READY))

    def test_el_comprobante_de_hace_horas_no_paga_este_pedido(self):
        """Era de otro pedido: el cliente pide dos veces la misma noche."""
        from .signals import PROOF_MARKER

        self._comprobante(
            f"[El cliente envió una imagen. Contenido: {PROOF_MARKER} Nequi por $38.000,00.]",
            minutos=180,
        )
        self.assertIn("mándanos el comprobante", self._aviso(Order.Status.READY))

    def test_el_comprobante_de_hace_un_rato_sí_es_de_este_pedido(self):
        """Casi siempre paga mientras le tomamos el pedido, antes de crearlo."""
        from .signals import PROOF_MARKER

        self._comprobante(
            f"[El cliente envió una imagen. Contenido: {PROOF_MARKER} Nequi por $22.000,00.]",
            minutos=10,
        )
        self.assertIn("Ya tenemos tu comprobante", self._aviso(Order.Status.READY))

    def test_el_efectivo_no_sabe_de_comprobantes(self):
        from .signals import PROOF_MARKER

        self._comprobante(f"[El cliente envió una imagen. Contenido: {PROOF_MARKER} $22.000.]")
        self.order.payment_method = Order.PaymentMethod.CASH
        self.assertIn("Ten listico el efectivo", self._aviso(Order.Status.READY))

    def test_el_pago_ya_confirmado_manda_sobre_el_comprobante(self):
        from .signals import PROOF_MARKER

        self._comprobante(f"[El cliente envió una imagen. Contenido: {PROOF_MARKER} $22.000.]")
        self.order.is_paid = True
        self.assertIn("Tu pago ya quedó confirmado", self._aviso(Order.Status.READY))

    def test_la_visión_marca_los_comprobantes_para_que_se_reconozcan(self):
        """Sin el marcador en el prompt, el archivo no distingue una foto."""
        from .media import IMAGE_PROMPT
        from .signals import PROOF_MARKER

        self.assertIn(f'empieza la descripción con "{PROOF_MARKER}"', IMAGE_PROMPT)


class UbicacionQueLlegaTardeTests(TestCase):
    """El pedido se toma sin ubicación y el cliente la manda después.

    Para él ya la dio: nadie va a repetirla porque el sistema la pidió tarde.
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.order = Order.objects.create(
            source=Order.Source.WHATSAPP,
            order_type=Order.OrderType.DELIVERY,
            customer_name="Estefania",
            customer_phone=PHONE,
            customer_notes=missing.note([missing.LOCATION, "el método de pago"], "Sin ají"),
        )

    def test_la_ubicacion_entra_sola_al_pedido_abierto(self):
        missing.attach_location(self.contact, Decimal("0.9821"), Decimal("-77.7912"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.delivery_lat, Decimal("0.9821000"))
        self.assertEqual(self.order.delivery_lng, Decimal("-77.7912000"))

    def test_lo_que_ya_no_falta_sale_de_la_nota_y_lo_demas_se_queda(self):
        missing.attach_location(self.contact, Decimal("0.9821"), Decimal("-77.7912"))
        self.order.refresh_from_db()
        self.assertNotIn(missing.LOCATION, self.order.customer_notes)
        self.assertIn("el método de pago", self.order.customer_notes)
        self.assertIn("Sin ají", self.order.customer_notes, "la nota del cliente no se toca")

    def test_cuando_no_falta_nada_mas_la_nota_del_cliente_queda_limpia(self):
        self.order.customer_notes = missing.note([missing.LOCATION], "Sin ají")
        self.order.save(update_fields=["customer_notes"])
        missing.attach_location(self.contact, Decimal("0.9821"), Decimal("-77.7912"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.customer_notes, "Sin ají")

    def test_un_pedido_ya_entregado_no_se_toca(self):
        # update() y no save(): cambiar el estado avisaría al cliente
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.DELIVERED)
        self.assertEqual(missing.attach_location(self.contact, Decimal("0.98"), Decimal("-77.79")), [])
        self.order.refresh_from_db()
        self.assertIsNone(self.order.delivery_lat)

    def test_no_le_pisa_la_ubicacion_a_un_pedido_que_ya_la_tenia(self):
        self.order.delivery_lat = Decimal("0.9000000")
        self.order.delivery_lng = Decimal("-77.7000000")
        self.order.save(update_fields=["delivery_lat", "delivery_lng"])
        self.assertEqual(missing.attach_location(self.contact, Decimal("0.98"), Decimal("-77.79")), [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.delivery_lat, Decimal("0.9000000"))

    def test_la_ubicacion_no_dispara_el_aviso_de_estado_al_cliente(self):
        """Completar un dato no es un cambio de estado: el cliente no se entera."""
        with patch("apps.whatsapp.kapso.send_text") as send:
            missing.attach_location(self.contact, Decimal("0.9821"), Decimal("-77.7912"))
        send.assert_not_called()


class PedidoQueSeCreaConLoQueHayTests(TestCase):
    """El prompt manda crear el pedido aunque falte un dato (chat 06/09)."""

    def test_el_prompt_no_deja_la_conversacion_esperando_la_ubicacion(self):
        prompt = build_system_prompt()
        self.assertIn("UN PEDIDO CONFIRMADO SE CREA SIEMPRE", prompt)
        self.assertIn("NO te quedes esperándola", prompt)
        self.assertNotIn("que es OBLIGATORIA para todo domicilio", prompt)

    def test_el_prompt_conserva_la_regla_dura_de_no_dar_por_hecho_el_pedido(self):
        """Crear con lo que hay no es decir que existe sin haberlo creado."""
        prompt = build_system_prompt()
        self.assertIn('crear_pedido responde "PEDIDO CREADO"', prompt)


class PedidoConLoQueElClienteQuisoDarTests(TestCase):
    """Los datos se piden todos; ninguno vale el pedido.

    Reconstruye los dos chats del 06/09 que se atendieron a mano:
    - Estefa: la ubicación no llegó (131060) y el pedido nunca existió.
    - Anyi: "te envío el dinero cuando estén aquí" — paga al recibir.
    """

    def setUp(self):
        from apps.business.models import Business
        from apps.orders.models import StoreSettings
        from apps.products.models import Category, Product, ProductVariant
        from django.conf import settings as dj_settings

        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food", defaults={"name": "Frostbyte Food"}
        )
        categoria = Category.objects.create(name="Granizados", slug="granizados", business=food)
        producto = Product.objects.create(name="Blue Berry", category=categoria, business=food)
        self.variante = ProductVariant.objects.create(
            product=producto, name="Extragrande", sku="BB-14", price=14000
        )
        cfg = StoreSettings.load()
        cfg.is_open = True
        cfg.customer_ordering_enabled = True
        cfg.delivery_fee = 2000
        cfg.save()
        self.centro = (dj_settings.DELIVERY_CENTER_LAT, dj_settings.DELIVERY_CENTER_LNG)
        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def _crear(self, **kwargs):
        datos = {
            "items": [{"variante_id": self.variante.id, "cantidad": 1, "notas": ""}],
            "nombre_cliente": "Estefania Patiño",
        }
        datos.update(kwargs)
        return self.tools["crear_pedido"].invoke(datos)

    def _con_ubicacion(self):
        self.contact.last_location_lat, self.contact.last_location_lng = self.centro
        self.contact.last_location_at = timezone.now()
        self.contact.save()

    def test_estefa_el_pedido_entra_con_la_direccion_escrita_y_sin_ubicacion(self):
        """Ella dio "Barrio la merced, Transversal 4 #17-41"; la ubicación se perdió."""
        resultado = self._crear(
            metodo_pago="cash", paga_con="20000", direccion="Transversal 4 #17-41",
            referencia="Barrio la merced",
        )
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertEqual(order.delivery_address, "Transversal 4 #17-41")
        self.assertIsNone(order.delivery_lat)
        self.assertIn("dio dirección escrita", order.customer_notes)
        self.assertIn("Paga en efectivo con $20.000", order.customer_notes)

    def test_anyi_paga_cuando_llegue_el_domiciliario(self):
        """"Te envío el dinero cuando estén aquí": es un sí, no un pedido a medias."""
        self._con_ubicacion()
        resultado = self._crear(metodo_pago="nequi", paga_al_recibir=True)
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertEqual(order.payment_method, Order.PaymentMethod.NEQUI)
        self.assertFalse(order.is_paid, "el comprobante no ha llegado")
        self.assertIn("Paga al recibir el pedido", order.customer_notes)
        self.assertFalse(
            order.customer_notes.startswith(missing.PREFIX),
            "eligió cómo paga: no hay nada pendiente",
        )

    def test_en_efectivo_no_se_repite_que_paga_al_recibir(self):
        """El efectivo se paga en la puerta por definición; decirlo sobra."""
        self._con_ubicacion()
        self._crear(metodo_pago="cash", paga_con="exacto", paga_al_recibir=True)
        self.assertNotIn("Paga al recibir", Order.objects.get().customer_notes)

    def test_sin_nada_mas_que_los_items_y_el_nombre_el_pedido_existe(self):
        """El peor caso: solo se sabe qué quiere y quién es. Igual entra."""
        resultado = self._crear()
        self.assertIn("PEDIDO CREADO", resultado)
        order = Order.objects.get()
        self.assertEqual(order.customer_name, "Estefania Patiño")
        self.assertEqual(order.payment_method, "")
        for pendiente in (missing.LOCATION, "el método de pago"):
            self.assertIn(pendiente, order.customer_notes)

    def test_fuera_de_la_zona_sigue_sin_haber_pedido(self):
        """Un dato que falta se pregunta; una entrega imposible no se promete."""
        self.contact.last_location_lat = Decimal("1.5000000")
        self.contact.last_location_lng = Decimal("-78.5000000")
        self.contact.last_location_at = timezone.now()
        self.contact.save()
        resultado = self._crear(metodo_pago="nequi")
        self.assertIn("ERROR", resultado)
        self.assertIn("FUERA de la zona", resultado)
        self.assertEqual(Order.objects.count(), 0)

    def test_el_local_cerrado_sigue_sin_tomar_pedidos(self):
        from apps.orders.models import StoreSettings

        cfg = StoreSettings.load()
        cfg.is_open = False
        cfg.save()
        self.assertIn("ERROR", self._crear(metodo_pago="nequi"))
        self.assertEqual(Order.objects.count(), 0)

    def test_un_pedido_sin_items_no_es_un_pedido(self):
        self.assertIn("ERROR", self._crear(items=[]))
        self.assertEqual(Order.objects.count(), 0)

    def test_el_prompt_pide_el_comprobante_pero_no_lo_espera(self):
        prompt = build_system_prompt()
        self.assertIn("pide que envíe el comprobante cuando pague", prompt)
        self.assertIn("NUNCA se espera para crear el pedido", prompt)
        self.assertIn("paga_al_recibir=True", prompt)


class EscaladaAHumanoTests(TestCase):
    """La pausa que pide el agente caduca; la del panel no.

    Chats reales: Anyi V escaló el 06/09 ("me figura como entregado, ya le pasé
    el caso al equipo") y el 15/09, nueve días después, escribió "Hola, quiero
    hacer un pedido a domicilio" y no le contestó nadie durante diez minutos.
    Lo mismo Natt Studio (19/08) y Dayana (27/08): tres clientes con el agente
    apagado para siempre porque human_handoff no se apaga solo.
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE, profile_name="Anyi V")
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def _escalar(self, motivo="el cliente pide hablar con una persona"):
        with patch("apps.whatsapp.tools._avisar_escalada"):
            return self.tools["solicitar_humano"].invoke({"motivo": motivo})

    def test_escalar_no_enciende_el_interruptor_permanente(self):
        self._escalar()
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.human_handoff)

    def test_escalar_pausa_al_agente_ahora(self):
        self._escalar()
        self.contact.refresh_from_db()
        self.assertIsNotNone(self.contact.human_until)
        self.assertGreater(self.contact.human_until, timezone.now())

    def test_la_pausa_caduca_y_el_agente_vuelve(self):
        """Al día siguiente el cliente encuentra al agente, no un silencio."""
        self._escalar()
        self.contact.refresh_from_db()
        manana = timezone.now() + datetime.timedelta(days=1)
        self.assertLess(self.contact.human_until, manana)

    def test_escalar_no_acorta_una_pausa_mas_larga(self):
        lejos = timezone.now() + datetime.timedelta(days=2)
        self.contact.human_until = lejos
        self.contact.save()
        self._escalar()
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.human_until, lejos)

    def test_el_dueno_recibe_el_aviso_de_que_alguien_espera(self):
        config = AgentSettings.load()
        config.owner_phones = "573164277879"
        config.save()
        self.contact.last_phone_number_id = "pn-1"
        self.contact.save()
        with patch("apps.whatsapp.tools.kapso.send_text") as enviar:
            self.tools["solicitar_humano"].invoke({"motivo": "pago en disputa"})
        enviar.assert_called_once()
        _, destino, cuerpo = enviar.call_args[0]
        self.assertEqual(destino, "573164277879")
        self.assertIn("Anyi V", cuerpo)
        self.assertIn("pago en disputa", cuerpo)

    def test_un_aviso_que_falla_no_tumba_la_pausa(self):
        config = AgentSettings.load()
        config.owner_phones = "573164277879"
        config.save()
        self.contact.last_phone_number_id = "pn-1"
        self.contact.save()
        with patch("apps.whatsapp.tools.kapso.send_text", side_effect=RuntimeError("Kapso caído")):
            self.tools["solicitar_humano"].invoke({"motivo": "queja"})
        self.contact.refresh_from_db()
        self.assertGreater(self.contact.human_until, timezone.now())

    def test_el_interruptor_del_panel_sigue_siendo_permanente(self):
        """Apagar el agente a mano no caduca: eso lo decide una persona."""
        self.contact.human_handoff = True
        self.contact.save()
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.human_handoff)


@override_settings(WHATSAPP_STATUS_NOTICE_DELAY_SECONDS=0)
class AvisoRepetidoTests(TransactionTestCase):
    """Un pedido que retrocede no vuelve a avisar lo que ya avisó.

    Chat real del 06/09: a Anyi le llegó "✅ entregado" a las 18:47, contestó
    "Aún no me entregan", y a las 19:11 y 19:17 le llegaron "va en camino" y
    "entregado" otra vez. El equipo había devuelto el pedido a la cocina.

    Aquí los estados se mueven uno a uno y con calma: la espera va en cero para
    que cada aviso salga por su cuenta. Lo que pasa cuando se mueven de un
    tirón lo prueba AvisosEnRafagaTests.
    """

    def setUp(self):
        WhatsAppContact.objects.create(phone=PHONE, last_phone_number_id="pn-1")
        self.order = Order.objects.create(
            source=Order.Source.WHATSAPP,
            order_type=Order.OrderType.DELIVERY,
            customer_name="Anyi",
            customer_phone=PHONE,
            payment_method=Order.PaymentMethod.NEQUI,
        )

    def _mover(self, status, enviados):
        self.order.status = status
        with patch("apps.whatsapp.kapso.send_text", side_effect=lambda *a: enviados.append(a[2])):
            self.order.save()
            time.sleep(0.3)

    def test_el_mismo_estado_no_se_avisa_dos_veces(self):
        enviados = []
        self._mover(Order.Status.PREPARING, enviados)
        self._mover(Order.Status.DELIVERED, enviados)
        self._mover(Order.Status.PREPARING, enviados)  # el equipo lo devuelve
        self._mover(Order.Status.DELIVERED, enviados)
        entregados = [t for t in enviados if "entregado" in t]
        self.assertEqual(len(entregados), 1, enviados)

    def test_cada_estado_nuevo_sí_se_avisa(self):
        enviados = []
        self._mover(Order.Status.PREPARING, enviados)
        self._mover(Order.Status.READY, enviados)
        self._mover(Order.Status.DELIVERED, enviados)
        self.assertEqual(len(enviados), 3, enviados)

    def test_queda_anotado_de_qué_se_avisó(self):
        self._mover(Order.Status.PREPARING, [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.notified_statuses, [Order.Status.PREPARING])


class CancelarEsPagarTests(TestCase):
    """"Cancelar" en Colombia es pagar; anular es otra cosa.

    Chat real del 20-09: a "¿Con qué billete vas a pagar?" Natt contestó
    "Cancelo completo" —pagaba con el valor exacto— y el agente le respondió
    "Ese pedido ya figura como entregado, así que no puedo cancelarlo por acá",
    hablando de un pedido de dos días antes. Ella tuvo que explicarle que había
    "cancelado el pedido con 10 mil pesos".
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.tools = {t.name: t for t in build_tools(self.contact)}
        self.order = Order.objects.create(
            source=Order.Source.WHATSAPP,
            order_type=Order.OrderType.DELIVERY,
            customer_name="Natt",
            customer_phone=PHONE,
        )

    def _cancelar(self, frase, numero=None):
        return self.tools["cancelar_pedido"].invoke(
            {
                "numero_pedido": numero or self.order.order_number,
                "lo_que_dijo_el_cliente": frase,
            }
        )

    def _lectura(self, cual):
        """El modelo barato ya leyó la frase y dijo esto."""
        return patch("apps.whatsapp.intencion.leer_cancelar", return_value=cual)

    def _lectura_cruda(self, texto):
        """Lo que el modelo devuelve tal cual, sin limpiar."""
        cliente = patch("apps.whatsapp.media._openai_client")
        mock = cliente.start()
        self.addCleanup(cliente.stop)
        mock.return_value.chat.completions.create.return_value = Mock(
            choices=[Mock(message=Mock(content=texto))]
        )

        class _Ctx:
            def __enter__(inner):
                return mock

            def __exit__(inner, *exc):
                return False

        return _Ctx()

    def test_cancelo_completo_no_cancela_nada(self):
        resultado = self._cancelar("Cancelo completo")
        self.assertIn("ERROR", resultado)
        self.assertIn("PAGAR", resultado)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_las_formas_de_pagar_no_cancelan(self):
        for frase in (
            "Cancelo completo",
            "¿Cuánto le cancelo?",
            "Lo cancelo por Nequi",
            "Ya cancelé, ahí le mando el comprobante",
            "Cancelo con 20 mil",
            "Cancelo en efectivo",
        ):
            with self.subTest(frase=frase):
                self.assertIn("ERROR", self._cancelar(frase), frase)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_con_una_frase_de_pago_no_se_cancela_nada(self):
        """El caso de Natt: contestaba con qué billete pagaba."""
        with self._lectura("pagar"):
            resultado = self._cancelar("Cancelo completo")
        self.assertIn("ERROR", resultado)
        self.assertIn("PAGAR", resultado)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_lo_dudoso_tampoco_cancela(self):
        """Una pregunta, una condición o un cambio de producto no son órdenes."""
        with self._lectura("dudoso"):
            resultado = self._cancelar("¿Se puede anular?")
        self.assertIn("ERROR", resultado)
        self.assertIn("no pide anular nada", resultado)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_quien_sí_pide_anular_lo_consigue(self):
        with self._lectura("anular"):
            resultado = self._cancelar("Cancélame el pedido, ya no lo quiero")
        self.assertIn("CANCELADO", resultado)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

    def test_si_el_modelo_falla_el_pedido_se_queda_quieto(self):
        """El lado seguro del error: sin lectura no se toca nada."""
        from . import intencion

        with patch(
            "apps.whatsapp.media._openai_client", side_effect=RuntimeError("sin red")
        ):
            self.assertEqual(intencion.leer_cancelar("Cancélalo"), "dudoso")

    def test_lo_que_el_modelo_conteste_raro_es_dudoso(self):
        from . import intencion

        for respuesta in ("Anular el pedido", "", "sí", "PAGAR."):
            with self.subTest(respuesta=respuesta):
                with self._lectura_cruda(respuesta):
                    self.assertEqual(intencion.leer_cancelar("Cancélalo"), "dudoso")

    def test_la_lectura_va_con_el_modelo_barato(self):
        """No es trabajo del modelo del agente: una palabra, sin razonar."""
        from django.conf import settings as dj

        from . import intencion

        with self._lectura_cruda("anular") as cliente:
            intencion.leer_cancelar("Cancélalo")
        kwargs = cliente.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], dj.WHATSAPP_SUMMARY_MODEL)
        self.assertNotEqual(dj.WHATSAPP_SUMMARY_MODEL, dj.WHATSAPP_AGENT_MODEL)

    def test_un_pedido_de_otro_día_no_se_le_explica_al_cliente(self):
        """El de otro día ya terminó: hablar de él fue lo que confundió a Natt.

        Pero si el cliente está reclamando justo ese, callarse lo deja solo: el
        error le deja al agente las dos salidas.
        """
        self.order.status = Order.Status.DELIVERED
        self.order.save()
        ayer = timezone.now() - datetime.timedelta(days=2)
        Order.objects.filter(pk=self.order.pk).update(created_at=ayer)
        with self._lectura("anular"):
            resultado = self._cancelar("Cancélame el pedido, ya no lo quiero")
        self.assertIn("ERROR", resultado)
        self.assertIn("NO se lo cuentes", resultado)
        self.assertIn("solicitar_humano", resultado)

    def test_el_pedido_de_hoy_marcado_entregado_se_escala(self):
        """Si el equipo lo cerró por error, callarse deja al cliente sin nadie."""
        self.order.status = Order.Status.DELIVERED
        self.order.save()
        with self._lectura("anular"):
            resultado = self._cancelar("Cancélame el pedido, nunca llegó")
        self.assertIn("ERROR", resultado)
        self.assertIn("solicitar_humano", resultado)
        self.assertNotIn("otro día", resultado)


@unittest.skipUnless(
    os.getenv("PROBAR_INTENCION_CON_EL_MODELO") == "1",
    "llama al modelo de verdad; se corre a mano: PROBAR_INTENCION_CON_EL_MODELO=1",
)
class LecturaDeCancelarConElModeloTests(TestCase):
    """La matriz contra el modelo de verdad, no contra un doble.

    Estas 38 frases son las que tumbaron cuatro rondas de expresiones
    regulares: cada patrón nuevo arreglaba tres casos y abría otros tres, hasta
    que quedó claro que el sentido de la frase no cabe en una lista. El modelo
    barato las lee todas bien. Se corre a mano cuando se toque el prompt de
    intencion.py, que es lo único que puede romperlas.
    """

    PAGAR = (
        "Cancelo completo",
        "¿Cuánto le cancelo?",
        "Lo cancelo por Nequi",
        "Ya cancelé, ahí le mando el comprobante",
        "Cancelo con 20 mil",
        "Cancelo con 20000",
        "Cancelo en efectivo",
        "Ya no tengo efectivo, cancelo por Nequi",
        "Cancelo el pedido al recibir",
        "Cancelé el pedido al recibir",
        "Voy a cancelar el pedido completo",
        "Quiero cancelar el pedido por Nequi",
        # Las dos que escribió Natt el 20-09
        "Es decir que canceló el pedido con 10 mil pesos",
        "Ya cancelé",
    )
    ANULAR = (
        "Cancélame el pedido por favor",
        "Ya no lo quiero, cancélalo",
        "Mejor no, déjalo así",
        "Cancélalo",
        "Cancélalo, mi familia ya comió",
        "cancelame el domicilio que ya no estoy en la casa",
        "Cancelar el pedido por favor",
        "Cancelen el pedido",
        "anúlalo",
        "Ya no quiero nada",
        # La negación es del motivo, no de la orden
        "No voy a estar en casa cancélalo",
        "No puedo ir a recogerlo cancelen el pedido",
    )
    DUDOSO = (
        # Prohibir no es mandar
        "No anules el pedido, ya pagué",
        "No quiero que lo anules",
        "No me vayas a cancelar el pedido",
        "Por favor nunca anules el pedido",
        "No quiero que me vayan a cancelar el pedido",
        # Preguntar tampoco
        "¿Me cancelaron el pedido?",
        "¿Se puede anular?",
        "¿Qué pasa si quiero cancelar el pedido?",
        # Ni poner una condición que el agente no puede evaluar solo
        "Si no hay de mango, cancélalo",
        # Cambiar un producto no es cancelar el pedido entero
        "No lo quiero con whisky",
        "Ya no quiero la hamburguesa, solo las papas",
        "Mejor no le ponga cebolla",
    )

    def _leer(self, frases):
        from concurrent.futures import ThreadPoolExecutor

        from . import intencion

        with ThreadPoolExecutor(10) as pool:
            return list(pool.map(intencion.leer_cancelar, frases))

    def test_ninguna_frase_de_pago_anula_el_pedido(self):
        """El fallo grave: cancelarle el pedido a quien estaba pagando."""
        for frase, lectura in zip(self.PAGAR, self._leer(self.PAGAR)):
            with self.subTest(frase=frase):
                self.assertEqual(lectura, "pagar", frase)

    def test_quien_pide_anular_se_entiende(self):
        for frase, lectura in zip(self.ANULAR, self._leer(self.ANULAR)):
            with self.subTest(frase=frase):
                self.assertEqual(lectura, "anular", frase)

    def test_lo_que_no_es_una_orden_no_anula(self):
        for frase, lectura in zip(self.DUDOSO, self._leer(self.DUDOSO)):
            with self.subTest(frase=frase):
                self.assertNotEqual(lectura, "anular", frase)


class AvisosEnRafagaTests(TransactionTestCase):
    """Tres estados de un tirón son un aviso, no tres.

    Chat real del 20-09: el equipo cerró el pedido de Angelly cuando ya se lo
    habían entregado y le marcó cocina, salida y entrega seguidas. A las
    21:17:39, 21:17:40 y 21:17:41 le llegaron los tres mensajes: "ya está en la
    cocina", "va en camino" y "entregado". Los dos primeros eran mentira en el
    momento en que los leyó.
    """

    def setUp(self):
        WhatsAppContact.objects.create(phone=PHONE, last_phone_number_id="pn-1")
        self.order = Order.objects.create(
            source=Order.Source.WHATSAPP,
            order_type=Order.OrderType.DELIVERY,
            customer_name="Angelly",
            customer_phone=PHONE,
            payment_method=Order.PaymentMethod.CASH,
        )

    def _cerrar_de_un_tiron(self):
        """Los tres estados seguidos, como los marca el equipo en el panel."""
        enviados = []
        with patch("apps.whatsapp.kapso.send_text", side_effect=lambda *a: enviados.append(a[2])):
            for status in (
                Order.Status.PREPARING,
                Order.Status.READY,
                Order.Status.DELIVERED,
            ):
                self.order.status = status
                self.order.save()
            time.sleep(1.2)  # más que la espera de los avisos
        return enviados

    @override_settings(WHATSAPP_STATUS_NOTICE_DELAY_SECONDS=0.4)
    def test_solo_llega_el_ultimo_estado(self):
        enviados = self._cerrar_de_un_tiron()
        self.assertEqual(len(enviados), 1, enviados)
        self.assertIn("entregado", enviados[0])

    @override_settings(WHATSAPP_STATUS_NOTICE_DELAY_SECONDS=0.4)
    def test_solo_queda_anotado_lo_que_de_verdad_salió(self):
        """Lo que no se envió se desmarca: no está avisado."""
        self._cerrar_de_un_tiron()
        self.order.refresh_from_db()
        self.assertEqual(self.order.notified_statuses, [Order.Status.DELIVERED])

    @override_settings(WHATSAPP_STATUS_NOTICE_DELAY_SECONDS=0.4)
    def test_otra_instancia_del_pedido_no_pisa_lo_ya_avisado(self):
        """El panel y el hilo del aviso miran el mismo pedido por separado.

        La lista de avisados no es del pedido, es del sistema de avisos, y un
        save() corriente lleva todas las columnas: la instancia que el panel
        cargó hace un minuto borraba lo que el hilo acababa de anotar, y el
        aviso no salía nunca.
        """
        enviados = []
        with patch("apps.whatsapp.kapso.send_text", side_effect=lambda *a: enviados.append(a[2])):
            self.order.status = Order.Status.READY
            self.order.save()
            otra = Order.objects.get(pk=self.order.pk)  # el panel, por su lado
            otra.status = Order.Status.PREPARING  # se equivocó de botón
            otra.save()
            time.sleep(1.2)
            otra.status = Order.Status.READY  # ahora sí salió
            otra.save()
            time.sleep(1.2)
        self.assertEqual(len(enviados), 2, enviados)
        self.assertIn("va en camino", enviados[-1])

    @override_settings(WHATSAPP_STATUS_NOTICE_DELAY_SECONDS=0.4)
    def test_el_estado_corregido_vuelve_a_avisar(self):
        """El equipo se equivoca de botón y lo devuelve: el aviso sigue debiéndose.

        Si el descarte marcara el estado como avisado, el cliente se quedaría
        sin saber nunca que su pedido salió.
        """
        enviados = []
        with patch("apps.whatsapp.kapso.send_text", side_effect=lambda *a: enviados.append(a[2])):
            self.order.status = Order.Status.READY
            self.order.save()
            self.order.status = Order.Status.PREPARING  # se equivocó de botón
            self.order.save()
            time.sleep(1.2)
            self.order.status = Order.Status.READY  # ahora sí salió
            self.order.save()
            time.sleep(1.2)
        self.assertEqual(len(enviados), 2, enviados)
        self.assertIn("en la cocina", enviados[0])
        self.assertIn("va en camino", enviados[1])


class NumeroDePedidoTests(TestCase):
    """El número lleva la fecha local, no la de UTC.

    Después de las 19:00 en Colombia, timezone.now() ya es del día siguiente:
    cinco de los once pedidos de WhatsApp nacieron con la fecha de mañana y el
    agente se la explicó a un cliente como "la fecha en que quedó creado".
    """

    def test_el_numero_usa_la_fecha_local(self):
        order = Order.objects.create(
            source=Order.Source.WHATSAPP,
            order_type=Order.OrderType.DELIVERY,
            customer_name="Daniel",
            customer_phone=PHONE,
        )
        self.assertTrue(
            order.order_number.startswith(timezone.localdate().strftime("%Y%m%d")),
            order.order_number,
        )

    def test_un_pedido_de_la_noche_no_nace_con_la_fecha_de_manana(self):
        """Las 20:36 del 15/09 en Cumbal son las 01:36 del 16/09 en UTC."""
        noche = timezone.now().replace(hour=1, minute=36)  # UTC
        with patch("django.utils.timezone.now", return_value=noche):
            order = Order.objects.create(
                source=Order.Source.WHATSAPP,
                order_type=Order.OrderType.DELIVERY,
                customer_name="Daniel",
                customer_phone=PHONE,
            )
        esperado = timezone.localtime(noche).strftime("%Y%m%d")
        self.assertTrue(order.order_number.startswith(esperado), order.order_number)


class OpcionesQueNoSeCobranTests(TestCase):
    """Una opción con recargo no se ofrece mientras el pedido no sepa cobrarla.

    El 15/09, el pedido 20260916-6B022D salió con "Salchipapa con Queso
    (Personal)" más "Salchicha ranchera" (+$5.000) cobrada a $18.000: las
    elecciones viajan como texto en las notas del item y cotizar_pedido solo
    multiplica el precio de la variante. El campo price_delta se escribe en el
    panel y no lo cobra nadie, en ningún canal.
    """

    def setUp(self):
        from apps.business.models import Business
        from apps.products.models import (
            Category,
            ModifierGroup,
            ModifierOption,
            Product,
            ProductModifierGroup,
            ProductVariant,
        )

        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food", defaults={"name": "Frostbyte Food"}
        )
        categoria = Category.objects.create(name="Salchipapas", slug="salchipapas", business=food)
        self.producto = Product.objects.create(
            name="Salchipapa con Queso",
            slug="salchipapa-con-queso",
            category=categoria,
            business=food,
            description="La clásica con queso fundido",
        )
        ProductVariant.objects.create(
            product=self.producto, name="Personal", sku="SPQ-1", price=18000
        )
        self.carnes = ModifierGroup.objects.create(
            name="Elige tu carne", business=food, min_select=1, max_select=1
        )
        for nombre in ("Res", "Cerdo", "Pollo"):
            ModifierOption.objects.create(group=self.carnes, name=nombre, price_delta=0)
        self.adiciones = ModifierGroup.objects.create(
            name="Adiciones", business=food, min_select=0, max_select=7
        )
        for nombre, precio in (("Queso fundido", 2000), ("Salchicha ranchera", 5000)):
            ModifierOption.objects.create(group=self.adiciones, name=nombre, price_delta=precio)
        for orden, grupo in enumerate((self.carnes, self.adiciones)):
            ProductModifierGroup.objects.create(
                product=self.producto, group=grupo, display_order=orden
            )
        contact = WhatsAppContact.objects.create(phone=PHONE)
        self.tools = {t.name: t for t in build_tools(contact)}

    def _detalle(self):
        return self.tools["consultar_producto"].invoke({"producto_slug": "salchipapa-con-queso"})

    def test_una_opcion_con_recargo_no_se_ofrece(self):
        detalle = self._detalle()
        self.assertNotIn("Salchicha ranchera", detalle)
        self.assertNotIn("Queso fundido", detalle)

    def test_el_grupo_entero_de_recargos_desaparece(self):
        self.assertNotIn("Adiciones", self._detalle())

    def test_las_opciones_sin_recargo_se_siguen_ofreciendo(self):
        detalle = self._detalle()
        self.assertIn("Elige tu carne", detalle)
        for carne in ("Res", "Cerdo", "Pollo"):
            self.assertIn(carne, detalle)

    def test_el_precio_de_la_variante_sigue_ahi(self):
        self.assertIn("$18.000", self._detalle())

    def test_un_producto_solo_con_recargos_no_se_anuncia_personalizable(self):
        """Si no queda nada que preguntar, el menú no puede decir que lo hay."""
        from apps.products.models import ProductModifierGroup

        ProductModifierGroup.objects.filter(group=self.carnes).delete()
        menu = self.tools["consultar_menu"].invoke({})
        self.assertIn("Salchipapa con Queso", menu)
        self.assertNotIn("personalizable", menu)

    def test_con_opciones_gratis_sigue_siendo_personalizable(self):
        self.assertIn("personalizable", self.tools["consultar_menu"].invoke({}))

    def test_la_busqueda_usa_el_mismo_criterio(self):
        from apps.products.models import ProductModifierGroup

        ProductModifierGroup.objects.filter(group=self.carnes).delete()
        resultado = self.tools["buscar_producto"].invoke({"texto": "salchipapa con queso"})
        self.assertIn("Salchipapa con Queso", resultado)
        self.assertNotIn("personalizable", resultado)


class MensajeViejoQueSeRelleTests(TestCase):
    """Abrir el chat en la app no es escribir en él.

    Chat real del 13/09: a las 15:15 llegó un webhook con un saliente del
    equipo… escrito el 1 de septiembre, doce días antes. Alguien abrió la
    conversación en la app de WhatsApp Business, el mensaje pasó a "leído" y
    Kapso lo reenvió con su texto original. El backend lo leyó como que un
    humano acababa de intervenir y pausó al agente treinta minutos. Un minuto
    después Natalia escribió "Buenas tardes, quiero realizar un pedido" y sus
    cinco mensajes quedaron sin respuesta hasta que una persona la atendió.
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=BSUID, wa_user_id=BSUID)

    def _procesar(self, payload, key):
        from .worker import _handle_outbound, extract_outbound_messages

        event = WebhookEvent.objects.create(
            idempotency_key=key, payload=payload, event_type="whatsapp.message.sent"
        )
        _handle_outbound(event, extract_outbound_messages(payload))
        self.contact.refresh_from_db()
        return event

    def test_un_mensaje_de_hace_dias_no_pausa_al_agente(self):
        viejo = timezone.now() - datetime.timedelta(days=12)
        self._procesar(app_reply_payload("Su pedido va en camino", sent_at=viejo), "viejo-1")
        self.assertIsNone(self.contact.human_until)

    def test_el_mensaje_viejo_tampoco_entra_al_hilo_otra_vez(self):
        viejo = timezone.now() - datetime.timedelta(days=12)
        with patch("apps.whatsapp.agent.record_messages") as record:
            self._procesar(app_reply_payload("Su pedido va en camino", sent_at=viejo), "viejo-2")
        record.assert_not_called()

    def test_lo_que_el_equipo_acaba_de_escribir_sigue_pausando(self):
        self._procesar(app_reply_payload("Buenas tardes si señor"), "nuevo-1")
        self.assertIsNotNone(self.contact.human_until)
        self.assertGreater(self.contact.human_until, timezone.now())

    def test_un_retraso_normal_de_kapso_sigue_contando_como_intervencion(self):
        """Kapso entrega en segundos y se le han visto 3 min de retraso."""
        hace_poco = timezone.now() - datetime.timedelta(minutes=3)
        self._procesar(app_reply_payload("Ya va en camino", sent_at=hace_poco), "nuevo-2")
        self.assertIsNotNone(self.contact.human_until)

    def test_un_saliente_sin_timestamp_se_sigue_tratando_como_humano(self):
        """Si Kapso no manda la hora, no se puede descartar: pausa."""
        payload = app_reply_payload("Ubicación porfa")
        del payload["data"][0]["message"]["timestamp"]
        self._procesar(payload, "sin-hora")
        self.assertIsNotNone(self.contact.human_until)


class AcusesDelMismoMensajeTests(TestCase):
    """Los acuses de recibo no son mensajes nuevos.

    WhatsApp manda un webhook por cada estado (enviado, entregado, leído) del
    MISMO mensaje: al chat de Anyi del 15/09 le llegaron 38 webhooks de un solo
    wamid. Cada uno renovaba la pausa otros minutos y metía otra copia del
    texto en el hilo del modelo.
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=BSUID, wa_user_id=BSUID)

    def _procesar(self, payload, key):
        from .worker import _handle_outbound, extract_outbound_messages

        event = WebhookEvent.objects.create(
            idempotency_key=key, payload=payload, event_type="whatsapp.message.sent"
        )
        _handle_outbound(event, extract_outbound_messages(payload))
        self.contact.refresh_from_db()
        return event

    def test_el_segundo_acuse_no_renueva_la_pausa(self):
        payload = app_reply_payload("Buenas noches", wamid="wamid.mismo")
        self._procesar(payload, "acuse-1")
        primera = self.contact.human_until
        self.assertIsNotNone(primera)
        self._procesar(payload, "acuse-2")  # el mismo mensaje, ahora "leído"
        self.assertEqual(self.contact.human_until, primera)

    def test_el_texto_no_se_duplica_en_el_hilo(self):
        payload = app_reply_payload("Dime qué necesitas?", wamid="wamid.mismo2")
        with patch("apps.whatsapp.agent.record_messages") as record:
            self._procesar(payload, "dup-1")
            self._procesar(payload, "dup-2")
            self._procesar(payload, "dup-3")
        self.assertEqual(record.call_count, 1)

    def test_un_mensaje_distinto_del_equipo_si_renueva(self):
        self._procesar(app_reply_payload("Buenas noches", wamid="wamid.a"), "a")
        primera = self.contact.human_until
        self._procesar(app_reply_payload("Que sabor?", wamid="wamid.b"), "b")
        self.assertGreater(self.contact.human_until, primera)


class PausaCortaTests(TestCase):
    """Jaime (15/09): "si el humano interviene, Frosty no se pause tanto".

    La pausa se renueva con cada mensaje del equipo, así que mientras alguien
    atienda no se acaba; lo que cambia es cuánto espera el cliente desde el
    último mensaje del equipo hasta que el agente vuelve a estar disponible.
    """

    def test_la_pausa_por_intervencion_es_corta(self):
        from django.conf import settings as dj

        self.assertLessEqual(dj.WHATSAPP_HUMAN_PAUSE_MINUTES, 15)

    def test_la_escalada_pausa_mas_que_una_intervencion_pero_tambien_caduca(self):
        from django.conf import settings as dj

        self.assertGreater(dj.WHATSAPP_HANDOFF_PAUSE_MINUTES, dj.WHATSAPP_HUMAN_PAUSE_MINUTES)
        self.assertLessEqual(dj.WHATSAPP_HANDOFF_PAUSE_MINUTES, 12 * 60)


class UbicacionQueSeRecuerdaTests(TestCase):
    """Al que ya nos dio su ubicación no se le pide otra vez.

    Jaime (15/09): "quiero que la ubicación se guarde y, cuando alguien vuelve
    a pedir, decir a la misma o a otra; si dice a la misma, ya no decir que
    envíe la ubicación".
    """

    def setUp(self):
        from django.conf import settings as dj_settings

        self.contact = WhatsAppContact.objects.create(phone=PHONE, customer_name="Daniel")
        self.centro = (dj_settings.DELIVERY_CENTER_LAT, dj_settings.DELIVERY_CENTER_LNG)
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def _con_ubicacion(self, label="", dias=0):
        self.contact.last_location_lat = Decimal(str(round(self.centro[0], 7)))
        self.contact.last_location_lng = Decimal(str(round(self.centro[1], 7)))
        self.contact.last_location_at = timezone.now() - datetime.timedelta(days=dias)
        self.contact.last_location_label = label
        self.contact.save()

    def test_la_ubicacion_compartida_guarda_como_se_llama(self):
        from .worker import extract_inbound_messages

        payload = webhook_payload("", 1)
        payload["data"][0]["message"]["type"] = "location"
        payload["data"][0]["message"]["location"] = {
            "latitude": self.centro[0],
            "longitude": self.centro[1],
            "name": "Mundo Fotográfico",
            "address": "Cl. 19 #10-7",
        }
        del payload["data"][0]["message"]["text"]
        mensajes = extract_inbound_messages(payload)
        self.assertEqual(
            mensajes[0]["location"]["label"], "Mundo Fotográfico · Cl. 19 #10-7"
        )

    def test_el_historial_dice_que_no_se_la_vuelva_a_pedir(self):
        self._con_ubicacion(label="Mundo Fotográfico · Cl. 19 #10-7", dias=2)
        historial = self.tools["consultar_historial_cliente"].invoke({})
        self.assertIn("Mundo Fotográfico", historial)
        self.assertIn("NO le pidas que la comparta otra vez", historial)
        self.assertIn("mismo sitio", historial)

    def test_sin_ubicacion_guardada_no_se_inventa_nada(self):
        historial = self.tools["consultar_historial_cliente"].invoke({})
        self.assertNotIn("Ubicación guardada", historial)

    def test_una_ubicacion_sin_nombre_tambien_se_ofrece(self):
        """WhatsApp casi nunca manda nombre ni dirección: igual la tenemos."""
        self._con_ubicacion(label="", dias=3)
        historial = self.tools["consultar_historial_cliente"].invoke({})
        self.assertIn("Ubicación guardada: la que compartió", historial)

    def test_la_cobertura_de_otro_dia_pregunta_en_vez_de_pedir(self):
        self._con_ubicacion(label="Mundo Fotográfico", dias=4)
        with patch("apps.whatsapp.tools.kapso.recent_undelivered", return_value=[]):
            resultado = self.tools["verificar_cobertura"].invoke({})
        self.assertIn("Mundo Fotográfico", resultado)
        self.assertIn("NO le pidas que la comparta de nuevo", resultado)
        self.assertIn("DENTRO de la zona", resultado)

    def test_el_prompt_manda_mirar_antes_de_pedir(self):
        prompt = build_system_prompt()
        self.assertIn("MIRA SI YA LA TIENES", prompt)
        self.assertIn("va al mismo sitio", prompt)


class ResumenDeLaConversacionTests(TestCase):
    """La conversación se resume cuando se hace larga, no se acarrea entera.

    Jaime (15/09): "cuántos mensajes estamos teniendo en cuenta para el
    contexto? no sería prudente poner toda la conversación de toda la vida,
    pero sí quiero tener un resumen". Medido ese día contra producción: el hilo
    se renueva cada día, y un día activo llega a 10.156 tokens de historial
    (Daniel el 13/09), la mayor parte respuestas de tools —menús y búsquedas—
    que ya no sirven una vez el pedido está armado.
    """

    def test_el_agente_lleva_el_middleware_de_resumen(self):
        from langchain.agents.middleware import SummarizationMiddleware

        middleware = agent_mod._summarization_middleware()
        self.assertIsInstance(middleware, SummarizationMiddleware)

    def test_resume_con_el_modelo_barato_y_no_con_el_del_agente(self):
        """Condensar es trabajo mecánico: el modelo caro se reserva para atender."""
        from django.conf import settings as dj

        self.assertNotEqual(dj.WHATSAPP_SUMMARY_MODEL, dj.WHATSAPP_AGENT_MODEL)
        middleware = agent_mod._summarization_middleware()
        self.assertEqual(middleware.model.model_name, dj.WHATSAPP_SUMMARY_MODEL)

    def test_conserva_literales_los_ultimos_mensajes(self):
        """El tramo final es donde se cierra el pedido: ahí no se resume nada."""
        middleware = agent_mod._summarization_middleware()
        self.assertEqual(middleware.keep, ("messages", 14))

    def test_el_prompt_del_resumen_protege_los_datos_del_pedido(self):
        prompt = agent_mod.SUMMARY_PROMPT
        self.assertIn("variante_id", prompt)
        self.assertIn("EXACTOS", prompt)
        self.assertIn("si el pedido ya se creó", prompt.lower())
        self.assertIn("LO QUE PROMETIÓ EL EQUIPO", prompt)

    def test_el_resumen_se_pide_en_espanol_y_sin_inventar(self):
        prompt = agent_mod.SUMMARY_PROMPT
        self.assertIn("en español", prompt)
        self.assertIn("sin inventar nada", prompt)
        self.assertNotIn("ARTIFACTS", prompt)  # el de fábrica es para agentes de código

    def test_el_hilo_es_del_cliente_y_no_del_dia(self):
        """Lo que acota el hilo es el resumen, no cortarlo cada mañana."""
        contact = WhatsAppContact.objects.create(phone=PHONE)
        self.assertEqual(agent_mod._thread_id(contact), f"wa:{PHONE}")
        self.assertNotIn(timezone.localdate().isoformat(), agent_mod._thread_id(contact))


class ResumenEnUnTurnoRealTests(TestCase):
    """El resumen corriendo dentro de un turno, con modelos falsos.

    Lo que hay que proteger no es la configuración sino que el turno siga
    funcionando: si el middleware rompe el grafo, el agente deja de contestar.
    """

    def _grafo(self, respuesta="Listo, va un granizado."):
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import (
            FakeMessagesListChatModel,
        )
        from langchain_core.messages import AIMessage

        self.resumidor = FakeMessagesListChatModel(
            responses=[AIMessage(content="QUIÉN ES: Prueba. QUÉ QUIERE: 1 granizado grande.")]
        )
        middleware = agent_mod._ResumenEnEspanol(
            model=self.resumidor,
            trigger=("tokens", 200),
            keep=("messages", 4),
            summary_prompt=agent_mod.SUMMARY_PROMPT,
        )
        return create_agent(
            model=FakeMessagesListChatModel(responses=[AIMessage(content=respuesta)]),
            tools=[],
            system_prompt="eres frosty",
            middleware=[middleware],
        )

    def _conversacion_larga(self):
        from langchain_core.messages import AIMessage, HumanMessage

        mensajes = []
        for i in range(30):
            mensajes.append(HumanMessage(content=f"mensaje del cliente numero {i} con relleno"))
            mensajes.append(AIMessage(content=f"respuesta del agente numero {i} con relleno"))
        return mensajes

    def test_una_conversacion_larga_se_resume_y_el_turno_responde(self):
        from langchain_core.messages import HumanMessage

        entrada = self._conversacion_larga() + [HumanMessage(content="quiero un granizado")]
        salida = self._grafo().invoke({"messages": entrada})
        self.assertLess(len(salida["messages"]), len(entrada))
        self.assertEqual(salida["messages"][-1].content, "Listo, va un granizado.")

    def test_el_resumen_queda_en_el_hilo_presentado_en_espanol(self):
        from langchain_core.messages import HumanMessage

        entrada = self._conversacion_larga() + [HumanMessage(content="quiero un granizado")]
        salida = self._grafo().invoke({"messages": entrada})
        textos = [m.content for m in salida["messages"] if isinstance(m.content, str)]
        resumen = next(t for t in textos if "QUIÉN ES" in t)
        self.assertIn("Esto es lo que se ha hablado con el cliente", resumen)
        self.assertNotIn("Here is a summary", resumen)

    def test_una_conversacion_corta_no_gasta_el_resumidor(self):
        from langchain_core.messages import HumanMessage

        grafo = self._grafo()
        grafo.invoke({"messages": [HumanMessage(content="hola")]})
        self.assertEqual(len(self.resumidor.responses), 1, "no debió consumirse")


class ClienteQueVuelveTests(TestCase):
    """El cliente que ya pidió no vuelve a ser un desconocido cada mañana.

    Jaime (15/09): "si quiera el agente tenga contexto de las conversaciones, o
    conozca del usuario que ya pidió antes, sus productos favoritos... con el
    hilo de cada día no conocemos a la persona, sería como una persona nueva
    cada conversación aunque sea la misma y ya haya pedido antes".
    """

    def setUp(self):
        from apps.business.models import Business
        from apps.products.models import Category, Product, ProductVariant

        negocio, _ = Business.objects.get_or_create(
            slug="frostbyte", defaults={"name": "Frostbyte"}
        )
        categoria = Category.objects.create(name="Granizados", slug="granizados", business=negocio)
        self.maracuya = Product.objects.create(
            name="Granizado de Maracuyá", category=categoria, business=negocio, description="Fruta"
        )
        self.mora = Product.objects.create(
            name="Granizado de Mora", category=categoria, business=negocio, description="Fruta"
        )
        self.v_maracuya = ProductVariant.objects.create(
            product=self.maracuya, name="Grande", sku="GM-1", price=10000
        )
        self.v_mora = ProductVariant.objects.create(
            product=self.mora, name="Grande", sku="GMO-1", price=10000
        )
        self.contact = WhatsAppContact.objects.create(phone=PHONE, customer_name="Camila")
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def _pedido(self, variante, cantidad=1):
        from apps.orders.models import OrderItem

        order = Order.objects.create(
            source=Order.Source.WHATSAPP,
            order_type=Order.OrderType.DELIVERY,
            customer_name="Camila",
            customer_phone=PHONE,
            status=Order.Status.DELIVERED,
        )
        OrderItem.objects.create(
            order=order,
            product_variant=variante,
            quantity=cantidad,
            unit_price=variante.price,
            subtotal=variante.price * cantidad,
        )
        return order

    def test_el_historial_dice_lo_que_mas_pide(self):
        for _ in range(3):
            self._pedido(self.v_maracuya)
        self._pedido(self.v_mora)
        historial = self.tools["consultar_historial_cliente"].invoke({})
        self.assertIn("Lo que más pide: Granizado de Maracuyá", historial)

    def test_un_producto_pedido_una_sola_vez_no_es_un_favorito(self):
        self._pedido(self.v_mora)
        historial = self.tools["consultar_historial_cliente"].invoke({})
        self.assertNotIn("Lo que más pide", historial)

    def test_el_hilo_no_lleva_la_fecha(self):
        self.assertEqual(agent_mod._thread_id(self.contact), f"wa:{PHONE}")

    def test_tras_unos_dias_el_turno_avisa_de_que_es_otra_conversacion(self):
        self.contact.last_message_at = timezone.now() - datetime.timedelta(days=3)
        corte = agent_mod._corte_de_sesion(self.contact)
        self.assertIn("conversación NUEVA", corte)
        self.assertIn("3 días", corte)
        self.assertIn("NINGÚN pedido de entonces sigue vivo", corte)

    def test_dentro_de_la_misma_charla_no_se_corta_nada(self):
        self.contact.last_message_at = timezone.now() - datetime.timedelta(minutes=20)
        self.assertEqual(agent_mod._corte_de_sesion(self.contact), "")

    def test_un_cliente_nuevo_no_tiene_nada_que_separar(self):
        self.contact.last_message_at = None
        self.assertEqual(agent_mod._corte_de_sesion(self.contact), "")

    def test_el_prompt_le_dice_que_conoce_al_cliente(self):
        prompt = build_system_prompt()
        self.assertIn("lo tratas como lo que es: alguien", prompt)
        self.assertIn("un pedido de otro día NO sigue vivo", prompt)


class AcusesEnParaleloTests(TestCase):
    """El acuse que llega mientras se procesa el primero tampoco es un mensaje.

    Preguntar "¿ya lo tratamos?" y registrarlo eran dos operaciones separadas,
    y los acuses del mismo mensaje llegan con uno o dos segundos de diferencia
    a cuatro hilos del pool: los tres podían ver la tabla vacía y los tres
    escribían. Aquí se anula el atajo para reproducir esa carrera; quien tiene
    que cortarla es el unique de wamid (ChatMessage.claim).
    """

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone=BSUID, wa_user_id=BSUID)
        atajo = patch("apps.whatsapp.worker._ya_tratado", return_value=False)
        atajo.start()
        self.addCleanup(atajo.stop)

    def _procesar(self, payload, key):
        from .worker import _handle_outbound, extract_outbound_messages

        event = WebhookEvent.objects.create(
            idempotency_key=key, payload=payload, event_type="whatsapp.message.sent"
        )
        _handle_outbound(event, extract_outbound_messages(payload))
        self.contact.refresh_from_db()
        event.refresh_from_db()
        return event

    def test_el_texto_entra_una_sola_vez_al_hilo(self):
        payload = app_reply_payload("Paga en efectivo o nequi,?", wamid="wamid.carrera")
        with patch("apps.whatsapp.agent.record_messages") as record:
            self._procesar(payload, "carrera-1")
            self._procesar(payload, "carrera-2")
            self._procesar(payload, "carrera-3")
        self.assertEqual(record.call_count, 1)
        self.assertEqual(ChatMessage.objects.filter(wamid="wamid.carrera").count(), 1)

    def test_el_acuse_no_renueva_la_pausa(self):
        payload = app_reply_payload("Ya sale", wamid="wamid.carrera2")
        with patch("apps.whatsapp.agent.record_messages"):
            self._procesar(payload, "carrera2-1")
            primera = self.contact.human_until
            self._procesar(payload, "carrera2-2")
        self.assertIsNotNone(primera)
        self.assertEqual(self.contact.human_until, primera)

    def test_el_evento_repetido_queda_marcado_como_acuse(self):
        payload = app_reply_payload("Listo", wamid="wamid.carrera3")
        with patch("apps.whatsapp.agent.record_messages"):
            self._procesar(payload, "carrera3-1")
            event = self._procesar(payload, "carrera3-2")
        self.assertEqual(event.status, WebhookEvent.Status.IGNORED)
        self.assertIn("acuses", event.error)


class MensajeEntranteRepetidoTests(TestCase):
    """El mismo mensaje del cliente en dos webhooks se contesta una vez.

    Kapso reparte claves de idempotencia distintas, así que el filtro del
    webhook no reconoce el reenvío; el wamid del mensaje sí. Sin esto, un
    reenvío significaría dos turnos del agente sobre lo mismo.
    """

    def _procesar(self, payload, key):
        from .worker import _process_event

        event = WebhookEvent.objects.create(idempotency_key=key, payload=payload)
        _process_event(event)
        event.refresh_from_db()
        return event

    def test_el_segundo_webhook_no_encola_otro_turno(self):
        payload = webhook_payload("hola, quiero pedir", message_id="wamid.repe")
        with patch("apps.whatsapp.worker._enqueue_turn") as encolar, self.settings(**FAST):
            self._procesar(payload, "repe-1")
            evento = self._procesar(payload, "repe-2")
        self.assertEqual(encolar.call_count, 1)
        self.assertEqual(ChatMessage.objects.filter(wamid="wamid.repe").count(), 1)
        self.assertEqual(evento.status, WebhookEvent.Status.IGNORED)
        self.assertIn("recibido", evento.error)

    def test_un_mensaje_nuevo_del_mismo_cliente_si_encola(self):
        with patch("apps.whatsapp.worker._enqueue_turn") as encolar, self.settings(**FAST):
            self._procesar(webhook_payload("hola", message_id="wamid.uno"), "uno")
            self._procesar(webhook_payload("¿hay pecera?", message_id="wamid.dos"), "dos")
        self.assertEqual(encolar.call_count, 2)


RESCATE = dict(
    WHATSAPP_RESCUE_AFTER_MINUTES=3,
    WHATSAPP_RESCUE_WINDOW_MINUTES=60,
    WHATSAPP_AGENT_ENABLED=True,
)


@override_settings(**RESCATE)
class VigiaDeClientesSinRespuestaTests(TestCase):
    """El cliente que quedó esperando no tiene que volver a escribir.

    La cola de turnos vive en la memoria del proceso: un despliegue en hora de
    servicio se lleva por delante lo que esté dentro. El 16-09 hubo tres entre
    las 22:47 y las 23:43. El barrido es la red debajo de eso.
    """

    def setUp(self):
        from . import watchdog

        self.watchdog = watchdog
        worker._pending.clear()
        worker._active.clear()
        self.contact = WhatsAppContact.objects.create(
            phone=PHONE,
            last_phone_number_id=PHONE_NUMBER_ID,
            last_message_at=timezone.now() - datetime.timedelta(minutes=10),
        )
        # El hilo del modelo no tiene el mensaje: es el caso del turno perdido
        hilo = patch("apps.whatsapp.agent.ultimo_del_cliente", return_value="")
        self.hilo = hilo.start()
        self.addCleanup(hilo.stop)

    def _mensaje(self, body="hay pecera granizada?", minutos=10, wamid="wamid.espera",
                 direction=ChatMessage.Direction.INBOUND):
        msg = ChatMessage.objects.create(
            wamid=wamid, phone=PHONE, direction=direction, body=body,
            author=(
                ChatMessage.Author.CUSTOMER
                if direction == ChatMessage.Direction.INBOUND
                else ChatMessage.Author.AGENT
            ),
        )
        cuando = timezone.now() - datetime.timedelta(minutes=minutos)
        ChatMessage.objects.filter(pk=msg.pk).update(created_at=cuando)
        msg.refresh_from_db()
        return msg

    def _barrer(self, replies=("Claro, sí hay. ¿Te la dejo lista?",)):
        enviados = []
        turn = AgentTurn(replies=replies, message_ids=(), mutated=False)
        with patch("apps.whatsapp.agent.run_turn", return_value=turn) as correr, patch(
            "apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0
        ), patch(
            "apps.whatsapp.kapso.send_text",
            side_effect=lambda pid, to, text: enviados.append(text),
        ):
            rescatados = self.watchdog.barrer()
        self.contact.refresh_from_db()
        return rescatados, enviados, correr

    def test_al_que_quedo_esperando_se_le_contesta(self):
        self._mensaje()
        rescatados, enviados, _ = self._barrer()
        self.assertEqual(rescatados, 1)
        self.assertEqual(enviados, ["Claro, sí hay. ¿Te la dejo lista?"])
        self.assertEqual(self.contact.rescued_wamid, "wamid.espera")

    def test_el_mensaje_perdido_se_le_entrega_al_agente(self):
        self._mensaje()
        _, _, correr = self._barrer()
        texto = correr.call_args.args[1]
        self.assertIn("hay pecera granizada?", texto)
        self.assertIn("sin responder", texto)

    def test_lo_que_el_hilo_ya_tiene_no_se_le_repite(self):
        """El turno murió después de meter el mensaje: dárselo otra vez sería
        contestarle dos veces lo mismo."""
        self._mensaje()
        self.hilo.return_value = "hay pecera granizada?"
        _, _, correr = self._barrer()
        texto = correr.call_args.args[1]
        self.assertNotIn("hay pecera granizada?", texto)
        self.assertIn("sigue esperando", texto)

    def test_no_se_contesta_dos_veces_el_mismo_mensaje(self):
        self._mensaje()
        self._barrer()
        rescatados, enviados, correr = self._barrer()
        self.assertEqual(rescatados, 0)
        self.assertEqual(enviados, [])
        correr.assert_not_called()

    def test_si_el_agente_decide_callarse_no_sale_nada(self):
        """Un "gracias" no necesita respuesta, y el modelo es quien lo decide."""
        self._mensaje(body="listo, gracias!")
        rescatados, enviados, _ = self._barrer(replies=())
        self.assertEqual(enviados, [])
        self.assertEqual(rescatados, 1)  # atendido: no se vuelve a intentar

    def test_si_ya_le_respondimos_no_hay_rescate(self):
        self._mensaje(minutos=12)
        self._mensaje(
            body="Sí, tenemos", minutos=11, wamid="wamid.resp",
            direction=ChatMessage.Direction.OUTBOUND,
        )
        self.assertEqual(self.watchdog.esperando(), [])

    def test_no_se_pisa_a_la_persona_que_esta_atendiendo(self):
        self._mensaje()
        self.contact.human_until = timezone.now() + datetime.timedelta(minutes=5)
        self.contact.save(update_fields=["human_until"])
        self.assertEqual(self.watchdog.esperando(), [])

    def test_el_interruptor_manual_del_panel_manda(self):
        self._mensaje()
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(human_handoff=True)
        self.assertEqual(self.watchdog.esperando(), [])

    def test_la_pausa_vencida_deja_pasar_al_agente(self):
        """Nadie del equipo volvió a escribir: el cliente sigue esperando."""
        self._mensaje()
        self.contact.human_until = timezone.now() - datetime.timedelta(minutes=1)
        self.contact.save(update_fields=["human_until"])
        self.assertEqual(len(self.watchdog.esperando()), 1)

    def test_no_revive_una_conversacion_de_ayer(self):
        self._mensaje(minutos=180)
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(
            last_message_at=timezone.now() - datetime.timedelta(minutes=180)
        )
        self.assertEqual(self.watchdog.esperando(), [])

    def test_el_turno_normal_tiene_su_oportunidad_primero(self):
        """Recién llegado no es lo mismo que sin respuesta: el agrupado de
        mensajes puede estar esperando todavía a que el cliente termine."""
        self._mensaje(minutos=1)
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(
            last_message_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        self.assertEqual(self.watchdog.esperando(), [])

    def test_no_se_mete_con_un_turno_vivo(self):
        self._mensaje()
        worker._active.add(PHONE)
        self.addCleanup(worker._active.discard, PHONE)
        self.assertEqual(self.watchdog.esperando(), [])

    def test_el_contacto_bloqueado_sigue_ignorado(self):
        self._mensaje()
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(is_blocked=True)
        self.assertEqual(self.watchdog.esperando(), [])

    def test_con_el_agente_apagado_no_barre(self):
        self._mensaje()
        with override_settings(WHATSAPP_AGENT_ENABLED=False):
            rescatados, enviados, correr = self._barrer()
        self.assertEqual(rescatados, 0)
        correr.assert_not_called()

    def test_un_sticker_tambien_cuenta_como_respuesta(self):
        """Un turno puede contestar solo con un sticker o una foto: eso no deja
        texto en el archivo, pero sí el wamid de lo enviado."""
        ultimo = self._mensaje()
        SentMessage.objects.create(wamid="wamid.sticker", to_phone=PHONE)
        self.assertEqual(self.watchdog.esperando(), [])
        SentMessage.objects.filter(wamid="wamid.sticker").update(
            created_at=ultimo.created_at - datetime.timedelta(minutes=1)
        )
        self.assertEqual(len(self.watchdog.esperando()), 1)

    def test_el_mensaje_que_llega_mientras_tanto_cancela_el_rescate(self):
        """Entre el barrido y el turno el cliente escribió: lo atiende el
        camino normal, con todo junto, y el vigía se aparta."""
        ultimo = self._mensaje()
        worker._active.add(PHONE)
        self.addCleanup(worker._active.discard, PHONE)
        with patch("apps.whatsapp.agent.run_turn") as correr:
            self.assertIsNone(self.watchdog.rescatar(self.contact, ultimo))
        correr.assert_not_called()

    def _del_equipo(self, body="26.000", minutos=12):
        """Un mensaje que escribió una persona del equipo, no el agente."""
        msg = ChatMessage.objects.create(
            wamid=f"wamid.humano.{minutos}",
            phone=PHONE,
            direction=ChatMessage.Direction.OUTBOUND,
            author=ChatMessage.Author.HUMAN,
            body=body,
        )
        cuando = timezone.now() - datetime.timedelta(minutes=minutos)
        ChatMessage.objects.filter(pk=msg.pk).update(created_at=cuando)
        return msg

    def test_tras_un_companero_el_vigia_avisa_que_no_reabra_el_pedido(self):
        """Chat real del 19-09: el equipo cerró el precio en 26.000, el cliente
        contestó "Gracias" y el vigía retomó con "¿Con qué billete vas a pagar,
        veci?", una pregunta que el humano ya había dejado resuelta."""
        self._del_equipo()
        ultimo = self._mensaje(body="Gracias")
        nota = self.watchdog._texto_del_turno(self.contact, ultimo, timezone.now())
        self.assertIn("un compañero del equipo estuvo atendiendo", nota)
        self.assertIn("no repitas sus preguntas", nota)
        # Dentro de los corchetes: ahí es donde el modelo lee al sistema. Fuera
        # se leería como algo que escribió el cliente
        self.assertLess(nota.index("un compañero del equipo"), nota.index("]"), nota)

    def test_sin_compañero_de_por_medio_la_nota_no_cambia(self):
        ultimo = self._mensaje(body="Gracias")
        nota = self.watchdog._texto_del_turno(self.contact, ultimo, timezone.now())
        self.assertNotIn("un compañero del equipo estuvo atendiendo", nota)

    def test_lo_que_el_compañero_escribio_despues_no_cuenta(self):
        """Solo pesa lo que el equipo dijo ANTES del mensaje del cliente."""
        ultimo = self._mensaje(body="Gracias", minutos=20)
        self._del_equipo(minutos=5)
        nota = self.watchdog._texto_del_turno(self.contact, ultimo, timezone.now())
        self.assertNotIn("un compañero del equipo estuvo atendiendo", nota)



class DomiciliosQueVuelvenTests(TestCase):
    """Al que se quedó sin domicilio se le avisa cuando vuelven a haberlos.

    Chat real 2026-08-19 y el mismo patrón varias veces más: el cliente pide un
    domicilio, están apagados porque no hay domiciliario, Frosty se lo dice, el
    cliente contesta "ah bueno, gracias" y ahí muere la venta. Media hora
    después vuelve a haber quien lo lleve y nadie se lo cuenta.

    Escribirle a quien no pidió nada es peor que perder la venta, así que casi
    todos estos tests son de gente que NO debe recibir el aviso.
    """

    def setUp(self):
        from apps.orders.models import StoreSettings

        from . import domicilios

        self.domicilios = domicilios
        worker._pending.clear()
        worker._active.clear()
        self.cfg = StoreSettings.load()
        self.cfg.is_open = True
        self.cfg.customer_ordering_enabled = True
        self.cfg.save()
        self._prendidos(minutos=30)
        self.contact = WhatsAppContact.objects.create(
            phone=PHONE,
            profile_name="Anyi V",
            last_phone_number_id=PHONE_NUMBER_ID,
            last_message_at=timezone.now() - datetime.timedelta(hours=2),
            delivery_missed_at=timezone.now() - datetime.timedelta(hours=2),
        )
        self.mensaje = self._escribio("hola, hacen domicilios?", horas=2)

    def _prendidos(self, minutos):
        """Los domicilios llevan prendidos ese rato."""
        cuando = timezone.now() - datetime.timedelta(minutes=minutos)
        type(self.cfg).objects.filter(pk=1).update(ordering_changed_at=cuando)
        self.cfg.refresh_from_db()

    def _escribio(self, body, horas=2, wamid="wamid.cliente", phone=PHONE):
        msg = ChatMessage.objects.create(
            wamid=wamid, phone=phone, direction=ChatMessage.Direction.INBOUND,
            body=body, author=ChatMessage.Author.CUSTOMER,
        )
        cuando = timezone.now() - datetime.timedelta(hours=horas)
        ChatMessage.objects.filter(pk=msg.pk).update(created_at=cuando)
        msg.refresh_from_db()
        return msg

    def _barrer(self, replies=("¡Ya tenemos domicilios! ¿Te mando la pecera?",)):
        enviados = []
        turn = AgentTurn(replies=replies, message_ids=("m1",), mutated=False)
        with patch("apps.whatsapp.agent.run_turn", return_value=turn) as correr, patch(
            "apps.whatsapp.agent.discard_turn"
        ), patch("apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0), patch(
            "apps.whatsapp.kapso.send_text",
            side_effect=lambda pid, to, text: enviados.append(text),
        ):
            avisados = self.domicilios.barrer()
        self.contact.refresh_from_db()
        return avisados, enviados, correr

    # --- el aviso ---

    def test_al_que_se_quedo_sin_domicilio_se_le_avisa(self):
        avisados, enviados, _ = self._barrer()
        self.assertEqual(avisados, 1)
        self.assertEqual(enviados, ["¡Ya tenemos domicilios! ¿Te mando la pecera?"])
        self.assertIsNotNone(self.contact.delivery_notified_at)

    def test_el_agente_recibe_el_contexto_y_la_licencia_para_callarse(self):
        _, _, correr = self._barrer()
        texto = correr.call_args.args[1]
        self.assertIn("NO había servicio de domicilios", texto)
        self.assertIn("ya volvió a haberlo", texto)
        self.assertIn("NO escribas nada", texto)
        self.assertTrue(correr.call_args.kwargs["silence_ok"])

    def test_si_no_esperaba_ningun_domicilio_no_sale_nada(self):
        """Lo pidió, pero el hilo dice que ya lo resolvió: el modelo se calla."""
        avisados, enviados, _ = self._barrer(replies=())
        self.assertEqual(enviados, [])
        self.assertEqual(avisados, 1)  # atendido: no se vuelve a intentar

    # --- nadie recibe dos mensajes ---

    def test_no_se_avisa_dos_veces_por_la_misma_reactivacion(self):
        self._barrer()
        avisados, enviados, correr = self._barrer()
        self.assertEqual(avisados, 0)
        self.assertEqual(enviados, [])
        correr.assert_not_called()

    def test_tampoco_en_la_siguiente_reactivacion_de_la_noche(self):
        """Apagar y prender otra vez no es un cliente nuevo esperando: si él no
        volvió a chocar con la puerta cerrada, no hay nada que contarle."""
        self._barrer()
        self._prendidos(minutos=5)
        avisados, enviados, correr = self._barrer()
        self.assertEqual(avisados, 0)
        self.assertEqual(enviados, [])
        correr.assert_not_called()

    def test_si_vuelve_a_chocar_con_la_puerta_cerrada_sí_se_le_avisa(self):
        self._barrer()
        self.contact.refresh_from_db()
        self.domicilios.anotar(self.contact)
        # Chocó con la puerta cerrada durante la segunda caída, antes de que
        # volvieran a prenderse
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(
            delivery_missed_at=timezone.now() - datetime.timedelta(minutes=10)
        )
        self._prendidos(minutos=5)
        avisados, enviados, _ = self._barrer()
        self.assertEqual(avisados, 1)
        self.assertEqual(len(enviados), 1)

    def test_dos_barridos_a_la_vez_solo_dejan_un_aviso(self):
        """Dos réplicas, o el proceso viejo y el nuevo durante un despliegue:
        el reclamo es un UPDATE condicionado, así que uno de los dos pierde."""
        otro = WhatsAppContact.objects.get(pk=self.contact.pk)
        ahora = timezone.now()
        self.assertTrue(self.domicilios.reclamar(self.contact, ahora))
        self.assertFalse(self.domicilios.reclamar(otro, ahora))

    def test_despues_del_aviso_el_vigia_no_lo_vuelve_a_tocar(self):
        """Aunque el modelo se calle y no quede ningún mensaje enviado, ese
        mensaje del cliente ya lo atendió este barrido."""
        from . import watchdog

        self._barrer(replies=())
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.rescued_wamid, self.mensaje.wamid)
        self.assertEqual(watchdog.esperando(), [])

    def test_un_fallo_del_aviso_no_deja_sin_correr_al_rescate(self):
        from . import watchdog

        with patch(
            "apps.whatsapp.domicilios.barrer", side_effect=RuntimeError("base caída")
        ), patch("apps.whatsapp.watchdog.barrer") as rescate:
            watchdog._una_vuelta()
        rescate.assert_called_once()

    def test_los_dos_barridos_miran_la_misma_hora(self):
        """El rescate tiene ventana máxima: si contara desde que le toca el
        turno, un aviso de domicilios lento dejaría fuera de ella a quien
        estaba en el borde y ese cliente no se rescataría nunca."""
        from . import watchdog

        with patch("apps.whatsapp.domicilios.barrer") as aviso, patch(
            "apps.whatsapp.watchdog.barrer"
        ) as rescate:
            watchdog._una_vuelta()
        self.assertIsNotNone(rescate.call_args.args[0])
        self.assertEqual(aviso.call_args.args, rescate.call_args.args)

    # --- quién no entra ---

    def test_el_que_nunca_pidio_domicilio_no_recibe_nada(self):
        """Preguntó la dirección con los domicilios apagados: la tool lo marcó,
        pero él nunca habló de que se lo llevaran."""
        ChatMessage.objects.all().delete()
        self._escribio("hola, dónde quedan ustedes?")
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_el_que_solo_venia_a_recoger_tampoco(self):
        ChatMessage.objects.all().delete()
        self._escribio("buenas, yo paso a recoger una pecera")
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_pedirlo_sin_la_palabra_domicilio_igual_cuenta(self):
        ChatMessage.objects.all().delete()
        self._escribio("me lo pueden llevar hasta mi casa?")
        self.assertEqual(len(self.domicilios.pendientes()), 1)

    def test_el_que_escribio_con_los_domicilios_ya_activos_no_entra(self):
        self.contact.delivery_missed_at = timezone.now()
        self.contact.save(update_fields=["delivery_missed_at"])
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_el_que_volvio_a_escribir_ya_con_servicio_tampoco_entra(self):
        """Marca vieja, pero la conversación siguió: volvió a preguntar con los
        domicilios ya prendidos y el camino normal lo atendió sabiendo que hay
        servicio. Escribirle encima sería el segundo mensaje sobre lo mismo; si
        se quedó sin respuesta, el que va es el rescate."""
        self._escribio(
            "y hacen domicilios hasta el barrio?", horas=0.25, wamid="wamid.despues"
        )
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_la_intencion_no_se_lee_de_un_mensaje_posterior_al_aviso(self):
        """Lo marcó una consulta cualquiera con la puerta cerrada y nunca pidió
        domicilio; el único mensaje que habla de domicilios es posterior a la
        reactivación, así que no autoriza nada."""
        ChatMessage.objects.all().delete()
        self._escribio("buenas, están abiertos?", horas=2)
        self._escribio("me lo llevan a la casa?", horas=0.25, wamid="wamid.despues")
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_fuera_de_la_ventana_de_whatsapp_no_se_le_escribe(self):
        """La ventana se cuelga de cuándo escribió ÉL, no de cuándo corrió
        nuestra tool: WhatsApp solo deja escribir primero dentro de las 24 h."""
        ChatMessage.objects.all().delete()
        self._escribio("hacen domicilios?", horas=21)
        self.contact.delivery_missed_at = timezone.now() - datetime.timedelta(minutes=40)
        self.contact.save(update_fields=["delivery_missed_at"])
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_una_ventana_mal_puesta_no_saca_el_aviso_de_las_24_horas(self):
        ChatMessage.objects.all().delete()
        self._escribio("hacen domicilios?", horas=23)
        with override_settings(WHATSAPP_DELIVERY_REENGAGE_WINDOW_HOURS=30):
            self.assertEqual(self.domicilios.pendientes(), [])

    def test_sin_ningun_mensaje_suyo_no_hay_aviso(self):
        ChatMessage.objects.all().delete()
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_recien_prendidos_no_se_avisa_todavia(self):
        """Por si fue un toque sin querer: apagarlos otra vez no cuesta nada."""
        self._prendidos(minutos=0)
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_con_los_domicilios_apagados_no_hay_nada_que_avisar(self):
        self.cfg.customer_ordering_enabled = False
        self.cfg.save()
        self.assertIsNone(self.domicilios.reactivacion())

    def test_con_el_local_cerrado_tampoco(self):
        self.cfg.is_open = False
        self.cfg.save()
        self.assertIsNone(self.domicilios.reactivacion())

    def test_sin_marca_nadie_entra(self):
        """El campo nace vacío: el día del despliegue no se barre el pasado."""
        self.contact.delivery_missed_at = None
        self.contact.save(update_fields=["delivery_missed_at"])
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_el_contacto_bloqueado_sigue_ignorado(self):
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(is_blocked=True)
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_no_se_pisa_a_la_persona_que_esta_atendiendo(self):
        self.contact.human_until = timezone.now() + datetime.timedelta(minutes=5)
        self.contact.save(update_fields=["human_until"])
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_el_interruptor_manual_del_panel_manda(self):
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(human_handoff=True)
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_no_se_mete_con_un_turno_vivo(self):
        worker._active.add(PHONE)
        self.addCleanup(worker._active.discard, PHONE)
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_sin_numero_por_donde_escribirle_no_hay_aviso(self):
        self.contact.last_phone_number_id = ""
        self.contact.save(update_fields=["last_phone_number_id"])
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_el_tope_por_reactivacion_corta_la_tanda(self):
        """El corte que en Ungga faltó: la primera corrida habría mandado
        ochenta avisos de golpe."""
        for i in range(4):
            phone = f"57300111000{i}"
            WhatsAppContact.objects.create(
                phone=phone,
                last_phone_number_id=PHONE_NUMBER_ID,
                delivery_missed_at=timezone.now() - datetime.timedelta(hours=1),
            )
            self._escribio("hacen domicilios?", horas=1, wamid=f"wamid.{i}", phone=phone)
        with override_settings(WHATSAPP_DELIVERY_REENGAGE_MAX=2):
            self.assertEqual(len(self.domicilios.pendientes()), 2)
            avisados, _, _ = self._barrer()
            self.assertEqual(avisados, 2)
            self.assertEqual(self.domicilios.pendientes(), [])

    def test_la_tanda_se_reparte_en_vueltas_para_no_frenar_al_rescate(self):
        """El rescate corre detrás de este barrido en el mismo hilo: diez
        turnos de modelo seguidos lo dejarían esperando minutos. El vigía
        vuelve cada minuto, así que repartir no es recortar."""
        for i in range(4):
            phone = f"57300222000{i}"
            WhatsAppContact.objects.create(
                phone=phone,
                last_phone_number_id=PHONE_NUMBER_ID,
                delivery_missed_at=timezone.now() - datetime.timedelta(hours=1),
            )
            self._escribio("hacen domicilios?", horas=1, wamid=f"wamid.v{i}", phone=phone)
        self.assertEqual(len(self.domicilios.pendientes()), self.domicilios.POR_VUELTA)

    def test_el_cupo_se_cuenta_otra_vez_antes_de_escribir(self):
        """Entre armar la lista y escribirle pueden haber salido avisos: una
        corrida a mano del command, o el proceso viejo durante un despliegue."""
        with override_settings(WHATSAPP_DELIVERY_REENGAGE_MAX=1):
            listos = self.domicilios.pendientes()
            self.assertEqual(len(listos), 1)
            WhatsAppContact.objects.create(
                phone="573002223333",
                last_phone_number_id=PHONE_NUMBER_ID,
                delivery_notified_at=timezone.now(),
            )
            with patch("apps.whatsapp.agent.run_turn") as correr:
                self.assertIsNone(self.domicilios.avisar(listos[0]))
            correr.assert_not_called()
        self.contact.refresh_from_db()
        self.assertIsNotNone(self.contact.delivery_missed_at)

    def test_si_entra_una_persona_del_equipo_mientras_piensa_no_sale_el_aviso(self):
        """El estado que hay en memoria es el de antes de pensar: entre el
        turno y el envío alguien del equipo pudo tomar la conversación."""
        def atendio(*args, **kwargs):
            WhatsAppContact.objects.filter(pk=self.contact.pk).update(human_handoff=True)
            return AgentTurn(
                replies=("¡Ya tenemos domicilios!",), message_ids=("m1",), mutated=False
            )

        enviados = []
        with patch("apps.whatsapp.agent.run_turn", side_effect=atendio), patch(
            "apps.whatsapp.agent.discard_turn"
        ) as descartar, patch("apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0), patch(
            "apps.whatsapp.kapso.send_text",
            side_effect=lambda pid, to, text: enviados.append(text),
        ):
            self.domicilios.barrer()
        self.assertEqual(enviados, [])
        descartar.assert_called_once()

    def test_el_aviso_se_piensa_sin_poder_mandar_nada(self):
        """Una foto o unos botones salen en el momento en que el modelo los pide.

        Y este aviso todavía puede descartarse entero después de pensarlo: si
        el turno hubiera podido mandar algo, el cliente se quedaría con la foto
        de un domicilio que al final no se le ofrece. Se le quita el
        phone_number_id, que es lo que enciende esas tools; el texto se
        entrega después, cuando ya se sabe que el aviso sigue siendo verdad.
        """
        vistos = []

        def mirar(contact, texto, **kwargs):
            vistos.append(kwargs)
            return AgentTurn(
                replies=("¡Ya tenemos domicilios!",), message_ids=("m1",), mutated=False
            )

        with patch("apps.whatsapp.agent.run_turn", side_effect=mirar), patch(
            "apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0
        ), patch("apps.whatsapp.kapso.send_text"):
            self.domicilios.barrer()
        self.assertEqual(len(vistos), 1, vistos)
        self.assertFalse(vistos[0].get("phone_number_id"), vistos[0])
        self.assertTrue(vistos[0].get("silence_ok"), vistos[0])

    def test_al_que_acaba_de_escribir_no_se_le_manda_encima(self):
        """Tiene un turno corriendo ahora mismo, y puede ser el de otro proceso.
        Lo tapa la misma regla: los domicilios llevan prendidos al menos el
        margen de reactivación, así que su mensaje es posterior a ella."""
        ChatMessage.objects.all().delete()
        self._escribio("me lo llevan a la casa?", horas=1, wamid="wamid.viejo")
        self.assertEqual(len(self.domicilios.pendientes()), 1)  # con esto solo, entra
        self._escribio("ahi sigo esperando", horas=0, wamid="wamid.recien")
        self.assertEqual(self.domicilios.pendientes(), [])

    def test_dos_candidatos_no_se_pasan_del_tope_con_el_ultimo_cupo(self):
        """Con un cupo y dos contactos distintos, el UPDATE condicional no
        arbitra —cada uno reclama su propia fila—, así que el tope lo tiene que
        sostener el reclamo transaccional."""
        otro = WhatsAppContact.objects.create(
            phone="573004445555",
            last_phone_number_id=PHONE_NUMBER_ID,
            delivery_missed_at=timezone.now() - datetime.timedelta(hours=1),
        )
        self._escribio("hacen domicilios?", horas=1, wamid="wamid.otro", phone=otro.phone)
        turn = AgentTurn(replies=("¡Ya hay domicilios!",), message_ids=("m1",), mutated=False)
        with override_settings(WHATSAPP_DELIVERY_REENGAGE_MAX=1), patch(
            "apps.whatsapp.agent.run_turn", return_value=turn
        ), patch("apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0), patch(
            "apps.whatsapp.kapso.send_text"
        ):
            self.assertIsNotNone(self.domicilios.avisar(self.contact))
            self.assertIsNone(self.domicilios.avisar(otro))
        otro.refresh_from_db()
        self.assertIsNone(otro.delivery_notified_at)

    def test_si_escribe_y_le_contestan_mientras_piensa_no_sale_el_aviso(self):
        """El turno normal entra, contesta y termina mientras el modelo prepara
        el aviso: para cuando se mira, _active ya está limpio y solo queda el
        mensaje del cliente en la base."""
        def contestaron(*args, **kwargs):
            self._escribio("bueno y ya hay domicilio?", horas=0, wamid="wamid.entretanto")
            return AgentTurn(
                replies=("¡Ya tenemos domicilios!",), message_ids=("m1",), mutated=False
            )

        enviados = []
        with patch("apps.whatsapp.agent.run_turn", side_effect=contestaron), patch(
            "apps.whatsapp.agent.discard_turn"
        ) as descartar, patch("apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0), patch(
            "apps.whatsapp.kapso.send_text",
            side_effect=lambda pid, to, text: enviados.append(text),
        ):
            self.domicilios.barrer()
        self.assertEqual(enviados, [])
        descartar.assert_called_once()

    def test_con_el_aviso_apagado_no_barre(self):
        with override_settings(WHATSAPP_DELIVERY_REENGAGE_ENABLED=False):
            avisados, enviados, correr = self._barrer()
        self.assertEqual(avisados, 0)
        correr.assert_not_called()

    def test_con_el_agente_apagado_no_barre(self):
        with override_settings(WHATSAPP_AGENT_ENABLED=False):
            avisados, enviados, correr = self._barrer()
        self.assertEqual(avisados, 0)
        correr.assert_not_called()

    # --- el mundo cambia mientras el modelo piensa ---

    def test_si_apagan_los_domicilios_mientras_tanto_el_aviso_no_sale(self):
        """Ese mensaje ya no sería verdad cuando llegue al teléfono."""
        def apagarlos(*args, **kwargs):
            self.cfg.customer_ordering_enabled = False
            self.cfg.save()
            return AgentTurn(replies=("¡Ya hay domicilios!",), message_ids=("m1",), mutated=False)

        enviados = []
        with patch("apps.whatsapp.agent.run_turn", side_effect=apagarlos), patch(
            "apps.whatsapp.agent.discard_turn"
        ) as descartar, patch("apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0), patch(
            "apps.whatsapp.kapso.send_text",
            side_effect=lambda pid, to, text: enviados.append(text),
        ):
            self.domicilios.avisar(self.contact)
        self.assertEqual(enviados, [])
        descartar.assert_called_once()

    def test_si_el_cliente_escribe_mientras_tanto_el_aviso_no_sale(self):
        def escribe(*args, **kwargs):
            worker._active.add(PHONE)
            return AgentTurn(replies=("¡Ya hay domicilios!",), message_ids=("m1",), mutated=False)

        self.addCleanup(worker._active.discard, PHONE)
        enviados = []
        with patch("apps.whatsapp.agent.run_turn", side_effect=escribe), patch(
            "apps.whatsapp.agent.discard_turn"
        ), patch("apps.whatsapp.worker.MESSAGE_GAP_SECONDS", 0), patch(
            "apps.whatsapp.kapso.send_text",
            side_effect=lambda pid, to, text: enviados.append(text),
        ):
            self.domicilios.avisar(self.contact)
        self.assertEqual(enviados, [])

    def test_el_mensaje_que_llega_antes_del_turno_cancela_el_aviso(self):
        worker._active.add(PHONE)
        self.addCleanup(worker._active.discard, PHONE)
        with patch("apps.whatsapp.agent.run_turn") as correr:
            self.assertIsNone(self.domicilios.avisar(self.contact))
        correr.assert_not_called()


class CommandDeAvisarDomiciliosTests(TestCase):
    """El command solo mira. El vigía del servidor es quien escribe, y este
    proceso no ve sus turnos vivos: son memoria del otro."""

    def setUp(self):
        from apps.orders.models import StoreSettings

        cfg = StoreSettings.load()
        cfg.is_open = True
        cfg.customer_ordering_enabled = True
        cfg.save()

    def test_el_command_nunca_escribe(self):
        from django.core.management import call_command

        with patch("apps.whatsapp.domicilios.barrer") as barrido, patch(
            "apps.whatsapp.domicilios.avisar"
        ) as aviso:
            call_command("avisar_domicilios", stdout=StringIO())
            call_command("avisar_domicilios", "--dry-run", stdout=StringIO())
        barrido.assert_not_called()
        aviso.assert_not_called()

    def test_no_hay_manera_de_pedirle_que_escriba(self):
        """Ni con un flag: la única vía de envío es el vigía."""
        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError):
            call_command("avisar_domicilios", "--enviar", stdout=StringIO())


class MarcaDeDomicilioPerdidoTests(TestCase):
    """La marca la ponen las tools, no el modelo: pedírsela a él sería confiar
    en que se acuerde (ver la regla de las palabras vetadas)."""

    def setUp(self):
        from apps.business.models import Business
        from apps.orders.models import StoreSettings
        from apps.products.models import Category, Product, ProductVariant

        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food", defaults={"name": "Frostbyte Food"}
        )
        categoria = Category.objects.create(name="Granizados", slug="granizados", business=food)
        producto = Product.objects.create(name="Pecera", category=categoria, business=food)
        self.variante = ProductVariant.objects.create(
            product=producto, name="Personal", sku="PEC-1", price=18000
        )
        self.cfg = StoreSettings.load()
        self.cfg.is_open = True
        self.cfg.customer_ordering_enabled = False
        self.cfg.save()
        self.contact = WhatsAppContact.objects.create(phone=PHONE)
        self.tools = {t.name: t for t in build_tools(self.contact)}

    def test_la_puerta_cerrada_queda_anotada(self):
        self.tools["consultar_estado_tienda"].invoke({})
        self.contact.refresh_from_db()
        self.assertIsNotNone(self.contact.delivery_missed_at)

    def test_la_marca_no_se_repisa_en_cada_consulta(self):
        """Pisarla en cada tool call alargaría sola la ventana de WhatsApp."""
        self.tools["consultar_estado_tienda"].invoke({})
        self.contact.refresh_from_db()
        primera = self.contact.delivery_missed_at
        self.tools["consultar_estado_tienda"].invoke({})
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.delivery_missed_at, primera)

    def test_con_domicilios_activos_no_se_marca_a_nadie(self):
        self.cfg.customer_ordering_enabled = True
        self.cfg.save()
        self.tools["consultar_estado_tienda"].invoke({})
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.delivery_missed_at)

    def test_el_local_cerrado_no_es_un_domicilio_perdido(self):
        """Cerrado no hay ningún canal: ese cliente no se quedó sin domicilio,
        se quedó sin local."""
        self.cfg.is_open = False
        self.cfg.save()
        self.tools["consultar_estado_tienda"].invoke({})
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.delivery_missed_at)

    def test_una_marca_que_falla_no_le_quita_la_respuesta_al_cliente(self):
        with patch(
            "apps.whatsapp.domicilios.anotar", side_effect=RuntimeError("base caída")
        ):
            resultado = self.tools["consultar_estado_tienda"].invoke({})
        self.assertIn("no hay servicio de domicilios", resultado)

    def test_el_pedido_rechazado_por_no_haber_domicilios_tambien_marca(self):
        resultado = self.tools["crear_pedido"].invoke({
            "items": [{"variante_id": self.variante.id, "cantidad": 1, "notas": ""}],
            "nombre_cliente": "Anyi",
            "para_recoger": False,
        })
        self.assertIn("ERROR", resultado)
        self.contact.refresh_from_db()
        self.assertIsNotNone(self.contact.delivery_missed_at)

    def test_con_el_aviso_apagado_no_se_acumulan_marcas(self):
        """Si el interruptor solo callara al barrido, el día que se prenda
        saldrían avisos por lo que pasó mientras estuvo apagado."""
        with override_settings(WHATSAPP_DELIVERY_REENGAGE_ENABLED=False):
            self.tools["consultar_estado_tienda"].invoke({})
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.delivery_missed_at)

    def test_el_pedido_creado_borra_la_marca(self):
        """Pasó a recoger: su caso está cerrado y no hay nada que avisarle."""
        self.tools["consultar_estado_tienda"].invoke({})
        self.tools["crear_pedido"].invoke({
            "items": [{"variante_id": self.variante.id, "cantidad": 1, "notas": ""}],
            "nombre_cliente": "Anyi",
            "para_recoger": True,
        })
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.delivery_missed_at)


class FechaDeLosDomiciliosTests(TestCase):
    """Prender los domicilios queda fechado venga de donde venga.

    Si el aviso dependiera de la vista del panel, prenderlos desde el admin de
    Django o desde un shell no le avisaría a nadie.
    """

    def setUp(self):
        from apps.orders.models import StoreSettings

        self.StoreSettings = StoreSettings
        cfg = StoreSettings.load()
        cfg.is_open = True
        cfg.customer_ordering_enabled = False
        cfg.save()

    def test_prenderlos_queda_fechado(self):
        cfg = self.StoreSettings.load()
        cfg.customer_ordering_enabled = True
        cfg.save(update_fields=["customer_ordering_enabled"])
        self.assertIsNotNone(self.StoreSettings.load().ordering_changed_at)

    def test_guardar_otra_cosa_no_mueve_la_fecha(self):
        cfg = self.StoreSettings.load()
        cfg.customer_ordering_enabled = True
        cfg.save()
        antes = self.StoreSettings.load().ordering_changed_at
        cfg = self.StoreSettings.load()
        cfg.delivery_fee = Decimal("3000.00")
        cfg.save()
        self.assertEqual(self.StoreSettings.load().ordering_changed_at, antes)

    def test_una_instancia_vieja_no_inventa_una_reactivacion(self):
        """Dos pestañas del panel abiertas: la de atrás guarda la tarifa con el
        interruptor viejo en memoria y no puede fechar lo que no tocó. Sin
        refresh_from_db a propósito: con él dejaría de ser una instancia vieja
        y el test no probaría nada."""
        vieja = self.StoreSettings.load()
        nueva = self.StoreSettings.load()
        nueva.customer_ordering_enabled = True
        nueva.save(update_fields=["customer_ordering_enabled"])
        fecha = self.StoreSettings.load().ordering_changed_at
        vieja.delivery_fee = Decimal("4000.00")
        vieja.save(update_fields=["delivery_fee"])
        self.assertEqual(self.StoreSettings.load().ordering_changed_at, fecha)
        self.assertTrue(self.StoreSettings.load().customer_ordering_enabled)

    def test_abrir_el_local_desde_una_pantalla_vieja_no_prende_un_episodio(self):
        """El panel guarda por campos: cerrar el local desde una pantalla
        cargada antes de prender los domicilios llegaba aquí con el interruptor
        viejo, fechaba una reactivación que no fue y con ella una tanda de
        avisos a clientes que nadie pidió."""
        nueva = self.StoreSettings.load()
        nueva.customer_ordering_enabled = True
        nueva.save(update_fields=["customer_ordering_enabled"])
        fecha = self.StoreSettings.load().ordering_changed_at
        vieja = self.StoreSettings.load()
        vieja.customer_ordering_enabled = False  # lo que tenía la pantalla al cargar
        vieja.is_open = False
        vieja.save(update_fields=["is_open"])
        self.assertEqual(self.StoreSettings.load().ordering_changed_at, fecha)

    def test_apagarlos_tambien_queda_fechado(self):
        cfg = self.StoreSettings.load()
        cfg.customer_ordering_enabled = True
        cfg.save()
        primera = self.StoreSettings.load().ordering_changed_at
        cfg = self.StoreSettings.load()
        cfg.customer_ordering_enabled = False
        cfg.save()
        self.assertGreater(self.StoreSettings.load().ordering_changed_at, primera)
