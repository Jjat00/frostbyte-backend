"""Tools del agente de pedidos por WhatsApp.

Cada tool consulta o muta directamente el ORM de Django. Se construyen por
contacto (closure) para que el agente nunca pueda operar sobre pedidos de
otro cliente. Todas devuelven strings: es lo que el modelo lee.
"""

import logging
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from apps.orders.coverage import coverage_label, is_within_delivery_area
from apps.orders.models import Order, OrderItem, StoreSettings
from apps.products.models import Category, Product, ProductVariant

from . import domicilios
from . import intencion
from . import kapso
from . import missing
from . import stickers as stickers_media
from .models import AgentSettings, Sticker, StickerDraft

logger = logging.getLogger(__name__)


def normalize_phone(phone):
    """Deja solo dígitos (ej. '+57 300 123-4567' -> '573001234567')."""
    return re.sub(r"\D", "", phone or "")


def _celular_colombiano(value):
    """Normaliza un celular colombiano a 57XXXXXXXXXX; '' si no lo parece."""
    digits = normalize_phone(value)
    if len(digits) == 10 and digits.startswith("3"):
        return "57" + digits
    if len(digits) == 12 and digits.startswith("573"):
        return digits
    return ""


def _cop(value):
    """Formatea pesos colombianos: 12000 -> $12.000"""
    return "$" + f"{value:,.0f}".replace(",", ".")


# Palabras con las que el cliente rodea al producto y que no ayudan a buscar.
_STOPWORDS = {
    "una", "uno", "unas", "unos", "quiero", "quisiera", "para", "con", "por",
    "favor", "del", "los", "las", "que", "pedir", "domicilio", "hola", "buenas",
    "buenos", "dias", "tardes", "noches", "tienen", "tiene", "tienes", "hay",
    "venden", "vende", "precio", "cuanto", "cuesta", "vale", "manda", "mandame",
    "porfa", "algo", "dos", "tres", "mas", "sirven", "todavia", "aun", "esta",
    "estan", "disponible", "disponibles", "pedido", "cuestan", "valen",
}

# Términos genéricos con los que el cliente pide algo que en el menú se llama
# de otra forma ("¿qué hay de comer?" -> Salchipapas). Se SUMAN a sus palabras
# (nunca las reemplazan) y apuntan al nombre en singular de una CATEGORÍA real:
# el resultado siempre sale del ORM. Si nace una categoría nueva de comida o de
# bebida, agrégala aquí para que el genérico también la encuentre.
_ALIAS = {
    "papa": ("salchipapa",),
    "salchicha": ("salchipapa",),
    "picada": ("salchipapa",),
    "comida": ("salchipapa",),
    "comer": ("salchipapa",),
    "almuerzo": ("salchipapa",),
    "hambre": ("salchipapa",),
    "gaseosa": ("bebida",),
    "refresco": ("bebida",),
    "trago": ("coctel",),
    "licor": ("coctel",),
    "cocktail": ("coctel",),
}

# Cuántos productos se listan como mucho en una búsqueda
_MAX_RESULTADOS = 12


def _normalize(text):
    """Minúsculas y sin tildes: 'Clásica' -> 'clasica'."""
    plain = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in plain if unicodedata.category(c) != "Mn")


def _singular(word):
    """Singular aproximado en español: 'salchipapas' -> 'salchipapa'."""
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _tokens(text):
    """Palabras normalizadas y en singular de un texto del menú."""
    return {
        _singular(w)
        for w in re.findall(r"[a-z0-9]+", _normalize(text))
        if len(w) >= 3
    }


def _search_words(texto):
    """Palabras útiles de lo que escribió el cliente, más sus sinónimos."""
    raw = {w for w in re.findall(r"[a-z0-9]+", _normalize(texto)) if len(w) >= 3}
    words = {_singular(w) for w in raw - _STOPWORDS} - _STOPWORDS
    return words | {alias for w in words for alias in _ALIAS.get(w, ())}


def _words_match(a, b):
    """Si dos palabras nombran lo mismo (plural, prefijo o error de tecleo)."""
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    if len(a) >= 5 and len(b) >= 5:
        return SequenceMatcher(None, a, b).ratio() >= 0.85
    return False


def _order_summary(order):
    lines = [
        f"Pedido {order.order_number} · {order.get_order_type_display()} · "
        f"estado: {order.get_status_display()}"
    ]
    items = order.items.select_related("product_variant__product")
    grouped = {}
    for item in items:
        key = (item.product_variant_id, item.notes)
        grouped.setdefault(key, {"item": item, "qty": 0})
        grouped[key]["qty"] += item.quantity
    for entry in grouped.values():
        item = entry["item"]
        variant = item.product_variant
        note = f" ({item.notes})" if item.notes else ""
        lines.append(
            f"- {entry['qty']}x {variant.product.name} {variant.name}{note} · {_cop(item.unit_price * entry['qty'])}"
        )
    if order.delivery_fee:
        lines.append(f"Envío: {_cop(order.delivery_fee)}")
    pago = order.get_payment_method_display() or (
        "al recoger en el local" if order.order_type == Order.OrderType.PICKUP else "sin definir"
    )
    lines.append(f"TOTAL: {_cop(order.total)} · pago: {pago}")
    if order.delivery_address:
        lines.append(f"Dirección: {order.delivery_address}")
    return "\n".join(lines)


def _customer_orders(contact):
    """Pedidos históricos del contacto, emparejados por los últimos dígitos."""
    if kapso.is_bsuid(contact.phone):
        # Si WhatsApp oculta su número, el pedido lleva el celular que él dio
        # (domicilios) o el propio BSUID (para recoger)
        orders = Order.objects.filter(customer_phone=contact.phone)
        digits = normalize_phone(contact.contact_phone)[-10:]
        if digits:
            orders = orders | Order.objects.filter(customer_phone__endswith=digits)
        return orders
    digits = normalize_phone(contact.phone)[-10:]
    if not digits:
        return Order.objects.none()
    return Order.objects.filter(customer_phone__endswith=digits)


def _coincidencias(products, words, exigir_variantes=True):
    """Puntúa los productos contra las palabras del cliente, de más a menos.

    Devuelve [(puntaje, producto, variantes activas)]. Se usa dos veces: con lo
    que está a la venta hoy y, si eso no da nada, con lo apagado, para poder
    distinguir "se acabó" de "no lo vendemos" (`exigir_variantes=False` porque
    un producto apagado puede tener las variantes apagadas también).
    """
    scored = []
    for product in products:
        variants = [v for v in product.variants.all() if v.is_active]
        if not variants and exigir_variantes:
            continue
        # el peso dice qué tan directa es la coincidencia: el nombre manda,
        # la categoría permite que "salchipapas" traiga todas las que hay
        fuertes = (
            (_tokens(product.name), 3),
            (_tokens(product.category.name), 2),
        )
        # el tamaño y la descripción solo desempatan: "1 grande" o "personal"
        # no nombran ningún producto, y por sí solos traerían medio menú
        debiles = (
            (_tokens(" ".join(v.name for v in variants)), 1),
            (_tokens(product.description), 1),
        )
        score = 0
        identificado = False
        for word in words:
            peso = max(
                (w for tokens, w in fuertes if any(_words_match(word, t) for t in tokens)),
                default=0,
            )
            if peso:
                identificado = True
            else:
                peso = max(
                    (w for tokens, w in debiles if any(_words_match(word, t) for t in tokens)),
                    default=0,
                )
            score += peso
        if identificado:
            scored.append((score, product, variants))
    scored.sort(key=lambda row: (-row[0], row[1].name))
    return scored


def _lo_que_mas_pide(contact):
    """Los productos que más ha pedido este cliente, del más repetido al menos.

    Los últimos pedidos ya se listan uno por uno, pero de esa lista no se ve de
    un vistazo lo que de verdad le gusta: quien lleva cuatro granizados de
    maracuyá en seis pedidos tiene un favorito, y es lo que un mesero recuerda
    de un cliente que vuelve.
    """
    from collections import Counter

    conteo = Counter()
    items = (
        OrderItem.objects.filter(
            order__in=_customer_orders(contact).exclude(status=Order.Status.CANCELLED)
        )
        .select_related("product_variant__product")
        .order_by()
    )
    for item in items:
        variant = item.product_variant
        if variant and variant.product:
            conteo[variant.product.name] += item.quantity
    frecuentes = [nombre for nombre, veces in conteo.most_common(3) if veces >= 2]
    return ", ".join(frecuentes)


def _ubicacion_guardada(contact):
    """Cómo nombrarle al cliente la ubicación que ya tenemos suya, o "".

    Un cliente que vuelve no tiene por qué mandar otra vez su ubicación: ya la
    dio. Lo que toca es preguntarle si es al mismo sitio (Jaime, 15/09), y para
    eso hay que poder nombrarlo.
    """
    if contact.last_location_lat is None or contact.last_location_lng is None:
        return ""
    if contact.last_location_at:
        cuando = timezone.localtime(contact.last_location_at)
        fecha = (
            "hoy"
            if cuando.date() == timezone.localdate()
            else f"del {cuando.strftime('%d/%m')}"
        )
    else:
        fecha = "de otra vez"
    sitio = (contact.last_location_label or "").strip()
    return f"{sitio} ({fecha})" if sitio else f"la que compartió {fecha}"


def _opciones_ofrecibles(group):
    """Las opciones del grupo que el agente puede ofrecer de verdad.

    Deja fuera las que llevan recargo. El pedido guarda las elecciones como
    TEXTO en las notas del item, así que `cotizar_pedido` no las suma: ofrecer
    una opción con precio es regalarla. Pasó el 15/09 con el pedido
    20260916-6B022D —salchipapa con queso más salchicha ranchera (+$5.000)
    cobrada a $18.000— y el campo `price_delta` no lo cobra nadie en ningún
    canal, solo se escribe en el panel.

    Un grupo obligatorio cuyas opciones cuesten todas se queda sin ofrecer, y
    es lo correcto mientras esto sea así: es preferible no dar a elegir a dar
    a elegir gratis. El día que el recargo se sume de verdad, este filtro sobra.
    """
    return [opt for opt in group.options.all() if opt.is_active and not opt.price_delta]


def _es_personalizable(product):
    """Si al agente le queda algo que preguntarle al cliente de este producto."""
    return any(
        pm.is_active and pm.group.is_active and _opciones_ofrecibles(pm.group)
        for pm in product.modifier_links.all()
    )


def _catalogo_de_hoy():
    """Los nombres de todo lo que está a la venta hoy, por categoría.

    Sin precios ni variantes a propósito: esto no se le muestra al cliente, se
    le da al modelo para que resuelva por su cuenta lo que la comparación de
    letras no puede ("pesera" es la "Pecera granizada"). Cargarlo de cifras
    solo lo tentaría a volcarlo al chat.
    """
    lineas = []
    categories = (
        Category.objects.filter(is_active=True, business__is_active=True)
        .select_related("business")
        .order_by("business__display_order", "display_order", "name")
    )
    for category in categories:
        nombres = [
            product.name
            for product in Product.objects.filter(
                category=category, is_active=True, is_coming_soon=False
            )
            .prefetch_related("variants")
            .order_by("name")
            if any(v.is_active for v in product.variants.all())
        ]
        if nombres:
            lineas.append(f"[{category.name}] {', '.join(nombres)}")
    return "\n".join(lineas) if lineas else "Hoy no hay ningún producto activo."


def _avisar_escalada(contact, motivo):
    """Le avisa al dueño por WhatsApp que un chat quedó esperando a una persona.

    Sin esto, escalar es un agujero: el agente se calla, el cliente se queda
    mirando el chat y nadie del equipo se entera a menos que abra el inbox.
    Pasó de verdad —un comprobante de $57.000 sin respuesta el 27/08— así que
    el aviso sale por el mismo WhatsApp que el equipo ya tiene abierto.

    Es best-effort: si el aviso falla, la pausa ya quedó puesta y el turno del
    cliente no se cae por eso.
    """
    try:
        config = AgentSettings.load()
        numeros = sorted(config.owner_numbers())
        if not numeros:
            return
        phone_number_id = contact.last_phone_number_id or (
            settings.KAPSO_PHONE_NUMBER_IDS[0]
            if settings.KAPSO_PHONE_NUMBER_IDS
            else ""
        )
        if not phone_number_id:
            return
        quien = contact.customer_name or contact.profile_name or "un cliente"
        aviso = (
            f"🔔 {quien} ({contact.phone}) está esperando a una persona.\n"
            f"Motivo: {motivo or 'sin motivo'}"
        )
        for numero in numeros:
            if numero[-10:] == normalize_phone(contact.phone)[-10:]:
                continue  # el dueño probando no se avisa a sí mismo
            kapso.send_text(phone_number_id, numero, aviso)
    except Exception:
        logger.exception("No se pudo avisar al dueño de la escalada de %s", contact.phone)


class ItemPedido(BaseModel):
    variante_id: int = Field(description="ID de la variante (sale de consultar_menu o consultar_producto)")
    cantidad: int = Field(1, ge=1, le=50, description="Cuántas unidades")
    notas: str = Field("", description="Personalizaciones elegidas y aclaraciones de este item (ej. 'sin cebolla, salsa extra')")


class TurnContext:
    """Lo que las tools necesitan para poner algo en el chat durante el turno.

    Las tools de envío (sticker, foto, botones, reacción) escriben en WhatsApp
    en el momento en que el modelo las llama, no al final del turno. Eso deja
    dos cosas distintas que hay que saber después, y confundirlas se nota:

    `posted`: quedó un mensaje en el chat del cliente, así que el turno ya no
    se puede descartar y rehacer.
    `answered`: el turno ya respondió algo, aunque no haya sido un mensaje. Una
    reacción sola es una respuesta completa —el prompt se lo permite—, y sin
    esto un "gracias" contestado con un ❤️ recibía además un "¿me lo repites?".
    `sticker_urge`: si este turno puede llevar sticker (ver mood.py). Se tira
    una vez y lo leen los dos lados, el prompt y la tool. En None —pruebas por
    shell— no hay dado y la tool manda lo que le pidan.
    `sticker`: el que el modelo eligió, esperando a que salga el texto. No se
    manda dentro de la tool a propósito: una persona escribe y REMATA con el
    sticker, y el modelo solo escribe su mensaje después de llamar a las tools.
    Mandándolo en el momento llegaba siempre delante, que es justo al revés.
    """

    def __init__(self, phone_number_id="", message_id="", sticker_urge=None):
        self.phone_number_id = phone_number_id
        self.message_id = message_id
        self.posted = False
        self.answered = False
        self.sticker_urge = sticker_urge
        self.sticker = None

    @property
    def can_send(self):
        return bool(self.phone_number_id)


def build_tools(contact, turn=None):
    """Construye las tools ligadas a un WhatsAppContact concreto.

    `turn` es el TurnContext del turno en curso; sin él (pruebas por shell) las
    tools que mandan cosas al chat no se le ofrecen al modelo.
    """
    config = AgentSettings.load()

    @tool
    def consultar_estado_tienda() -> str:
        """Consulta si Frostbyte está abierto y qué canales admiten pedido ahora
        (domicilio y para recoger). Úsala SIEMPRE antes de ofrecer productos o
        crear un pedido, y responde en el orden que te indique.
        """
        cfg = StoreSettings.load()
        # Chat real del 01/09: con el local cerrado el agente habló de los
        # canales ("no hay domicilios ni recogida") y encima ofreció pasar por
        # el pedido. El local cerrado no es un dato más de la lista: manda
        # sobre todo lo demás, así que la tool no devuelve nada más que eso.
        if not cfg.is_open:
            return (
                "LOCAL CERRADO ahora mismo. No se puede tomar NINGÚN pedido: ni a "
                "domicilio ni para recoger. Dile de una que el local está cerrado y "
                f"que {cfg.reopening_hint()}. NO le ofrezcas encargar, ni pasar por el "
                "pedido, ni hablar de domicilios: no hay ningún canal abierto. Si "
                "insiste o pregunta por otra cosa del local, pásale el número de "
                "contacto."
            )
        # Recoger no tiene interruptor: si el local está abierto, el cliente
        # puede pasar por su pedido. Lo único que se prende y se apaga es el
        # domicilio, que es el que cuesta un domiciliario.
        servicio = "ACTIVOS" if cfg.customer_ordering_enabled else "SIN SERVICIO AHORA"
        lineas = [
            f"Local ABIERTO. Domicilios: {servicio}. Pedidos para recoger: SIEMPRE, "
            "con el local abierto.",
            f"Tarifa de envío: {_cop(cfg.delivery_fee)} (para recoger no se cobra envío).",
            f"Puedes tomar pedidos A DOMICILIO: {'sí' if cfg.customer_ordering_enabled else 'NO'}.",
            "Puedes tomar pedidos PARA RECOGER: sí.",
        ]
        if not cfg.customer_ordering_enabled:
            # Queda anotado que este cliente llegó con la puerta cerrada: si los
            # domicilios vuelven dentro de la ventana, el vigía mira el hilo y
            # decide si hay algo que retomar (ver domicilios.py). Es una nota
            # al margen de una tool de solo lectura: si la escritura falla, el
            # cliente pierde un aviso, no su respuesta
            try:
                domicilios.anotar(contact)
            except Exception:
                logger.exception("No se pudo anotar el domicilio perdido de %s", contact.phone)
            lineas.append(
                "Ahora mismo no hay servicio de domicilios, pero el local SÍ encarga para "
                "recoger: dile al cliente 'justo en este momento no tenemos servicio de "
                "domicilios' y ofrécele encargar y pasar por el pedido antes de despedir a nadie."
            )
        return " ".join(lineas)

    @tool
    def consultar_menu() -> str:
        """Devuelve el menú vigente: categorías y productos ACTIVOS con sus
        variantes, precios y el variante_id necesario para pedir.
        Los productos marcados (personalizable) tienen opciones: consulta el
        detalle con consultar_producto antes de agregarlos al pedido.
        """
        categories = (
            Category.objects.filter(is_active=True, business__is_active=True)
            .select_related("business")
            .order_by("business__display_order", "display_order", "name")
        )
        lines = []
        for category in categories:
            products = (
                Product.objects.filter(category=category, is_active=True, is_coming_soon=False)
                .prefetch_related("variants", "modifier_links__group__options")
                .order_by("name")
            )
            product_lines = []
            for product in products:
                variants = [v for v in product.variants.all() if v.is_active]
                if not variants:
                    continue
                prices = "; ".join(f"{v.name} {_cop(v.price)} [variante_id={v.id}]" for v in variants)
                extra = (
                    f" (personalizable, slug='{product.slug}')"
                    if _es_personalizable(product)
                    else ""
                )
                product_lines.append(f"  - {product.name}: {prices}{extra}")
            if product_lines:
                lines.append(f"[{category.name} · {category.business.name}]")
                lines.extend(product_lines)
        return "\n".join(lines) if lines else "No hay productos activos en este momento."

    @tool
    def consultar_producto(producto_slug: str) -> str:
        """Detalle de un producto: descripción, variantes con precio y sus
        opciones de personalización (grupos de modificadores con reglas).

        Args:
            producto_slug: slug del producto tal como aparece en consultar_menu
        """
        try:
            product = (
                Product.objects.prefetch_related(
                    "variants", "modifier_links__group__options"
                ).get(slug=producto_slug, is_active=True)
            )
        except Product.DoesNotExist:
            return (
                f"No existe un producto activo con slug '{producto_slug}'. "
                "Esto NO significa que no lo vendamos: el slug puede estar mal. "
                "Búscalo con buscar_producto para obtener el slug correcto."
            )

        lines = [f"{product.name}: {product.description or 'sin descripción'}"]
        for variant in product.variants.all():
            if variant.is_active:
                lines.append(f"- {variant.name}: {_cop(variant.price)} [variante_id={variant.id}]")

        for pmg in product.modifier_links.all():
            if not pmg.is_active or not pmg.group.is_active:
                continue
            group = pmg.group
            ofrecibles = _opciones_ofrecibles(group)
            if not ofrecibles:
                # Grupo entero de opciones con recargo: no se ofrece ninguna
                # (ver _opciones_ofrecibles), así que no hay grupo que mostrar.
                continue
            min_sel, max_sel = pmg.effective_min, pmg.effective_max
            if min_sel == 0:
                rule = f"opcional, hasta {max_sel}"
            elif max_sel == 1:
                rule = "elige 1"
            else:
                rule = f"elige entre {min_sel} y {max_sel}"
            options = ", ".join(opt.name for opt in ofrecibles)
            lines.append(f"Opciones '{group.name}' ({rule}): {options}")
            lines.append(
                "NOTA: las opciones elegidas van en el campo 'notas' del item al crear el pedido."
            )
        return "\n".join(lines)

    @tool
    def buscar_producto(texto: str) -> str:
        """Busca en el menú productos que se parezcan a lo que el cliente pidió.
        Úsala SIEMPRE antes de decir que algo "no está disponible": los clientes
        casi nunca escriben el nombre exacto (ej. "salchipapas" son la
        'Salchipapa Clásica', la 'Salchipapa Especial Frostbyte'...).
        Busca por nombre, categoría, tamaño y descripción, y aguanta plurales,
        tildes que faltan y errores de tecleo.

        Args:
            texto: lo que el cliente pidió, con sus palabras
        """
        words = _search_words(texto)
        if not words:
            return "ERROR: dame palabras del producto a buscar."
        products = (
            Product.objects.filter(
                is_active=True,
                is_coming_soon=False,
                category__is_active=True,
                category__business__is_active=True,
            )
            .select_related("category", "category__business")
            .prefetch_related("variants", "modifier_links__group__options")
        )
        scored = _coincidencias(products, words)

        if not scored:
            # Dos cosas distintas que antes se decían igual y costaron ventas:
            #
            # 1. Lo que hoy no está pero SÍ es nuestro. El catálogo se apaga y
            #    se enciende a diario según lo que se acaba: el 14/09 una
            #    clienta mandó una foto de la carta preguntando el precio de la
            #    salchipapa y se le contestó "no tenemos salchipapas", que ella
            #    lee como "no venden eso", y se fue. Agotado no es inexistente.
            # 2. Lo que está mal escrito. "pesera" no llega a "Pecera granizada"
            #    (0,83 de parecido contra un umbral de 0,85) y ahí se fueron dos
            #    peceras de $30.000. Eso NO se arregla bajando el umbral: el
            #    criterio de si "pesera" es "pecera" lo pone el modelo, que para
            #    eso lee esta respuesta. Aquí solo se le entrega la lista de lo
            #    que hay —nombres, sin precios— para que decida.
            agotados = _coincidencias(
                Product.objects.filter(is_active=False)
                .select_related("category", "category__business")
                .prefetch_related("variants")
                | Product.objects.filter(category__is_active=False)
                .select_related("category", "category__business")
                .prefetch_related("variants"),
                words,
                exigir_variantes=False,
            )
            hoy = _catalogo_de_hoy()
            if agotados:
                nombres = ", ".join(p.name for _s, p, _v in agotados[:5])
                return (
                    f"'{texto}' SÍ es nuestro ({nombres}), pero HOY no está "
                    "disponible: se acabó o no se preparó. Díselo así —que hoy no "
                    "hay, NUNCA que no lo vendemos— y ofrécele lo que sí hay hoy.\n"
                    f"{hoy}"
                )
            return (
                f"Sin coincidencias literales para '{texto}'. Puede que el cliente "
                "lo haya escrito distinto a como se llama en la carta: esto es TODO "
                "lo que hay hoy.\n"
                f"{hoy}\n"
                "Si alguno de esos es lo que quiso decir, búscalo otra vez con ESE "
                "nombre para obtener sus precios y su variante_id. Si ninguno encaja, "
                "no lo vendemos. Y si lo que escribió no nombra ningún producto (solo "
                "un tamaño, una cantidad o un saludo), pregúntale qué quiere: NO "
                "elijas por él."
            )

        grouped = {}
        for _score, product, variants in scored[:_MAX_RESULTADOS]:
            grouped.setdefault(product.category, []).append((product, variants))
        lines = []
        for category, entries in grouped.items():
            lines.append(f"[{category.name} · {category.business.name}]")
            for product, variants in entries:
                prices = "; ".join(
                    f"{v.name} {_cop(v.price)} [variante_id={v.id}]" for v in variants
                )
                extra = (
                    f" (personalizable, slug='{product.slug}')"
                    if _es_personalizable(product)
                    else ""
                )
                lines.append(f"  - {product.name}: {prices}{extra}")
        sobrantes = len(scored) - _MAX_RESULTADOS
        if sobrantes > 0:
            lines.append(
                f"(y {sobrantes} coincidencia(s) menos parecida(s); usa consultar_menu si hace falta)"
            )
        return "Coincidencias en el menú:\n" + "\n".join(lines)

    @tool
    def consultar_historial_cliente() -> str:
        """Últimos pedidos de ESTE cliente: qué pidió, cuándo y por cuánto.
        Úsala para saludar por su nombre, sugerir 'lo de siempre' o recomendar
        según sus gustos. También devuelve su dirección habitual si se conoce.
        """
        orders = (
            _customer_orders(contact)
            .exclude(status=Order.Status.CANCELLED)
            .prefetch_related("items__product_variant__product")
            .order_by("-created_at")[:5]
        )
        known_name = contact.customer_name or contact.profile_name
        ubicacion = _ubicacion_guardada(contact)
        aviso_ubicacion = (
            f"Ubicación guardada: {ubicacion}. NO le pidas que la comparta otra vez: "
            "pregúntale si el domicilio va al mismo sitio. Si dice que sí, sigue sin "
            "pedirle nada más; solo si te dice que es a otro lado le pides la ubicación nueva."
            if ubicacion
            else ""
        )
        if not orders:
            partes = []
            if known_name:
                partes.append(
                    f"Este cliente no tiene pedidos anteriores, pero su nombre de perfil "
                    f"de WhatsApp es: {known_name}."
                )
            else:
                partes.append("Este cliente no tiene pedidos anteriores registrados.")
            if aviso_ubicacion:
                partes.append(aviso_ubicacion)
            return " ".join(partes)
        lines = []
        if known_name:
            lines.append(f"Nombre conocido: {known_name}")
        if aviso_ubicacion:
            lines.append(aviso_ubicacion)
        if contact.default_address:
            lines.append(
                f"Dirección habitual: {contact.default_address}"
                + (f" (ref: {contact.default_reference})" if contact.default_reference else "")
            )
        if contact.notes:
            lines.append(f"Preferencias guardadas: {contact.notes}")
        favoritos = _lo_que_mas_pide(contact)
        if favoritos:
            lines.append(f"Lo que más pide: {favoritos}")
        for order in orders:
            date = timezone.localtime(order.created_at).strftime("%Y-%m-%d")
            items = ", ".join(
                f"{item.product_variant.product.name} {item.product_variant.name}"
                for item in order.items.all()[:6]
            )
            lines.append(f"- {date}: {items} · {_cop(order.total)}")
        return "\n".join(lines)

    @tool
    def cotizar_pedido(
        items: list[ItemPedido], paga_con: str = "", para_recoger: bool = False
    ) -> str:
        """Calcula el total EXACTO de un pedido (items + envío) sin crearlo.
        Úsala SIEMPRE antes de mostrar el resumen al cliente y copia sus cifras
        tal cual: nunca sumes precios ni calcules vueltas por tu cuenta.

        Args:
            items: items del pedido con variante_id, cantidad y notas
            paga_con: SOLO efectivo: billete que DIJO el cliente (ej. '50000'),
                o 'exacto' si dice que paga completo/justo. PROHIBIDO inventar
                un valor que el cliente no mencionó.
            para_recoger: True si el cliente pasa por el pedido al local (sin envío)
        """
        if not items:
            return "ERROR: no hay items para cotizar."
        cfg = StoreSettings.load()
        lines = []
        subtotal = Decimal("0.00")
        for item in items:
            try:
                variant = ProductVariant.objects.select_related("product").get(
                    pk=item.variante_id, is_active=True, product__is_active=True
                )
            except ProductVariant.DoesNotExist:
                return f"ERROR: la variante {item.variante_id} no existe o no está activa. Revisa el menú."
            line_total = (variant.price or Decimal("0.00")) * item.cantidad
            subtotal += line_total
            lines.append(
                f"- {item.cantidad}x {variant.product.name} {variant.name} · {_cop(line_total)}"
            )
        envio = Decimal("0.00") if para_recoger else cfg.delivery_fee
        total = subtotal + envio
        if para_recoger:
            lines.append("Para recoger en el local: sin envío.")
        else:
            lines.append(f"Envío: {_cop(envio)}")
        lines.append(f"TOTAL: {_cop(total)}")
        billete = re.sub(r"\D", "", paga_con)
        if billete:
            billete = Decimal(billete)
            if billete < total:
                lines.append(
                    f"OJO: el billete ({_cop(billete)}) no alcanza para el total; "
                    "pregunta al cliente cómo completa el pago."
                )
            else:
                lines.append(
                    f"Billete OK ({_cop(billete)}). NO menciones vueltas al cliente: "
                    "el dato queda en el pedido y el equipo las alista."
                )
        # Chat real del 27/08: el agente mostró la cotización como si el pedido
        # ya existiera ("te aviso cuando esté listo") y nunca llamó crear_pedido
        lines.append(
            "ESTO ES SOLO UNA COTIZACIÓN: el pedido NO está creado. Muestra este "
            "resumen al cliente (a domicilio, pregunta antes el método de pago si "
            "aún no lo sabes) y espera su confirmación; el pedido existe SOLO "
            "cuando crear_pedido responda PEDIDO CREADO."
        )
        return "\n".join(lines)

    @tool
    def crear_pedido(
        items: list[ItemPedido],
        nombre_cliente: str = "",
        metodo_pago: str = "",
        direccion: str = "",
        referencia: str = "",
        paga_con: str = "",
        notas: str = "",
        para_recoger: bool = False,
        telefono_contacto: str = "",
        paga_al_recibir: bool = False,
    ) -> str:
        """Crea el pedido DEFINITIVO, a domicilio o para recoger en el local.

        A domicilio: llámala cuando el cliente confirmó el resumen (items y
        total). La ubicación de WhatsApp hace de dirección y la toma el sistema
        por su cuenta (verifícala antes con verificar_cobertura); tú nunca
        manejas coordenadas. La dirección escrita es opcional y NO se le pide.
        Lo ÚNICO imprescindible son los items y el nombre: si falta la
        ubicación, el método de pago o el celular de contacto, el pedido SE
        CREA IGUAL y la tool te dice qué quedó pendiente para que el equipo lo
        cuadre. Nunca dejes un pedido sin crear porque un dato no llegó.
        Para recoger (para_recoger=True): llámala cuando el cliente confirme el
        resumen (items y TOTAL de cotizar_pedido). NO pidas dirección,
        ubicación, teléfono ni método de pago (paga al recoger en el local, sin
        envío); responde que el pedido quedó creado.

        Args:
            items: items del pedido con variante_id, cantidad y notas
            nombre_cliente: nombre de quien recibe o de quien pasa a recoger; si
                lo omites se usa el nombre ya conocido del cliente
            metodo_pago: cash o nequi (los únicos que acepta el local; un pago
                por llave Bre-B va como nequi, porque la llave es ese mismo
                número). Pregúntalo siempre a domicilio, pero si el cliente no
                lo dijo déjalo vacío y crea el pedido igual; para recoger va
                vacío (paga al recoger)
            direccion: dirección escrita del cliente (solo domicilio) y SOLO
                si la dio por su cuenta; déjala vacía si no la dijo, porque no
                se le pide: la ubicación que compartió es la dirección
            referencia: punto de referencia, solo si el cliente lo mencionó
                (domicilio); tampoco se le pide
            paga_con: SOLO efectivo: billete que DIJO el cliente (ej. '50000'),
                o 'exacto' si dice que paga completo/justo. PROHIBIDO inventar
                un valor que el cliente no mencionó.
            notas: aclaraciones generales del pedido
            para_recoger: True si el cliente pasa por el pedido al local
            telefono_contacto: celular del cliente (10 dígitos) SOLO para
                domicilios de clientes a los que WhatsApp no les muestra el
                número (el sistema te lo avisa); en otro caso déjalo vacío
            paga_al_recibir: True si el cliente dijo que paga cuando le
                entreguen (típico del Nequi que manda al llegar el
                domiciliario). Queda anotado para que el equipo lo cobre allí;
                NUNCA esperes el comprobante para crear el pedido
        """
        cfg = StoreSettings.load()
        if not cfg.is_open:
            return (
                "ERROR: el local está CERRADO ahora mismo; no se pueden crear pedidos "
                "de ningún tipo. Dile al cliente que está cerrado y que "
                f"{cfg.reopening_hint()}; no le ofrezcas encargar ni recoger."
            )
        if not para_recoger and not cfg.customer_ordering_enabled:
            # Ya no es que preguntara: tenía el pedido armado y se quedó sin él
            try:
                domicilios.anotar(contact)
            except Exception:
                logger.exception("No se pudo anotar el domicilio perdido de %s", contact.phone)
            return (
                "ERROR: justo en este momento no hay servicio de domicilios; "
                "díselo al cliente con esas palabras. Sí puedes tomarlo PARA RECOGER "
                "(para_recoger=True): ofrécelo antes de despedir al cliente."
            )
        if metodo_pago and metodo_pago not in Order.ACTIVE_PAYMENT_METHODS:
            return f"ERROR: metodo_pago inválido. Usa uno de: {', '.join(Order.ACTIVE_PAYMENT_METHODS)}."
        if not items:
            return "ERROR: el pedido no tiene items."
        contact.refresh_from_db()
        nombre = (
            nombre_cliente.strip() or contact.customer_name or contact.profile_name
        ).strip()[:200]
        if not nombre:
            return (
                "ERROR: falta el nombre de quien "
                + ("pasa a recoger el pedido" if para_recoger else "recibe el pedido")
                + ": pregúntale solo eso."
            )
        celular = ""
        if kapso.is_bsuid(contact.phone):
            # WhatsApp no nos muestra el número de este cliente. Un domicilio
            # lleva el celular que él mismo dio, por si el equipo necesita
            # llamarlo; para recoger no hace falta (viene al local)
            if telefono_contacto.strip() and not _celular_colombiano(telefono_contacto):
                return (
                    "ERROR: ese celular de contacto no es válido. Pide un número celular "
                    "de 10 dígitos (ej. 300 123 4567)."
                )
            celular = _celular_colombiano(telefono_contacto) or contact.contact_phone
        # Un pedido a domicilio sin ubicación SÍ se crea: lo caro no es que le
        # falte un dato, es que el pedido no exista (chat del 06/09, la
        # ubicación que WhatsApp no nos entregó dejó la conversación muerta).
        # Lo que falta se anota para que el equipo lo pida; lo único que sigue
        # frenando el pedido es una ubicación que SABEMOS que está fuera.
        sin_ubicacion = not para_recoger and (
            contact.last_location_lat is None or contact.last_location_lng is None
        )
        if not para_recoger and not sin_ubicacion and not is_within_delivery_area(
            contact.last_location_lat, contact.last_location_lng
        ):
            return (
                f"ERROR: la ubicación del cliente está FUERA de la zona de domicilios "
                f"({coverage_label()}). NO crees el pedido: explícale "
                "con amabilidad que por ahora no llegamos hasta allá."
            )
        pendientes = []
        if sin_ubicacion:
            pendientes.append(
                missing.LOCATION + (" (dio dirección escrita)" if direccion.strip() else "")
            )
        if not para_recoger and not metodo_pago:
            pendientes.append("el método de pago")
        if not para_recoger and kapso.is_bsuid(contact.phone) and not celular:
            pendientes.append("el celular de contacto")
        if metodo_pago == Order.PaymentMethod.CASH and not paga_con:
            pendientes.append("con qué billete paga")

        variants = {}
        for item in items:
            try:
                variants[item.variante_id] = ProductVariant.objects.select_related(
                    "product"
                ).get(pk=item.variante_id, is_active=True, product__is_active=True)
            except ProductVariant.DoesNotExist:
                return f"ERROR: la variante {item.variante_id} no existe o no está activa. Revisa el menú."

        customer_notes = notas.strip()
        if paga_al_recibir and metodo_pago != Order.PaymentMethod.CASH:
            # En efectivo se paga al recibir por definición; con Nequi no, y el
            # domiciliario tiene que saber que va a cobrar en la puerta.
            customer_notes = (customer_notes + "\nPaga al recibir el pedido.").strip()
        if metodo_pago == Order.PaymentMethod.CASH and paga_con:
            billete = re.sub(r"\D", "", paga_con)
            billete_txt = (
                f"Paga en efectivo con {_cop(Decimal(billete))}."
                if billete
                else "Paga en efectivo con el valor exacto."
            )
            customer_notes = (customer_notes + "\n" + billete_txt).strip()
        # Lo que falta va en la primera línea de las notas: es lo que el equipo
        # ve en la tarjeta del pedido sin abrirla, y es lo que tiene que pedir.
        customer_notes = missing.note(pendientes, customer_notes)

        with transaction.atomic():
            order = Order.objects.create(
                source=Order.Source.WHATSAPP,
                order_type=(
                    Order.OrderType.PICKUP if para_recoger else Order.OrderType.DELIVERY
                ),
                customer_name=nombre,
                # El número que el staff puede llamar; si no hay (para recoger
                # sin número visible) queda el BSUID, que es lo que signals
                # necesita para notificarle el estado por WhatsApp
                customer_phone=celular
                or (contact.phone if kapso.is_bsuid(contact.phone) else normalize_phone(contact.phone)),
                customer_notes=customer_notes,
                payment_method=metodo_pago,
                delivery_address="" if para_recoger else direccion.strip()[:300],
                delivery_reference="" if para_recoger else referencia.strip()[:300],
                delivery_lat=None if para_recoger else contact.last_location_lat,
                delivery_lng=None if para_recoger else contact.last_location_lng,
                delivery_fee=Decimal("0.00") if para_recoger else cfg.delivery_fee,
            )
            for item in items:
                variant = variants[item.variante_id]
                unit_price = variant.price or Decimal("0.00")
                # Un OrderItem por unidad, igual que el checkout web
                for _ in range(item.cantidad):
                    OrderItem.objects.create(
                        order=order,
                        product_variant=variant,
                        quantity=1,
                        unit_price=unit_price,
                        subtotal=unit_price,
                        notes=item.notas[:200],
                    )
            order.calculate_totals()
            order.save()

        # Hizo su pedido (a domicilio o para recoger): ya no se quedó esperando
        # nada, así que si los domicilios vuelven no hay que avisarle de ellos
        domicilios.olvidar(contact)
        contact.customer_name = nombre
        campos = ["customer_name", "updated_at"]
        if celular and contact.contact_phone != celular:
            contact.contact_phone = celular
            campos.append("contact_phone")
        # La dirección escrita ya no se pide (la ubicación compartida es la
        # dirección): si el pedido no trae ninguna, se conserva la que hubiera
        # de antes en vez de borrarla.
        if not para_recoger and direccion.strip():
            contact.default_address = direccion.strip()[:300]
            contact.default_reference = referencia.strip()[:300]
            campos += ["default_address", "default_reference"]
        contact.save(update_fields=campos)

        from apps.orders.consumers import broadcast_orders_update

        broadcast_orders_update()

        cierre = (
            "El cliente pasa por él al local y paga al recogerlo; dile el TOTAL y "
            "que le avisas cuando esté listo."
            if para_recoger
            else "Sale a domicilio."
        )
        aviso = ""
        if pendientes:
            falta = "la dirección" if sin_ubicacion else "lo que falta"
            aviso = (
                f"\nOJO: el pedido quedó creado con datos pendientes "
                f"({', '.join(pendientes)}). El equipo ya los ve y se los pide al "
                f"cliente. A él dile que su pedido quedó tomado y, en una línea, "
                f"que el equipo le confirma {falta}; NO le repitas la instrucción "
                f"que ya no funcionó ni lo dejes esperando."
            )
        return (
            f"PEDIDO CREADO. {cierre}\n{_order_summary(order)}\n"
            f"Código de consulta: {order.access_code}.{aviso}"
        )

    @tool
    def modificar_pedido(
        numero_pedido: str,
        agregar_items: list[ItemPedido] = [],
        quitar_variante_id: int = 0,
        quitar_cantidad: int = 0,
        nueva_direccion: str = "",
        nueva_referencia: str = "",
    ) -> str:
        """Modifica un pedido de este cliente mientras siga PENDIENTE (la cocina
        aún no lo toma). Puede agregar items, quitar unidades de una variante o
        corregir la dirección.

        Args:
            numero_pedido: número del pedido (ej. 20260713-A1B2C3)
            agregar_items: items nuevos a sumar
            quitar_variante_id: variante a la que se le quitan unidades
            quitar_cantidad: cuántas unidades quitar de esa variante
            nueva_direccion: dirección corregida (vacío = no cambiar)
            nueva_referencia: referencia corregida (vacío = no cambiar)
        """
        try:
            order = _customer_orders(contact).get(order_number=numero_pedido.strip())
        except Order.DoesNotExist:
            return f"ERROR: no encontré el pedido {numero_pedido} de este cliente."
        if order.status != Order.Status.PENDING:
            return (
                f"ERROR: el pedido ya está '{order.get_status_display()}' y no se puede modificar. "
                "Ofrécele contactar a un humano si es urgente."
            )

        with transaction.atomic():
            for item in agregar_items:
                try:
                    variant = ProductVariant.objects.select_related("product").get(
                        pk=item.variante_id, is_active=True, product__is_active=True
                    )
                except ProductVariant.DoesNotExist:
                    return f"ERROR: la variante {item.variante_id} no existe o no está activa."
                unit_price = variant.price or Decimal("0.00")
                for _ in range(item.cantidad):
                    OrderItem.objects.create(
                        order=order,
                        product_variant=variant,
                        quantity=1,
                        unit_price=unit_price,
                        subtotal=unit_price,
                        notes=item.notas[:200],
                    )
            if quitar_variante_id and quitar_cantidad:
                removable = list(
                    order.items.filter(
                        product_variant_id=quitar_variante_id, is_paid=False
                    )[:quitar_cantidad]
                )
                if len(removable) < quitar_cantidad:
                    return "ERROR: el pedido no tiene tantas unidades de esa variante."
                for order_item in removable:
                    order_item.delete()
            if not order.items.exists():
                return "ERROR: el pedido quedaría vacío; usa cancelar_pedido en su lugar."
            if nueva_direccion:
                order.delivery_address = nueva_direccion.strip()[:300]
            if nueva_referencia:
                order.delivery_reference = nueva_referencia.strip()[:300]
            order.calculate_totals()
            order.save()

        from apps.orders.consumers import broadcast_orders_update

        broadcast_orders_update()
        return f"PEDIDO ACTUALIZADO.\n{_order_summary(order)}"

    @tool
    def cancelar_pedido(numero_pedido: str, lo_que_dijo_el_cliente: str) -> str:
        """Anula un pedido de este cliente mientras siga PENDIENTE.

        OJO: en Colombia "cancelar" casi siempre significa PAGAR. Úsala solo
        cuando el cliente pida de verdad que no le mandemos el pedido.

        Args:
            numero_pedido: número del pedido a anular
            lo_que_dijo_el_cliente: la frase TEXTUAL con la que lo pidió
        """
        lectura = intencion.leer_cancelar(
            lo_que_dijo_el_cliente, intencion.lo_ultimo_nuestro(contact)
        )
        if lectura == "pagar":
            return (
                "ERROR: no se canceló nada. Con eso el cliente está hablando de PAGAR, "
                "no de anular el pedido: aquí 'cancelar' es pagar. Si contestaba con qué "
                "billete paga, 'completo' o 'exacto' es paga_con='exacto'. Sigue con el "
                "pedido y NO le menciones ninguna cancelación."
            )
        if lectura != "anular":
            return (
                "ERROR: no se canceló nada porque esa frase no pide anular nada: puede "
                "ser una pregunta, una condición, un cambio de un producto o justo lo "
                "contrario ('no me lo vaya a cancelar'). Contéstale lo que dijo; si de "
                "verdad quiere que no le mandemos el pedido, que te lo diga y vuelves."
            )
        try:
            order = _customer_orders(contact).get(order_number=numero_pedido.strip())
        except Order.DoesNotExist:
            return (
                f"ERROR: no encontré el pedido {numero_pedido} de este cliente. "
                "No le hables de pedidos que no existen: si quería anular algo, "
                "pregúntale a cuál se refiere."
            )
        if order.status != Order.Status.PENDING:
            cerrado = order.status in (Order.Status.DELIVERED, Order.Status.CANCELLED)
            de_hoy = timezone.localtime(order.created_at).date() == timezone.localdate()
            if cerrado and not de_hoy:
                return (
                    f"ERROR: el pedido {order.order_number} es de otro día, ya está "
                    f"'{order.get_status_display()}' y terminó hace rato. Si el cliente "
                    "no lo nombró, NO se lo cuentes ni le expliques por qué no puedes "
                    "cancelarlo —él habla de lo de ahora—: averigua a qué se refiere. "
                    "Pero si está reclamando ESE pedido (que nunca le llegó, que llegó "
                    "mal), no lo discutas: usa solicitar_humano."
                )
            if cerrado:
                return (
                    f"ERROR: el pedido {order.order_number} figura "
                    f"'{order.get_status_display()}' y ya no se cancela por aquí. Es de "
                    "hoy, así que el cliente sabe de cuál habla: si dice que no le "
                    "llegó o que está mal, no lo discutas —usa solicitar_humano para "
                    "que el equipo lo revise."
                )
            return (
                f"ERROR: el pedido ya está '{order.get_status_display()}'; "
                "no se puede cancelar por este medio."
            )
        order.mark_as_cancelled()

        from apps.orders.consumers import broadcast_orders_update

        broadcast_orders_update()
        return f"Pedido {order.order_number} CANCELADO."

    @tool
    def consultar_pedido(numero_pedido: str = "") -> str:
        """Estado actual de un pedido de este cliente. Sin argumento devuelve
        el pedido activo más reciente.

        Args:
            numero_pedido: número del pedido (vacío = el más reciente activo)
        """
        orders = _customer_orders(contact).order_by("-created_at")
        if numero_pedido.strip():
            orders = orders.filter(order_number=numero_pedido.strip())
        else:
            orders = orders.filter(
                status__in=[Order.Status.PENDING, Order.Status.PREPARING, Order.Status.READY]
            )
        order = orders.first()
        if not order:
            return "No encontré pedidos activos de este cliente."
        return _order_summary(order)

    @tool
    def guardar_preferencia(preferencia: str) -> str:
        """Guarda una preferencia DURADERA del cliente para futuras visitas
        (ej. 'no le gusta la cebolla', 'siempre pide granizado de café grande').
        No la uses para datos de un solo pedido.

        Args:
            preferencia: la preferencia en una frase corta
        """
        existing = contact.notes.strip()
        contact.notes = (existing + "\n- " + preferencia.strip()).strip() if existing else "- " + preferencia.strip()
        contact.save(update_fields=["notes", "updated_at"])
        return "Preferencia guardada."

    @tool
    def verificar_cobertura() -> str:
        """Verifica si la ubicación de WhatsApp que compartió el cliente está
        dentro de la zona de domicilios. Úsala APENAS el cliente comparta su
        ubicación, siempre antes de crear el pedido (la ubicación es
        OBLIGATORIA para todo domicilio) y SIEMPRE antes de decirle que no te
        ha llegado: la tool sabe si WhatsApp nos la bloqueó. Lee la ubicación
        registrada por el sistema: no necesita coordenadas.
        """
        contact.refresh_from_db()
        if contact.last_location_lat is None or contact.last_location_lng is None:
            # El cliente pudo mandarla y que WhatsApp no nos la entregara: eso
            # no llega por webhook, hay que preguntárselo a Kapso
            if kapso.recent_undelivered(contact.phone):
                return (
                    "OJO: el cliente SÍ intentó enviarnos algo hace poco (muy "
                    "probablemente la ubicación) pero WhatsApp no nos lo entregó: "
                    "nos llegó vacío. NO le digas que no la ha compartido ni repitas "
                    "la misma instrucción. Dile que su ubicación no llegó (pasa "
                    "cuando se envía desde WhatsApp Web o un dispositivo vinculado) y "
                    "pídele que la reenvíe DESDE EL CELULAR (clip de adjuntar → "
                    "Ubicación → Enviar ubicación actual). Si ya lo intentó dos veces "
                    "o no puede, SIGUE con el pedido y créalo sin ubicación: el "
                    "equipo le confirma la dirección después."
                )
            return (
                "El cliente NO ha compartido su ubicación de WhatsApp todavía. "
                "Pídesela una vez (clip de adjuntar → Ubicación → Enviar ubicación "
                "actual) y sigue con el resto del pedido en el mismo turno; no te "
                "quedes esperándola. Si no llega, el pedido se crea igual: el "
                "equipo le confirma la dirección después."
            )
        lines = []
        if is_within_delivery_area(contact.last_location_lat, contact.last_location_lng):
            lines.append(
                "DENTRO de la zona de domicilios: se puede entregar en esa ubicación."
            )
        else:
            lines.append(
                f"FUERA de la zona de domicilios ({coverage_label()}): NO se "
                "puede hacer el domicilio a esa ubicación. Explícalo "
                "con amabilidad y no tomes el pedido; si el cliente comparte otra "
                "ubicación que sí esté dentro, se puede."
            )
        if contact.last_location_at and timezone.localtime(
            contact.last_location_at
        ).date() < timezone.localdate():
            fecha = timezone.localtime(contact.last_location_at).strftime("%d/%m/%Y")
            sitio = (contact.last_location_label or "").strip()
            donde = f" ({sitio})" if sitio else ""
            lines.append(
                f"OJO: la ubicación es del {fecha}{donde}, de una conversación "
                "anterior. NO le pidas que la comparta de nuevo: pregúntale si el "
                "pedido va al mismo sitio y, si dice que sí, sigue. Solo si es otro "
                "lugar le pides la ubicación nueva."
            )
        return "\n".join(lines)

    @tool
    def solicitar_humano(motivo: str) -> str:
        """Pausa al agente para este cliente y deja la conversación en manos del
        equipo humano. Úsala si el cliente lo pide o si la situación te supera
        (quejas serias, pagos en disputa, temas fuera del menú).

        Args:
            motivo: por qué se necesita un humano
        """
        # La pausa CADUCA a propósito. Antes esto encendía human_handoff, que no
        # se apaga solo: tres clientes quedaron sin agente durante semanas (Anyi
        # V escaló el 06/09 y el 15/09 nadie le respondió hasta que entró una
        # persona 10 minutos después). Mientras el equipo atienda, cada mensaje
        # suyo renueva la pausa por su cuenta (ver worker); si nadie entra, el
        # agente vuelve solo en vez de dejar el chat mudo para siempre.
        # human_handoff queda para el interruptor manual del panel.
        pausa = timezone.now() + timedelta(
            minutes=settings.WHATSAPP_HANDOFF_PAUSE_MINUTES
        )
        if not contact.human_until or contact.human_until < pausa:
            contact.human_until = pausa
            contact.save(update_fields=["human_until", "updated_at"])
        _avisar_escalada(contact, motivo)
        return (
            "Listo: el agente queda en pausa para este cliente y el equipo verá la "
            "conversación. Despídete indicando que una persona le escribirá pronto."
        )

    @tool
    def enviar_sticker(nombre: str) -> str:
        """Elige el sticker con el que rematas este turno: sale al final, detrás
        del texto que escribas. Puede ir solo, sin texto, cuando el gesto era
        toda tu respuesta. Elige por el "cuándo usarlo" de la lista que tienes
        en tus instrucciones, no por su nombre.

        Args:
            nombre: el nombre exacto del sticker, tal como aparece en tu lista
        """
        # El turno que no toca se respeta aquí y no solo en el prompt: es lo
        # que convierte el "a veces sí, a veces no" en algo real y no en una
        # sugerencia que el modelo sigue cuando le parece.
        if turn.sticker_urge is not None and not turn.sticker_urge.allowed:
            return (
                "Este turno va sin sticker. Responde con texto y no menciones que ibas "
                "a mandar uno."
            )
        wanted = _normalize(nombre)
        catalog = Sticker.catalog()
        sticker = next((s for s in catalog if _normalize(s.label) == wanted), None)
        if sticker is None:
            # El modelo se inventa nombres cuando la lista no le cuadra; darle
            # los que existen es más barato que un turno perdido
            names = ", ".join(s.label for s in catalog) or "ninguno"
            return f"No existe el sticker '{nombre}'. Los que hay son: {names}."
        # Queda apuntado y lo manda el worker cuando el texto ya salió (ver
        # stickers.deliver): así el orden es el de una persona y, si el turno
        # acaba descartándose, el cliente no se queda con un sticker suelto.
        turn.sticker = sticker
        turn.answered = True
        return (
            f"Listo: el sticker «{sticker.label}» sale al final de este turno. Si el gesto "
            "era toda tu respuesta, no escribas nada más y ya está; si aún te falta decir "
            "algo, escríbelo y saldrá antes del sticker. No lo menciones ni lo describas."
        )

    @tool
    def enviar_foto_producto(producto_slug: str) -> str:
        """Manda al chat la foto real de un producto. Úsala cuando el cliente
        pregunte cómo es algo o pida verlo, en vez de describírselo.

        Args:
            producto_slug: slug del producto, tal como sale de consultar_menu o buscar_producto
        """
        product = Product.objects.filter(slug=producto_slug, is_active=True).first()
        if product is None:
            return (
                f"No hay producto activo con slug '{producto_slug}'. "
                "Búscalo con buscar_producto para tener el slug correcto."
            )
        if not product.image_url:
            return (
                f"{product.name} no tiene foto cargada. Descríbeselo con lo que sepas "
                "del menú y pásale el enlace de la carta si quiere verlo."
            )
        result = kapso.send_image(turn.phone_number_id, contact.phone, product.image_url)
        if result is None:
            return "No se pudo mandar la foto. Sigue con texto y no la menciones."
        turn.posted = True
        turn.answered = True
        return f"Foto de {product.name} enviada. El cliente ya la vio: no la describas."

    @tool
    def enviar_botones(texto: str, opciones: list[str]) -> str:
        """Manda un mensaje con botones para que el cliente toque en vez de
        escribir. Su único uso es confirmar el pedido (Sí / Cambiar algo /
        Cancelar). Lo que toque te llega como si lo hubiera escrito.

        NUNCA la uses para el método de pago: esa pregunta va en texto. Tampoco
        en preguntas abiertas (qué quiere pedir, su dirección, el sabor): ahí
        los botones dejan fuera respuestas válidas.

        Args:
            texto: la pregunta completa, con el resumen o el total si aplica
            opciones: entre 2 y 3 respuestas, de máximo 20 caracteres cada una
        """
        choices = [str(o).strip() for o in opciones if str(o).strip()][:3]
        if len(choices) < 2:
            return "Los botones necesitan al menos dos opciones. Pregúntalo con texto normal."
        if any(len(c) > 20 for c in choices):
            return (
                "Alguna opción pasa de 20 caracteres y WhatsApp la rechaza. "
                "Acórtalas y vuelve a intentar."
            )
        buttons = [(f"btn_{i}", c) for i, c in enumerate(choices)]
        result = kapso.send_buttons(turn.phone_number_id, contact.phone, texto, buttons)
        if result is None:
            return "No se pudieron mandar los botones. Haz la misma pregunta con texto normal."
        turn.posted = True
        turn.answered = True
        return (
            "Botones enviados con esa pregunta. YA ESTÁ DICHA: no la repitas en texto, "
            "responde vacío y espera a que el cliente toque una."
        )

    @tool
    def reaccionar(emoji: str) -> str:
        """Reacciona con un emoji al último mensaje del cliente, como haría una
        persona. No manda mensaje ni lo notifica: es solo el gesto.

        Reacciona cuando el mensaje trae algo que registrar (gracias, un
        chiste, una buena noticia, algo que salió mal), no a una pregunta
        normal ni a un dato del pedido: un bot que reacciona a todo es ruido.
        Una reacción por turno.

        Args:
            emoji: un solo emoji (❤️, 😂, 🔥, 👀, 🙌, 😢)
        """
        emoji = (emoji or "").strip()
        if not emoji:
            return "Falta el emoji."
        result = kapso.send_reaction(
            turn.phone_number_id, contact.phone, turn.message_id, emoji
        )
        if result is None:
            return "No se pudo reaccionar. Sigue normal y no lo menciones."
        turn.answered = True
        return "Reacción puesta. No la menciones ni la describas."

    @tool
    def guardar_sticker(nombre: str, cuando_usarlo: str) -> str:
        """Convierte en sticker el ÚLTIMO archivo que te mandó el dueño (imagen,
        sticker o video corto) y lo guarda en el banco. Solo para el dueño.

        Args:
            nombre: nombre corto para pedirlo después, ej. "granizado feliz"
            cuando_usarlo: el MOMENTO en que hay que mandarlo, no lo que se ve
                en el dibujo. Ej. "para celebrar que el pedido quedó listo".
        """
        draft = StickerDraft.objects.filter(contact=contact).first()
        if draft is None:
            return (
                "No tienes ningún archivo pendiente. Pídele que te mande primero la "
                "imagen, el sticker o el video, y luego lo guardas."
            )
        nombre = (nombre or "").strip()
        cuando_usarlo = (cuando_usarlo or "").strip()
        if not nombre or not cuando_usarlo:
            return "Faltan el nombre o el cuándo usarlo. Pregúntaselos antes de guardar."
        existente = next(
            (s for s in Sticker.objects.all() if _normalize(s.label) == _normalize(nombre)), None
        )
        try:
            data, animated = stickers_media.from_upload(bytes(draft.data), draft.kind)
        except stickers_media.StickerError as exc:
            return f"No se pudo convertir: {exc}"

        campos = {
            "description": cuando_usarlo[:200],
            "data": data,
            "byte_size": len(data),
            "is_animated": animated,
            "is_active": True,
        }
        if existente:
            # Mismo nombre = lo está reemplazando; el banco no admite duplicados
            for campo, valor in campos.items():
                setattr(existente, campo, valor)
            existente.save()
            sticker = existente
            verbo = "reemplazado"
        else:
            sticker = Sticker.objects.create(label=nombre[:60], **campos)
            verbo = "guardado"
        draft.delete()
        aviso = ""
        if draft.kind == "video" and not animated:
            aviso = " El video no cabía animado, así que quedó como imagen fija."
        return (
            f"Sticker '{sticker.label}' {verbo} ({sticker.byte_size // 1024} KB"
            f"{', animado' if animated else ''}). Ya lo puedes mandar en ese momento."
            f"{aviso} Confírmaselo en una línea."
        )

    @tool
    def listar_stickers() -> str:
        """Los stickers del banco con su nombre, su momento y si están activos.
        Solo para el dueño.
        """
        todos = list(Sticker.objects.all())
        if not todos:
            return "El banco está vacío: todavía no tienes ningún sticker."
        lineas = [
            f"- {s.label}: {s.description}"
            + ("" if s.is_active else " [DESACTIVADO]")
            + (f" · enviado {s.sent_count} veces" if s.sent_count else "")
            for s in todos
        ]
        return "\n".join(lineas)

    @tool
    def actualizar_sticker(nombre: str, nuevo_nombre: str = "", cuando_usarlo: str = "") -> str:
        """Cambia el nombre o el momento de uso de un sticker que ya existe.
        Solo para el dueño.

        Args:
            nombre: el sticker a cambiar, por su nombre actual
            nuevo_nombre: opcional, cómo se debe llamar de ahora en adelante
            cuando_usarlo: opcional, el momento nuevo en que hay que mandarlo
        """
        sticker = next(
            (s for s in Sticker.objects.all() if _normalize(s.label) == _normalize(nombre)), None
        )
        if sticker is None:
            return f"No existe un sticker llamado '{nombre}'. Míralos con listar_stickers."
        if nuevo_nombre.strip():
            sticker.label = nuevo_nombre.strip()[:60]
        if cuando_usarlo.strip():
            sticker.description = cuando_usarlo.strip()[:200]
        sticker.save()
        return f"Listo: '{sticker.label}' ahora se usa {sticker.description}."

    @tool
    def quitar_sticker(nombre: str) -> str:
        """Saca un sticker del banco: deja de existir para ti. Solo para el dueño.
        No lo borra del todo (se puede recuperar desde el panel de administración).

        Args:
            nombre: el sticker a quitar
        """
        sticker = next(
            (s for s in Sticker.objects.all() if _normalize(s.label) == _normalize(nombre)), None
        )
        if sticker is None:
            return f"No existe un sticker llamado '{nombre}'. Míralos con listar_stickers."
        sticker.is_active = False
        sticker.save(update_fields=["is_active", "updated_at"])
        return f"'{sticker.label}' quitado del banco: ya no lo vas a mandar."

    @tool
    def ajustar_tono(instrucciones: str) -> str:
        """Ajusta CÓMO hablas con los clientes, de forma permanente. Solo para
        el dueño, y solo cuando te lo pide explícitamente.

        Son retoques sobre la personalidad que el dueño ya eligió en el panel
        ("trata de usted", "sin emojis"), no la personalidad entera: cambiarla
        por completo se hace allá, no por chat.

        Lo que guardes aquí manda sobre tu estilo por defecto y se aplica desde
        la siguiente conversación. Escribe el texto COMPLETO que debe quedar, no
        solo lo nuevo: reemplaza lo anterior. Antes de guardar, dile al dueño con
        qué texto te vas a quedar y espera su visto bueno. Deja el campo vacío
        para volver a tu tono normal.

        Args:
            instrucciones: las reglas de estilo completas, o "" para volver al tono por defecto
        """
        config = AgentSettings.load()
        config.tone = (instrucciones or "").strip()[:2000]
        config.save(update_fields=["tone", "updated_at"])
        if not config.tone:
            return "Tono restablecido: vuelves a hablar como de costumbre."
        return f"Tono guardado. De ahora en adelante: {config.tone}"

    tools = [
        consultar_estado_tienda,
        consultar_menu,
        consultar_producto,
        buscar_producto,
        consultar_historial_cliente,
        cotizar_pedido,
        crear_pedido,
        modificar_pedido,
        cancelar_pedido,
        consultar_pedido,
        guardar_preferencia,
        verificar_cobertura,
        solicitar_humano,
    ]
    # Las tools que escriben en el chat necesitan por dónde mandarlo. Una tool
    # apagada se retira de la lista además de salir del prompt: describirle al
    # modelo algo que no puede llamar solo produce promesas que el turno no
    # cumple, y el cliente lo lee como que el bot está roto.
    if turn is not None and turn.can_send:
        if config.stickers_enabled and Sticker.catalog():
            tools.append(enviar_sticker)
        if config.product_photos_enabled:
            tools.append(enviar_foto_producto)
        if config.quick_replies_enabled:
            tools.append(enviar_botones)
        if config.reactions_enabled and turn.message_id:
            tools.append(reaccionar)
    # El dueño configura al agente por chat. Estas tools tocan cómo habla y qué
    # manda, nunca el dinero: los pedidos, los precios y los estados se siguen
    # gestionando con las mismas tools que para cualquier cliente.
    if config.is_owner(contact.phone):
        tools += [
            guardar_sticker,
            listar_stickers,
            actualizar_sticker,
            quitar_sticker,
            ajustar_tono,
        ]
    return tools
