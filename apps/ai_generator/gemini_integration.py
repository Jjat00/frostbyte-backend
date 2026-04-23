"""
Gemini Image Generator for Frostbyte AI Generator app.

Uses Google Gemini's image generation models (gemini-3-pro-image-preview)
as an alternative to OpenAI for generating professional menu images.
"""

from google import genai
from google.genai import types
from typing import Optional, Dict
from django.conf import settings
from PIL import Image
from io import BytesIO
import logging
import os

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
BRAND_BACKGROUND_PATH = os.path.join(_MODULE_DIR, 'assets', 'fondo_frostbyte.png')


class GeminiImageGenerator:
    """Generador de imagenes profesionales usando Google Gemini"""

    MODEL_CHOICES = {
        'gemini-3-pro-image-preview': 'gemini-3-pro-image-preview',
        'gemini-3.1-flash-image-preview': 'gemini-3.1-flash-image-preview',
    }

    def __init__(self, model: str = 'gemini-3-pro-image-preview'):
        api_key = getattr(settings, 'GEMINI_API_KEY', '')
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY no esta configurado. "
                "Agrega GEMINI_API_KEY en el archivo .env"
            )
        self.client = genai.Client(api_key=api_key)
        self.model = self.MODEL_CHOICES.get(model, model)

    def build_prompt(
        self,
        user_prompt: str,
        has_reference: bool,
        transparent_background: bool,
        has_brand_background: bool = False,
    ) -> str:
        """Construye prompt para edicion profesional de imagenes de menu"""

        if has_reference:
            prompt = """Transform the FIRST image (product) using the visual STYLE of the SECOND image (reference).

APPLY from reference image (STYLE ONLY):
- Lighting effects (glows, neon rings, highlights)
- Visual atmosphere (sparkles, particles, splashes)
- Dynamic energy and dramatic presentation
- Background effects and mood

STRICTLY PRESERVE from original product (DO NOT CHANGE):
- Original product COLORS (keep green if green, keep red if red, etc.)
- Product shape, labels, text, logos EXACTLY as shown
- All text must remain readable and unchanged

IMPORTANT: Do NOT copy the reference product's colors onto the original product. Only copy the STYLE and EFFECTS.

"""
        else:
            prompt = """Transform this product photo into a premium, professional menu-quality image.

Enhance:
- Lighting to make product look appetizing
- Textures and details
- Overall professional quality

Preserve:
- Product identity, labels, text, logos exactly as shown

"""

        if transparent_background:
            prompt += "BACKGROUND: Use a plain, solid, single-color background (preferably white or light gray). Keep the product cleanly separated from the background with sharp, well-defined edges. No gradients, no textures, no props on the background.\n"
        elif has_brand_background:
            bg_index = "THIRD" if has_reference else "SECOND"
            prompt += (
                f"BACKGROUND: Use the {bg_index} image as the Frostbyte brand "
                "backdrop. Compose this as a professional product photograph "
                "shot with a 50mm lens at f/2.8:\n"
                "- Place the product centered and sharply in focus\n"
                "- Apply a natural depth-of-field blur (bokeh) to the brand "
                "backdrop so the product pops while the backdrop stays "
                "recognizable as Frostbyte\n"
                "- Match the product lighting to the backdrop ambient light "
                "(color temperature, highlights, soft shadows under the "
                "product)\n"
                "- Preserve the backdrop's Frostbyte branding, colors, and "
                "overall composition; do not replace or distort it\n"
                "- Add a subtle contact shadow under the product so it looks "
                "physically placed, not pasted\n"
            )
        else:
            prompt += "BACKGROUND: Professional, elegant, complementing the product.\n"

        if user_prompt:
            prompt += f"\nAdditional instructions: {user_prompt}"

        return prompt

    def generate_professional_menu_image(
        self,
        original_image_path: str,
        reference_image_path: Optional[str] = None,
        user_prompt: str = "",
        transparent_background: bool = True,
    ) -> Dict:
        """Genera imagen profesional usando Gemini generate_content con modalidad IMAGE"""

        # Usar el fondo de marca Frostbyte cuando no se pide transparencia
        use_brand_background = (
            not transparent_background
            and os.path.exists(BRAND_BACKGROUND_PATH)
        )

        full_prompt = self.build_prompt(
            user_prompt=user_prompt,
            has_reference=reference_image_path is not None,
            transparent_background=transparent_background,
            has_brand_background=use_brand_background,
        )

        logger.info(f"Generating image with Gemini model: {self.model}")

        # Build contents: images + prompt
        contents = []

        # Add original image
        with open(original_image_path, "rb") as f:
            original_data = f.read()

        original_mime = self._detect_mime_type(original_data)
        contents.append(types.Part.from_bytes(data=original_data, mime_type=original_mime))

        # Add reference image if provided
        if reference_image_path:
            with open(reference_image_path, "rb") as f:
                reference_data = f.read()
            reference_mime = self._detect_mime_type(reference_data)
            contents.append(types.Part.from_bytes(data=reference_data, mime_type=reference_mime))

        # Add Frostbyte brand background when applicable
        if use_brand_background:
            with open(BRAND_BACKGROUND_PATH, "rb") as f:
                brand_bg_data = f.read()
            brand_bg_mime = self._detect_mime_type(brand_bg_data)
            contents.append(
                types.Part.from_bytes(data=brand_bg_data, mime_type=brand_bg_mime)
            )

        # Add text prompt
        contents.append(full_prompt)

        # Generate with IMAGE modality
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=['TEXT', 'IMAGE'],
                image_config=types.ImageConfig(
                    aspect_ratio="1:1",
                    image_size="2K",
                ),
            ),
        )

        # Extract image from response
        if response.parts:
            for part in response.parts:
                if part.inline_data is not None:
                    # Get raw bytes from inline_data
                    raw_bytes = part.inline_data.data

                    # Convert to PNG using PIL
                    img = Image.open(BytesIO(raw_bytes))
                    output = BytesIO()
                    img.save(output, format='PNG')
                    image_data = output.getvalue()

                    # Remove background if transparent was requested
                    if transparent_background:
                        image_data = self._remove_background(image_data)

                    return {'image_data': image_data}

        raise Exception("Gemini no genero ninguna imagen en la respuesta")

    def _remove_background(self, image_data: bytes) -> bytes:
        """Remueve el fondo de la imagen usando rembg (IA local)."""
        try:
            from rembg import remove
            result = remove(image_data)
            logger.info("Background removed successfully with rembg")
            return result
        except ImportError:
            logger.warning("rembg not installed, skipping background removal")
            return image_data
        except Exception as e:
            logger.warning(f"Background removal failed: {e}")
            return image_data

    def _detect_mime_type(self, image_data: bytes) -> str:
        """Detecta el MIME type de una imagen"""
        try:
            img = Image.open(BytesIO(image_data))
            mime_map = {
                'PNG': 'image/png',
                'JPEG': 'image/jpeg',
                'WEBP': 'image/webp',
                'GIF': 'image/gif',
            }
            return mime_map.get(img.format, 'image/png')
        except Exception:
            return 'image/png'
