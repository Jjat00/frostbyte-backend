from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from openai import OpenAI
from django.utils import timezone
import os


@api_view(['GET'])
@permission_classes([AllowAny])
def get_motivational_phrase(request):
    """
    Genera una frase motivacional o dato curioso histórico usando OpenAI basada en la fecha actual
    """
    try:
        # Obtener la API key desde las variables de entorno
        api_key = os.getenv('OPENAI_API_KEY')

        if not api_key:
            return Response(
                {'error': 'OpenAI API key no configurada'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Inicializar el cliente de OpenAI
        client = OpenAI(api_key=api_key)

        # Obtener la fecha actual en la zona horaria configurada (America/Bogota)
        fecha_actual = timezone.localtime(timezone.now())
        dia_semana = fecha_actual.strftime('%A')
        dia_mes = fecha_actual.day
        mes = fecha_actual.strftime('%B')
        anio = fecha_actual.year

        print("😕😕", dia_semana, dia_mes, mes, anio)
        # Crear el prompt para OpenAI
        prompt = f"""Hoy es {dia_semana} {dia_mes} de {mes} de {anio}.

Genera una frase motivacional basada en la fecha actual elige una de las siguientes opciones aleatoriamente:

OPCIÓN 1 - Frase motivacional:
Una frase inspiradora relacionada con el día de la semana, el inicio/mitad/fin de mes
Debe ser positiva, breve y motivadora, debe ser una frase de autor conocido.

OPCIÓN 2 - Dato curioso histórico:
Busca un evento importante que haya ocurrido un {dia_mes} de {mes} en años anteriores



REGLAS:
- Máximo 20 palabras
- NO usar emojis
- Tono casual pero profesional
- Solo devuelve la frase o dato, sin comillas ni explicaciones adicionales
- Asegúrate de que el dato histórico sea verídico
- la respuesta debe estar en español
"""

        # Llamar a la API de OpenAI
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                # {"role": "system", "content": "Eres un experto en dar datos curiosos e interesantes basados en la fecha actual y también un creador de frases motivacionales inspiradoras. Tus datos históricos son siempre precisos y verificables."},
                {"role": "system", "content": "Eres un experto en motivar a las personas a ser mejores y a cumplir sus objetivos."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=60,
            temperature=0.9
        )

        # Extraer la frase generada
        frase = response.choices[0].message.content.strip()

        return Response({
            'phrase': frase,
            'date': fecha_actual.strftime('%Y-%m-%d'),
            'day': dia_semana
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {'error': f'Error al generar frase: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
