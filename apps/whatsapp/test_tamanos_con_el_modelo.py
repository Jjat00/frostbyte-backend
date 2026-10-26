"""Conversaciones completas contra el modelo de verdad: el tamaño que se cotiza.

Chat real: una clienta pidió la salchipapa más barata, corrigió
"No para 1" y Frosty contestó "Entendido, la Clásica para 2". El prompt ponía
"para uno" como ejemplo de cantidad, y en lo que tiene tamaños por personas
(Personal / Para 2) es el tamaño.

Cada caso es un chat: los mensajes del cliente se mandan uno por turno por
`run_turn`, con las tools de verdad sobre una carta de prueba. Se mira la
última cotización (o pedido) que la tool aceptó, no la que el modelo intentó:
qué variantes y cuántas. En el turno de una corrección, además, la respuesta
tiene que cotizar el precio del tamaño nuevo (y si una cotización ya existía,
tiene que ser la nueva); cuando falta el tamaño, tiene que preguntarlo; y un
caso confirma el pedido y mira lo que quedó guardado.

Llama al modelo de verdad y cuesta plata: se corre a mano cuando se toque el
prompt del pedido. Los tests del evaluador (al final) no llaman al modelo y
corren con la suite normal.

    PROBAR_FROSTY_CON_EL_MODELO=1 ANTHROPIC_API_KEY=... \\
        python manage.py test apps.whatsapp.test_tamanos_con_el_modelo
"""

import os
import re
import unittest
from typing import NamedTuple
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from . import agent as agent_mod
from .models import WhatsAppContact

# Lo que se puede cotizar o crear; el último que la tool aceptó es el que manda.
_TOOLS_DE_PEDIDO = ("cotizar_pedido", "crear_pedido")

# La carta de prueba: producto -> ((variante, precio), ...).
CARTA = {
    "Salchipapa Clásica": (("Personal", 16000), ("Para 2", 25000)),
    "Salchipapa con Queso": (("Personal", 18000), ("Para 2", 28000)),
    "Granizado de Cereza": (("Pequeño", 8000), ("Grande", 12000)),
    "Agua Cristal": (("Botella", 3000),),
}
PRECIOS = {(p, v): precio for p, variantes in CARTA.items() for v, precio in variantes}


class Caso(NamedTuple):
    nombre: str
    mensajes: list
    # {(producto, variante): cantidad} al final; None = no debe cotizar nada
    # todavía (le falta un dato y tiene que preguntarlo).
    esperado: dict | None
    # Turno (índice en mensajes) en que el cliente corrige el tamaño.
    corrige_en: int | None = None
    # Con esperado None: las opciones que la pregunta tiene que ofrecer.
    pregunta: tuple = ()
    # El último mensaje confirma: el pedido guardado tiene que ser el esperado.
    confirma: bool = False


CASOS = (
    # --- El chat real ---
    Caso(
        "chat_real_corrige_a_para_1",
        ["Tiene salchipapa", "La más varata es para 2", "No para 1", "Para recoger"],
        {("Salchipapa Clásica", "Personal"): 1},
        corrige_en=2,
    ),
    # --- "para 1" y sus formas eligen Personal ---
    Caso(
        "clasica_para_1",
        ["Hola, una salchipapa clásica para 1, para recoger"],
        {("Salchipapa Clásica", "Personal"): 1},
    ),
    Caso(
        "clasica_para_uno",
        ["Me regalas una salchipapa clásica para uno? paso a recogerla"],
        {("Salchipapa Clásica", "Personal"): 1},
    ),
    Caso(
        "para_mi_solo",
        ["Quiero una salchipapa con queso, es para mí solo. La recojo en el local"],
        {("Salchipapa con Queso", "Personal"): 1},
    ),
    Caso(
        "individual",
        ["una clásica individual para recoger porfa"],
        {("Salchipapa Clásica", "Personal"): 1},
    ),
    Caso(
        "para_una_persona_tras_preguntar",
        ["Hola quiero una salchipapa clásica", "para una persona", "la recojo yo"],
        {("Salchipapa Clásica", "Personal"): 1},
    ),
    Caso(
        "personal_explicito",
        ["Una salchipapa con queso personal para recoger"],
        {("Salchipapa con Queso", "Personal"): 1},
    ),
    # --- "para 2" y sus formas eligen Para 2 ---
    Caso(
        "clasica_para_2",
        ["Una salchipapa clásica para 2, para recoger"],
        {("Salchipapa Clásica", "Para 2"): 1},
    ),
    Caso(
        "para_dos_en_letras",
        ["buenas, una clásica para dos personas, paso por ella"],
        {("Salchipapa Clásica", "Para 2"): 1},
    ),
    Caso(
        "para_compartir",
        ["una salchipapa con queso para compartir, para recoger"],
        {("Salchipapa con Queso", "Para 2"): 1},
    ),
    # --- Cantidad y tamaño juntos: el número no se pierde ---
    Caso(
        "dos_personales",
        ["Dos salchipapas clásicas personales para recoger"],
        {("Salchipapa Clásica", "Personal"): 2},
    ),
    Caso(
        "dos_para_1",
        ["dos clásicas para 1, para recoger"],
        {("Salchipapa Clásica", "Personal"): 2},
    ),
    Caso(
        "dos_para_2",
        ["2 salchipapas clásicas para 2, las recojo"],
        {("Salchipapa Clásica", "Para 2"): 2},
    ),
    Caso(
        "mixto_dos_tamanos",
        ["Una clásica para 2 y una con queso para 1, para recoger"],
        {
            ("Salchipapa Clásica", "Para 2"): 1,
            ("Salchipapa con Queso", "Personal"): 1,
        },
    ),
    # --- El cliente corrige el tamaño: manda lo último, en ese mismo turno ---
    Caso(
        "corrige_a_personal",
        ["Una salchipapa clásica para 2", "mejor la personal", "para recoger"],
        {("Salchipapa Clásica", "Personal"): 1},
        corrige_en=1,
    ),
    Caso(
        "corrige_a_para_2",
        ["Una clásica personal", "no, mejor para 2", "para recoger"],
        {("Salchipapa Clásica", "Para 2"): 1},
        corrige_en=1,
    ),
    Caso(
        "corrige_con_no_para_1",
        ["Una salchipapa clásica para 2", "No para 1", "la recojo"],
        {("Salchipapa Clásica", "Personal"): 1},
        corrige_en=1,
    ),
    Caso(
        "corrige_tras_cotizar",
        ["Una clásica para 2 para recoger", "ay no, era para 1"],
        {("Salchipapa Clásica", "Personal"): 1},
        corrige_en=1,
    ),
    Caso(
        "confirma_tras_corregir",
        ["Una clásica para 2 para recoger", "no, para 1", "sí, confírmalo"],
        {("Salchipapa Clásica", "Personal"): 1},
        corrige_en=1,
        confirma=True,
    ),
    Caso(
        "pregunta_y_elige",
        [
            "cuánto vale la clásica?",
            "y la para 2 alcanza para dos?",
            "no, solo voy yo, dame la de uno",
            "para recoger",
        ],
        {("Salchipapa Clásica", "Personal"): 1},
    ),
    # --- En lo que no tiene tamaños por personas, "para uno" no elige tamaño ---
    Caso(
        "granizado_grande_para_uno",
        ["un granizado de cereza grande, para uno, para recoger"],
        {("Granizado de Cereza", "Grande"): 1},
    ),
    Caso(
        "granizado_para_uno_sin_tamano",
        ["un granizado de cereza para uno, para recoger"],
        None,
        pregunta=("Pequeño", "Grande"),
    ),
    Caso(
        "granizado_para_compartir_sin_tamano",
        ["un granizado de cereza para compartir, para recoger"],
        None,
        pregunta=("Pequeño", "Grande"),
    ),
    Caso(
        "salchipapa_y_granizado",
        ["una clásica para uno y un granizado de cereza pequeño, para recoger"],
        {
            ("Salchipapa Clásica", "Personal"): 1,
            ("Granizado de Cereza", "Pequeño"): 1,
        },
    ),
    # --- Variante única: no hay nada que elegir ---
    Caso(
        "agua_para_uno",
        ["un agua cristal para uno, para recoger"],
        {("Agua Cristal", "Botella"): 1},
    ),
    Caso(
        "dos_aguas_para_compartir",
        ["dos aguas cristal para compartir, las recojo"],
        {("Agua Cristal", "Botella"): 2},
    ),
    # --- Sin tamaño no se asume ninguno: se pregunta ---
    Caso(
        "sin_tamano_pregunta",
        ["Quiero una salchipapa clásica para recoger"],
        None,
        pregunta=("Personal", "Para 2"),
    ),
)


# ---------------------------------------------------------------------------
# El evaluador: funciones puras, probadas sin modelo al final del archivo.


def ultima_aceptada(mensajes, variantes):
    """{(producto, variante): cantidad} de la última cotización o pedido que la
    tool aceptó; None si no hubo ninguna. Una llamada sin respuesta de la tool, o
    con una que empieza por ERROR, no cuenta."""
    respuestas = {
        getattr(m, "tool_call_id", None): str(m.content)
        for m in mensajes
        if getattr(m, "type", "") == "tool"
    }
    ultimo = None
    for mensaje in mensajes:
        for call in getattr(mensaje, "tool_calls", None) or []:
            if call["name"] not in _TOOLS_DE_PEDIDO:
                continue
            respuesta = respuestas.get(call.get("id"))
            if respuesta is None or respuesta.lstrip().upper().startswith("ERROR"):
                continue
            ultimo = call["args"].get("items", [])
    if ultimo is None:
        return None
    armado = {}
    for item in ultimo:
        clave = variantes.get(item["variante_id"], ("?", item["variante_id"]))
        armado[clave] = armado.get(clave, 0) + item.get("cantidad", 1)
    return armado


# Cómo se nombra cada variante en un chat. "Para 2" va antes que "Personal" en
# ninguna parte: cada patrón es una palabra o frase completa.
ALIAS = {
    "Personal": r"personal|individual|para (?:1|uno|una|una persona|m[ií] sol[oa])|(?:la|el) de (?:1|uno)",
    "Para 2": r"para (?:2|dos)(?: personas)?|(?:la|el) de (?:2|dos)|para compartir",
    "Pequeño": r"peque[ñn][oa]|chic[oa]",
    "Grande": r"grande",
    "Botella": r"botella",
}
_PRECIO = r"\d{1,3}(?:[.,]\d{3})+|\d{4,}"
# Fin de oración: . ! ? ; o salto de línea, sin partir "16.000".
_FIN = re.compile(r"(?<!\d)[.;!?\n]|[.;!?\n](?!\d)")


def _oraciones(texto):
    return [o for o in _FIN.split(texto) if o.strip()]


# "no la Para 2", "en lugar de la Clásica para dos": esa variante es la que se deja.
_DESCARTE = re.compile(r"(?:(?<!\w)no|en lugar de|en vez de)\s+(?:(?:la|el|las|los)\s+)?(?:\w+\s+){0,2}$", re.I)


def _menciones(oracion, opciones):
    """[(posición, (tipo, valor))] en orden de aparición. tipo es "elegida" o
    "descartada" para una variante (valor: su nombre) y "precio" (valor: entero)."""
    marcas = [
        (m.start(), ("descartada" if _DESCARTE.search(oracion[: m.start()]) else "elegida", v))
        for v in opciones
        for m in re.finditer(rf"(?<!\w)(?:{ALIAS[v]})(?!\w)", oracion, re.I)
    ]
    marcas += [(m.start(), ("precio", int(re.sub(r"[.,]", "", m.group()))))
               for m in re.finditer(_PRECIO, oracion)]
    return sorted(marcas)


def fallo_de_correccion(respuesta, esperado):
    """Por qué la respuesta a una corrección no la refleja, o None si la refleja.

    El último tamaño que elige (sin contar los que descarta: "no la Para 2") tiene
    que ser el nuevo, y el precio nuevo tiene que ir con él: en su oración, la
    variante más cercana antes del precio (o, si no hay ninguna antes, la primera
    después) es su dueña, y tiene que ser el tamaño nuevo y no uno descartado."""
    (producto, variante), = esperado
    opciones = [v for v, _ in CARTA[producto]]
    nuevo = PRECIOS[(producto, variante)]
    elegidas = [v for o in _oraciones(respuesta) for _, (t, v) in _menciones(o, opciones) if t == "elegida"]
    if not elegidas or elegidas[-1] != variante:
        return f"no termina en el tamaño nuevo ({variante})"
    for oracion in _oraciones(respuesta):
        marcas = _menciones(oracion, opciones)
        for i, (_, (tipo, valor)) in enumerate(marcas):
            if tipo != "precio" or valor != nuevo:
                continue
            antes = [m for _, m in marcas[:i] if m[0] != "precio"]
            despues = [m for _, m in marcas[i + 1:] if m[0] != "precio"]
            dueña = antes[-1] if antes else (despues[0] if despues else ("elegida", variante))
            if dueña == ("elegida", variante):
                return None
    return f"no le pone el precio nuevo ({nuevo}) al tamaño {variante}"


def fallo_de_pregunta(respuesta, opciones):
    """Por qué la respuesta no pregunta el tamaño que falta, o None si lo pregunta.

    Hace falta una pregunta (la oración que termina en "?") que pida el tamaño o
    ponga a elegir entre al menos dos de las opciones; lo que diga fuera de esa
    pregunta no cuenta."""
    partes = re.split(f"({_FIN.pattern})", respuesta)
    preguntas = [o for o, fin in zip(partes[::2], partes[1::2]) if fin == "?" and o.strip("¿ ")]
    if not preguntas:
        return "no hace ninguna pregunta"
    for pregunta in preguntas:
        nombradas = {v for _, (t, v) in _menciones(pregunta, opciones) if t != "precio"}
        if re.search(r"tama[ñn]o", pregunta, re.I) or len(nombradas) >= 2:
            return None
    return f"no pregunta el tamaño ({' / '.join(opciones)})"


@unittest.skipUnless(
    os.getenv("PROBAR_FROSTY_CON_EL_MODELO") == "1",
    "llama al modelo de verdad; se corre a mano: PROBAR_FROSTY_CON_EL_MODELO=1",
)
class TamanoConElModeloTests(TestCase):
    def setUp(self):
        from langgraph.checkpoint.memory import InMemorySaver

        from apps.business.models import Business
        from apps.orders.models import StoreSettings
        from apps.products.models import Category, Product, ProductVariant

        food, _ = Business.objects.get_or_create(
            slug="frostbyte-food", defaults={"name": "Frostbyte Food", "display_order": 2}
        )
        bebidas, _ = Business.objects.get_or_create(
            slug="frostbyte", defaults={"name": "Frostbyte", "display_order": 1}
        )
        salchipapas = Category.objects.create(
            name="Salchipapas", slug="salchipapas", business=food
        )
        granizados = Category.objects.create(
            name="Granizados", slug="granizados", business=bebidas
        )
        categorias = {
            "Salchipapa Clásica": (salchipapas, food),
            "Salchipapa con Queso": (salchipapas, food),
            "Granizado de Cereza": (granizados, bebidas),
            "Agua Cristal": (granizados, bebidas),
        }
        self.variantes = {}
        for i, (nombre, tamanos) in enumerate(CARTA.items()):
            categoria, negocio = categorias[nombre]
            producto = Product.objects.create(
                name=nombre, category=categoria, business=negocio, description=nombre
            )
            for j, (tamano, precio) in enumerate(tamanos):
                variante = ProductVariant.objects.create(
                    product=producto, name=tamano, sku=f"T-{i}-{j}", price=precio
                )
                self.variantes[variante.id] = (nombre, tamano)

        cfg = StoreSettings.load()
        cfg.is_open = True
        cfg.customer_ordering_enabled = True
        cfg.delivery_fee = 2000
        cfg.save()

        # Un hilo en memoria por test: nada pasa por el Postgres del checkpointer.
        patcher = patch.object(agent_mod, "get_checkpointer", return_value=InMemorySaver())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hilo(self, contact):
        agent = agent_mod._build_agent(contact)
        estado = agent.get_state(
            {"configurable": {"thread_id": agent_mod._thread_id(contact)}}
        )
        return estado.values.get("messages", [])

    def _pedidos_guardados(self, desde_id):
        from apps.orders.models import Order

        pedidos = []
        for order in Order.objects.filter(id__gt=desde_id).prefetch_related("items"):
            armado = {}
            for item in order.items.all():
                clave = self.variantes.get(item.product_variant_id, ("?", item.product_variant_id))
                armado[clave] = armado.get(clave, 0) + item.quantity
            pedidos.append(armado)
        return pedidos

    def _correr(self, i, caso):
        from apps.orders.models import Order

        desde_id = Order.objects.order_by("-id").values_list("id", flat=True).first() or 0
        contact = WhatsAppContact.objects.create(
            phone=f"57300000{i:04d}", customer_name="Ana", profile_name="Ana"
        )
        respuestas = []

        def chat():
            return "\n" + "\n".join(
                f"  cliente: {m}\n  frosty:  {r}" for m, r in zip(caso.mensajes, respuestas)
            )

        for turno, mensaje in enumerate(caso.mensajes):
            respuestas.append(" / ".join(agent_mod.run_turn(contact, mensaje).replies))
            if turno == caso.corrige_en:
                fallo = fallo_de_correccion(respuestas[-1], caso.esperado)
                self.assertIsNone(fallo, f"al corregir {fallo}{chat()}")
                cotizado = ultima_aceptada(self._hilo(contact), self.variantes)
                if cotizado is not None:
                    self.assertEqual(cotizado, caso.esperado, f"al corregir cotiza otra cosa{chat()}")

        self.assertEqual(ultima_aceptada(self._hilo(contact), self.variantes), caso.esperado, chat())
        if caso.esperado is None:
            fallo = fallo_de_pregunta(respuestas[-1], caso.pregunta)
            self.assertIsNone(fallo, f"{fallo}{chat()}")
        if caso.confirma:
            self.assertEqual(self._pedidos_guardados(desde_id), [caso.esperado], chat())

    def test_cada_chat_cotiza_el_tamano_que_pidio_el_cliente(self):
        for i, caso in enumerate(CASOS):
            with self.subTest(caso=caso.nombre):
                self._correr(i, caso)


class EvaluadorSinModeloTests(SimpleTestCase):
    """El evaluador no puede aprobar el error que motivó el arreglo."""

    VARIANTES = {1: ("Salchipapa Clásica", "Personal"), 2: ("Salchipapa Clásica", "Para 2")}
    ESPERADO = {("Salchipapa Clásica", "Personal"): 1}

    def _cotiza(self, call_id, variante_id, respuesta_tool):
        from langchain_core.messages import AIMessage, ToolMessage

        llamada = AIMessage(
            content="",
            tool_calls=[{
                "id": call_id,
                "name": "cotizar_pedido",
                "args": {"items": [{"variante_id": variante_id, "cantidad": 1}]},
            }],
        )
        if respuesta_tool is None:
            return [llamada]
        return [llamada, ToolMessage(content=respuesta_tool, tool_call_id=call_id)]

    def test_una_cotizacion_rechazada_no_cuenta(self):
        hilo = self._cotiza("a", 1, "ERROR: cotización rechazada")
        self.assertIsNone(ultima_aceptada(hilo, self.VARIANTES))

    def test_una_llamada_sin_respuesta_de_la_tool_no_cuenta(self):
        self.assertIsNone(ultima_aceptada(self._cotiza("a", 1, None), self.VARIANTES))

    def test_manda_la_ultima_aceptada(self):
        hilo = (
            self._cotiza("a", 2, "Total: $25.000")
            + self._cotiza("b", 1, "Total: $16.000")
            + self._cotiza("c", 2, "ERROR: variante inactiva")
        )
        self.assertEqual(ultima_aceptada(hilo, self.VARIANTES), self.ESPERADO)

    def test_la_respuesta_del_chat_real_no_pasa(self):
        self.assertIsNotNone(fallo_de_correccion("Entendido, la Clásica para 2 ($25.000)", self.ESPERADO))
        self.assertIsNotNone(fallo_de_correccion("Entendido, la Clásica para 2", self.ESPERADO))

    def test_terminar_en_el_tamano_rechazado_no_pasa(self):
        for texto in (
            "La personal vale $16.000. Te dejo la para 2 en $25.000",
            "La Personal cuesta $16.000. Entendido, te dejo la Clásica para 2.",
            "Entendido, la Clásica para 2 cuesta $16.000.",
            "Te dejo la Para 2 por $25.000, no la Personal de $16.000.",
            "Entendido, la Clásica para 2 por $25.000 en lugar de la Personal de $16.000.",
        ):
            with self.subTest(texto=texto):
                self.assertIsNotNone(fallo_de_correccion(texto, self.ESPERADO))

    def test_la_correccion_bien_hecha_pasa_en_cualquier_redaccion(self):
        for texto in (
            "Listo, cambio a la Clásica Personal: $16.000",
            "Va, la de uno entonces, 16.000 pesitos",
            "Cambié la para 2 ($25.000) por la personal ($16.000)",
            "La Personal cuesta $16.000; la Para 2 cuesta $25.000. ¿Te dejo la Personal?",
            "$16.000 la clásica para uno, listo",
            "Listo, la Clásica Personal por $16.000, no la Para 2.",
            "Va la personal de $16.000 en lugar de la para dos.",
            "No hay problema, la personal queda en $16.000",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(fallo_de_correccion(texto, self.ESPERADO))

    def test_sin_pregunta_o_sin_opciones_no_pasa(self):
        opciones = ("Personal", "Para 2")
        self.assertIsNotNone(fallo_de_pregunta("", opciones))
        self.assertIsNotNone(fallo_de_pregunta("¡Gracias por escribirnos!", opciones))
        self.assertIsNotNone(fallo_de_pregunta("¿Cómo vas a pagar?", opciones))
        self.assertIsNotNone(fallo_de_pregunta("La Personal es un tamaño y la Para 2 es otro. ¿Pagas por Nequi?", opciones))
        self.assertIsNotNone(fallo_de_pregunta("El tamaño es Personal. ¿Confirmas?", opciones))
        self.assertIsNotNone(fallo_de_pregunta("¿La quieres Personal?", opciones))
        self.assertIsNone(fallo_de_pregunta("¿La quieres Personal o Para 2?", opciones))
        self.assertIsNone(fallo_de_pregunta("¿Personal o para dos?", opciones))
        self.assertIsNone(fallo_de_pregunta("Claro. ¿De qué tamaño la quieres?", opciones))
        self.assertIsNone(fallo_de_pregunta("Tenemos dos: ¿personal ($16.000) o para compartir ($25.000)?", opciones))
        self.assertIsNone(fallo_de_pregunta("¿Pequeño o grande?", ("Pequeño", "Grande")))
