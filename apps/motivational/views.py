from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from django.utils import timezone
from django.core.cache import cache
from apps.products.models import Product
import os


PHRASE_CACHE_TTL = 30 * 60  # 30 minutos


def _generate_phrase():
    """
    Genera una frase motivacional usando OpenAI.
    Separada para poder cachear el resultado sin acoplar al request.
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return None

    client = OpenAI(api_key=api_key)

    fecha_actual = timezone.localtime(timezone.now())
    dia_semana = fecha_actual.strftime("%A")
    dia_mes = fecha_actual.day
    mes = fecha_actual.strftime("%B")
    anio = fecha_actual.year

    # Obtener productos activos agrupados por categoría
    active_products = Product.objects.filter(
        is_active=True, category__is_active=True
    ).select_related("category").order_by("category__display_order", "name")

    products_by_category = {}
    for product in active_products:
        category_name = product.category.name
        if category_name not in products_by_category:
            products_by_category[category_name] = []
        products_by_category[category_name].append(product.name)

    products_formatted = "\n".join(
        f"- {category}: {', '.join(products)}"
        for category, products in products_by_category.items()
    )

    prompt = f"""Hoy es {dia_semana} {dia_mes} de {mes} de {anio}.

        Productos activos de Frostbyte por categoría:
{products_formatted}
        Crea una frase motivacional o dato curioso histórico basada en la fecha actual
        y invita a consumir uno de los productos activos de Frostbyte.
        La frase debe ser breve y motivadora.
        La frase debe ser en español colombiano.
        La frase debe ser en tono juvenil.
        La frase debe ser en tono casual.
        La frase debe ser en tono cercano.
        La frase debe ser en tono amigable.
        La frase debe ser en tono relajado.
        La frase debe ser en tono inspirador.
        tienes que ser gracioso y divertido.
        puedes usars humor pesado y grosero.
        maximo 30 palabras. no mas de 30 palabras.
        """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Eres un experto Colombiano en dar datos curiosos e interesantes basados en la fecha actual y también un creador de frases motivacionales inspiradoras. Tus datos históricos son siempre precisos y verificables."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=80,
        temperature=0.9,
        timeout=10,
    )

    frase = response.choices[0].message.content.strip()

    return {
        "phrase": frase,
        "date": fecha_actual.strftime("%Y-%m-%d"),
        "day": dia_semana,
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def get_motivational_phrase(request):
    """
    Genera una frase motivacional o dato curioso histórico usando OpenAI basada en la fecha actual.
    Cachea el resultado por 30 minutos para no bloquear el thread pool con llamadas a OpenAI.
    """
    try:
        cache_key = "motivational_phrase"
        cached = cache.get(cache_key)

        if cached:
            return Response(cached, status=status.HTTP_200_OK)

        result = _generate_phrase()

        if result is None:
            return Response(
                {"error": "OpenAI API key no configurada"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        cache.set(cache_key, result, PHRASE_CACHE_TTL)

        return Response(result, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": f"Error al generar frase: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
