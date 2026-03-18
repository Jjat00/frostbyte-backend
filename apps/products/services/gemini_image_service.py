"""
Google Gemini Image Generation Service for Frostbyte Menu Items

This service handles AI-powered image generation for menu products using
Google's Gemini image generation models. It provides the same interface
as OpenAIImageService for seamless provider switching.

Technical Notes (Gemini 2026):
- Model: gemini-3-pro-image-preview (best quality)
- Model: gemini-3.1-flash-image-preview (fast, efficient)
- Supports aspect ratio and image size configuration
- Image generation via generate_content with IMAGE response modality

Author: Frostbyte Team
Version: 1.0.0
"""

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import Optional

from PIL import Image
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION AND ENUMS
# =============================================================================

class GeminiImageModel(Enum):
    """Available Gemini image generation models."""
    GEMINI_3_PRO_IMAGE = "gemini-3-pro-image-preview"          # Best quality
    GEMINI_3_1_FLASH_IMAGE = "gemini-3.1-flash-image-preview"  # Fast, efficient


class GeminiAspectRatio(Enum):
    """Supported aspect ratios for Gemini image generation."""
    SQUARE = "1:1"        # Best for menu items
    LANDSCAPE_4_3 = "4:3"
    LANDSCAPE_16_9 = "16:9"
    LANDSCAPE_3_2 = "3:2"
    PORTRAIT_2_3 = "2:3"
    PORTRAIT_3_4 = "3:4"


class GeminiImageSize(Enum):
    """Output image size options."""
    SMALL = "512"     # 512px
    MEDIUM = "1K"     # ~1024px
    LARGE = "2K"      # ~2048px
    EXTRA_LARGE = "4K"  # ~4096px


@dataclass
class GeminiImageConfig:
    """Configuration for Gemini image generation requests."""
    model: GeminiImageModel = GeminiImageModel.GEMINI_3_PRO_IMAGE
    aspect_ratio: GeminiAspectRatio = GeminiAspectRatio.SQUARE
    image_size: GeminiImageSize = GeminiImageSize.LARGE
    output_format: str = "png"  # png, webp, jpeg

    # Retry configuration
    max_retries: int = 3
    retry_delay_base: float = 1.0
    timeout: float = 120.0

    # Cost optimization
    use_cache: bool = True
    cache_ttl: int = 86400 * 7  # 7 days


@dataclass
class GeminiGenerationResult:
    """Result of a Gemini image generation request."""
    success: bool
    image_data: Optional[bytes] = None
    image_url: Optional[str] = None
    error_message: Optional[str] = None
    model_used: Optional[str] = None
    estimated_cost: Optional[float] = None
    from_cache: bool = False
    generation_time: Optional[float] = None


# =============================================================================
# PRICING ESTIMATES (2026 - Gemini)
# =============================================================================

GEMINI_PRICING_PER_IMAGE = {
    GeminiImageModel.GEMINI_3_PRO_IMAGE: 0.04,
    GeminiImageModel.GEMINI_3_1_FLASH_IMAGE: 0.02,
}


# =============================================================================
# MAIN SERVICE CLASS
# =============================================================================

class GeminiImageService:
    """
    Service for generating menu item images using Google Gemini's image API.

    Features:
    - Multi-image input (original + reference style)
    - Transparent background via prompt engineering
    - Automatic retry with exponential backoff
    - Response caching for cost optimization
    - White element preservation strategies
    - Same result interface as OpenAIImageService
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Gemini Image Service.

        Args:
            api_key: Gemini API key. If not provided, uses GEMINI_API_KEY setting.
        """
        self.api_key = api_key or getattr(settings, 'GEMINI_API_KEY', None)
        if not self.api_key:
            raise ValueError(
                "Gemini API key is required. Set GEMINI_API_KEY in environment "
                "or pass api_key parameter."
            )

        try:
            from google import genai
            from google.genai import types
            self.genai = genai
            self.types = types
            self.client = genai.Client(api_key=self.api_key)
        except ImportError:
            raise ImportError(
                "google-genai package is required. Install with: pip install google-genai"
            )

        self.default_config = GeminiImageConfig()

    # -------------------------------------------------------------------------
    # MAIN GENERATION METHODS
    # -------------------------------------------------------------------------

    def generate_menu_image(
        self,
        product_name: str,
        product_description: str,
        original_image: Optional[bytes] = None,
        reference_image: Optional[bytes] = None,
        style_description: Optional[str] = None,
        additional_instructions: str = "",
        white_elements: Optional[list[str]] = None,
        config: Optional[GeminiImageConfig] = None
    ) -> GeminiGenerationResult:
        """
        Generate a menu item image with optional style reference.

        Args:
            product_name: Name of the product
            product_description: Detailed product description
            original_image: Original product image bytes (for editing/reference)
            reference_image: Style reference image bytes
            style_description: Text description of desired style
            additional_instructions: Extra instructions from user
            white_elements: List of white ingredients to preserve
            config: Generation configuration

        Returns:
            GeminiGenerationResult with image data or error information
        """
        config = config or self.default_config
        start_time = time.time()

        # Build the prompt
        from .openai_image_service import PromptTemplates
        prompt = PromptTemplates.build_menu_prompt(
            product_name=product_name,
            product_description=product_description,
            style_description=style_description or "Professional food photography style",
            additional_instructions=additional_instructions,
            white_elements=white_elements
        )

        # Check cache first
        if config.use_cache:
            cache_key = self._generate_cache_key(prompt, config)
            cached_result = cache.get(cache_key)
            if cached_result:
                logger.info(f"Cache hit for product: {product_name}")
                return GeminiGenerationResult(
                    success=True,
                    image_data=cached_result,
                    from_cache=True,
                    generation_time=time.time() - start_time
                )

        # Generate the image
        try:
            result = self._generate_image(
                prompt=prompt,
                original_image=original_image,
                reference_image=reference_image,
                config=config
            )

            # Cache successful results
            if result.success and result.image_data and config.use_cache:
                cache.set(cache_key, result.image_data, config.cache_ttl)

            result.generation_time = time.time() - start_time
            return result

        except Exception as e:
            logger.exception(f"Unexpected error generating image for {product_name}")
            return GeminiGenerationResult(
                success=False,
                error_message=f"Error inesperado: {str(e)}",
                generation_time=time.time() - start_time
            )

    def generate_preview(
        self,
        product_name: str,
        brief_description: str,
        category_slug: str,
        config: Optional[GeminiImageConfig] = None
    ) -> GeminiGenerationResult:
        """
        Generate a quick preview image (lower cost).

        Uses gemini-3.1-flash-image-preview for cost efficiency.

        Args:
            product_name: Product name
            brief_description: Short description
            category_slug: Product category for style hints
            config: Optional config override

        Returns:
            GeminiGenerationResult with preview image
        """
        preview_config = config or GeminiImageConfig(
            model=GeminiImageModel.GEMINI_3_1_FLASH_IMAGE,
            image_size=GeminiImageSize.MEDIUM,
        )

        from .openai_image_service import PromptTemplates, FoodCategoryDescriptors
        category_info = FoodCategoryDescriptors.get_category_info(category_slug)
        style_keywords = category_info.get("style_keywords", "professional food photo")

        prompt = PromptTemplates.get_quick_prompt(
            product_name=product_name,
            brief_description=brief_description,
            style_keywords=style_keywords
        )

        return self._generate_image(prompt=prompt, config=preview_config)

    def regenerate_with_feedback(
        self,
        previous_image: bytes,
        feedback: str,
        original_prompt: str,
        config: Optional[GeminiImageConfig] = None
    ) -> GeminiGenerationResult:
        """
        Regenerate an image incorporating user feedback.

        Args:
            previous_image: The previously generated image
            feedback: User feedback on what to change
            original_prompt: The original generation prompt
            config: Generation configuration

        Returns:
            GeminiGenerationResult with improved image
        """
        config = config or self.default_config

        enhanced_prompt = f"""
{original_prompt}

ADJUSTMENTS BASED ON FEEDBACK:
{feedback}

Please incorporate these changes while maintaining the overall style and quality.
"""

        return self._generate_image(
            prompt=enhanced_prompt,
            original_image=previous_image,
            config=config
        )

    # -------------------------------------------------------------------------
    # API INTERACTION METHODS
    # -------------------------------------------------------------------------

    def _generate_image(
        self,
        prompt: str,
        original_image: Optional[bytes] = None,
        reference_image: Optional[bytes] = None,
        config: Optional[GeminiImageConfig] = None
    ) -> GeminiGenerationResult:
        """
        Generate an image using Gemini's generate_content endpoint with IMAGE modality.

        Args:
            prompt: Text prompt for generation
            original_image: Optional original image bytes (for editing)
            reference_image: Optional style reference image bytes
            config: Generation configuration

        Returns:
            GeminiGenerationResult
        """
        config = config or self.default_config

        # Build contents array
        contents = []

        # Add original image if provided (for editing)
        if original_image:
            contents.append(self._create_image_part(original_image))
            contents.append("Use the above image as the base. ")

        # Add reference image if provided (for style transfer)
        if reference_image:
            contents.append(self._create_image_part(reference_image))
            contents.append("Use the above image as a style reference. ")

        # Add the text prompt
        contents.append(prompt)

        # Build generation config
        generation_config = self.types.GenerateContentConfig(
            response_modalities=['TEXT', 'IMAGE'],
            image_config=self.types.ImageConfig(
                aspect_ratio=config.aspect_ratio.value,
                image_size=config.image_size.value,
            ),
        )

        # Execute with retry logic
        return self._execute_with_retry(
            contents=contents,
            generation_config=generation_config,
            config=config
        )

    def _create_image_part(self, image_data: bytes) -> object:
        """Create a Gemini image part from raw bytes."""
        # Detect mime type
        img = Image.open(BytesIO(image_data))
        mime_map = {
            'PNG': 'image/png',
            'JPEG': 'image/jpeg',
            'WEBP': 'image/webp',
            'GIF': 'image/gif',
        }
        mime_type = mime_map.get(img.format, 'image/png')

        return self.types.Part.from_bytes(
            data=image_data,
            mime_type=mime_type,
        )

    def _execute_with_retry(
        self,
        contents: list,
        generation_config,
        config: GeminiImageConfig
    ) -> GeminiGenerationResult:
        """
        Execute a Gemini API call with retry logic.

        Args:
            contents: List of content parts (text + images)
            generation_config: Gemini generation configuration
            config: Service configuration with retry settings

        Returns:
            GeminiGenerationResult
        """
        last_error = None

        for attempt in range(config.max_retries):
            try:
                response = self.client.models.generate_content(
                    model=config.model.value,
                    contents=contents,
                    config=generation_config,
                )

                # Extract image from response parts
                if response.parts:
                    for part in response.parts:
                        if part.inline_data is not None:
                            # Get the raw image bytes
                            image = part.as_image()

                            # Convert PIL Image to bytes in desired format
                            output = BytesIO()
                            save_format = config.output_format.upper()
                            if save_format == 'JPG':
                                save_format = 'JPEG'

                            # For PNG, preserve RGBA mode
                            if save_format == 'PNG':
                                image.save(output, format='PNG')
                            elif save_format == 'WEBP':
                                image.save(output, format='WEBP', quality=95)
                            else:
                                # JPEG doesn't support transparency
                                if image.mode == 'RGBA':
                                    image = image.convert('RGB')
                                image.save(output, format='JPEG', quality=95)

                            image_bytes = output.getvalue()

                            return GeminiGenerationResult(
                                success=True,
                                image_data=image_bytes,
                                model_used=config.model.value,
                                estimated_cost=self._estimate_cost(config)
                            )

                return GeminiGenerationResult(
                    success=False,
                    error_message="No se genero ninguna imagen en la respuesta de Gemini"
                )

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Check for content policy / safety violations
                if 'safety' in error_str or 'blocked' in error_str:
                    logger.error(f"Content safety violation: {e}")
                    return GeminiGenerationResult(
                        success=False,
                        error_message="La imagen solicitada viola las politicas de contenido. "
                                     "Por favor, modifica la descripcion."
                    )

                # Check for rate limiting
                if 'rate' in error_str or '429' in error_str:
                    wait_time = config.retry_delay_base * (2 ** attempt)
                    logger.warning(
                        f"Rate limit hit, waiting {wait_time}s "
                        f"(attempt {attempt + 1}/{config.max_retries})"
                    )
                    time.sleep(wait_time)
                    continue

                # Check for invalid image
                if 'invalid' in error_str and 'image' in error_str:
                    return GeminiGenerationResult(
                        success=False,
                        error_message="La imagen proporcionada no es valida. "
                                     "Asegurate de usar PNG, JPEG o WebP."
                    )

                # Other errors - retry
                logger.warning(
                    f"Gemini API error: {e}, retrying "
                    f"(attempt {attempt + 1}/{config.max_retries})"
                )
                time.sleep(config.retry_delay_base * (2 ** attempt))

        return GeminiGenerationResult(
            success=False,
            error_message=f"Error despues de {config.max_retries} intentos: {str(last_error)}"
        )

    # -------------------------------------------------------------------------
    # UTILITY METHODS
    # -------------------------------------------------------------------------

    def _generate_cache_key(self, prompt: str, config: GeminiImageConfig) -> str:
        """Generate a unique cache key for the generation request."""
        key_data = (
            f"{prompt}:{config.model.value}:"
            f"{config.aspect_ratio.value}:{config.image_size.value}"
        )
        return f"gemini_img:{hashlib.sha256(key_data.encode()).hexdigest()[:16]}"

    def _estimate_cost(self, config: GeminiImageConfig) -> float:
        """Estimate the cost of a Gemini image generation."""
        return GEMINI_PRICING_PER_IMAGE.get(config.model, 0.04)
