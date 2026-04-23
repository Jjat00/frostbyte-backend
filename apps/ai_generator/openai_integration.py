import base64
from openai import OpenAI
from typing import Optional, Dict
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class OpenAIImageGenerator:
    """Generador de imágenes profesionales usando los modelos GPT Image de OpenAI"""

    DEFAULT_MODEL = 'gpt-image-1.5'
    SUPPORTED_MODELS = {'gpt-image-1.5', 'gpt-image-2'}

    def __init__(self, model: Optional[str] = None):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resolved = model or self.DEFAULT_MODEL
        if resolved not in self.SUPPORTED_MODELS:
            logger.warning(
                f"Modelo OpenAI '{resolved}' no reconocido; usando {self.DEFAULT_MODEL}"
            )
            resolved = self.DEFAULT_MODEL
        self.model = resolved

    def build_prompt(self, user_prompt: str, has_reference: bool, transparent_background: bool) -> str:
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

        full_prompt = self.build_prompt(
            user_prompt=user_prompt,
            has_reference=reference_image_path is not None,
            transparent_background=transparent_background,
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
                image_list = [f_orig, f_ref]

            response = self.client.images.edit(
                model=self.model,
                image=image_list,
                prompt=full_prompt,
                size="1024x1024",
                quality="high",
                n=1,
                background="transparent" if transparent_background else "auto",
            )
        finally:
            for f in files_to_close:
                f.close()

        image_b64 = response.data[0].b64_json
        image_data = base64.b64decode(image_b64)

        # El API de OpenAI ya maneja transparencia con background="transparent"
        # NO aplicamos post-procesado que borra píxeles blancos (dañaba texto blanco)

        return {'image_data': image_data}

