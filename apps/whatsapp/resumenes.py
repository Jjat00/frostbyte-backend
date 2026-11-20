"""Resúmenes de ventas que le llegan al dueño por WhatsApp.

Tres resúmenes, los tres al cerrar el local desde el panel (orders.views,
al crear el StoreStatusEvent de cierre):

- **Día**: siempre. Cubre la jornada de 6:00 a. m. a 6:00 a. m., la misma
  de la gráfica de horario, así que lo vendido pasada la medianoche cuenta
  en la noche que empezó el día anterior.
- **Semana** (lunes a domingo): al cerrar la jornada del domingo.
- **Mes**: al cerrar la jornada del último día del mes.

Respaldo: si el domingo o el último día no se cerró desde el panel, el vigía
de WhatsApp (watchdog._una_vuelta) manda la semana o el mes desde las 8:00
del día siguiente. OwnerReport marca lo ya enviado (restricción única), así
que el respaldo no repite lo que salió al cerrar ni un reinicio lo duplica.
Pasado un día, el respaldo ya no lo manda: un resumen de la semana pasada
que llega el jueves no sirve.

El dueño no siempre tiene abierta la ventana de 24 h de WhatsApp, así que el
mensaje sale como PLANTILLA aprobada por Meta (WHATSAPP_REPORT_TEMPLATE_DAY y
..._PERIOD). Si la plantilla falla (no existe todavía, la rechazaron) se
intenta el mismo texto como mensaje libre, que llega si la ventana está
abierta. Las variables de una plantilla no aceptan saltos de línea: por eso
la estructura vive en la plantilla y aquí solo se arman las cifras.

Las cifras salen de las mismas funciones del panel de analítica
(FinancialAnalyticsViewSet): ventas cobradas por fecha de creación del
pedido, sin cancelados y con los descuentos restados. Todo junto, sin
separar Frostbyte de Frostbyte Food.

Apagado por defecto (OWNER_REPORTS_ENABLED): en local el .env tiene las
credenciales buenas de Kapso, y cada cierre de prueba le llegaría al dueño.
"""

import logging
import threading
from datetime import datetime, time, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import IntegrityError, close_old_connections
from django.db.models import Count, Sum
from django.utils import timezone

from apps.analytics.views import FinancialAnalyticsViewSet
from apps.orders.models import Order, OrderItem, StoreStatusEvent

from . import kapso
from .models import AgentSettings, OwnerReport

logger = logging.getLogger(__name__)

DAY_START_HOUR = 6  # la jornada va de 6:00 a. m. a 6:00 a. m. (analytics/schedule.py)
BACKUP_HOUR = 8  # el respaldo de semana o mes sale desde las 8 a. m. (a las 6 el dueño duerme)
LATE_LIMIT = timedelta(days=1)  # pasado esto, el respaldo ya no sale

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
CANALES = (
    (Order.Source.STAFF, "Local"),
    (Order.Source.WHATSAPP, "WhatsApp"),
    (Order.Source.CUSTOMER, "App"),
)

# Textos de las plantillas tal como se registran en Meta. El respaldo en
# texto libre usa los mismos, así el dueño lee lo mismo llegue por donde
# llegue. Si se cambia uno aquí, hay que cambiar la plantilla en Kapso.
# Nada de despedidas ni guiños: el 2026-11-16 Meta pasó a Marketing la primera
# versión del cierre (resumen_cierre_dia) por terminar en "Buen descanso 🌙".
TEMPLATE_DAY_TEXT = (
    "📊 Cierre del {{1}}\n"
    "Ventas: {{2}}\n"
    "Pedidos: {{3}} · Ticket promedio: {{4}}\n"
    "Por canal: {{5}}\n"
    "Más vendido: {{6}}\n"
    "Abierto: {{7}}\n"
    "Reporte automático generado al cerrar el local."
)
TEMPLATE_PERIOD_TEXT = (
    "📈 Resumen de {{1}}\n"
    "Ventas: {{2}} ({{3}})\n"
    "Pedidos: {{4}} · Ticket promedio: {{5}}\n"
    "Por canal: {{6}}\n"
    "Más vendido: {{7}}\n"
    "Días abiertos: {{8}}\n"
    "Gastos: {{9}} · Margen: {{10}}\n"
    "El detalle está en el panel de analítica."
)

_finanzas = FinancialAnalyticsViewSet()


# --- Formato ---------------------------------------------------------------

def _cop(value):
    """12000 -> $12.000"""
    return "$" + f"{Decimal(value or 0):,.0f}".replace(",", ".")


def _hora(moment):
    """Hora de reloj: 1:30 p. m."""
    local = timezone.localtime(moment)
    h12 = local.hour % 12 or 12
    sufijo = "a. m." if local.hour < 12 else "p. m."
    return f"{h12}:{local.minute:02d} {sufijo}"


def _duracion(delta):
    minutos = int(delta.total_seconds() // 60)
    horas, minutos = divmod(minutos, 60)
    if not horas:
        return f"{minutos} min"
    return f"{horas} h {minutos} min" if minutos else f"{horas} h"


def _cambio(actual, anterior, nombre):
    """'+12 % vs. la semana anterior'. Sin base no hay porcentaje que valga."""
    if not anterior:
        return f"sin ventas {nombre} para comparar"
    pct = (Decimal(actual) - Decimal(anterior)) / Decimal(anterior) * 100
    signo = "+" if pct >= 0 else "−"
    return f"{signo}{abs(pct):.0f} % vs. {nombre}"


def _render(texto, params):
    for i, valor in reversed(list(enumerate(params, start=1))):
        texto = texto.replace("{{%d}}" % i, valor)
    return texto


# --- Rangos ----------------------------------------------------------------

def _inicio_jornada(fecha):
    return timezone.make_aware(datetime.combine(fecha, time(DAY_START_HOUR)))


def jornada_de(moment):
    """Fecha de la jornada a la que pertenece un instante."""
    return (timezone.localtime(moment) - timedelta(hours=DAY_START_HOUR)).date()


def semana_terminada(ahora):
    """(lunes, domingo) de la última semana cuya jornada del domingo ya cerró."""
    hoy = jornada_de(ahora)
    lunes_actual = hoy - timedelta(days=hoy.weekday())
    lunes = lunes_actual - timedelta(days=7)
    return lunes, lunes + timedelta(days=6)


def mes_terminado(ahora):
    """(día 1, último día) del último mes cuya última jornada ya cerró."""
    hoy = jornada_de(ahora)
    primero = hoy.replace(day=1) - relativedelta(months=1)
    return primero, hoy.replace(day=1) - timedelta(days=1)


# --- Cifras ----------------------------------------------------------------

def _items_cobrados(inicio, fin):
    """Mismo criterio que el panel: items pagados de pedidos no cancelados."""
    return OrderItem.objects.filter(
        is_paid=True, order__created_at__gte=inicio, order__created_at__lt=fin,
    ).exclude(order__status=Order.Status.CANCELLED)


def _por_canal(inicio, fin):
    ids = _items_cobrados(inicio, fin).values("order_id").distinct()
    conteo = dict(
        Order.objects.filter(id__in=ids).values_list("source").annotate(n=Count("id"))
    )
    partes = [f"{nombre} {conteo[clave]}" for clave, nombre in CANALES if conteo.get(clave)]
    return " · ".join(partes) or "sin pedidos"


def _mas_vendido(inicio, fin):
    fila = (
        _items_cobrados(inicio, fin)
        .values("product_variant__product__name")
        .annotate(unidades=Sum("quantity"))
        .order_by("-unidades", "product_variant__product__name")
        .first()
    )
    if not fila:
        return "sin ventas"
    return f"{fila['product_variant__product__name']} ({fila['unidades']})"


def _ventas(inicio, fin):
    # El panel usa rangos cerrados (lte); con el fin exclusivo se resta un
    # microsegundo para no contar dos veces el pedido de las 6:00 en punto.
    hasta = fin - timedelta(microseconds=1)
    ventas = _finanzas._calculate_revenue(inicio, hasta)
    pedidos = _finanzas._calculate_orders_count(inicio, hasta)
    ticket = ventas / pedidos if pedidos else Decimal("0")
    return ventas, pedidos, ticket


def _jornadas_abiertas(inicio, fin):
    """{fecha de jornada: (primera apertura, último cierre)} del rango."""
    jornadas = {}
    eventos = StoreStatusEvent.objects.filter(created_at__gte=inicio, created_at__lt=fin)
    for evento in eventos.order_by("created_at"):
        apertura, cierre = jornadas.get(jornada_de(evento.created_at), (None, None))
        if evento.is_open:
            apertura = apertura or evento.created_at
        else:
            cierre = evento.created_at
        jornadas[jornada_de(evento.created_at)] = (apertura, cierre)
    return jornadas


def resumen_dia(cierre):
    """Parámetros de la plantilla del cierre, para la jornada de `cierre`."""
    fecha = jornada_de(cierre)
    inicio = _inicio_jornada(fecha)
    fin = cierre + timedelta(microseconds=1)
    ventas, pedidos, ticket = _ventas(inicio, fin)

    apertura, _ = _jornadas_abiertas(inicio, fin).get(fecha, (None, None))
    if apertura:
        abierto = f"{_hora(apertura)} a {_hora(cierre)} ({_duracion(cierre - apertura)})"
    else:
        abierto = f"hasta las {_hora(cierre)}"

    return [
        f"{DIAS[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]}",
        _cop(ventas),
        str(pedidos),
        _cop(ticket),
        _por_canal(inicio, fin),
        _mas_vendido(inicio, fin),
        abierto,
    ]


def resumen_periodo(desde, hasta, etiqueta, anterior_desde, nombre_anterior):
    """Parámetros de la plantilla de semana o mes (fechas de jornada, inclusivas)."""
    inicio = _inicio_jornada(desde)
    fin = _inicio_jornada(hasta + timedelta(days=1))
    ventas, pedidos, ticket = _ventas(inicio, fin)
    ventas_antes, _, _ = _ventas(_inicio_jornada(anterior_desde), inicio)

    # Los gastos se anotan por fecha calendario, no por jornada.
    dia_ini = timezone.make_aware(datetime.combine(desde, time.min))
    dia_fin = timezone.make_aware(datetime.combine(hasta, time.max))
    gastos = (
        _finanzas._calculate_inventory_expenses(dia_ini, dia_fin)
        + _finanzas._calculate_operational_expenses(dia_ini, dia_fin)
    )
    margen = f"{(ventas - gastos) / ventas * 100:.0f} %" if ventas else "sin ventas"

    jornadas = _jornadas_abiertas(inicio, fin)
    horas = sum(
        (c - a for a, c in jornadas.values() if a and c and c > a), timedelta()
    )
    dias = f"{len(jornadas)} ({_duracion(horas)})" if horas else str(len(jornadas))

    return [
        etiqueta,
        _cop(ventas),
        _cambio(ventas, ventas_antes, nombre_anterior),
        str(pedidos),
        _cop(ticket),
        _por_canal(inicio, fin),
        _mas_vendido(inicio, fin),
        dias,
        _cop(gastos),
        margen,
    ]


def etiqueta_semana(lunes, domingo):
    if lunes.month == domingo.month:
        return f"la semana del {lunes.day} al {domingo.day} de {MESES[domingo.month - 1]}"
    return (
        f"la semana del {lunes.day} de {MESES[lunes.month - 1]} "
        f"al {domingo.day} de {MESES[domingo.month - 1]}"
    )


def etiqueta_mes(primero):
    return f"{MESES[primero.month - 1]} {primero.year}"


# --- Envío -----------------------------------------------------------------

def _enviar(plantilla, texto_plantilla, params):
    """Manda el resumen a cada número del dueño. True si llegó a alguno."""
    numeros = sorted(AgentSettings.load().owner_numbers())
    ids = settings.KAPSO_PHONE_NUMBER_IDS
    if not numeros or not ids:
        logger.warning("Resumen %s sin dueño o sin número de Kapso; no se envía", plantilla)
        return False
    algun_ok = False
    for numero in numeros:
        ok = kapso.send_template(
            ids[0], numero, plantilla, settings.WHATSAPP_REPORT_TEMPLATE_LANGUAGE, params,
        )
        if not ok:
            # La plantilla no está aprobada o Meta la rechazó: el texto libre
            # llega si el dueño le escribió al número en las últimas 24 h.
            logger.warning("Plantilla %s falló; se intenta como texto libre", plantilla)
            ok = kapso.send_text(ids[0], numero, _render(texto_plantilla, params))
        algun_ok = algun_ok or bool(ok)
    return algun_ok


def _enviar_semana(lunes, domingo):
    params = resumen_periodo(
        lunes, domingo, etiqueta_semana(lunes, domingo),
        lunes - timedelta(days=7), "la semana anterior",
    )
    _enviar(settings.WHATSAPP_REPORT_TEMPLATE_PERIOD, TEMPLATE_PERIOD_TEXT, params)


def _enviar_mes(primero, ultimo):
    params = resumen_periodo(
        primero, ultimo, etiqueta_mes(primero),
        primero - relativedelta(months=1), "el mes anterior",
    )
    _enviar(settings.WHATSAPP_REPORT_TEMPLATE_PERIOD, TEMPLATE_PERIOD_TEXT, params)


def resumenes_del_cierre(cierre):
    """Lo que toca mandar al cerrar: el día y, si toca, la semana y el mes.

    Va en orden: primero el día, después la semana (si la jornada es domingo)
    y por último el mes (si es la del último día del mes). La semana y el mes
    dejan su fila en OwnerReport para que el respaldo de las 8 a. m. no los
    repita; si se vuelve a cerrar esa misma noche, salen otra vez con las
    cifras al día, igual que el resumen del día.
    """
    _enviar(settings.WHATSAPP_REPORT_TEMPLATE_DAY, TEMPLATE_DAY_TEXT, resumen_dia(cierre))

    fecha = jornada_de(cierre)
    if fecha.weekday() == 6:
        lunes = fecha - timedelta(days=6)
        OwnerReport.objects.get_or_create(kind=OwnerReport.Kind.WEEK, period_start=lunes)
        _enviar_semana(lunes, fecha)
    if (fecha + timedelta(days=1)).day == 1:
        primero = fecha.replace(day=1)
        OwnerReport.objects.get_or_create(kind=OwnerReport.Kind.MONTH, period_start=primero)
        _enviar_mes(primero, fecha)


def enviar_cierre(evento):
    """Resúmenes al cerrar el local. Corre en un hilo: no frena el panel."""
    if not settings.OWNER_REPORTS_ENABLED or evento.is_open:
        return

    def _send():
        try:
            resumenes_del_cierre(evento.created_at)
        except Exception:
            logger.exception("No se pudo enviar el resumen del cierre")
        finally:
            close_old_connections()

    threading.Thread(target=_send, name="resumen-cierre", daemon=True).start()


def _reclamar(tipo, desde):
    """Crea la fila del envío; False si otro proceso ya lo hizo."""
    try:
        OwnerReport.objects.create(kind=tipo, period_start=desde)
        return True
    except IntegrityError:
        return False


def _toca(tipo, desde, hasta, ahora):
    """Son más de las 8 a. m. del día siguiente, no pasó un día más y no se ha enviado."""
    desde_las = timezone.make_aware(
        datetime.combine(hasta + timedelta(days=1), time(BACKUP_HOUR))
    )
    if not (desde_las <= ahora < desde_las + LATE_LIMIT):
        return False
    return not OwnerReport.objects.filter(kind=tipo, period_start=desde).exists()


def barrer(ahora=None):
    """Respaldo: la semana o el mes que no salieron al cerrar.

    Lo normal es que salgan con el cierre del domingo o del último día del
    mes. Si esa noche no se cerró desde el panel (o el local no abrió), el
    vigía los manda desde las 8:00 del día siguiente. Lo llama cada minuto;
    casi siempre son dos consultas que dicen que no hay nada que hacer.
    """
    if not settings.OWNER_REPORTS_ENABLED:
        return
    ahora = ahora or timezone.now()

    lunes, domingo = semana_terminada(ahora)
    if _toca(OwnerReport.Kind.WEEK, lunes, domingo, ahora) and _reclamar(OwnerReport.Kind.WEEK, lunes):
        _enviar_semana(lunes, domingo)

    primero, ultimo = mes_terminado(ahora)
    if _toca(OwnerReport.Kind.MONTH, primero, ultimo, ahora) and _reclamar(OwnerReport.Kind.MONTH, primero):
        _enviar_mes(primero, ultimo)
