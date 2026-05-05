from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import AnonRateThrottle
from openai import OpenAI
from django.utils import timezone
from django.core.cache import cache
from apps.products.models import Product
from .models import RecommenderLog, MotherDedication
from .serializers import (
    MotherDedicationCreateSerializer,
    MotherDedicationListSerializer,
)
import os
import json
import random
import base64
from google import genai
from google.genai import types


class MotherDedicationCreateThrottle(AnonRateThrottle):
    rate = "10/hour"


MOTHERS_DAY_CACHE_TTL = 5 * 60  # 5 minutos para que rote más seguido


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


def _generate_mothers_day_phrase():
    """
    Genera una frase llena de amor para el Día de la Madre usando OpenAI.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    client = OpenAI(api_key=api_key)

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

    prompt = f"""Hoy se celebra el Día de la Madre.

Productos activos de Frostbyte por categoría:
{products_formatted}

Crea un mensaje lleno de amor, ternura y gratitud para honrar a las mamás.
El mensaje debe:
- Celebrar el amor incondicional, la entrega y la fortaleza de las mamás
- Ser tierno, emotivo y profundamente sincero
- Mencionar sutilmente algún producto de Frostbyte como invitación a consentirlas
- Estar en español colombiano
- Tono cálido, cercano y amoroso
- Máximo 35 palabras
- NO usar hashtags
- Ser DIFERENTE cada vez que se genera, usa creatividad y variación"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "Eres un escritor colombiano sensible especializado en mensajes "
                           "llenos de amor para mamás. Cada mensaje que creas es único, "
                           "emotivo y honra a las madres. Nunca repites frases."
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=120,
        temperature=1.0,
        timeout=10,
    )

    frase = response.choices[0].message.content.strip()
    frase = frase.strip('"').strip("'").strip("“").strip("”")

    return {
        "phrase": frase,
        "date": "2026-05-10",
        "theme": "dia-madre",
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def get_mothers_day_phrase(request):
    """
    Genera una frase llena de amor para el Día de la Madre.
    Cache de 5 minutos para que rote frecuentemente.
    El parámetro ?nocache=1 fuerza una frase nueva.
    """
    try:
        nocache = request.query_params.get("nocache", "0") == "1"
        cache_key = "mothers_day_phrase"

        if not nocache:
            cached = cache.get(cache_key)
            if cached:
                return Response(cached, status=status.HTTP_200_OK)

        result = _generate_mothers_day_phrase()

        if result is None:
            return Response(
                {"error": "OpenAI API key no configurada"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        cache.set(cache_key, result, MOTHERS_DAY_CACHE_TTL)
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


# ─── Día de la Madre — Generador de tarjetas con IA ───────────────────


@api_view(["POST"])
@permission_classes([AllowAny])
def generate_mothers_day_phrase(request):
    """
    Genera una frase llena de amor para el Día de la Madre usando Gemini.
    El usuario puede luego editarla antes de generar la tarjeta.

    Input (JSON):
      - mother_name (optional): nombre de la mamá para personalizar la frase

    Output:
      { "phrase": "..." }
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return Response(
            {"error": "GEMINI_API_KEY no configurada."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    mother_name = (request.data.get("mother_name") or "").strip()

    prompt = (
        "Genera UNA frase profundamente emotiva, tierna y llena de amor para celebrar el Día de la Madre. "
        "La frase debe expresar gratitud, amor incondicional y reconocimiento al sacrificio, ternura y entrega de las mamás. "
        "Debe ser en español colombiano, tono cálido, sincero y conmovedor. "
        "Máximo 25 palabras. NO uses hashtags. NO uses comillas. "
        "Sé creativa, cada frase debe ser ÚNICA y diferente, que toque el corazón."
    )

    if mother_name:
        prompt += f'\nPersonaliza la frase mencionando el nombre "{mother_name}" de forma natural y cariñosa.'

    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
                response_modalities=["TEXT"],
            ),
        )

        phrase = response.text.strip().strip('"').strip("'").strip("“").strip("”")

        return Response({"phrase": phrase}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": f"Error al generar frase: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def generate_mothers_day_image(request):
    """
    Genera una imagen de tarjeta para el Día de la Madre usando Google Gemini
    con la foto de la mamá (o foto compartida) incorporada al diseño.

    Input (multipart/form-data):
      - image       (required) : foto de la mamá/familia para incluir en el diseño
      - phrase      (optional) : frase personalizada para la tarjeta
      - mother_name (optional) : nombre de la mamá homenajeada
      - from_name   (optional) : nombre de quien envía ("de parte de...")

    Output:
      {
        "image_base64": "<base64 string>",
        "mime_type": "image/png",
        "phrase_used": "<frase incluida en la tarjeta>"
      }
    """
    image_file = request.FILES.get("image")
    if not image_file:
        return Response(
            {"error": "El campo 'image' es requerido."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if image_file.content_type not in allowed_types:
        return Response(
            {"error": "Tipo de archivo no soportado. Usa JPEG, PNG o WebP."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return Response(
            {"error": "GEMINI_API_KEY no configurada."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    phrase = (request.data.get("phrase") or "").strip()
    mother_name = (request.data.get("mother_name") or "").strip()
    from_name = (request.data.get("from_name") or "").strip()

    phrase_used = phrase if phrase else (
        "Mamá, eres mi refugio y mi cielo. Gracias por tanto amor. ¡Feliz Día!"
    )

    prompt_parts = [
        "Diseñar una tarjeta estética (aesthetic) llena de amor para el Día de la Madre.",
        "",
        "FOTO ADJUNTA:",
        "- Usar la foto adjunta EXACTAMENTE IGUAL, sin modificarla, sin cambiar colores, "
        "sin aplicar filtros directamente sobre la foto.",
        "- Solo agregar un marco decorativo cálido y elegante alrededor de la foto.",
        "- La foto debe ser el elemento central y protagónico de la tarjeta.",
        "",
        "ESTILO VISUAL:",
        "- Paleta cálida y maternal: rosa pastel, durazno, dorado suave, crema, marfil.",
        "- ANALIZA los colores predominantes de la foto para armonizar — si la foto tiene tonos cálidos, "
        "intensifica los rosados/dorados; si es neutra, usa la paleta base maternal.",
        "- Estilo aesthetic, romántico y atemporal — como una carta de amor a mamá.",
        "- Luz cálida, textura suave tipo papel acuarela, sombras delicadas.",
        "- Elementos decorativos: rosas suaves, peonías, tulipanes, hojas verde sage, "
        "destellos dorados, lazos delicados, pequeños corazones discretos.",
        "- Composición limpia, romántica y armoniosa.",
        "- Formato cuadrado estilo post de Instagram (1:1), alta calidad.",
        "- Diseño que transmita ternura, gratitud y amor profundo.",
        "",
        "TEXTO A INCLUIR EN LA TARJETA:",
        '- Texto principal con tipografía script elegante: "Feliz Día Mamá"',
        f'- Frase de amor (destácala con tipografía bonita y legible): "{phrase_used}"',
    ]

    if mother_name:
        prompt_parts.append(
            f'- Nombre de la mamá homenajeada (tipografía elegante, zona visible): "{mother_name}"'
        )

    if from_name:
        prompt_parts.append(
            f'- Mensaje de remitente (texto más pequeño, parte inferior): "Con amor, {from_name}"'
        )

    prompt_parts += [
        '- Marca sutil "Frostbyte" en una esquina (pequeño, discreto, no invasivo).',
        "",
        "IMPORTANTE: El resultado debe verse como una carta de amor visual, cálida, "
        "elegante y digna de regalar. La foto NO debe ser alterada ni filtrada, solo "
        "enmarcada con cariño dentro del diseño.",
    ]

    full_prompt = "\n".join(prompt_parts)

    try:
        image_bytes = image_file.read()
        image_mime = image_file.content_type

        client = genai.Client(api_key=api_key)

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type=image_mime),
                    types.Part.from_text(text=full_prompt),
                ],
            )
        ]

        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
            image_config=types.ImageConfig(
                aspect_ratio="1:1",
                image_size="1K",
            ),
            response_modalities=["IMAGE"],
        )

        generated_image_data = None
        generated_mime_type = "image/png"

        for chunk in client.models.generate_content_stream(
            model="gemini-3.1-flash-image-preview",
            contents=contents,
            config=generate_content_config,
        ):
            if chunk.parts:
                for part in chunk.parts:
                    if part.inline_data and part.inline_data.data:
                        generated_image_data = part.inline_data.data
                        generated_mime_type = part.inline_data.mime_type or "image/png"
                        break
            if generated_image_data:
                break

        if not generated_image_data:
            return Response(
                {"error": "Gemini no devolvió una imagen. Intenta con otra foto."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if isinstance(generated_image_data, (bytes, bytearray)):
            image_base64 = base64.b64encode(generated_image_data).decode("utf-8")
        else:
            image_base64 = generated_image_data

        return Response(
            {
                "image_base64": image_base64,
                "mime_type": generated_mime_type,
                "phrase_used": phrase_used,
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        return Response(
            {"error": f"Error al generar imagen: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ─── Dedicatorias Día de la Madre ─────────────────────────────────────


@api_view(["GET"])
@permission_classes([AllowAny])
def list_mother_dedications(request):
    """Lista las dedicatorias aprobadas para el muro de amor a mamá."""
    dedications = MotherDedication.objects.filter(is_approved=True)[:100]
    serializer = MotherDedicationListSerializer(dedications, many=True)
    return Response(
        {"count": len(serializer.data), "results": serializer.data},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([MotherDedicationCreateThrottle])
def create_mother_dedication(request):
    """Crea una nueva dedicatoria para el muro del Día de la Madre."""
    serializer = MotherDedicationCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
    )
    serializer.save(ip_address=ip)

    return Response(
        {"message": "Tu dedicatoria a mamá fue enviada con amor."},
        status=status.HTTP_201_CREATED,
    )
