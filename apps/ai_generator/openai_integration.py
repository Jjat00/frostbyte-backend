import os
import base64
from openai import OpenAI
from PIL import Image
import io
from typing import Optional, Dict
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class OpenAIImageGenerator:
    """Generador de imágenes profesionales usando OpenAI GPT Image"""

    # Pricing (actualizado a 2026)
    PRICING = {
        'gpt-image-1.5': 0.040,  # $0.04 por imagen
        'gpt-image-1': 0.020,
        'gpt-image-1-mini': 0.010,
    }

    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = 'gpt-image-1.5'  # Modelo más reciente

    def build_prompt(
        self,
        user_prompt: str,
        has_reference: bool = False,
        context: str = "restaurant menu"
    ) -> str:
        """
        Construye el prompt completo para OpenAI

        Args:
            user_prompt: Instrucciones del usuario
            has_reference: Si se proporcionó imagen de referencia
            context: Contexto de uso (menu, social media, etc.)

        Returns:
            Prompt completo optimizado
        """
        base_prompt = f"""Create a professional, high-quality photograph suitable for a {context}.

The image should be:
- Well-lit with professional lighting
- Sharp and in focus
- Appetizing and visually appealing
- Clean and attractive composition
- High resolution and print-ready
- Commercial-grade quality suitable for menus and marketing
"""

        if has_reference:
            base_prompt += "\nMatch the visual style and aesthetic of the reference image provided."

        if user_prompt:
            base_prompt += f"\n\nAdditional instructions: {user_prompt}"

        base_prompt += "\n\nEnsure the final image is suitable for commercial use in restaurant menus and marketing materials."

        return base_prompt

    def generate_professional_menu_image(
        self,
        original_image_path: str,
        reference_image_path: Optional[str] = None,
        user_prompt: str = "",
        transparent_background: bool = True,
        size: str = "1024x1024",
        quality: str = "hd"
    ) -> Dict:
        """
        Genera una imagen profesional de menú usando OpenAI

        Args:
            original_image_path: Path a la imagen original del producto
            reference_image_path: Path a la imagen de referencia (opcional)
            user_prompt: Instrucciones adicionales del usuario
            transparent_background: Si generar con fondo transparente
            size: Tamaño de la imagen (1024x1024, 1792x1024, 1024x1792)
            quality: Calidad (standard, hd)

        Returns:
            Dict con:
                - image_data: bytes de la imagen generada
                - full_prompt: prompt completo usado
                - cost_usd: costo de la generación
                - metadata: metadata adicional
        """
        try:
            # Construir prompt
            full_prompt = self.build_prompt(
                user_prompt=user_prompt,
                has_reference=reference_image_path is not None
            )

            logger.info(f"Generating image with model {self.model}")
            logger.debug(f"Prompt: {full_prompt[:100]}...")

            # Si hay imagen de referencia, analizar su estilo
            style_description = None
            if reference_image_path:
                style_description = self._analyze_reference_style(
                    reference_image_path)
                full_prompt += f"\n\nVisual style reference: {style_description}"

            # Nota: OpenAI GPT Image 1.5 actualmente no soporta edición directa de imágenes
            # Usamos generate con prompt descriptivo basado en la imagen original

            # Leer y analizar imagen original
            original_description = self._analyze_image(original_image_path)
            full_prompt = f"Based on this image: {original_description}\n\n{full_prompt}"

            # Para modelos GPT Image (gpt-image-1.5, etc.) no se usa response_format;
            # siempre devuelven base64. quality debe ser high/medium/low (no hd/standard).
            quality_param = "high" if quality == "hd" else (
                "medium" if quality == "standard" else quality)
            background_param = "transparent" if transparent_background else "auto"

            response = self.client.images.generate(
                model=self.model,
                prompt=full_prompt,
                size=size,
                quality=quality_param,
                n=1,
                background=background_param,
            )

            # Obtener imagen generada
            image_b64 = response.data[0].b64_json
            image_data = base64.b64decode(image_b64)

            # Post-procesamiento para fondo transparente si es necesario
            if transparent_background:
                image_data = self._ensure_transparent_background(image_data)

            # Calcular costo
            cost = self.PRICING.get(self.model, 0.04)

            return {
                'image_data': image_data,
                'full_prompt': full_prompt,
                'cost_usd': cost,
                'metadata': {
                    'model': self.model,
                    'size': size,
                    'quality': quality,
                    'transparent': transparent_background,
                    'revised_prompt': getattr(response.data[0], 'revised_prompt', None)
                }
            }

        except Exception as e:
            logger.error(f"OpenAI generation failed: {e}")
            raise Exception(f"Error al generar imagen: {str(e)}")

    def _analyze_image(self, image_path: str) -> str:
        """
        Analiza la imagen usando GPT-4 Vision para generar descripción
        """
        try:
            # Leer imagen y convertir a base64
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            # Determinar tipo de imagen
            img_ext = image_path.lower().split('.')[-1]
            mime_type = f"image/{img_ext}" if img_ext in [
                'jpg', 'jpeg', 'png', 'webp'] else "image/jpeg"

            response = self.client.chat.completions.create(
                model="gpt-4o",  # Modelo con visión
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Describe this food/beverage product in detail for image generation purposes. Focus on: what it is, colors, presentation, ingredients visible, container/glass type. Be specific and concise (2-3 sentences)."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_data}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=150
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.warning(f"Failed to analyze image: {e}")
            return "a food/beverage product"

    def _analyze_reference_style(self, image_path: str) -> str:
        """
        Analiza la imagen de referencia usando GPT-4 Vision
        para extraer descripción del estilo
        """
        try:
            # Leer imagen y convertir a base64
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            # Determinar tipo de imagen
            img_ext = image_path.lower().split('.')[-1]
            mime_type = f"image/{img_ext}" if img_ext in [
                'jpg', 'jpeg', 'png', 'webp'] else "image/jpeg"

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Describe the visual style, lighting, composition, and aesthetic of this image in 2-3 sentences. Focus on aspects that can be replicated in food photography."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_data}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=150
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.warning(f"Failed to analyze reference image: {e}")
            return "professional food photography style"

    def _ensure_transparent_background(self, image_data: bytes) -> bytes:
        """
        Asegura que el fondo sea transparente.
        Aplica procesamiento para remover fondo blanco/claro.
        """
        try:
            # Abrir imagen
            img = Image.open(io.BytesIO(image_data))

            # Convertir a RGBA si no lo está
            if img.mode != 'RGBA':
                img = img.convert('RGBA')

            # Obtener datos de la imagen
            data = img.getdata()
            new_data = []

            # Remover píxeles blancos/casi blancos (común cuando OpenAI falla)
            for item in data:
                # Si el píxel es casi blanco, hacerlo transparente
                # Threshold: RGB > 240 (muy blanco)
                if item[0] > 240 and item[1] > 240 and item[2] > 240:
                    new_data.append((255, 255, 255, 0))  # Transparente
                else:
                    new_data.append(item)

            img.putdata(new_data)

            # Guardar como PNG
            output = io.BytesIO()
            img.save(output, format='PNG', optimize=True)
            return output.getvalue()

        except Exception as e:
            logger.error(f"Background removal failed: {e}")
            # Retornar imagen original si falla
            return image_data

    def _has_transparency(self, img: Image.Image) -> bool:
        """Verifica si la imagen tiene píxeles transparentes"""
        if img.mode != 'RGBA':
            return False

        alpha = img.split()[3]  # Canal alpha
        # Si hay algún píxel no totalmente opaco
        return alpha.getextrema()[0] < 255
