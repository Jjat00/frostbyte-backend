"""Tools del agente de pedidos por WhatsApp.

Cada tool consulta o muta directamente el ORM de Django. Se construyen por
contacto (closure) para que el agente nunca pueda operar sobre pedidos de
otro cliente. Todas devuelven strings: es lo que el modelo lee.
"""

import re
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from apps.orders.models import Order, OrderItem, StoreSettings
from apps.products.models import Category, Product, ProductVariant


def normalize_phone(phone):
    """Deja solo dígitos (ej. '+57 300 123-4567' -> '573001234567')."""
    return re.sub(r"\D", "", phone or "")


def _cop(value):
    """Formatea pesos colombianos: 12000 -> $12.000"""
    return "$" + f"{value:,.0f}".replace(",", ".")


def _order_summary(order):
    lines = [f"Pedido {order.order_number} · estado: {order.get_status_display()}"]
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
        domicilios = "ACTIVOS" if cfg.customer_ordering_enabled else "PAUSADOS"
        return (
            f"Local: {abierto}. Domicilios en línea: {domicilios}. "
            f"Tarifa de envío: {_cop(cfg.delivery_fee)}. "
            f"Se pueden crear pedidos: {'sí' if (cfg.is_open and cfg.customer_ordering_enabled) else 'NO'}."
        )

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
            return f"No existe un producto activo con slug '{producto_slug}'."

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
    def crear_pedido(
        items: list[ItemPedido],
        nombre_cliente: str,
        direccion: str,
        metodo_pago: str,
        referencia: str = "",
        paga_con: str = "",
        notas: str = "",
        latitud: float = 0.0,
        longitud: float = 0.0,
    ) -> str:
        """Crea el pedido a domicilio DEFINITIVO. Llámala SOLO después de que el
        cliente confirmó explícitamente el resumen completo (items, dirección,
        total con envío y método de pago).

        Args:
            items: items del pedido con variante_id, cantidad y notas
            nombre_cliente: nombre de quien recibe
            direccion: dirección de entrega completa
            metodo_pago: uno de: cash, transfer, nequi, daviplata
            referencia: punto de referencia o indicaciones para el domiciliario
            paga_con: SOLO efectivo: billete con el que paga (ej. '50000')
            notas: aclaraciones generales del pedido
            latitud: SOLO si el cliente compartió su ubicación de WhatsApp (lat del mensaje)
            longitud: SOLO si el cliente compartió su ubicación de WhatsApp (lng del mensaje)
        """
        cfg = StoreSettings.load()
        if not cfg.is_open:
            return "ERROR: el local está CERRADO ahora mismo; no se pueden crear pedidos."
        if not cfg.customer_ordering_enabled:
            return "ERROR: los domicilios están pausados en este momento."
        if metodo_pago not in Order.PaymentMethod.values:
            return f"ERROR: metodo_pago inválido. Usa uno de: {', '.join(Order.PaymentMethod.values)}."
        if not items:
            return "ERROR: el pedido no tiene items."
        if metodo_pago == Order.PaymentMethod.CASH and not paga_con:
            return "ERROR: para pago en efectivo pregunta primero con qué billete paga (paga_con)."

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
            billete_txt = _cop(Decimal(billete)) if billete else paga_con
            customer_notes = (customer_notes + f"\nPaga en efectivo con {billete_txt}.").strip()

        with transaction.atomic():
            order = Order.objects.create(
                source=Order.Source.WHATSAPP,
                order_type=Order.OrderType.DELIVERY,
                customer_name=nombre_cliente.strip()[:200],
                customer_phone=normalize_phone(contact.phone),
                customer_notes=customer_notes,
                payment_method=metodo_pago,
                delivery_address=direccion.strip()[:300],
                delivery_reference=referencia.strip()[:300],
                delivery_lat=Decimal(str(round(latitud, 7))) if (latitud or longitud) else None,
                delivery_lng=Decimal(str(round(longitud, 7))) if (latitud or longitud) else None,
                delivery_fee=cfg.delivery_fee,
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
        contact.default_address = direccion.strip()[:300]
        contact.default_reference = referencia.strip()[:300]
        contact.save(update_fields=["customer_name", "default_address", "default_reference", "updated_at"])

        from apps.orders.consumers import broadcast_orders_update

        broadcast_orders_update()

        return (
            f"PEDIDO CREADO.\n{_order_summary(order)}\n"
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
        consultar_historial_cliente,
        crear_pedido,
        modificar_pedido,
        cancelar_pedido,
        consultar_pedido,
        guardar_preferencia,
        solicitar_humano,
    ]
