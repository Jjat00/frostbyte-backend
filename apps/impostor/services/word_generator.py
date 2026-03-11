import json
import logging

from django.conf import settings
from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Eres un generador de palabras para un juego de mesa tipo "El Impostor".
Tu trabajo es generar pares de palabras en español: una palabra real y una palabra trampa.

Reglas:
- La palabra real es la que conocen los ciudadanos
- La palabra trampa es similar pero diferente, para confundir al impostor
- Ambas deben pertenecer a la misma categoria
- La palabra trampa debe ser lo suficientemente parecida para que el impostor pueda fingir, pero diferente para que los ciudadanos lo noten
- Las palabras deben ser sustantivos o conceptos conocidos en español latinoamericano
- No uses palabras ofensivas o inapropiadas
- Responde SOLO con JSON valido, sin markdown ni explicaciones"""


def generate_words(category_name, count=5, exclude_words=None):
    """Genera pares de palabras con IA para una categoria dada."""
    if not settings.OPENAI_API_KEY:
        return None

    exclude_words = exclude_words or []
    exclude_str = ""
    if exclude_words:
        exclude_str = f"\n\nNO repitas estas palabras que ya se usaron: {', '.join(exclude_words)}"

    user_prompt = (
        f"Genera {count} pares de palabras para la categoria '{category_name}'.\n"
        f"Cada par debe tener 'word' (palabra real) y 'trap_word' (palabra trampa similar pero diferente).\n"
        f"Responde con un JSON array: [{{'word': '...', 'trap_word': '...'}}]"
        f"{exclude_str}"
    )

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.9,
            max_tokens=500,
        )

        content = response.choices[0].message.content.strip()
        # Limpiar posible markdown
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            content = content.rsplit("```", 1)[0]

        words = json.loads(content)

        if not isinstance(words, list) or len(words) == 0:
            return None

        # Validar estructura
        valid_words = []
        for w in words:
            if isinstance(w, dict) and "word" in w and "trap_word" in w:
                valid_words.append({
                    "word": str(w["word"]).strip(),
                    "trap_word": str(w["trap_word"]).strip(),
                })

        return valid_words if valid_words else None

    except Exception as e:
        logger.error(f"Error generando palabras con IA: {e}")
        return None
