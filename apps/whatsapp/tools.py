"""Tools del agente de pedidos por WhatsApp.

Cada tool consulta o muta directamente el ORM de Django. Se construyen por
contacto (closure) para que el agente nunca pueda operar sobre pedidos de
otro cliente. Todas devuelven strings: es lo que el modelo lee.
"""

import re
import unicodedata
from decimal import Decimal
from difflib import SequenceMatcher

from django.db import transaction
from django.utils import timezone
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from apps.orders.coverage import coverage_label, is_within_delivery_area
from apps.orders.models import Order, OrderItem, StoreSettings
from apps.products.models import Category, Product, ProductVariant

from . import kapso


def normalize_phone(phone):
    """Deja solo dígitos (ej. '+57 300 123-4567' -> '573001234567')."""
    return re.sub(r"\D", "", phone or "")


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
    lines.append(f"TOTAL: {_cop(order.total)} · pago: {order.get_payment_method_display() or 'sin definir'}")
    if order.delivery_address:
        lines.append(f"Dirección: {order.delivery_address}")
    return "\n".join(lines)


def _customer_orders(contact):
    """Pedidos históricos del contacto, emparejados por los últimos dígitos."""
    if kapso.is_bsuid(contact.phone):  # sin número: el pedido guarda el BSUID
        return Order.objects.filter(customer_phone=contact.phone)
    digits = normalize_phone(contact.phone)[-10:]
    if not digits:
        return Order.objects.none()
    return Order.objects.filter(customer_phone__endswith=digits)


class ItemPedido(BaseModel):
    variante_id: int = Field(description="ID de la variante (sale de consultar_menu o consultar_producto)")
    cantidad: int = Field(1, ge=1, le=50, description="Cuántas unidades")
    notas: str = Field("", description="Personalizaciones elegidas y aclaraciones de este item (ej. 'sin cebolla, salsa extra')")


def build_tools(contact):
    """Construye las tools ligadas a un WhatsAppContact concreto."""

    @tool
    def consultar_estado_tienda() -> str:
        """Consulta si Frostbyte está abierto y si los domicilios están activos.
        Úsala SIEMPRE antes de ofrecer productos o crear un pedido.
        """
        cfg = StoreSettings.load()
        abierto = "ABIERTO" if cfg.is_open else "CERRADO"
        domicilios = "ACTIVOS" if cfg.customer_ordering_enabled else "SIN SERVICIO AHORA"
        recoger = "ACTIVOS" if cfg.pickup_enabled else "SIN SERVICIO AHORA"
        puede_domicilio = cfg.is_open and cfg.customer_ordering_enabled
        puede_recoger = cfg.is_open and cfg.pickup_enabled
        lineas = [
            f"Local: {abierto}. Domicilios: {domicilios}. Pedidos para recoger: {recoger}.",
            f"Tarifa de envío: {_cop(cfg.delivery_fee)} (para recoger no se cobra envío).",
            f"Puedes tomar pedidos A DOMICILIO: {'sí' if puede_domicilio else 'NO'}.",
            f"Puedes tomar pedidos PARA RECOGER: {'sí' if puede_recoger else 'NO'}.",
        ]
        if puede_recoger and not puede_domicilio:
            lineas.append(
                "Ahora mismo no hay servicio de domicilios pero el local SÍ encarga para "
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
                .prefetch_related("variants", "modifier_links")
                .order_by("name")
            )
            product_lines = []
            for product in products:
                variants = [v for v in product.variants.all() if v.is_active]
                if not variants:
                    continue
                prices = "; ".join(f"{v.name} {_cop(v.price)} [variante_id={v.id}]" for v in variants)
                configurable = any(pm.is_active for pm in product.modifier_links.all())
                extra = f" (personalizable, slug='{product.slug}')" if configurable else ""
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
            min_sel, max_sel = pmg.effective_min, pmg.effective_max
            if min_sel == 0:
                rule = f"opcional, hasta {max_sel}"
            elif max_sel == 1:
                rule = "elige 1"
            else:
                rule = f"elige entre {min_sel} y {max_sel}"
            options = ", ".join(
                f"{opt.name}{' +' + _cop(opt.price_delta) if opt.price_delta else ''}"
                for opt in group.options.all()
                if opt.is_active
            )
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
            .prefetch_related("variants", "modifier_links")
        )
        scored = []
        for product in products:
            variants = [v for v in product.variants.all() if v.is_active]
            if not variants:
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

        if not scored:
            disponibles = list(
                dict.fromkeys(
                    Category.objects.filter(
                        is_active=True,
                        business__is_active=True,
                        products__is_active=True,
                        products__is_coming_soon=False,
                    )
                    .order_by("business__display_order", "display_order", "name")
                    .values_list("name", flat=True)
                )
            )
            return (
                f"Sin coincidencias para '{texto}' en el menú. "
                f"Categorías con productos hoy: {', '.join(disponibles)}. "
                "Si lo que pidió el cliente encaja con alguna de esas categorías, "
                "búscala por su nombre; si no encaja con ninguna, no lo vendemos."
            )

        scored.sort(key=lambda row: (-row[0], row[1].name))
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
                configurable = any(pm.is_active for pm in product.modifier_links.all())
                extra = f" (personalizable, slug='{product.slug}')" if configurable else ""
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
        if not orders:
            if known_name:
                return (
                    f"Este cliente no tiene pedidos anteriores, pero su nombre de perfil "
                    f"de WhatsApp es: {known_name}."
                )
            return "Este cliente no tiene pedidos anteriores registrados."
        lines = []
        if known_name:
            lines.append(f"Nombre conocido: {known_name}")
        if contact.default_address:
            lines.append(
                f"Dirección habitual: {contact.default_address}"
                + (f" (ref: {contact.default_reference})" if contact.default_reference else "")
            )
        if contact.notes:
            lines.append(f"Preferencias guardadas: {contact.notes}")
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
        return "\n".join(lines)

    @tool
    def crear_pedido(
        items: list[ItemPedido],
        nombre_cliente: str,
        metodo_pago: str,
        direccion: str = "",
        referencia: str = "",
        paga_con: str = "",
        notas: str = "",
        para_recoger: bool = False,
    ) -> str:
        """Crea el pedido DEFINITIVO, a domicilio o para recoger en el local.
        Llámala SOLO después de que el cliente confirmó explícitamente el
        resumen completo (items, total y método de pago).

        A domicilio: la dirección y la ubicación de WhatsApp son OBLIGATORIAS;
        la ubicación la toma el sistema por su cuenta (verifícala antes con
        verificar_cobertura) y tú nunca manejas coordenadas.
        Para recoger (para_recoger=True): no pidas dirección ni ubicación, no se
        cobra envío y el cliente pasa por el pedido al local.

        Args:
            items: items del pedido con variante_id, cantidad y notas
            nombre_cliente: nombre de quien recibe o de quien pasa a recoger
            metodo_pago: uno de: cash, nequi (son los únicos que acepta el local)
            direccion: dirección de entrega completa (solo domicilio)
            referencia: punto de referencia para el domiciliario (solo domicilio)
            paga_con: SOLO efectivo: billete que DIJO el cliente (ej. '50000'),
                o 'exacto' si dice que paga completo/justo. PROHIBIDO inventar
                un valor que el cliente no mencionó.
            notas: aclaraciones generales del pedido
            para_recoger: True si el cliente pasa por el pedido al local
        """
        cfg = StoreSettings.load()
        if not cfg.is_open:
            return "ERROR: el local está CERRADO ahora mismo; no se pueden crear pedidos."
        if para_recoger and not cfg.pickup_enabled:
            return (
                "ERROR: justo en este momento no se reciben pedidos para recoger; "
                "díselo al cliente con esas palabras."
            )
        if not para_recoger and not cfg.customer_ordering_enabled:
            return (
                "ERROR: justo en este momento no hay servicio de domicilios; "
                "díselo al cliente con esas palabras."
                + (
                    " Sí puedes tomarlo PARA RECOGER (para_recoger=True): ofrécelo "
                    "antes de despedir al cliente."
                    if cfg.pickup_enabled
                    else ""
                )
            )
        if not para_recoger and not direccion.strip():
            return "ERROR: para un domicilio hace falta la dirección de entrega."
        if metodo_pago not in Order.ACTIVE_PAYMENT_METHODS:
            return f"ERROR: metodo_pago inválido. Usa uno de: {', '.join(Order.ACTIVE_PAYMENT_METHODS)}."
        if not items:
            return "ERROR: el pedido no tiene items."
        if metodo_pago == Order.PaymentMethod.CASH and not paga_con:
            return "ERROR: para pago en efectivo pregunta primero con qué billete paga (paga_con)."
        contact.refresh_from_db()
        if not para_recoger and (
            contact.last_location_lat is None or contact.last_location_lng is None
        ):
            return (
                "ERROR: falta la ubicación de WhatsApp del cliente y es OBLIGATORIA "
                "para el domicilio. Pídele que la comparta (clip de adjuntar → "
                "Ubicación → Enviar ubicación actual); si no puede compartirla, usa "
                "solicitar_humano."
            )
        if not para_recoger and not is_within_delivery_area(
            contact.last_location_lat, contact.last_location_lng
        ):
            return (
                f"ERROR: la ubicación del cliente está FUERA de la zona de domicilios "
                f"({coverage_label()}). NO crees el pedido: explícale "
                "con amabilidad que por ahora no llegamos hasta allá."
            )

        variants = {}
        for item in items:
            try:
                variants[item.variante_id] = ProductVariant.objects.select_related(
                    "product"
                ).get(pk=item.variante_id, is_active=True, product__is_active=True)
            except ProductVariant.DoesNotExist:
                return f"ERROR: la variante {item.variante_id} no existe o no está activa. Revisa el menú."

        customer_notes = notas.strip()
        if metodo_pago == Order.PaymentMethod.CASH and paga_con:
            billete = re.sub(r"\D", "", paga_con)
            billete_txt = (
                f"Paga en efectivo con {_cop(Decimal(billete))}."
                if billete
                else "Paga en efectivo con el valor exacto."
            )
            customer_notes = (customer_notes + "\n" + billete_txt).strip()

        with transaction.atomic():
            order = Order.objects.create(
                source=Order.Source.WHATSAPP,
                order_type=(
                    Order.OrderType.PICKUP if para_recoger else Order.OrderType.DELIVERY
                ),
                customer_name=nombre_cliente.strip()[:200],
                # Si el cliente oculta su número, el pedido guarda su BSUID: es
                # lo que permite notificarle el estado (signals) y reconocerlo
                customer_phone=(
                    contact.phone
                    if kapso.is_bsuid(contact.phone)
                    else normalize_phone(contact.phone)
                ),
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

        contact.customer_name = nombre_cliente.strip()[:200]
        campos = ["customer_name", "updated_at"]
        if not para_recoger:
            contact.default_address = direccion.strip()[:300]
            contact.default_reference = referencia.strip()[:300]
            campos += ["default_address", "default_reference"]
        contact.save(update_fields=campos)

        from apps.orders.consumers import broadcast_orders_update

        broadcast_orders_update()

        cierre = (
            "El cliente pasa por él al local; avísale cuando esté listo."
            if para_recoger
            else "Sale a domicilio."
        )
        return (
            f"PEDIDO CREADO. {cierre}\n{_order_summary(order)}\n"
            f"Código de consulta: {order.access_code}."
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
    def cancelar_pedido(numero_pedido: str) -> str:
        """Cancela un pedido de este cliente mientras siga PENDIENTE.

        Args:
            numero_pedido: número del pedido a cancelar
        """
        try:
            order = _customer_orders(contact).get(order_number=numero_pedido.strip())
        except Order.DoesNotExist:
            return f"ERROR: no encontré el pedido {numero_pedido} de este cliente."
        if order.status != Order.Status.PENDING:
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
                    "Ubicación → Enviar ubicación actual). Si ya lo intentó dos veces, "
                    "no insistas más: usa solicitar_humano."
                )
            return (
                "El cliente NO ha compartido su ubicación de WhatsApp todavía. "
                "Pídele que la comparta (clip de adjuntar → Ubicación → Enviar "
                "ubicación actual): sin ella no se puede crear el pedido."
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
            lines.append(
                f"OJO: la ubicación es del {fecha} (conversación anterior). Confirma "
                "con el cliente que la entrega es en ese mismo punto; si es otro "
                "lugar, pídele que comparta la ubicación nueva."
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
        contact.human_handoff = True
        contact.save(update_fields=["human_handoff", "updated_at"])
        return (
            "Listo: el agente queda en pausa para este cliente y el equipo verá la "
            "conversación. Despídete indicando que una persona le escribirá pronto."
        )

    return [
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
