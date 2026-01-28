"""
Cloudflare R2 Upload Service

Handles file uploads to Cloudflare R2 storage using the S3-compatible API.
"""

import boto3
import uuid
from datetime import datetime
from io import BytesIO
from PIL import Image
from django.conf import settings


class R2UploadError(Exception):
    """Custom exception for R2 upload errors."""
    pass


class R2UploadService:
    """Service for uploading files to Cloudflare R2."""

    ALLOWED_CONTENT_TYPES = settings.ALLOWED_IMAGE_TYPES
    MAX_FILE_SIZE = settings.FILE_UPLOAD_MAX_SIZE

    def __init__(self):
        self._validate_settings()
        self.client = self._create_client()

    def _validate_settings(self):
        """Validate that all required R2 settings are configured."""
        required_settings = [
            ('R2_ACCOUNT_ID', settings.R2_ACCOUNT_ID),
            ('R2_ACCESS_KEY_ID', settings.R2_ACCESS_KEY_ID),
            ('R2_SECRET_ACCESS_KEY', settings.R2_SECRET_ACCESS_KEY),
            ('R2_BUCKET_NAME', settings.R2_BUCKET_NAME),
            ('R2_PUBLIC_URL', settings.R2_PUBLIC_URL),
        ]

        missing = [name for name, value in required_settings if not value]
        if missing:
            raise R2UploadError(
                f"Missing R2 configuration: {', '.join(missing)}. "
                "Please configure these environment variables."
            )

    def _create_client(self):
        """Create and return a boto3 S3 client configured for R2."""
        endpoint_url = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

        return boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name='auto',
        )

    def validate_file(self, file):
        """
        Validate the uploaded file.

        Args:
            file: Django UploadedFile object

        Raises:
            R2UploadError: If validation fails
        """
        # Check file size
        if file.size > self.MAX_FILE_SIZE:
            max_mb = self.MAX_FILE_SIZE / (1024 * 1024)
            raise R2UploadError(
                f"El archivo es demasiado grande. Tamaño máximo: {max_mb:.0f}MB"
            )

        # Check content type
        content_type = file.content_type
        if content_type not in self.ALLOWED_CONTENT_TYPES:
            allowed = ', '.join(t.split('/')[-1].upper() for t in self.ALLOWED_CONTENT_TYPES)
            raise R2UploadError(
                f"Tipo de archivo no permitido. Tipos permitidos: {allowed}"
            )

        # Validate it's actually an image using Pillow
        try:
            file.seek(0)
            img = Image.open(file)
            img.verify()
            file.seek(0)
        except Exception:
            raise R2UploadError("El archivo no es una imagen válida")

    def _generate_filename(self, original_filename):
        """
        Generate a unique filename for the uploaded file.

        Args:
            original_filename: Original filename from upload

        Returns:
            str: Unique filename with timestamp and UUID
        """
        # Get file extension
        ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else 'jpg'

        # Generate unique name with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = uuid.uuid4().hex[:8]

        return f"products/{timestamp}_{unique_id}.{ext}"

    def upload(self, file):
        """
        Upload a file to R2 and return the public URL.

        Args:
            file: Django UploadedFile object

        Returns:
            str: Public URL of the uploaded file

        Raises:
            R2UploadError: If upload fails
        """
        # Validate file first
        self.validate_file(file)

        # Generate unique filename
        filename = self._generate_filename(file.name)

        try:
            # Read file content
            file.seek(0)
            file_content = file.read()

            # Upload to R2
            self.client.put_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=filename,
                Body=file_content,
                ContentType=file.content_type,
            )

            # Construct public URL
            public_url = f"{settings.R2_PUBLIC_URL.rstrip('/')}/{filename}"

            return public_url

        except Exception as e:
            raise R2UploadError(f"Error al subir la imagen: {str(e)}")

    def delete(self, url):
        """
        Delete a file from R2 by its URL.

        Args:
            url: Public URL of the file to delete

        Returns:
            bool: True if deletion was successful
        """
        if not url or not url.startswith(settings.R2_PUBLIC_URL):
            return False

        try:
            # Extract filename from URL
            filename = url.replace(f"{settings.R2_PUBLIC_URL.rstrip('/')}/", '')

            self.client.delete_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=filename,
            )
            return True
        except Exception:
            return False
