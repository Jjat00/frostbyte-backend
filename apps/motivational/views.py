from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from apps.products.models import Product
from .models import RecommenderLog
import os
import json
import random

# Prefijo de la clave de caché de la frase del día.
# Se versiona (sufijo) para invalidar el caché del día al cambiar el tipo de
# frase: al desplegar con un prefijo nuevo, la frase de hoy se regenera sola.
PHRASE_CACHE_PREFIX = "motivational_phrase_mundial"


def _seconds_until_local_midnight():
    """Segundos que faltan hasta la medianoche local (TTL de la frase del día)."""
    now = timezone.localtime(timezone.now())
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(60, int((tomorrow - now).total_seconds()))


# Datos curados, reales y verificables de la historia de los Mundiales. La IA
# NO inventa el hecho: solo redacta con estilo uno de estos y le suma el CTA al
# producto. Se rota a diario (uno por día). Mantener cada dato autocontenido y
# correcto; al agregar uno nuevo, verificarlo.
DATOS_MUNDIAL = [
    "Miroslav Klose es el máximo goleador en la historia de los Mundiales, con 16 goles entre 2002 y 2014.",
    "Just Fontaine marcó 13 goles en un solo Mundial, el de Suecia 1958, y nadie lo ha superado.",
    "Pelé es el único futbolista que ha ganado tres Copas del Mundo: 1958, 1962 y 1970.",
    "Con apenas 17 años, Pelé marcó en la final del Mundial de 1958 y es el goleador más joven en una final.",
    "En el Maracanazo de 1950, Uruguay venció a Brasil en el partido decisivo dentro del Maracaná, ante casi 200.000 personas.",
    "El primer Mundial se jugó en Uruguay en 1930 y el anfitrión se quedó con el título al ganar 4-2 a Argentina en la final.",
    "Brasil es la única selección que ha jugado todos los Mundiales y la más ganadora, con cinco títulos.",
    "El gol más rápido en la historia de los Mundiales lo marcó el turco Hakan Sükür a los 11 segundos, ante Corea del Sur en 2002.",
    "En 1986, Maradona le marcó a Inglaterra la 'Mano de Dios' y, minutos después, el 'Gol del Siglo' tras driblar a media defensa.",
    "Zinedine Zidane fue expulsado en la final de 2006 por un cabezazo a Materazzi y aun así fue elegido mejor jugador del torneo.",
    "La primera final de un Mundial definida por penales fue en 1994: Brasil venció a Italia cuando Roberto Baggio falló el suyo.",
    "En 2022, Lionel Messi llevó a Argentina al título en Catar y fue elegido el mejor jugador del torneo.",
    "En la final de 2018, Kylian Mbappé marcó con solo 19 años, el primer adolescente en anotar en una final desde Pelé.",
    "El Mundial de 2002 fue el primero jugado en Asia y el primero organizado por dos países a la vez: Corea del Sur y Japón.",
    "En 2002, Corea del Sur llegó hasta las semifinales, la mejor actuación de una selección asiática en la historia.",
    "Camerún, con Roger Milla y sus bailes en el banderín, fue en 1990 el primer equipo africano en llegar a cuartos de final.",
    "La 'Naranja Mecánica' de Holanda deslumbró en 1974 con su 'fútbol total', pero cayó en la final ante Alemania.",
    "Geoff Hurst es el único jugador que ha marcado tres goles en una final de Mundial: lo hizo en 1966, el único título de Inglaterra.",
    "En el 'Milagro de Berna' de 1954, Alemania Occidental remontó y venció en la final a la poderosa Hungría de Puskás.",
    "Lothar Matthäus es el futbolista con más partidos disputados en Mundiales: 25, en cinco ediciones distintas.",
    "El partido con más goles en un Mundial fue el Austria 7-5 Suiza de 1954: doce goles en un solo encuentro.",
    "Hungría le anotó diez goles a El Salvador (10-1) en España 1982, la mayor cantidad de goles de un equipo en un partido de Mundial.",
    "El primer Mundial con mascota oficial fue Inglaterra 1966, con 'World Cup Willie', un leoncito con la bandera británica.",
    "Garrincha fue la gran figura del título de Brasil en 1962, brillando incluso con Pelé lesionado desde el inicio del torneo.",
    "Antonio Carbajal, arquero mexicano, fue el primer futbolista en disputar cinco Copas del Mundo, entre 1950 y 1966.",
    "Italia y Alemania comparten el segundo lugar histórico de títulos mundialistas, con cuatro cada una.",
    "En 1930, en el primer Mundial, no hubo eliminatorias previas: las selecciones participaron por invitación.",
    "Francia ganó su primer Mundial como anfitriona en 1998 y repitió título veinte años después, en Rusia 2018.",
    "El brasileño Ronaldo Nazário fue el máximo goleador histórico de los Mundiales con 15 goles, hasta que Klose lo superó en 2014.",
    "En 1970 Brasil levantó su tercer título y se quedó en propiedad con el trofeo Jules Rimet, con un equipo legendario.",
]


def _generate_phrase():
    """
    Genera la frase del día: toma un dato curado y verificado del Mundial
    (rotado por día) y deja que OpenAI lo redacte con estilo colombiano y le
    sume el CTA a un producto activo. La IA no inventa el hecho, solo lo cuenta.
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

    # Productos activos, barajados con una semilla fija del día: así el CTA no
    # cae siempre en el mismo producto, pero se mantiene estable durante la
    # jornada (la frase se cachea por día).
    active_products = list(
        Product.objects.filter(is_active=True, category__is_active=True)
        .select_related("category")
    )
    random.Random(fecha_actual.timetuple().tm_yday).shuffle(active_products)

    products_by_category = {}
    for product in active_products:
        products_by_category.setdefault(product.category.name, []).append(product.name)

    products_formatted = "\n".join(
        f"- {category}: {', '.join(products)}"
        for category, products in products_by_category.items()
    )

    # Dato del día (determinístico por día del año): la IA no inventa el hecho,
    # solo redacta el que le pasamos. Rota a diario y, como se cachea por día,
    # basta con que cambie cada jornada.
    dato = DATOS_MUNDIAL[fecha_actual.timetuple().tm_yday % len(DATOS_MUNDIAL)]

    prompt = f"""Hoy es {dia_semana} {dia_mes} de {mes} de {anio}.

Tienes este dato 100% verificado sobre la historia de los Mundiales (NO lo cambies ni agregues hechos, nombres, años o cifras que no estén aquí):
«{dato}»

Tu tarea: contar ESE dato de forma fresca y divertida, en español colombiano juvenil, y enlazarlo de manera natural con una invitación a pasar por Frostbyte —bar de bebidas heladas en Cumbal, Nariño— a disfrutar UNO de estos productos activos, mencionándolo por su nombre:
{products_formatted}

Reglas:
- No inventes ni agregues datos extra: respeta el hecho tal como te lo di.
- Una sola frase fluida que una el dato con la invitación al producto.
- Varía el arranque (no empieces con "Un dato curioso" ni "¿Sabías que"); puedes arrancar por el protagonista, el año o la hazaña.
- Máximo 40 palabras. Sin comillas ni emojis."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Eres un comentarista deportivo colombiano, divertido y cercano. Te entregan un dato REAL del Mundial y tu trabajo es contarlo con gracia y enlazarlo con una invitación a Frostbyte. Nunca inventas ni alteras datos: respetas el hecho tal como te lo dan, solo le pones estilo."},
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
    Devuelve la frase motivacional del día (dato curioso o frase, basada en la
    fecha y los productos activos). Se genera con OpenAI UNA sola vez por día y
    se cachea: el resto de visitas del día reciben la misma frase sin volver a
    llamar a la IA. La clave incluye la fecha local, así que al cambiar el día
    se regenera automáticamente.
    """
    fecha = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    cache_key = f"{PHRASE_CACHE_PREFIX}:{fecha}"

    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached, status=status.HTTP_200_OK)

    try:
        result = _generate_phrase()

        if result is None:
            return Response(
                {"error": "OpenAI API key no configurada"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Solo cacheamos resultados válidos (un fallo de IA no debe quedar fijado).
        cache.set(cache_key, result, timeout=_seconds_until_local_midnight())
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
