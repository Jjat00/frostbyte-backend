"""Leer qué pide el cliente cuando dice "cancelar", con el modelo barato.

En Colombia "cancelar" significa PAGAR mucho más seguido que anular: "¿cuánto
le cancelo?", "cancelo completo", "lo cancelo por Nequi". El 20-09 Natt
contestó "Cancelo completo" a la pregunta de con qué billete pagaba, y el
agente fue a cancelarle un pedido de dos días antes y le respondió que ya
figuraba como entregado. Ella tuvo que explicárselo: "es decir que canceló el
pedido con 10 mil pesos".

Esto es la red debajo de la regla del prompt, y la pone un modelo y no una
lista de frases a propósito. Una expresión regular tiene que decidir por una
coincidencia dentro de la frase, sin el sentido de la frase entera, y el
español se le escapa siempre: "no lo quiero con whisky" es una modificación,
"no lo quiero, cancélalo" una renuncia y "no me lo vaya a cancelar" lo
contrario de las dos. Se probaron cuatro rondas de patrones y cada una dejaba
casos nuevos.

Va con el modelo barato (`WHATSAPP_SUMMARY_MODEL`, el de los resúmenes y la
visión), una sola palabra de salida y sin razonamiento: son milisegundos y
centésimas de centavo, y solo corre cuando el agente ya decidió cancelar algo.
"""

import logging

from django.conf import settings

from .llm import completion_params

logger = logging.getLogger(__name__)

PROMPT = """Trabajas en un local de comida y bebidas en Cumbal, Nariño (Colombia), leyendo \
lo que escribe un cliente por WhatsApp mientras le toman un pedido a domicilio.

En Colombia "cancelar" significa PAGAR muchísimo más seguido que anular: "¿cuánto le \
cancelo?", "cancelo completo", "lo cancelo por Nequi", "ya cancelé" son todos alguien \
pagando. Anular es cuando pide que no le mandemos el pedido.

Te doy lo último que escribimos nosotros y la frase del cliente. Lee la frase COMO \
RESPUESTA a eso: "déjalo así" contesta "¿le cambio el sabor?" dejando el pedido como \
estaba, y contesta "¿te lo preparo?" echándose para atrás. Responde con UNA sola palabra, \
sin nada más:

- anular: pide claramente que no le mandemos el pedido o que lo anulemos. También cuando \
se echa para atrás sin nombrarlo: "déjalo así", "mejor no", "olvídalo", "ya no quiero nada".
- pagar: está hablando de pagar, de cómo paga o de cuánto debe. Si junto a "cancelar" \
aparece cuánto o con qué —"completo", "exacto", "con un billete de 20", "con 20 mil", "al \
recibir", "por Nequi", "en efectivo"— es pagar, aunque diga "el pedido": "voy a cancelar el \
pedido completo" es alguien que paga el total, no alguien que lo anula.
- dudoso: cualquier otra cosa. Una pregunta ("¿se puede anular?"), una condición \
("si no hay de mango, cancélalo"), una prohibición ("no me lo vaya a cancelar"), un \
cambio de un producto y no del pedido entero ("no lo quiero con whisky", "ya no quiero \
la hamburguesa, solo las papas"), o simplemente que no esté claro.

Ante cualquier duda respondes dudoso: anular el pedido de alguien que estaba pagando es \
lo peor que puede pasar."""

# Lo que puede responder. Cualquier otra cosa se trata como dudoso.
LECTURAS = ("anular", "pagar", "dudoso")


def lo_ultimo_nuestro(contact):
    """Lo último que le escribimos a ese cliente, para leer su respuesta.

    Sin esto, "déjalo así" y "mejor no" llegan sueltos y pueden ser tanto una
    renuncia al pedido como un "no le pongas whisky": lo que cambia el sentido
    es la pregunta que contestan.
    """
    from .models import ChatMessage

    ultimo = (
        ChatMessage.objects.filter(
            phone=contact.phone[:30], direction=ChatMessage.Direction.OUTBOUND
        )
        .order_by("-created_at")
        .values_list("body", flat=True)
        .first()
    )
    return (ultimo or "").strip()[:400]


def leer_cancelar(frase, contexto=""):
    """Qué pide el cliente con esa frase: 'anular', 'pagar' o 'dudoso'.

    Nunca revienta: si el modelo no responde, la lectura es 'dudoso' y el
    pedido se queda como está, que es el lado seguro del error.
    """
    frase = (frase or "").strip()
    if not frase:
        return "dudoso"
    contexto = (contexto or "").strip()
    pregunta = (
        f"Lo último que escribimos: {contexto}\nEl cliente: {frase}"
        if contexto
        else f"El cliente: {frase}"
    )
    model = settings.WHATSAPP_SUMMARY_MODEL
    try:
        from .media import _openai_client

        result = _openai_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": pregunta},
            ],
            **completion_params(model, temperature=0, max_output_tokens=5, effort="none"),
        )
        lectura = (result.choices[0].message.content or "").strip().lower()
    except Exception:
        logger.exception("No se pudo leer la intención de cancelar de %r", frase)
        return "dudoso"
    return lectura if lectura in LECTURAS else "dudoso"
