from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from django.utils import timezone
from apps.products.models import Product
import os


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

        Estamos celebrando el Dia de Amor y Amistad en Frostbyte.
        Crea una frase romantica, tierna o sobre la amistad que invite a celebrar
        este dia especial consumiendo uno de los productos activos de Frostbyte.
        La frase debe hacer alusion al amor, la amistad, compartir, brindar juntos,
        o a celebrar con las personas que mas quieres.
        La frase debe ser en español colombiano.
        La frase debe ser en tono juvenil, casual, cercano y amigable.
        Puedes ser gracioso, creativo y usar humor colombiano.
        Puedes hacer juegos de palabras con los nombres de los productos y el amor/amistad.
        Maximo 30 palabras. No mas de 30 palabras.
        No uses hashtags ni emojis.
        """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Eres un experto colombiano en crear frases creativas y tiernas sobre el amor y la amistad. Trabajas para Frostbyte, un bar de bebidas heladas. Tus frases son romanticas, divertidas y siempre invitan a celebrar con una buena bebida."},
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
        result = _generate_phrase()

        if result is None:
            return Response(
                {"error": "OpenAI API key no configurada"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(result, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": f"Error al generar frase: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
