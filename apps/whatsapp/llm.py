"""Parámetros del modelo de lenguaje del agente de WhatsApp.

La familia GPT-5 razona antes de responder y eso cambia cómo se la llama: no
acepta `temperature` (salvo con esfuerzo de razonamiento `none`), el
presupuesto de salida se pide con `max_completion_tokens` porque los tokens de
razonamiento se descuentan de ahí, y el esfuerzo se gradúa con
`reasoning_effort`. Estos helpers arman los parámetros correctos para el modelo
configurado, así cambiar de modelo (o volver a `gpt-4o-mini`) es cambiar una
env var y nada más.
"""

from django.conf import settings

# Familias que razonan. `gpt-5-chat` es la excepción: es el GPT-5 sin
# razonamiento y se comporta como los modelos clásicos.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Margen de tokens para el razonamiento: se suma al presupuesto de la respuesta
# visible, que es lo único que mide quien llama. Sin margen el modelo gasta el
# cupo pensando y devuelve texto vacío.
REASONING_BUDGET = 2000


def is_reasoning_model(model):
    name = (model or "").lower()
    return name.startswith(REASONING_PREFIXES) and "chat" not in name


def _effort(effort):
    return effort or settings.WHATSAPP_AGENT_REASONING_EFFORT


def chat_model_params(model, temperature, effort=None):
    """Parámetros para `ChatOpenAI` (LangChain) según el modelo configurado.

    Con un modelo de razonamiento se usa la Responses API: es la que conserva
    el razonamiento entre las llamadas a tools de un mismo turno (el agente
    encadena varias por pedido), en vez de tirarlo en cada ida y vuelta.
    """
    if not is_reasoning_model(model):
        return {"temperature": temperature}
    effort = _effort(effort)
    params = {"reasoning_effort": effort, "use_responses_api": True}
    if effort == "none":
        # Sin razonamiento el modelo vuelve a aceptar temperature
        params["temperature"] = temperature
    return params


def completion_params(model, temperature, max_output_tokens, effort=None):
    """Parámetros para el SDK de OpenAI (`chat.completions.create`)."""
    if not is_reasoning_model(model):
        return {"temperature": temperature, "max_tokens": max_output_tokens}
    effort = _effort(effort)
    params = {
        "reasoning_effort": effort,
        "max_completion_tokens": max_output_tokens + REASONING_BUDGET,
    }
    if effort == "none":
        params["temperature"] = temperature
    return params
