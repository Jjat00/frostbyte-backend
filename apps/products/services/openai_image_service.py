"""
OpenAI Image Generation Service for Frostbyte Menu Items

This service handles AI-powered image generation for menu products using
OpenAI's gpt-image-1.5 model. It supports:
- Combining original images with reference style images
- Transparent background generation
- Robust error handling and retry logic
- Cost optimization strategies

Technical Notes (OpenAI 2026):
- Model: gpt-image-1.5 (recommended), gpt-image-1, gpt-image-1-mini
- Transparent background: background="transparent" with PNG/WebP output
- DALL-E 3 deprecated May 2026
- Known issue: White areas may become transparent unintentionally

Author: Frostbyte Team
Version: 1.0.0
"""

import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import Optional
from PIL import Image
import httpx
from openai import OpenAI, APIError, RateLimitError, APITimeoutError
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION AND ENUMS
# =============================================================================

class ImageModel(Enum):
    """Available OpenAI image generation models."""
    GPT_IMAGE_1_5 = "gpt-image-1.5"      # Most advanced, highest quality
    GPT_IMAGE_1 = "gpt-image-1"           # Standard quality
    GPT_IMAGE_1_MINI = "gpt-image-1-mini" # Fast, cost-effective


class ImageSize(Enum):
    """Supported image sizes."""
    SQUARE_1024 = "1024x1024"      # Best for menu items
    LANDSCAPE = "1536x1024"        # Wide format
    PORTRAIT = "1024x1536"         # Tall format
    AUTO = "auto"                  # Let API decide


class ImageQuality(Enum):
    """Image quality settings."""
    LOW = "low"           # Faster, cheaper
    MEDIUM = "medium"     # Balanced
    HIGH = "high"         # Best quality, slower
    AUTO = "auto"         # API decides based on prompt


class OutputFormat(Enum):
    """Output format options."""
    PNG = "png"           # Supports transparency
    WEBP = "webp"         # Supports transparency, smaller files
    JPEG = "jpeg"         # No transparency, smaller files


@dataclass
class ImageGenerationConfig:
    """Configuration for image generation requests."""
    model: ImageModel = ImageModel.GPT_IMAGE_1_5
    size: ImageSize = ImageSize.SQUARE_1024
    quality: ImageQuality = ImageQuality.HIGH
    output_format: OutputFormat = OutputFormat.PNG
    transparent_background: bool = True

    # Retry configuration
    max_retries: int = 3
    retry_delay_base: float = 1.0  # Exponential backoff base
    timeout: float = 120.0  # seconds

    # Cost optimization
    use_cache: bool = True
    cache_ttl: int = 86400 * 7  # 7 days


@dataclass
class GenerationResult:
    """Result of an image generation request."""
    success: bool
    image_data: Optional[bytes] = None
    image_url: Optional[str] = None
    error_message: Optional[str] = None
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    estimated_cost: Optional[float] = None
    from_cache: bool = False
    generation_time: Optional[float] = None


# =============================================================================
# PRICING ESTIMATES (2026)
# =============================================================================

PRICING_PER_IMAGE = {
    ImageModel.GPT_IMAGE_1_5: {
        ImageQuality.LOW: 0.02,
        ImageQuality.MEDIUM: 0.04,
        ImageQuality.HIGH: 0.08,
        ImageQuality.AUTO: 0.05,
    },
    ImageModel.GPT_IMAGE_1: {
        ImageQuality.LOW: 0.015,
        ImageQuality.MEDIUM: 0.03,
        ImageQuality.HIGH: 0.06,
        ImageQuality.AUTO: 0.04,
    },
    ImageModel.GPT_IMAGE_1_MINI: {
        ImageQuality.LOW: 0.005,
        ImageQuality.MEDIUM: 0.01,
        ImageQuality.HIGH: 0.02,
        ImageQuality.AUTO: 0.012,
    },
}


# =============================================================================
# PROMPT TEMPLATES
# =============================================================================

class PromptTemplates:
    """
    Prompt templates optimized for food/beverage menu item generation.

    Strategy:
    1. Start with subject isolation to prevent white transparency issues
    2. Describe the visual style based on reference image
    3. Add specific food photography techniques
    4. Include explicit anti-transparency instructions for white elements
    """

    # Base template for menu items with transparent background
    MENU_ITEM_TRANSPARENT = """
Create a professional product photo of {product_name}.

SUBJECT DESCRIPTION:
{product_description}

VISUAL STYLE (match this reference style):
{style_description}

PHOTOGRAPHY REQUIREMENTS:
- Professional food photography lighting (soft, diffused)
- Sharp focus on the product with slight depth of field
- Appetizing presentation with visible texture and details
- Clean, isolated subject ready for transparent background

CRITICAL - BACKGROUND AND TRANSPARENCY:
- The background MUST be completely empty/transparent
- The product should be the ONLY element in the image
- NO shadows on the background (shadows only on the product itself)
- Edges must be clean and well-defined for easy background removal

CRITICAL - WHITE ELEMENT PRESERVATION:
- Any white elements (cream, ice, whipped cream, marshmallows, white chocolate, milk foam)
  MUST remain SOLID WHITE and NOT become transparent
- White ingredients are PART OF THE PRODUCT, not background
- Maintain clear contrast between white product elements and the transparent background
- White surfaces should have subtle shading/texture to define their form

{additional_instructions}
"""

    # Template for items with visible white ingredients (extra protection)
    MENU_ITEM_WITH_WHITE_ELEMENTS = """
Create a professional product photo of {product_name}.

SUBJECT DESCRIPTION:
{product_description}

This product contains WHITE ELEMENTS that must be preserved:
{white_elements}

VISUAL STYLE (match this reference style):
{style_description}

PHOTOGRAPHY REQUIREMENTS:
- Professional food photography with soft studio lighting
- The white ingredients ({white_elements}) must appear SOLID and OPAQUE
- Add subtle warm tones or cream tints to white areas to ensure visibility
- Create gentle shadows ON the white elements to show their 3D form
- Sharp product focus, appetizing presentation

BACKGROUND REQUIREMENTS:
- Completely transparent/empty background
- NO background elements, surfaces, or props
- Clean product edges for compositing

IMPORTANT DISTINCTIONS:
- TRANSPARENT: Only the area AROUND the product
- SOLID WHITE: Any {white_elements} that are PART of the product
- The white parts of the product must NEVER become see-through

{additional_instructions}
"""

    # Template for style description extraction
    STYLE_ANALYSIS = """
Analyze this reference image and describe the visual style for recreation:

ANALYZE:
1. Lighting: Direction, softness, color temperature
2. Color palette: Dominant colors, saturation, contrast
3. Mood: Overall aesthetic feeling
4. Composition: Angle, framing, depth of field
5. Texture rendering: How textures are captured
6. Special effects: Any glow, steam, condensation, etc.

Provide a concise style description in 2-3 sentences.
"""

    # Quick generation template (for previews/drafts)
    QUICK_PREVIEW = """
Product photo of {product_name}: {brief_description}
Style: {style_keywords}
Requirements: Transparent background, isolated subject, food photography
Preserve white elements as solid (not transparent).
"""

    @classmethod
    def build_menu_prompt(
        cls,
        product_name: str,
        product_description: str,
        style_description: str,
        additional_instructions: str = "",
        white_elements: Optional[list[str]] = None
    ) -> str:
        """
        Build an optimized prompt for menu item generation.

        Args:
            product_name: Name of the product (e.g., "Mango Biche Granizado")
            product_description: Detailed description of the product
            style_description: Description of the desired visual style
            additional_instructions: Any extra requirements from the user
            white_elements: List of white ingredients to preserve

        Returns:
            Formatted prompt string
        """
        if white_elements:
            return cls.MENU_ITEM_WITH_WHITE_ELEMENTS.format(
                product_name=product_name,
                product_description=product_description,
                style_description=style_description,
                white_elements=", ".join(white_elements),
                additional_instructions=additional_instructions
            )

        return cls.MENU_ITEM_TRANSPARENT.format(
            product_name=product_name,
            product_description=product_description,
            style_description=style_description,
            additional_instructions=additional_instructions
        )

    @classmethod
    def get_quick_prompt(
        cls,
        product_name: str,
        brief_description: str,
        style_keywords: str
    ) -> str:
        """Build a quick/preview prompt for cost-effective generation."""
        return cls.QUICK_PREVIEW.format(
            product_name=product_name,
            brief_description=brief_description,
            style_keywords=style_keywords
        )


# =============================================================================
# FOOD CATEGORY DESCRIPTORS
# =============================================================================

class FoodCategoryDescriptors:
    """
    Pre-built descriptors for common Frostbyte product categories.
    These help maintain consistency across similar products.
    """

    GRANIZADO = {
        "style_keywords": "vibrant frozen beverage, crushed ice texture, fruit colors, refreshing",
        "common_white_elements": ["crushed ice", "whipped cream topping"],
        "photography_tips": "Show ice crystals, condensation on glass, vibrant syrup colors",
        "default_description": "A refreshing shaved ice beverage with colorful fruit syrup, "
                              "served in a clear cup showing layers of crushed ice and flavor"
    }

    FRAPPE = {
        "style_keywords": "creamy blended beverage, smooth texture, coffee tones, indulgent",
        "common_white_elements": ["whipped cream", "milk foam", "ice cream"],
        "photography_tips": "Creamy swirls, whipped cream peaks, drizzle details",
        "default_description": "A creamy blended frozen beverage with smooth texture, "
                              "topped with whipped cream and decorative drizzle"
    }

    MICHELADA = {
        "style_keywords": "savory beer cocktail, rimmed glass, bold garnishes, refreshing",
        "common_white_elements": ["salt rim", "foam"],
        "photography_tips": "Show the salt/chamoy rim, beer foam, lime garnish",
        "default_description": "A bold Mexican beer cocktail in a rimmed glass with "
                              "tomato-based mix, lime, and traditional garnishes"
    }

    SMOOTHIE = {
        "style_keywords": "healthy fruit blend, natural colors, fresh ingredients",
        "common_white_elements": ["yogurt swirl", "coconut flakes"],
        "photography_tips": "Fresh fruit pieces, smooth pour, natural lighting",
        "default_description": "A healthy blended fruit smoothie with vibrant natural colors "
                              "and visible fruit pieces"
    }

    HELADO = {
        "style_keywords": "artisan ice cream, creamy scoops, rich texture, indulgent",
        "common_white_elements": ["vanilla ice cream", "whipped cream", "white chocolate"],
        "photography_tips": "Show scoop texture, melting edges, toppings",
        "default_description": "Artisanal ice cream scoops with rich, creamy texture "
                              "and carefully placed toppings"
    }

    @classmethod
    def get_category_info(cls, category_slug: str) -> dict:
        """Get descriptor info for a product category."""
        mapping = {
            "granizados": cls.GRANIZADO,
            "frappes": cls.FRAPPE,
            "micheladas": cls.MICHELADA,
            "smoothies": cls.SMOOTHIE,
            "helados": cls.HELADO,
        }
        return mapping.get(category_slug, {})


# =============================================================================
# MAIN SERVICE CLASS
# =============================================================================

class OpenAIImageService:
    """
    Service for generating menu item images using OpenAI's image API.

    Features:
    - Multi-image input (original + reference style)
    - Transparent background support
    - Automatic retry with exponential backoff
    - Response caching for cost optimization
    - Content moderation integration
    - White element preservation strategies
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the OpenAI Image Service.

        Args:
            api_key: OpenAI API key. If not provided, uses OPENAI_API_KEY env var.
        """
        self.api_key = api_key or getattr(settings, 'OPENAI_API_KEY', None)
        if not self.api_key:
            raise ValueError(
                "OpenAI API key is required. Set OPENAI_API_KEY in environment "
                "or pass api_key parameter."
            )

        self.client = OpenAI(api_key=self.api_key)
        self.default_config = ImageGenerationConfig()

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
        config: Optional[ImageGenerationConfig] = None
    ) -> GenerationResult:
        """
        Generate a menu item image with optional style reference.

        This is the main entry point for menu image generation. It combines:
        1. Original product image (if available) - for editing/enhancement
        2. Reference style image (if available) - for style transfer
        3. Text prompt - for generation guidance

        Args:
            product_name: Name of the product
            product_description: Detailed product description
            original_image: Original product image bytes (for edit mode)
            reference_image: Style reference image bytes
            style_description: Text description of desired style
            additional_instructions: Extra instructions from user
            white_elements: List of white ingredients to preserve
            config: Generation configuration

        Returns:
            GenerationResult with image data or error information
        """
        config = config or self.default_config
        start_time = time.time()

        # Build the prompt
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
                return GenerationResult(
                    success=True,
                    image_data=cached_result,
                    from_cache=True,
                    generation_time=time.time() - start_time
                )

        # Determine which API method to use
        try:
            if original_image:
                # Use edit endpoint when we have an original image
                result = self._edit_image(
                    original_image=original_image,
                    prompt=prompt,
                    reference_image=reference_image,
                    config=config
                )
            else:
                # Use generate endpoint for new images
                result = self._generate_image(
                    prompt=prompt,
                    reference_images=[reference_image] if reference_image else None,
                    config=config
                )

            # Cache successful results
            if result.success and result.image_data and config.use_cache:
                cache.set(cache_key, result.image_data, config.cache_ttl)

            result.generation_time = time.time() - start_time
            return result

        except Exception as e:
            logger.exception(f"Unexpected error generating image for {product_name}")
            return GenerationResult(
                success=False,
                error_message=f"Unexpected error: {str(e)}",
                generation_time=time.time() - start_time
            )

    def generate_preview(
        self,
        product_name: str,
        brief_description: str,
        category_slug: str,
        config: Optional[ImageGenerationConfig] = None
    ) -> GenerationResult:
        """
        Generate a quick preview image (lower cost).

        Use this for draft previews before final generation.
        Uses gpt-image-1-mini for cost efficiency.

        Args:
            product_name: Product name
            brief_description: Short description
            category_slug: Product category for style hints
            config: Optional config override

        Returns:
            GenerationResult with preview image
        """
        # Use mini model for previews
        preview_config = config or ImageGenerationConfig(
            model=ImageModel.GPT_IMAGE_1_MINI,
            quality=ImageQuality.LOW,
            transparent_background=True
        )

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
        config: Optional[ImageGenerationConfig] = None
    ) -> GenerationResult:
        """
        Regenerate an image incorporating user feedback.

        Args:
            previous_image: The previously generated image
            feedback: User feedback on what to change
            original_prompt: The original generation prompt
            config: Generation configuration

        Returns:
            GenerationResult with improved image
        """
        config = config or self.default_config

        # Build feedback-enhanced prompt
        enhanced_prompt = f"""
{original_prompt}

ADJUSTMENTS BASED ON FEEDBACK:
{feedback}

Please incorporate these changes while maintaining the overall style and quality.
"""

        return self._edit_image(
            original_image=previous_image,
            prompt=enhanced_prompt,
            config=config
        )

    # -------------------------------------------------------------------------
    # API INTERACTION METHODS
    # -------------------------------------------------------------------------

    def _generate_image(
        self,
        prompt: str,
        reference_images: Optional[list[bytes]] = None,
        config: Optional[ImageGenerationConfig] = None
    ) -> GenerationResult:
        """
        Generate a new image using OpenAI's images.generate endpoint.

        API Endpoint: POST https://api.openai.com/v1/images/generations

        Args:
            prompt: Text prompt for generation
            reference_images: Optional list of reference image bytes
            config: Generation configuration

        Returns:
            GenerationResult
        """
        config = config or self.default_config

        # Build API parameters
        params = {
            "model": config.model.value,
            "prompt": prompt,
            "n": 1,
            "size": config.size.value,
            "quality": config.quality.value,
            "response_format": "b64_json",  # Get base64 for processing
        }

        # Add transparent background if supported
        if config.transparent_background:
            params["background"] = "transparent"
            # Ensure output format supports transparency
            if config.output_format in [OutputFormat.PNG, OutputFormat.WEBP]:
                params["output_format"] = config.output_format.value
            else:
                params["output_format"] = "png"  # Force PNG for transparency
        else:
            params["output_format"] = config.output_format.value

        # Add reference images if provided (for style guidance)
        if reference_images:
            # Encode reference images as base64
            image_inputs = []
            for ref_img in reference_images:
                if ref_img:
                    b64_ref = base64.b64encode(ref_img).decode('utf-8')
                    image_inputs.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_ref}"
                        }
                    })

            if image_inputs:
                # Use the multi-modal prompt format
                params["prompt"] = [
                    {"type": "text", "text": prompt},
                    *image_inputs
                ]

        # Execute with retry logic
        return self._execute_with_retry(
            operation=lambda: self.client.images.generate(**params),
            config=config
        )

    def _edit_image(
        self,
        original_image: bytes,
        prompt: str,
        reference_image: Optional[bytes] = None,
        mask: Optional[bytes] = None,
        config: Optional[ImageGenerationConfig] = None
    ) -> GenerationResult:
        """
        Edit an existing image using OpenAI's images.edit endpoint.

        API Endpoint: POST https://api.openai.com/v1/images/edits

        Args:
            original_image: Original image bytes to edit
            prompt: Instructions for editing
            reference_image: Optional style reference
            mask: Optional mask for partial editing
            config: Generation configuration

        Returns:
            GenerationResult
        """
        config = config or self.default_config

        # Prepare image file
        image_file = BytesIO(original_image)
        image_file.name = "image.png"

        # Build API parameters
        params = {
            "model": config.model.value,
            "image": image_file,
            "prompt": prompt,
            "n": 1,
            "size": config.size.value,
            "response_format": "b64_json",
        }

        # Add mask if provided
        if mask:
            mask_file = BytesIO(mask)
            mask_file.name = "mask.png"
            params["mask"] = mask_file

        # Add transparent background
        if config.transparent_background:
            params["background"] = "transparent"

        # Include reference image in prompt if provided
        if reference_image:
            b64_ref = base64.b64encode(reference_image).decode('utf-8')
            params["prompt"] = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64_ref}",
                        "detail": "high"  # Use high detail for style reference
                    }
                }
            ]

        return self._execute_with_retry(
            operation=lambda: self.client.images.edit(**params),
            config=config
        )

    def _execute_with_retry(
        self,
        operation,
        config: ImageGenerationConfig
    ) -> GenerationResult:
        """
        Execute an API operation with retry logic.

        Implements exponential backoff for rate limits and transient errors.

        Args:
            operation: Callable that performs the API request
            config: Configuration with retry settings

        Returns:
            GenerationResult
        """
        last_error = None

        for attempt in range(config.max_retries):
            try:
                response = operation()

                # Extract image data from response
                if hasattr(response, 'data') and len(response.data) > 0:
                    image_data = response.data[0]

                    if hasattr(image_data, 'b64_json'):
                        decoded_image = base64.b64decode(image_data.b64_json)

                        # Post-process to fix potential white transparency issues
                        decoded_image = self._post_process_transparency(decoded_image)

                        return GenerationResult(
                            success=True,
                            image_data=decoded_image,
                            model_used=config.model.value,
                            estimated_cost=self._estimate_cost(config)
                        )
                    elif hasattr(image_data, 'url'):
                        return GenerationResult(
                            success=True,
                            image_url=image_data.url,
                            model_used=config.model.value,
                            estimated_cost=self._estimate_cost(config)
                        )

                return GenerationResult(
                    success=False,
                    error_message="No image data in response"
                )

            except RateLimitError as e:
                last_error = e
                wait_time = config.retry_delay_base * (2 ** attempt)
                logger.warning(
                    f"Rate limit hit, waiting {wait_time}s before retry "
                    f"(attempt {attempt + 1}/{config.max_retries})"
                )
                time.sleep(wait_time)

            except APITimeoutError as e:
                last_error = e
                logger.warning(
                    f"API timeout, retrying (attempt {attempt + 1}/{config.max_retries})"
                )
                time.sleep(config.retry_delay_base)

            except APIError as e:
                last_error = e
                error_code = getattr(e, 'code', None)

                # Check for content policy violation
                if error_code == 'content_policy_violation':
                    logger.error(f"Content policy violation: {e}")
                    return GenerationResult(
                        success=False,
                        error_message="La imagen solicitada viola las politicas de contenido. "
                                     "Por favor, modifica la descripcion."
                    )

                # Check for invalid image errors
                if error_code == 'invalid_image':
                    logger.error(f"Invalid image provided: {e}")
                    return GenerationResult(
                        success=False,
                        error_message="La imagen proporcionada no es valida. "
                                     "Asegurate de usar PNG, JPEG o WebP."
                    )

                # Other API errors - may be transient
                logger.warning(
                    f"API error: {e}, retrying (attempt {attempt + 1}/{config.max_retries})"
                )
                time.sleep(config.retry_delay_base * (2 ** attempt))

        # All retries exhausted
        return GenerationResult(
            success=False,
            error_message=f"Error despues de {config.max_retries} intentos: {str(last_error)}"
        )

    # -------------------------------------------------------------------------
    # WHITE TRANSPARENCY FIX - POST PROCESSING
    # -------------------------------------------------------------------------

    def _post_process_transparency(self, image_data: bytes) -> bytes:
        """
        Post-process image to fix white areas that became transparent.

        This addresses the known OpenAI issue where white product elements
        may unintentionally become transparent.

        Strategy:
        1. Detect pixels that are nearly white but transparent
        2. Convert them to solid white
        3. Preserve intentional transparency (background)

        Args:
            image_data: Raw PNG image bytes

        Returns:
            Processed image bytes
        """
        try:
            img = Image.open(BytesIO(image_data))

            # Only process RGBA images
            if img.mode != 'RGBA':
                return image_data

            # Get pixel data
            pixels = img.load()
            width, height = img.size

            # Threshold values
            WHITE_THRESHOLD = 240  # RGB values above this are considered "white"
            ALPHA_THRESHOLD = 200  # Alpha below this might be incorrect transparency

            modified = False

            for y in range(height):
                for x in range(width):
                    r, g, b, a = pixels[x, y]

                    # Check if this is a "white" pixel that's semi-transparent
                    # This likely indicates the white transparency bug
                    if (r > WHITE_THRESHOLD and
                        g > WHITE_THRESHOLD and
                        b > WHITE_THRESHOLD and
                        0 < a < ALPHA_THRESHOLD):

                        # Make it fully opaque white
                        pixels[x, y] = (r, g, b, 255)
                        modified = True

            if modified:
                logger.info("Fixed white transparency issues in generated image")
                output = BytesIO()
                img.save(output, format='PNG')
                return output.getvalue()

            return image_data

        except Exception as e:
            logger.warning(f"Could not post-process image: {e}")
            return image_data

    def fix_white_transparency_aggressive(self, image_data: bytes) -> bytes:
        """
        Aggressive fix for white transparency - use when standard fix isn't enough.

        This method analyzes connected components and restores alpha
        for areas that appear to be product (not background).

        Args:
            image_data: Image bytes with transparency issues

        Returns:
            Fixed image bytes
        """
        try:
            img = Image.open(BytesIO(image_data)).convert('RGBA')
            pixels = img.load()
            width, height = img.size

            # Step 1: Find the bounding box of non-transparent content
            min_x, min_y = width, height
            max_x, max_y = 0, 0

            for y in range(height):
                for x in range(width):
                    _, _, _, a = pixels[x, y]
                    if a > 10:  # Has some opacity
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)

            # Step 2: Within the product bounding box, fix white transparency
            padding = 5
            for y in range(max(0, min_y - padding), min(height, max_y + padding)):
                for x in range(max(0, min_x - padding), min(width, max_x + padding)):
                    r, g, b, a = pixels[x, y]

                    # If inside bounding box and appears white-ish
                    if (min_x <= x <= max_x and min_y <= y <= max_y):
                        if r > 200 and g > 200 and b > 200 and a < 255:
                            # Restore full opacity
                            pixels[x, y] = (r, g, b, 255)

            output = BytesIO()
            img.save(output, format='PNG')
            return output.getvalue()

        except Exception as e:
            logger.warning(f"Aggressive transparency fix failed: {e}")
            return image_data

    # -------------------------------------------------------------------------
    # UTILITY METHODS
    # -------------------------------------------------------------------------

    def _generate_cache_key(self, prompt: str, config: ImageGenerationConfig) -> str:
        """Generate a unique cache key for the generation request."""
        key_data = f"{prompt}:{config.model.value}:{config.size.value}:{config.quality.value}"
        return f"openai_img:{hashlib.sha256(key_data.encode()).hexdigest()[:16]}"

    def _estimate_cost(self, config: ImageGenerationConfig) -> float:
        """Estimate the cost of an image generation."""
        return PRICING_PER_IMAGE.get(config.model, {}).get(config.quality, 0.05)

    def check_content_policy(self, prompt: str) -> tuple[bool, str]:
        """
        Pre-check if a prompt might violate content policy.

        Args:
            prompt: The generation prompt

        Returns:
            Tuple of (is_safe, message)
        """
        try:
            # Use OpenAI's moderation endpoint
            response = self.client.moderations.create(input=prompt)

            result = response.results[0]
            if result.flagged:
                flagged_categories = [
                    cat for cat, flagged in result.categories.__dict__.items()
                    if flagged
                ]
                return False, f"Contenido potencialmente inapropiado: {', '.join(flagged_categories)}"

            return True, "OK"

        except Exception as e:
            logger.warning(f"Moderation check failed: {e}")
            return True, "No se pudo verificar (continuando)"


# =============================================================================
# USAGE TRACKING AND RATE LIMITING
# =============================================================================

class ImageGenerationUsageTracker:
    """
    Tracks image generation usage per user/business for cost control.
    """

    # Default limits
    DEFAULT_DAILY_LIMIT = 50
    DEFAULT_MONTHLY_LIMIT = 500

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.daily_key = f"img_gen_daily:{user_id}"
        self.monthly_key = f"img_gen_monthly:{user_id}"

    def can_generate(self) -> tuple[bool, str]:
        """Check if user can generate more images."""
        daily_count = cache.get(self.daily_key, 0)
        monthly_count = cache.get(self.monthly_key, 0)

        if daily_count >= self.DEFAULT_DAILY_LIMIT:
            return False, f"Limite diario alcanzado ({self.DEFAULT_DAILY_LIMIT} imagenes)"

        if monthly_count >= self.DEFAULT_MONTHLY_LIMIT:
            return False, f"Limite mensual alcanzado ({self.DEFAULT_MONTHLY_LIMIT} imagenes)"

        return True, "OK"

    def record_generation(self, cost: float = 0.0):
        """Record a successful generation."""
        # Increment daily counter (expires at midnight)
        daily_count = cache.get(self.daily_key, 0)
        cache.set(self.daily_key, daily_count + 1, 86400)  # 24 hours

        # Increment monthly counter
        monthly_count = cache.get(self.monthly_key, 0)
        cache.set(self.monthly_key, monthly_count + 1, 86400 * 30)  # 30 days

        # Track costs
        cost_key = f"img_gen_cost:{self.user_id}"
        total_cost = cache.get(cost_key, 0.0)
        cache.set(cost_key, total_cost + cost, 86400 * 30)

    def get_usage_stats(self) -> dict:
        """Get current usage statistics."""
        return {
            "daily_used": cache.get(self.daily_key, 0),
            "daily_limit": self.DEFAULT_DAILY_LIMIT,
            "monthly_used": cache.get(self.monthly_key, 0),
            "monthly_limit": self.DEFAULT_MONTHLY_LIMIT,
            "estimated_cost_mtd": cache.get(f"img_gen_cost:{self.user_id}", 0.0)
        }


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

def example_generate_granizado_image():
    """
    Example: Generate a transparent image for a Mango Biche Granizado.
    """
    service = OpenAIImageService()

    # Configuration for high-quality transparent image
    config = ImageGenerationConfig(
        model=ImageModel.GPT_IMAGE_1_5,
        size=ImageSize.SQUARE_1024,
        quality=ImageQuality.HIGH,
        output_format=OutputFormat.PNG,
        transparent_background=True
    )

    # Generate the image
    result = service.generate_menu_image(
        product_name="Granizado de Mango Biche",
        product_description="""
        A refreshing shaved ice beverage featuring:
        - Vibrant green-yellow mango biche (unripe mango) syrup
        - Finely crushed ice with visible crystal texture
        - Served in a clear 16oz plastic cup
        - Topped with a generous swirl of white whipped cream
        - Garnished with a lime wedge and chamoy drizzle
        - Condensation droplets on the cup exterior
        """,
        style_description="""
        Professional food photography style with:
        - Soft, diffused studio lighting from upper-left
        - High color saturation emphasizing the green-yellow mango
        - Sharp focus on the cup with slight bokeh
        - Clean, modern aesthetic suitable for a menu board
        """,
        white_elements=["whipped cream", "crushed ice highlights"],
        additional_instructions="Make it look refreshing and appetizing for summer",
        config=config
    )

    if result.success:
        print(f"Image generated successfully!")
        print(f"Model: {result.model_used}")
        print(f"Estimated cost: ${result.estimated_cost:.3f}")
        print(f"Generation time: {result.generation_time:.2f}s")
        print(f"From cache: {result.from_cache}")

        # Save the image
        with open("granizado_mango.png", "wb") as f:
            f.write(result.image_data)
    else:
        print(f"Generation failed: {result.error_message}")


def example_with_reference_image():
    """
    Example: Generate image using a style reference.
    """
    service = OpenAIImageService()

    # Load reference image
    with open("reference_style.png", "rb") as f:
        reference_image = f.read()

    result = service.generate_menu_image(
        product_name="Frappe de Cafe Moka",
        product_description="""
        A rich, creamy coffee frappe featuring:
        - Deep brown coffee base with chocolate swirls
        - Blended with ice for smooth, thick texture
        - Topped with whipped cream peak
        - Chocolate drizzle on cream
        - Served in branded Frostbyte cup
        """,
        reference_image=reference_image,
        style_description="Match the lighting and color grading of the reference image",
        white_elements=["whipped cream"],
        config=ImageGenerationConfig(
            model=ImageModel.GPT_IMAGE_1_5,
            quality=ImageQuality.HIGH,
            transparent_background=True
        )
    )

    return result


def example_cost_effective_preview():
    """
    Example: Generate a quick preview for draft approval.
    """
    service = OpenAIImageService()

    result = service.generate_preview(
        product_name="Michelada Clasica",
        brief_description="Beer cocktail with tomato, lime, salt rim",
        category_slug="micheladas"
    )

    if result.success:
        print(f"Preview generated - Cost: ${result.estimated_cost:.3f}")

    return result
