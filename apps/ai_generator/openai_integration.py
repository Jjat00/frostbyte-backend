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

    def build_edit_prompt(
        self,
        user_prompt: str,
        has_reference: bool = False,
        transparent_background: bool = False,
        context: str = "restaurant menu"
    ) -> str:
        """
        Prompt para images.edit: producto de la imagen original mejorado;
        la referencia aporta estilo y efectos llamativos (aros de luz, salpicadura, etc.).
        """
        system_instructions = f"""Actúa como un fotógrafo profesional de alimentos y bebidas de clase mundial.
Tu objetivo es mejorar la foto del producto para que se vea de alta gama, digna de menú o campaña premium.

REGLAS:
1. La PRIMERA imagen es la IMAGEN FUENTE (el producto). La imagen generada debe mostrar ÚNICAMENTE ese producto mejorado. Mismo plato/bebida, mismo producto; no cambies qué es.

2. CONSERVA LOS COLORES DEL PRODUCTO ORIGINAL. El producto de la primera imagen debe mantener TODOS sus colores: color del borde/rim (ej. verde), color de la bebida, colores de la pajilla, colores de los dulces o ingredientes visibles, colores de la etiqueta y del empaque. No reemplaces la paleta del producto por los colores de la referencia. La referencia aporta efectos e iluminación, NO un nuevo esquema de color para el producto en sí.

3. La SEGUNDA imagen (si hay) es la REFERENCIA DE ESTILO. De ella aplica al producto de la primera imagen solo los EFECTOS que hacen sobresalir la referencia:
   - Efectos de luz: aros de luz (anillos luminosos, halos, rings), neones, resplandores alrededor del producto (pueden usar tonos que complementen, pero el producto en sí conserva sus colores).
   - Dinamismo: líquido estrellando/salpicando (splash), gotas, partículas de luz, estelas.
   - Iluminación dramática: rim light, reflejos, profundidad de campo, ambiente y mood.
   NO copies de la referencia: fondo, otros objetos, props, ni la paleta de color del producto. En la salida solo está el producto de la primera imagen CON sus colores originales y CON los efectos visuales de la referencia.

4. Mejora la textura del producto para que se vea apetitoso y profesional (efecto "food porn").

5. Salida fotorrealista, muy alta resolución, apta para {context} y marketing.
"""
        if transparent_background:
            system_instructions += """
6. FONDO: La imagen debe quedar SIN FONDO (fondo transparente). El producto (con sus colores originales) y los efectos visuales (aros de luz, salpicadura, partículas, neones) deben mantenerse sobre transparencia; solo se elimina el fondo de escenario.
"""
        else:
            system_instructions += "\n6. Fondo limpio y profesional.\n"

        user_part = user_prompt.strip(
        ) if user_prompt else "Haz que se vea increíble y profesional."
        return system_instructions + f"\nInstrucciones adicionales del usuario: {user_part}"

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
        Genera una imagen profesional de menú usando OpenAI images.edit.
        Envía la imagen original (y la de referencia) para que el modelo la edite
        y mantenga la identidad del producto aplicando solo el estilo.
        """
        try:
            full_prompt = self.build_edit_prompt(
                user_prompt=user_prompt,
                has_reference=reference_image_path is not None,
                transparent_background=transparent_background,
            )

            logger.info(
                f"Generating image with model {self.model} (edit mode)")
            logger.debug(f"Prompt: {full_prompt[:150]}...")

            quality_param = "high" if quality == "hd" else (
                "medium" if quality == "standard" else quality)
            background_param = "transparent" if transparent_background else "auto"

            # images.edit recibe la(s) imagen(es) a editar: fuente + opcional referencia
            files_to_close = []
            try:
                f_orig = open(original_image_path, "rb")
                files_to_close.append(f_orig)
                image_list = [f_orig]
                if reference_image_path:
                    f_ref = open(reference_image_path, "rb")
                    files_to_close.append(f_ref)
                    image_list = [f_orig, f_ref]

                response = self.client.images.edit(
                    model=self.model,
                    image=image_list,
                    prompt=full_prompt,
                    size=size,
                    quality=quality_param,
                    n=1,
                    background=background_param,
                )
            finally:
                for f in files_to_close:
                    f.close()

            image_b64 = response.data[0].b64_json
            image_data = base64.b64decode(image_b64)

            if transparent_background:
                image_data = self._ensure_transparent_background(image_data)

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
