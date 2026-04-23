import base64
import os
from openai import OpenAI
from typing import Optional, Dict
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
BRAND_BACKGROUND_PATH = os.path.join(_MODULE_DIR, 'assets', 'fondo_frostbyte.png')


class OpenAIImageGenerator:
    """Generador de imágenes profesionales usando los modelos GPT Image de OpenAI"""

    DEFAULT_MODEL = 'gpt-image-1.5'
    SUPPORTED_MODELS = {'gpt-image-1.5', 'gpt-image-2'}
    # Modelos que aceptan background="transparent" en images.edit.
    # gpt-image-2 rechaza ese valor con HTTP 400.
    TRANSPARENT_SUPPORTED_MODELS = {'gpt-image-1.5'}

    def __init__(self, model: Optional[str] = None):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resolved = model or self.DEFAULT_MODEL
        if resolved not in self.SUPPORTED_MODELS:
            logger.warning(
                f"Modelo OpenAI '{resolved}' no reconocido; usando {self.DEFAULT_MODEL}"
            )
            resolved = self.DEFAULT_MODEL
        self.model = resolved

    def build_prompt(
        self,
        user_prompt: str,
        has_reference: bool,
        transparent_background: bool,
        has_brand_background: bool = False,
    ) -> str:
        """Construye prompt para edición profesional de imágenes de menú"""

        if has_reference:
            # Prompt enfocado en aplicar el estilo de la referencia SIN cambiar colores del producto
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
            # Prompt sin referencia - solo mejorar
            prompt = """Transform this product photo into a premium, professional menu-quality image.

Enhance:
- Lighting to make product look appetizing
- Textures and details
- Overall professional quality

Preserve:
- Product identity, labels, text, logos exactly as shown

"""

        if transparent_background:
            prompt += "BACKGROUND: Transparent/removed - clean edges around product.\n"
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
        """Genera imagen profesional usando OpenAI images.edit"""

        supports_transparent = self.model in self.TRANSPARENT_SUPPORTED_MODELS
        effective_transparent = transparent_background and supports_transparent
        if transparent_background and not supports_transparent:
            logger.info(
                f"Modelo {self.model} no soporta background=transparent; "
                "se generará con fondo normal."
            )

        # Usar el fondo de marca Frostbyte cuando no se pide transparencia
        use_brand_background = (
            not effective_transparent
            and os.path.exists(BRAND_BACKGROUND_PATH)
        )

        full_prompt = self.build_prompt(
            user_prompt=user_prompt,
            has_reference=reference_image_path is not None,
            transparent_background=effective_transparent,
            has_brand_background=use_brand_background,
        )

        logger.info(f"Generating image with {self.model}")

        files_to_close = []
        try:
            f_orig = open(original_image_path, "rb")
            files_to_close.append(f_orig)
            image_list = [f_orig]

            if reference_image_path:
                f_ref = open(reference_image_path, "rb")
                files_to_close.append(f_ref)
                image_list.append(f_ref)

            if use_brand_background:
                f_bg = open(BRAND_BACKGROUND_PATH, "rb")
                files_to_close.append(f_bg)
                image_list.append(f_bg)

            edit_kwargs = {
                "model": self.model,
                "image": image_list,
                "prompt": full_prompt,
                "size": "1024x1024",
                "quality": "high",
                "n": 1,
            }
            if supports_transparent:
                edit_kwargs["background"] = (
                    "transparent" if effective_transparent else "auto"
                )
            response = self.client.images.edit(**edit_kwargs)
        finally:
            for f in files_to_close:
                f.close()

        image_b64 = response.data[0].b64_json
        image_data = base64.b64decode(image_b64)

        # El API de OpenAI ya maneja transparencia con background="transparent"
        # NO aplicamos post-procesado que borra píxeles blancos (dañaba texto blanco)

        return {'image_data': image_data}

