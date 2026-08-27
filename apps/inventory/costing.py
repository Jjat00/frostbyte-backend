"""Reglas de costeo de un producto a partir de su receta.

Una sola fuente para las cifras que se muestran al lado de un costo: ganancia,
margen, food cost y precio sugerido. La regla de precio es la de la casa:
el precio se fija para un food cost del 50 % (costo x2) redondeado a miles
hacia abajo.
"""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

# Food cost objetivo: parte del precio que se va en materia prima.
TARGET_FOOD_COST = Decimal("0.50")
# El precio sugerido se redondea hacia abajo a este multiplo.
PRICE_STEP = Decimal("1000")

_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")


def suggested_price(cost, target_food_cost=TARGET_FOOD_COST, step=PRICE_STEP):
    """Precio al que el costo pesa `target_food_cost`, redondeado abajo a `step`.

    8.300 -> 16.000 ; 12.700 -> 25.000 ; sin costo -> None.
    """
    if cost is None or cost <= 0:
        return None
    raw = Decimal(cost) / target_food_cost
    return (raw / step).to_integral_value(rounding=ROUND_DOWN) * step


def costing_figures(price, cost, has_recipe):
    """Cifras derivadas de un precio de venta y un costo de receta.

    Devuelve Decimals ya redondeados (o None cuando no hay receta o precio).
    `margin_pct` es la ganancia sobre el precio; `food_cost_pct` el costo
    sobre el precio. Suman 100 cuando ambos existen.
    """
    price = Decimal(price or 0)
    if not has_recipe:
        return {
            "cost": None,
            "profit": None,
            "margin_pct": None,
            "food_cost_pct": None,
            "suggested_price": None,
        }

    cost = Decimal(cost or 0).quantize(_CENT, rounding=ROUND_HALF_UP)
    profit = (price - cost).quantize(_CENT, rounding=ROUND_HALF_UP)
    if price > 0:
        margin_pct = (profit / price * 100).quantize(_TENTH, rounding=ROUND_HALF_UP)
        food_cost_pct = (cost / price * 100).quantize(_TENTH, rounding=ROUND_HALF_UP)
    else:
        margin_pct = None
        food_cost_pct = None

    return {
        "cost": cost,
        "profit": profit,
        "margin_pct": margin_pct,
        "food_cost_pct": food_cost_pct,
        "suggested_price": suggested_price(cost),
    }
