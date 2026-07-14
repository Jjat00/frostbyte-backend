"""Zona de cobertura de domicilios: radio máximo alrededor del local.

La usan el checkout web (CustomerOrderCreateSerializer) y el agente de
WhatsApp (verificar_cobertura / crear_pedido). Solo aplica cuando hay
coordenadas: una dirección escrita sin ubicación no se puede validar.
"""

import math

from django.conf import settings


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


def is_within_delivery_area(lat, lng):
    return (
        distance_km(settings.DELIVERY_CENTER_LAT, settings.DELIVERY_CENTER_LNG, lat, lng)
        <= settings.DELIVERY_RADIUS_KM
    )


def radius_label():
    """Radio legible para mensajes al cliente: 1.0 -> '1 km'."""
    return f"{settings.DELIVERY_RADIUS_KM:g} km"
