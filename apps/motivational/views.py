from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from django.utils import timezone
from apps.products.models import Product
from .models import RecommenderLog
import os
import json
import random


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
    Genera una frase nueva en cada petición.
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


def _get_active_products_list():
    """Retorna lista de productos activos con slug, nombre y categoría."""
    return Product.objects.filter(
        is_active=True, category__is_active=True
    ).select_related("category").order_by("category__display_order", "name")


def _build_product_lines(products):
    return "\n".join(
        f'- {p.name} (slug: {p.slug}) | categoría: {p.category.name} | {(p.description or "")[:100]}'
        for p in products
    )


def _parse_ai_recommendation(content, products):
    """Parsea la respuesta JSON de OpenAI; usa primer producto como fallback."""
    try:
        data = json.loads(content)
        product_slug = data.get("product_slug", "")
        reason = data.get("reason", "")
        product = products.filter(slug=product_slug).first()
        if not product:
            product = products.first()
        return product, reason
    except (json.JSONDecodeError, Exception):
        return products.first(), ""


def _serialize_product(product, reason):
    return {
        "product": {
            "name": product.name,
            "category": product.category.name,
            "category_slug": product.category.slug,
            "slug": product.slug,
            "description": product.description or "",
            "image_url": product.image_url or "",
        },
        "reason": reason,
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def recommend_by_mood(request):
    """
    Recomienda una bebida según el estado de ánimo del usuario.
    Input: {"mood": "tengo calor y quiero algo fuerte"}
    """
    mood = request.data.get("mood", "").strip()
    if not mood:
        return Response({"error": "El campo 'mood' es requerido."}, status=status.HTTP_400_BAD_REQUEST)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return Response({"error": "OpenAI API key no configurada"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    try:
        products = _get_active_products_list()
        products_list = list(products)
        random.shuffle(products_list)
        product_lines = _build_product_lines(products_list)

        prompt = f"""Eres un experto barman colombiano de Frostbyte, un bar de bebidas heladas.
Usa la descripción de cada producto para entender su contenido (alcohol, sabor, temperatura).

Productos disponibles:
{product_lines}

Basándote en lo que el cliente expresó, recomienda UNO de los productos más adecuados.
Responde ÚNICAMENTE con JSON puro (sin markdown, sin backticks):
{{"product_slug": "slug-del-producto", "reason": "razón personalizada en español colombiano, máximo 40 palabras, tono cercano y juvenil"}}"""

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "mood del cliente: " + mood},
            ],
            max_tokens=150,
            temperature=0.9,
            timeout=10,
        )

        content = response.choices[0].message.content.strip()
        product, reason = _parse_ai_recommendation(content, products)

        if not product:
            return Response({"error": "No hay productos activos disponibles."}, status=status.HTTP_404_NOT_FOUND)

        RecommenderLog.objects.create(
            session_type="mood",
            input_data={"mood": mood},
            recommended_product_name=product.name,
            recommended_product_slug=product.slug,
            ai_reason=reason,
            ip_address=request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                       or request.META.get("REMOTE_ADDR"),
        )

        return Response(_serialize_product(product, reason), status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": f"Error al generar recomendación: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([AllowAny])
def transcribe_audio(request):
    """
    Transcribe un archivo de audio a texto usando OpenAI Whisper.
    Input: audio file via request.FILES['audio']
    """
    audio_file = request.FILES.get("audio")
    if not audio_file:
        return Response(
            {"error": "Se requiere un archivo de audio."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return Response(
            {"error": "OpenAI API key no configurada"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        client = OpenAI(api_key=api_key)
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio_file.name, audio_file.read(), audio_file.content_type),
        )
        return Response({"text": transcription.text}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": f"Error al transcribir audio: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def recommend_by_quiz(request):
    """
    Recomienda una bebida según preferencias del quiz.
    Input: {"temperature": "frio", "taste": "dulce", "alcohol": "sin_alcohol"}
    """
    temperature = request.data.get("temperature", "").strip()
    taste = request.data.get("taste", "").strip()
    alcohol = request.data.get("alcohol", "").strip()

    if not all([temperature, taste, alcohol]):
        return Response(
            {"error": "Se requieren los campos 'temperature', 'taste' y 'alcohol'."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return Response({"error": "OpenAI API key no configurada"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    try:
        products = _get_active_products_list()
        products_list = list(products)
        random.shuffle(products_list)
        product_lines = _build_product_lines(products_list)

        prompt = f"""Eres un experto barman colombiano de Frostbyte, un bar de bebidas heladas.
Usa la descripción de cada producto para entender su contenido (alcohol, sabor, temperatura).

El cliente respondió el quiz con estas preferencias:
- Temperatura preferida: {temperature}
- Sabor que le llama: {taste}
- Alcohol: {alcohol}

Productos disponibles:
{product_lines}

Basándote en las preferencias del quiz, recomienda UNO de los productos más adecuados.
Responde ÚNICAMENTE con JSON puro (sin markdown, sin backticks):
{{"product_slug": "slug-del-producto", "reason": "razón personalizada en español colombiano, máximo 40 palabras, tono cercano y juvenil"}}"""

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un experto barman colombiano. Respondes SOLO con JSON puro, sin markdown."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=150,
            temperature=0.8,
            timeout=10,
        )

        content = response.choices[0].message.content.strip()
        product, reason = _parse_ai_recommendation(content, products)

        if not product:
            return Response({"error": "No hay productos activos disponibles."}, status=status.HTTP_404_NOT_FOUND)

        RecommenderLog.objects.create(
            session_type="quiz",
            input_data={"temperature": temperature, "taste": taste, "alcohol": alcohol},
            recommended_product_name=product.name,
            recommended_product_slug=product.slug,
            ai_reason=reason,
            ip_address=request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                       or request.META.get("REMOTE_ADDR"),
        )

        return Response(_serialize_product(product, reason), status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": f"Error al generar recomendación: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
