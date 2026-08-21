"""Zona de cobertura de domicilios: polígono dibujado o radio alrededor del local.

La usan el checkout web (CustomerOrderCreateSerializer) y el agente de
WhatsApp (verificar_cobertura / crear_pedido). Solo aplica cuando hay
coordenadas: una dirección escrita sin ubicación no se puede validar.

Hay dos formas de definir la zona, y el polígono manda:

1. `StoreSettings.delivery_area`: una o más figuras dibujadas por el admin en
   el mapa. Un círculo cubre barrios que no interesan y deja fuera calles que
   sí, así que la zona real se dibuja a mano.
2. `StoreSettings.delivery_radius_km`: el círculo de siempre, que queda como
   respaldo mientras no haya polígono (y como forma rápida de volver atrás).

El centro sigue en settings (env DELIVERY_CENTER_LAT/LNG: el local no se
mueve) y `settings.DELIVERY_RADIUS_KM` es el valor semilla del radio.
"""

import math

from django.conf import settings
from django.db import DatabaseError

# Límites del polígono: evitan una config gigante viajando en cada checkout y
# figuras degeneradas (con menos de 3 puntos no se encierra ningún área).
MAX_COVERAGE_POLYGONS = 8
MIN_POLYGON_POINTS = 3
MAX_POLYGON_POINTS = 200


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


def _store_settings():
    """Config del local. Import diferido para no crear un ciclo con models.py."""
    from .models import StoreSettings

    return StoreSettings.load()


def delivery_radius_km():
    """Radio vigente en km, leído de la configuración del local.

    Si la tabla aún no existe (migraciones a medio aplicar), cae al valor de
    settings.
    """
    try:
        return float(_store_settings().delivery_radius_km)
    except DatabaseError:
        return float(settings.DELIVERY_RADIUS_KM)


def clean_delivery_area(raw):
    """Valida y normaliza el polígono que llega de la API.

    Formato aceptado: lista de figuras, cada una lista de puntos `[lng, lat]`
    (orden GeoJSON). El anillo se cierra solo al dibujar, así que el último
    punto NO debe repetir al primero; si viene repetido, se descarta.

    Devuelve la lista normalizada (`[]` = sin polígono, se usa el radio) o
    lanza `ValueError` con un mensaje listo para mostrarle al staff.
    """
    if raw in (None, "", []):
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("La zona debe ser una lista de figuras.")

    # Comodidad: una sola figura suelta ([[lng, lat], ...]) también vale
    polygons = [raw] if _looks_like_single_polygon(raw) else raw

    if len(polygons) > MAX_COVERAGE_POLYGONS:
        raise ValueError(
            f"La zona no puede tener más de {MAX_COVERAGE_POLYGONS} figuras."
        )

    cleaned = []
    for polygon in polygons:
        if not isinstance(polygon, (list, tuple)):
            raise ValueError("Cada figura debe ser una lista de puntos.")
        points = [_clean_point(point) for point in polygon]
        # Un anillo cerrado explícito (primer punto repetido al final) es
        # válido en GeoJSON, pero aquí el cierre es implícito.
        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]
        if len(points) < MIN_POLYGON_POINTS:
            raise ValueError(
                f"Cada figura necesita al menos {MIN_POLYGON_POINTS} puntos."
            )
        if len(points) > MAX_POLYGON_POINTS:
            raise ValueError(
                f"Cada figura admite máximo {MAX_POLYGON_POINTS} puntos."
            )
        cleaned.append(points)
    return cleaned


def _looks_like_single_polygon(raw):
    """¿`raw` es una figura suelta ([[lng, lat], ...]) y no una lista de figuras?"""
    first = raw[0] if raw else None
    return (
        isinstance(first, (list, tuple))
        and len(first) == 2
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in first
        )
    )


def _clean_point(point):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError("Cada punto debe ser un par [longitud, latitud].")
    try:
        lng, lat = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        raise ValueError("Las coordenadas del polígono deben ser números.")
    if not (math.isfinite(lng) and math.isfinite(lat)):
        raise ValueError("Las coordenadas del polígono deben ser números.")
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        raise ValueError("Hay un punto con coordenadas fuera de rango.")
    # 7 decimales ≈ 1 cm: más precisión solo engorda el JSON
    return [round(lng, 7), round(lat, 7)]


def delivery_area():
    """Polígonos de cobertura vigentes, o `[]` si la zona es el círculo.

    Tolerante a propósito: si lo guardado quedó inservible, devuelve `[]` y la
    validación cae al radio en vez de dejar el pueblo entero fuera de zona.
    """
    try:
        raw = _store_settings().delivery_area
    except DatabaseError:
        return []
    try:
        return clean_delivery_area(raw)
    except ValueError:
        return []


def point_in_polygon(lat, lng, polygon):
    """Ray casting sobre lat/lng en grados (anillo cerrado implícitamente).

    Tratar los grados como un plano es exacto de sobra a escala de un pueblo;
    solo fallaría en un polígono que cruce el antimeridiano.
    """
    inside = False
    count = len(polygon)
    for i in range(count):
        lng1, lat1 = polygon[i]
        lng2, lat2 = polygon[(i + 1) % count]
        # ¿El lado cruza la horizontal que pasa por el punto?
        if (lat1 > lat) != (lat2 > lat):
            lng_at_lat = lng1 + (lat - lat1) * (lng2 - lng1) / (lat2 - lat1)
            if lng < lng_at_lat:
                inside = not inside
    return inside


def is_within_delivery_area(lat, lng):
    """¿La ubicación entra en la zona? El polígono manda; si no hay, el radio."""
    area = delivery_area()
    if area:
        return any(point_in_polygon(float(lat), float(lng), poly) for poly in area)
    return (
        distance_km(settings.DELIVERY_CENTER_LAT, settings.DELIVERY_CENTER_LNG, lat, lng)
        <= delivery_radius_km()
    )


def area_reach_km(area):
    """Distancia del local al punto más lejano de la zona dibujada, en km."""
    return max(
        (
            distance_km(
                settings.DELIVERY_CENTER_LAT, settings.DELIVERY_CENTER_LNG, lat, lng
            )
            for polygon in area
            for lng, lat in polygon
        ),
        default=0.0,
    )


def coverage_label():
    """Zona vigente en palabras, para los mensajes al cliente.

    Con polígono no se puede decir "X km alrededor del local" sin mentir: la
    zona es irregular, así que se nombra el alcance máximo como referencia.
    """
    area = delivery_area()
    if area:
        return (
            f"la zona de cobertura marcada en el mapa, hasta ~{area_reach_km(area):.1f} km "
            "del local según el sector"
        )
    return f"{delivery_radius_km():g} km alrededor del local"
