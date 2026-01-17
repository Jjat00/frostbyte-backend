from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from django.utils import timezone
from apps.products.models import Product
import os


@api_view(["GET"])
@permission_classes([AllowAny])
def get_motivational_phrase(request):
    """
    Genera una frase motivacional o dato curioso histórico usando OpenAI basada en la fecha actual
    """
    try:
        # Obtener la API key desde las variables de entorno
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            return Response(
                {"error": "OpenAI API key no configurada"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Inicializar el cliente de OpenAI
        client = OpenAI(api_key=api_key)

        # Obtener la fecha actual en la zona horaria configurada (America/Bogota)
        fecha_actual = timezone.localtime(timezone.now())
        dia_semana = fecha_actual.strftime("%A")
        dia_mes = fecha_actual.day
        mes = fecha_actual.strftime("%B")
        anio = fecha_actual.year

        # Obtener productos activos
        active_products = Product.objects.filter(is_active=True).values_list(
            "name", flat=True
        )
        products_list = list(active_products)

        print("😕😕", dia_semana, dia_mes, mes, anio)
        # Crear el prompt para OpenAI
        prompt = f"""Hoy es {dia_semana} {dia_mes} de {mes} de {anio}.
        
        Productos activos de Frostbyte: {", ".join(products_list)}
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
#         prompt = f"""Hoy es {dia_semana} {dia_mes} de {mes} de {anio}.

# DEBES elegir ALEATORIAMENTE una de las siguientes dos opciones (50% de probabilidad cada una):

# OPCIÓN 1 - Frase motivacional:
# Una frase inspiradora relacionada con el día de la semana, el inicio/mitad/fin de mes
# Debe ser positiva, breve y motivadora.

# OPCIÓN 2 - Dato curioso histórico:
# Busca un evento importante, interesante o curioso que haya ocurrido un {dia_mes} de {mes} en años anteriores
# Debe ser un dato histórico real y verificable.

# Productos activos de Frostbyte: {", ".join(products_list)}

# REGLAS PARA AMBAS OPCIONES:
# - El contenido (frase motivacional O dato curioso) y la invitación al consumo deben ser UNA SOLA FRASE combinada
# - El contenido debe fluir naturalmente hacia la invitación a consumir uno de los productos activos de Frostbyte (elige uno aleatoriamente)
# - NO incluir el autor de la frase, solo el mensaje
# - NO usar emojis
# - TONO JUVENIL COLOMBIANO: casual, relajado, cercano, amigable, con expresiones colombianas naturales
# - Puedes usar expresiones como "parce", "chévere", "bacano", "vamos", etc. de forma natural
# - Máximo 25 palabras en total
# - Solo devuelve la frase combinada, sin comillas ni explicaciones adicionales
# - La respuesta debe estar en español colombiano
# - El enfoque principal debe ser la invitación al consumo, integrada de forma natural y juvenil con el contenido

# Ejemplos de estilo juvenil colombiano:
# - Frase motivacional: "Parce, los sueños se cumplen cuando te atreves a empezar, así que dale, pruébate un mojito hoy."
# - Dato curioso: "Un día como hoy pasó algo chévere en la historia, celebra esa buena vibra con un mojito bien frío."
# """

        # Llamar a la API de OpenAI
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un experto Colombiano en dar datos curiosos e interesantes basados en la fecha actual y también un creador de frases motivacionales inspiradoras. Tus datos históricos son siempre precisos y verificables."},
                # {
                #     "role": "system",
                #     "content": "Eres un joven colombiano experto en crear frases motivacionales y datos curiosos históricos con un tono juvenil, casual y cercano. Tu especialidad es combinar contenido inspirador (ya sea motivacional o histórico) con invitaciones al consumo de forma natural y fluida en una sola frase. Hablas con expresiones colombianas naturales, eres amigable y relajado. Debes alternar aleatoriamente entre frases motivacionales y datos curiosos históricos. La invitación al consumo es el enfoque principal, pero presentada de manera juvenil, casual e integrada con el contenido.",
                # },
                {"role": "user", "content": prompt},
            ],
            max_tokens=80,
            temperature=0.9,
        )

        # Extraer la frase generada
        frase = response.choices[0].message.content.strip()

        print("🤖🤖 FRASE GENERADA: ", frase)

        return Response(
            {
                "phrase": frase,
                "date": fecha_actual.strftime("%Y-%m-%d"),
                "day": dia_semana,
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        return Response(
            {"error": f"Error al generar frase: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
