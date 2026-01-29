"""
API Views and Serializers for Image Generation Service

This module provides REST API endpoints for the OpenAI image generation service,
including:
- Single image generation
- Batch generation
- Preview generation
- Usage tracking
- Image regeneration with feedback

Endpoints:
- POST /api/products/images/generate/         - Generate menu item image
- POST /api/products/images/preview/          - Generate quick preview
- POST /api/products/images/regenerate/       - Regenerate with feedback
- GET  /api/products/images/usage/            - Get usage statistics
- POST /api/products/images/fix-transparency/ - Fix white transparency issues
"""

import base64
import logging
from io import BytesIO
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework import serializers
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image

from .openai_image_service import (
    OpenAIImageService,
    ImageGenerationConfig,
    ImageModel,
    ImageSize,
    ImageQuality,
    OutputFormat,
    ImageGenerationUsageTracker,
    FoodCategoryDescriptors,
)
from .r2_upload import R2UploadService, R2UploadError

logger = logging.getLogger(__name__)


# =============================================================================
# SERIALIZERS
# =============================================================================

class ImageGenerationRequestSerializer(serializers.Serializer):
    """Serializer for image generation requests."""

    # Required fields
    product_name = serializers.CharField(
        max_length=200,
        help_text="Name of the product (e.g., 'Granizado de Mango Biche')"
    )
    product_description = serializers.CharField(
        help_text="Detailed description of the product appearance"
    )

    # Optional fields
    style_description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Description of the desired visual style"
    )
    additional_instructions = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Extra instructions for the generation"
    )
    white_elements = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text="List of white ingredients to preserve (e.g., ['whipped cream', 'ice'])"
    )
    category_slug = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Product category for automatic style hints"
    )

    # Configuration options
    model = serializers.ChoiceField(
        choices=[
            ('gpt-image-1.5', 'GPT Image 1.5 (Best Quality)'),
            ('gpt-image-1', 'GPT Image 1 (Standard)'),
            ('gpt-image-1-mini', 'GPT Image 1 Mini (Fast/Cheap)'),
        ],
        default='gpt-image-1.5',
        required=False
    )
    quality = serializers.ChoiceField(
        choices=[
            ('low', 'Low'),
            ('medium', 'Medium'),
            ('high', 'High'),
            ('auto', 'Auto'),
        ],
        default='high',
        required=False
    )
    size = serializers.ChoiceField(
        choices=[
            ('1024x1024', 'Square (1024x1024)'),
            ('1536x1024', 'Landscape (1536x1024)'),
            ('1024x1536', 'Portrait (1024x1536)'),
            ('auto', 'Auto'),
        ],
        default='1024x1024',
        required=False
    )
    transparent_background = serializers.BooleanField(
        default=True,
        help_text="Generate with transparent background (PNG)"
    )
    output_format = serializers.ChoiceField(
        choices=[
            ('png', 'PNG (Supports Transparency)'),
            ('webp', 'WebP (Smaller, Supports Transparency)'),
            ('jpeg', 'JPEG (Smallest, No Transparency)'),
        ],
        default='png',
        required=False
    )

    # Image inputs (as base64)
    original_image_base64 = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Original product image as base64 (for edit mode)"
    )
    reference_image_base64 = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Style reference image as base64"
    )

    # Options
    upload_to_r2 = serializers.BooleanField(
        default=True,
        help_text="Automatically upload result to R2 storage"
    )
    skip_cache = serializers.BooleanField(
        default=False,
        help_text="Skip cache and force new generation"
    )

    def validate_original_image_base64(self, value):
        """Validate base64 image data."""
        if value:
            try:
                decoded = base64.b64decode(value)
                Image.open(BytesIO(decoded))
                return value
            except Exception:
                raise serializers.ValidationError(
                    "Invalid base64 image data for original_image"
                )
        return value

    def validate_reference_image_base64(self, value):
        """Validate base64 image data."""
        if value:
            try:
                decoded = base64.b64decode(value)
                Image.open(BytesIO(decoded))
                return value
            except Exception:
                raise serializers.ValidationError(
                    "Invalid base64 image data for reference_image"
                )
        return value


class PreviewGenerationRequestSerializer(serializers.Serializer):
    """Serializer for quick preview generation."""

    product_name = serializers.CharField(max_length=200)
    brief_description = serializers.CharField(max_length=500)
    category_slug = serializers.CharField(required=False, default="")


class RegenerationRequestSerializer(serializers.Serializer):
    """Serializer for regeneration with feedback."""

    previous_image_base64 = serializers.CharField(
        help_text="Previously generated image as base64"
    )
    feedback = serializers.CharField(
        help_text="User feedback on what to change"
    )
    original_prompt = serializers.CharField(
        help_text="The original generation prompt"
    )

    # Optional config overrides
    model = serializers.ChoiceField(
        choices=[
            ('gpt-image-1.5', 'GPT Image 1.5'),
            ('gpt-image-1', 'GPT Image 1'),
            ('gpt-image-1-mini', 'GPT Image 1 Mini'),
        ],
        default='gpt-image-1.5',
        required=False
    )
    quality = serializers.ChoiceField(
        choices=['low', 'medium', 'high', 'auto'],
        default='high',
        required=False
    )


class TransparencyFixRequestSerializer(serializers.Serializer):
    """Serializer for transparency fix requests."""

    image_base64 = serializers.CharField(
        help_text="Image with transparency issues as base64"
    )
    aggressive = serializers.BooleanField(
        default=False,
        help_text="Use aggressive fix algorithm"
    )


class GenerationResponseSerializer(serializers.Serializer):
    """Serializer for generation responses."""

    success = serializers.BooleanField()
    image_url = serializers.URLField(required=False, allow_null=True)
    image_base64 = serializers.CharField(required=False, allow_null=True)
    error_message = serializers.CharField(required=False, allow_null=True)
    model_used = serializers.CharField(required=False, allow_null=True)
    estimated_cost = serializers.FloatField(required=False, allow_null=True)
    from_cache = serializers.BooleanField(default=False)
    generation_time = serializers.FloatField(required=False, allow_null=True)


class UsageStatsSerializer(serializers.Serializer):
    """Serializer for usage statistics."""

    daily_used = serializers.IntegerField()
    daily_limit = serializers.IntegerField()
    monthly_used = serializers.IntegerField()
    monthly_limit = serializers.IntegerField()
    estimated_cost_mtd = serializers.FloatField()
    daily_remaining = serializers.IntegerField()
    monthly_remaining = serializers.IntegerField()


# =============================================================================
# API VIEWS
# =============================================================================

class ImageGenerationView(APIView):
    """
    Generate a menu item image using OpenAI.

    POST /api/products/images/generate/

    Accepts:
    - JSON body with generation parameters
    - Optional base64-encoded images

    Returns:
    - Generated image URL (if uploaded to R2)
    - Generated image as base64 (if not uploaded)
    - Usage and cost information
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request):
        serializer = ImageGenerationRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data

        # Check usage limits
        tracker = ImageGenerationUsageTracker(str(request.user.id))
        can_generate, message = tracker.can_generate()

        if not can_generate:
            return Response(
                {"success": False, "error_message": message},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Build configuration
        config = ImageGenerationConfig(
            model=ImageModel(data['model']),
            size=ImageSize(data['size']),
            quality=ImageQuality(data['quality']),
            output_format=OutputFormat(data['output_format']),
            transparent_background=data['transparent_background'],
            use_cache=not data['skip_cache']
        )

        # Decode images if provided
        original_image = None
        reference_image = None

        if data.get('original_image_base64'):
            original_image = base64.b64decode(data['original_image_base64'])

        if data.get('reference_image_base64'):
            reference_image = base64.b64decode(data['reference_image_base64'])

        # Get category hints if available
        white_elements = data.get('white_elements', [])
        if data.get('category_slug') and not white_elements:
            category_info = FoodCategoryDescriptors.get_category_info(data['category_slug'])
            white_elements = category_info.get('common_white_elements', [])

        # Initialize service and generate
        try:
            service = OpenAIImageService()

            # Check content policy first
            full_prompt = f"{data['product_name']}: {data['product_description']}"
            is_safe, moderation_message = service.check_content_policy(full_prompt)

            if not is_safe:
                return Response(
                    {"success": False, "error_message": moderation_message},
                    status=status.HTTP_400_BAD_REQUEST
                )

            result = service.generate_menu_image(
                product_name=data['product_name'],
                product_description=data['product_description'],
                original_image=original_image,
                reference_image=reference_image,
                style_description=data.get('style_description'),
                additional_instructions=data.get('additional_instructions', ''),
                white_elements=white_elements,
                config=config
            )

        except ValueError as e:
            return Response(
                {"success": False, "error_message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        if not result.success:
            return Response(
                {"success": False, "error_message": result.error_message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Record usage
        tracker.record_generation(result.estimated_cost or 0.0)

        # Upload to R2 if requested
        image_url = None
        if data['upload_to_r2'] and result.image_data:
            try:
                image_url = self._upload_to_r2(
                    result.image_data,
                    data['product_name'],
                    data['output_format']
                )
            except R2UploadError as e:
                logger.error(f"Failed to upload to R2: {e}")
                # Continue without URL, return base64 instead

        # Build response
        response_data = {
            "success": True,
            "image_url": image_url,
            "image_base64": base64.b64encode(result.image_data).decode('utf-8') if result.image_data and not image_url else None,
            "model_used": result.model_used,
            "estimated_cost": result.estimated_cost,
            "from_cache": result.from_cache,
            "generation_time": result.generation_time
        }

        return Response(response_data, status=status.HTTP_200_OK)

    def _upload_to_r2(self, image_data: bytes, product_name: str, format: str) -> str:
        """Upload generated image to R2 storage."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        # Create a file-like object
        filename = f"generated_{product_name.replace(' ', '_').lower()}.{format}"
        content_type = f"image/{format}"

        file = SimpleUploadedFile(
            name=filename,
            content=image_data,
            content_type=content_type
        )

        r2_service = R2UploadService()
        return r2_service.upload(file)


class ImagePreviewView(APIView):
    """
    Generate a quick preview image (lower cost).

    POST /api/products/images/preview/

    Uses gpt-image-1-mini for fast, cost-effective previews.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PreviewGenerationRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data

        try:
            service = OpenAIImageService()
            result = service.generate_preview(
                product_name=data['product_name'],
                brief_description=data['brief_description'],
                category_slug=data.get('category_slug', '')
            )
        except ValueError as e:
            return Response(
                {"success": False, "error_message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        if not result.success:
            return Response(
                {"success": False, "error_message": result.error_message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Track usage
        tracker = ImageGenerationUsageTracker(str(request.user.id))
        tracker.record_generation(result.estimated_cost or 0.0)

        return Response({
            "success": True,
            "image_base64": base64.b64encode(result.image_data).decode('utf-8') if result.image_data else None,
            "estimated_cost": result.estimated_cost,
            "generation_time": result.generation_time
        })


class ImageRegenerationView(APIView):
    """
    Regenerate an image with user feedback.

    POST /api/products/images/regenerate/

    Takes the previous image and feedback to generate an improved version.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RegenerationRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data

        # Check limits
        tracker = ImageGenerationUsageTracker(str(request.user.id))
        can_generate, message = tracker.can_generate()

        if not can_generate:
            return Response(
                {"success": False, "error_message": message},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Decode previous image
        previous_image = base64.b64decode(data['previous_image_base64'])

        config = ImageGenerationConfig(
            model=ImageModel(data['model']),
            quality=ImageQuality(data['quality']),
            transparent_background=True
        )

        try:
            service = OpenAIImageService()
            result = service.regenerate_with_feedback(
                previous_image=previous_image,
                feedback=data['feedback'],
                original_prompt=data['original_prompt'],
                config=config
            )
        except ValueError as e:
            return Response(
                {"success": False, "error_message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        if not result.success:
            return Response(
                {"success": False, "error_message": result.error_message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        tracker.record_generation(result.estimated_cost or 0.0)

        return Response({
            "success": True,
            "image_base64": base64.b64encode(result.image_data).decode('utf-8') if result.image_data else None,
            "estimated_cost": result.estimated_cost,
            "generation_time": result.generation_time
        })


class UsageStatsView(APIView):
    """
    Get current usage statistics.

    GET /api/products/images/usage/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        tracker = ImageGenerationUsageTracker(str(request.user.id))
        stats = tracker.get_usage_stats()

        # Add computed fields
        stats['daily_remaining'] = max(0, stats['daily_limit'] - stats['daily_used'])
        stats['monthly_remaining'] = max(0, stats['monthly_limit'] - stats['monthly_used'])

        serializer = UsageStatsSerializer(stats)
        return Response(serializer.data)


class TransparencyFixView(APIView):
    """
    Fix white transparency issues in an image.

    POST /api/products/images/fix-transparency/

    Addresses the known OpenAI issue where white elements become transparent.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TransparencyFixRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data

        try:
            image_data = base64.b64decode(data['image_base64'])
            service = OpenAIImageService()

            if data['aggressive']:
                fixed_image = service.fix_white_transparency_aggressive(image_data)
            else:
                fixed_image = service._post_process_transparency(image_data)

            return Response({
                "success": True,
                "image_base64": base64.b64encode(fixed_image).decode('utf-8')
            })

        except Exception as e:
            return Response(
                {"success": False, "error_message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ImageGenerationWithFilesView(APIView):
    """
    Generate image with file uploads (multipart form).

    POST /api/products/images/generate-upload/

    Alternative endpoint that accepts files directly instead of base64.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        # Extract files
        original_image = request.FILES.get('original_image')
        reference_image = request.FILES.get('reference_image')

        # Extract form data
        product_name = request.data.get('product_name')
        product_description = request.data.get('product_description')
        style_description = request.data.get('style_description', '')
        additional_instructions = request.data.get('additional_instructions', '')
        category_slug = request.data.get('category_slug', '')
        white_elements_str = request.data.get('white_elements', '[]')

        # Parse white_elements
        import json
        try:
            white_elements = json.loads(white_elements_str)
        except json.JSONDecodeError:
            white_elements = []

        # Validate required fields
        if not product_name or not product_description:
            return Response(
                {"success": False, "error_message": "product_name and product_description are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check limits
        tracker = ImageGenerationUsageTracker(str(request.user.id))
        can_generate, message = tracker.can_generate()

        if not can_generate:
            return Response(
                {"success": False, "error_message": message},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Read file data
        original_data = original_image.read() if original_image else None
        reference_data = reference_image.read() if reference_image else None

        # Get category hints
        if category_slug and not white_elements:
            category_info = FoodCategoryDescriptors.get_category_info(category_slug)
            white_elements = category_info.get('common_white_elements', [])

        # Generate
        try:
            service = OpenAIImageService()
            result = service.generate_menu_image(
                product_name=product_name,
                product_description=product_description,
                original_image=original_data,
                reference_image=reference_data,
                style_description=style_description,
                additional_instructions=additional_instructions,
                white_elements=white_elements
            )
        except ValueError as e:
            return Response(
                {"success": False, "error_message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        if not result.success:
            return Response(
                {"success": False, "error_message": result.error_message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        tracker.record_generation(result.estimated_cost or 0.0)

        # Upload to R2
        image_url = None
        if result.image_data:
            try:
                from django.core.files.uploadedfile import SimpleUploadedFile
                file = SimpleUploadedFile(
                    name=f"generated_{product_name.replace(' ', '_').lower()}.png",
                    content=result.image_data,
                    content_type="image/png"
                )
                r2_service = R2UploadService()
                image_url = r2_service.upload(file)
            except Exception as e:
                logger.error(f"R2 upload failed: {e}")

        return Response({
            "success": True,
            "image_url": image_url,
            "image_base64": base64.b64encode(result.image_data).decode('utf-8') if result.image_data and not image_url else None,
            "model_used": result.model_used,
            "estimated_cost": result.estimated_cost,
            "generation_time": result.generation_time
        })


# =============================================================================
# URL CONFIGURATION (to be added to urls.py)
# =============================================================================

"""
Add to apps/products/urls.py:

from django.urls import path
from .services.image_generation_api import (
    ImageGenerationView,
    ImagePreviewView,
    ImageRegenerationView,
    UsageStatsView,
    TransparencyFixView,
    ImageGenerationWithFilesView,
)

urlpatterns = [
    # ... existing patterns ...

    # Image Generation API
    path('images/generate/', ImageGenerationView.as_view(), name='image-generate'),
    path('images/generate-upload/', ImageGenerationWithFilesView.as_view(), name='image-generate-upload'),
    path('images/preview/', ImagePreviewView.as_view(), name='image-preview'),
    path('images/regenerate/', ImageRegenerationView.as_view(), name='image-regenerate'),
    path('images/usage/', UsageStatsView.as_view(), name='image-usage'),
    path('images/fix-transparency/', TransparencyFixView.as_view(), name='image-fix-transparency'),
]
"""
