"""Zona de cobertura de domicilios: radio máximo alrededor del local.

La usan el checkout web (CustomerOrderCreateSerializer) y el agente de
WhatsApp (verificar_cobertura / crear_pedido). Solo aplica cuando hay
coordenadas: una dirección escrita sin ubicación no se puede validar.

El centro sigue en settings (env DELIVERY_CENTER_LAT/LNG: el local no se
mueve). El radio vive en StoreSettings, editable por el staff desde la UI;
`settings.DELIVERY_RADIUS_KM` queda solo como valor de respaldo.
"""

import math

from django.conf import settings
from django.db import DatabaseError


def distance_km(lat1, lng1, lat2, lng2):
    """Distancia haversine en km entre dos puntos (lat/lng en grados)."""
    lat1, lng1, lat2, lng2 = (
        math.radians(float(v)) for v in (lat1, lng1, lat2, lng2)
    )
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(a))


def delivery_radius_km():
    """Radio vigente en km, leído de la configuración del local.

    Import diferido para no crear un ciclo con models.py. Si la tabla aún no
    existe (migraciones a medio aplicar), cae al valor de settings.
    """
    from .models import StoreSettings

    try:
        return float(StoreSettings.load().delivery_radius_km)
    except DatabaseError:
        return float(settings.DELIVERY_RADIUS_KM)


def is_within_delivery_area(lat, lng):
    return (
        distance_km(settings.DELIVERY_CENTER_LAT, settings.DELIVERY_CENTER_LNG, lat, lng)
        <= delivery_radius_km()
    )


def radius_label():
    """Radio legible para mensajes al cliente: 1.0 -> '1 km'."""
    return f"{delivery_radius_km():g} km"
