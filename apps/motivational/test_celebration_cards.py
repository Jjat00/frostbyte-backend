import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image
from rest_framework.test import APIRequestFactory
from .celebration_cards import generate_celebration_card, CardInput, card_prompt


def photo_file():
    out = BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(out, 'PNG')
    return SimpleUploadedFile('photo.png', out.getvalue(), content_type='image/png')


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CelebrationCardTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()

    def request(self, data):
        return generate_celebration_card(self.factory.post('/card/', data, format='multipart'))

    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_bad_file_rejected_before_provider(self, client):
        response = self.request({'image': SimpleUploadedFile('fake.png', b'not image', content_type='image/png')})
        self.assertEqual(response.status_code, 400)
        client.assert_not_called()

    def test_missing_photo_and_long_phrase(self):
        self.assertEqual(self.request({}).status_code, 400)
        self.assertEqual(self.request({'image': photo_file(), 'phrase': 'x' * 241}).status_code, 400)

    def test_size_limit(self):
        file = photo_file()
        file.size = 10 * 1024 * 1024 + 1
        serializer = CardInput(data={'image': file})
        self.assertFalse(serializer.is_valid())

    @patch.dict('os.environ', {'GEMINI_API_KEY': ''})
    def test_unconfigured_returns_service_unavailable(self):
        self.assertEqual(self.request({'image': photo_file()}).status_code, 503)

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_success_passes_photo_and_returns_image(self, factory):
        client = factory.return_value.__enter__.return_value
        client.models.generate_content.return_value.parts = [SimpleNamespace(inline_data=SimpleNamespace(data=b'output', mime_type='image/png'))]
        response = self.request({'image': photo_file(), 'phrase': 'Gracias por estar', 'to_name': 'Ana'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(base64.b64decode(response.data['image_base64']), b'output')
        args = client.models.generate_content.call_args.kwargs
        self.assertIn('Gracias por estar', args['contents'][1])
        self.assertIn('Ana', args['contents'][1])
        self.assertEqual(args['contents'][0].inline_data.mime_type, 'image/jpeg')
        self.assertEqual(args['config'].image_config.aspect_ratio, '4:5')

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_provider_error_is_not_exposed(self, factory):
        factory.return_value.__enter__.return_value.models.generate_content.side_effect = RuntimeError('private key or photo details')
        response = self.request({'image': photo_file()})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private', str(response.data))

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_no_image(self, factory):
        factory.return_value.__enter__.return_value.models.generate_content.return_value.parts = []
        self.assertEqual(self.request({'image': photo_file()}).status_code, 502)

    @patch.dict('os.environ', {'GEMINI_API_KEY': ''})
    def test_throttle(self):
        for _ in range(12):
            self.assertEqual(self.request({'image': photo_file()}).status_code, 503)
        self.assertEqual(self.request({'image': photo_file()}).status_code, 429)

    def test_prompt_preserves_identity_and_coordinates_accessories(self):
        prompt = card_prompt({'phrase': 'Te quiero'})
        for text in ['rostros', 'ropa', 'accesorios', 'satén', '#1b1017', 'No añadas bebidas alcohólicas', 'SOLO texto literal']:
            self.assertIn(text, prompt)
