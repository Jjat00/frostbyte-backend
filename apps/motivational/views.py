from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from django.core.cache import cache
from django.utils import timezone
from apps.products.models import Product
from .models import RecommenderLog
import os
import json
import random

# Prefijo de la clave de caché de la frase.
# Se versiona (sufijo) para invalidar el caché al cambiar el tipo de frase: al
# desplegar con un prefijo nuevo, la frase actual se regenera sola.
PHRASE_CACHE_PREFIX = "motivational_phrase_curiosidades"

# La frase rota cada 30 minutos: cada franja usa un dato curioso distinto y
# un producto distinto en el CTA, y se cachea para no llamar a la IA en cada
# visita (a lo sumo una generación por franja con tráfico).
PHRASE_SLOT_SECONDS = 30 * 60


def _current_slot():
    """Identificador de la franja de 30 min actual (global, alineado a epoch)."""
    return int(timezone.now().timestamp()) // PHRASE_SLOT_SECONDS


# Temas que rotan por franja de 30 min. Son ámbitos, no hechos: el dato
# concreto lo genera la IA dentro del tema de la franja, con la instrucción
# de usar solo hechos reales y ampliamente conocidos (sin cifras dudosas).
TEMAS_CURIOSOS = [
    "la historia del hielo y de cómo se enfriaban las bebidas antes de las neveras",
    "el origen de los granizados, raspados y bebidas heladas del mundo",
    "el origen o la historia de un coctel clásico (mojito, piña colada, daiquiri, margarita...)",
    "frutas andinas y colombianas usadas en bebidas (lulo, maracuyá, mora, guanábana...)",
    "la historia de la cerveza y las micheladas",
    "curiosidades científicas del frío (por qué congela, el 'cerebro congelado', el hielo transparente...)",
    "tradiciones de bebidas y postres helados de los Andes y de Colombia",
    "la historia de las gaseosas, sodas y aguas con gas",
    "curiosidades del oficio del bartender y de la coctelería",
    "el vino, el champán y las bebidas con historia milenaria",
    "curiosidades de Nariño y del sur de Colombia (volcanes, páramos, clima frío)",
    "bebidas sin alcohol y mocktails alrededor del mundo",
]


def _generate_phrase(seed):
    """
    Genera la frase de la franja: la IA crea un dato curioso REAL dentro del
    tema de la franja (rotado por ``seed``, la franja de 30 min) y le suma el
    CTA a un producto activo. El prompt le exige hechos ampliamente conocidos
    y le prohíbe cifras o años de los que no esté segura. ``seed`` también
    baraja el producto del CTA, así cada franja cambia de tema y de bebida.
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

    # Productos activos, barajados con la semilla de la franja: así el CTA no
    # cae siempre en el mismo producto, pero se mantiene estable durante los
    # 30 min (la frase se cachea por franja).
    active_products = list(
        Product.objects.filter(is_active=True, category__is_active=True)
        .select_related("category")
    )
    random.Random(seed).shuffle(active_products)

    products_by_category = {}
    for product in active_products:
        products_by_category.setdefault(product.category.name, []).append(product.name)

    products_formatted = "\n".join(
        f"- {category}: {', '.join(products)}"
        for category, products in products_by_category.items()
    )

    # Tema de la franja (determinístico por ``seed``): rota cada 30 min
    # recorriendo la lista, así a lo largo del día se ven temas distintos y
    # la IA no repite siempre el mismo dato estrella.
    tema = TEMAS_CURIOSOS[seed % len(TEMAS_CURIOSOS)]

    prompt = f"""Hoy es {dia_semana} {dia_mes} de {mes} de {anio}.

Genera UN dato curioso REAL sobre este tema: {tema}.

Luego enlázalo de manera natural con una invitación a pasar por Frostbyte —bar de bebidas heladas en Cumbal, Nariño— a disfrutar UNO de estos productos activos, mencionándolo por su nombre:
{products_formatted}

Reglas:
- El dato debe ser verdadero y ampliamente conocido. Si no estás totalmente seguro de una cifra, un año o un nombre, NO lo menciones: cuenta el dato sin ese detalle. Las leyendas se presentan como leyendas.
- Una sola frase fluida que una el dato con la invitación al producto, en español colombiano juvenil.
- Varía el arranque (no empieces con "Un dato curioso" ni "¿Sabías que"); puedes arrancar por el protagonista, el lugar o la época.
- Máximo 40 palabras. Sin comillas ni emojis."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Eres un anfitrión colombiano, divertido y cercano. Cuentas datos curiosos REALES con gracia y los enlazas con una invitación a Frostbyte. Jamás inventas hechos, cifras ni nombres: si dudas de un detalle, lo omites y el dato sigue siendo cierto."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=110,
        temperature=0.8,
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
    Devuelve la frase de la franja actual (dato curioso verificado + CTA a un
    producto activo). Se genera con OpenAI UNA sola vez por franja de 30 min y
    se cachea: el resto de visitas de la franja reciben la misma frase sin
    volver a llamar a la IA. La clave incluye la franja, así que cada 30 min
    rota el dato (y el producto) automáticamente.
    """
    slot = _current_slot()
    cache_key = f"{PHRASE_CACHE_PREFIX}:{slot}"

    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached, status=status.HTTP_200_OK)

    try:
        result = _generate_phrase(slot)

        if result is None:
            return Response(
                {"error": "OpenAI API key no configurada"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # TTL hasta el fin de la franja, para que el cambio quede alineado a los
        # bloques de 30 min. Solo cacheamos resultados válidos (un fallo de IA no
        # debe quedar fijado).
        ttl = int((slot + 1) * PHRASE_SLOT_SECONDS - timezone.now().timestamp())
        cache.set(cache_key, result, timeout=max(60, ttl))
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
