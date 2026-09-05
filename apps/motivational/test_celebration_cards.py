import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIRequestFactory
from .celebration_cards import (
    generate_celebration_card,
    suggest_celebration_phrase,
    CardInput,
    card_prompt,
)
from .models import CardGeneration

PNG = b'\x89PNG\r\n\x1a\n' + b'resto'
ONLY_GEMINI = {'GEMINI_API_KEY': 'test-key', 'OPENAI_API_KEY': ''}
BOTH_PROVIDERS = {'GEMINI_API_KEY': 'test-key', 'OPENAI_API_KEY': 'test-key'}
NO_PROVIDER = {'GEMINI_API_KEY': '', 'OPENAI_API_KEY': ''}


def photo_file():
    out = BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(out, 'PNG')
    return SimpleUploadedFile('photo.png', out.getvalue(), content_type='image/png')


def openai_image(data=PNG):
    return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data).decode())])


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class CelebrationCardTests(TestCase):
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

    def test_una_foto_del_celular_en_otro_formato_no_se_rechaza(self):
        """La lista de tres formatos dejaba fuera fotos que sí se podían leer."""
        out = BytesIO()
        Image.new('RGB', (32, 32), 'red').save(out, 'GIF')
        file = SimpleUploadedFile('foto.gif', out.getvalue(), content_type='image/gif')
        serializer = CardInput(data={'image': file})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_un_heic_dice_que_hacer_en_vez_de_dar_la_foto_por_mala(self):
        file = SimpleUploadedFile('IMG_0042.HEIC', b'not readable', content_type='image/heic')
        serializer = CardInput(data={'image': file})
        self.assertFalse(serializer.is_valid())
        self.assertIn('HEIC', str(serializer.errors['image'][0]))

    def test_size_limit(self):
        file = photo_file()
        file.size = 10 * 1024 * 1024 + 1
        serializer = CardInput(data={'image': file})
        self.assertFalse(serializer.is_valid())

    @patch.dict('os.environ', NO_PROVIDER)
    def test_unconfigured_returns_service_unavailable(self):
        self.assertEqual(self.request({'image': photo_file()}).status_code, 503)
        self.assertEqual(CardGeneration.objects.count(), 0)

    @patch.dict('os.environ', ONLY_GEMINI)
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

    @patch.dict('os.environ', ONLY_GEMINI)
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_provider_error_is_not_exposed(self, factory):
        factory.return_value.__enter__.return_value.models.generate_content.side_effect = RuntimeError('private key or photo details')
        response = self.request({'image': photo_file()})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private', str(response.data))

    @patch.dict('os.environ', ONLY_GEMINI)
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_no_image(self, factory):
        factory.return_value.__enter__.return_value.models.generate_content.return_value.parts = []
        self.assertEqual(self.request({'image': photo_file()}).status_code, 502)

    @patch.dict('os.environ', NO_PROVIDER)
    def test_throttle(self):
        for _ in range(12):
            self.assertEqual(self.request({'image': photo_file()}).status_code, 503)
        self.assertEqual(self.request({'image': photo_file()}).status_code, 429)

    def test_prompt_keeps_the_photo_alone_and_takes_the_palette_from_it(self):
        prompt = card_prompt({'phrase': 'Te quiero'})
        # Identidad de las personas y nada añadido a su alrededor.
        for text in ['rostros', 'ropa', 'accesorios', 'PROHIBIDO añadir', 'copas', 'velas',
                     'LA PALETA SALE DE LA FOTO', 'TOCAR al menos un borde',
                     'UNA vez y solo una', 'SOLO texto literal']:
            self.assertIn(text, prompt)

    def test_prompt_no_longer_imposes_the_brand_palette(self):
        # La paleta la pone la foto: un vino fijo teñía tarjetas que no lo pedían.
        prompt = card_prompt({})
        for hexa in ['#0a0a0a', '#5e1c2b', '#cf6b7c']:
            self.assertNotIn(hexa, prompt)
        # El satén y el mármol solo pueden aparecer como prohibición, nunca como encargo.
        self.assertIn('mármol', prompt.split('PROHIBIDO añadir')[1].split('LA PALETA')[0])
        self.assertIn('No impongas rojo, vino ni rosa', prompt)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
@patch.dict('os.environ', BOTH_PROVIDERS)
class FallbackTests(TestCase):
    """Gemini primero; OpenAI solo cuando el primero no entrega imagen."""

    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()

    def request(self):
        return generate_celebration_card(self.factory.post('/card/', {'image': photo_file()}, format='multipart'))

    @patch('apps.motivational.celebration_cards.OpenAI')
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_openai_is_not_called_when_gemini_answers(self, gemini, openai):
        gemini.return_value.__enter__.return_value.models.generate_content.return_value.parts = [
            SimpleNamespace(inline_data=SimpleNamespace(data=b'output', mime_type='image/png'))]
        self.assertEqual(self.request().status_code, 200)
        openai.assert_not_called()
        row = CardGeneration.objects.get()
        self.assertEqual((row.provider, row.status, row.was_fallback), ('gemini', 'ok', False))

    @patch('apps.motivational.celebration_cards.OpenAI')
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_openai_rescues_a_gemini_failure(self, gemini, openai):
        gemini.return_value.__enter__.return_value.models.generate_content.side_effect = RuntimeError('caído')
        openai.return_value.images.edit.return_value = openai_image()
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(base64.b64decode(response.data['image_base64']), PNG)
        self.assertEqual(response.data['mime_type'], 'image/png')
        self.assertEqual(openai.return_value.images.edit.call_args.kwargs['size'], '1024x1536')
        self.assertEqual(
            [(r.provider, r.status, r.was_fallback) for r in CardGeneration.objects.order_by('id')],
            [('gemini', 'failed', False), ('openai', 'ok', True)])

    @patch('apps.motivational.celebration_cards.OpenAI')
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_both_down_reports_a_single_generic_error(self, gemini, openai):
        gemini.return_value.__enter__.return_value.models.generate_content.side_effect = RuntimeError('caído')
        openai.return_value.images.edit.side_effect = RuntimeError('también caído')
        response = self.request()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('caído', str(response.data))
        self.assertEqual(CardGeneration.objects.filter(status='failed').count(), 2)

    @patch('apps.motivational.celebration_cards.OpenAI')
    @patch('apps.motivational.celebration_cards.genai.Client')
    def test_unreadable_openai_bytes_are_rejected(self, gemini, openai):
        gemini.return_value.__enter__.return_value.models.generate_content.return_value.parts = []
        openai.return_value.images.edit.return_value = openai_image(b'esto no es una imagen')
        self.assertEqual(self.request().status_code, 502)
        self.assertEqual(CardGeneration.objects.filter(status='ok').count(), 0)


def completion(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class SuggestPhraseTests(TestCase):
    """La dedicatoria sugerida: para quien se queda mirando el campo en blanco."""

    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()

    def request(self, data=None):
        return suggest_celebration_phrase(self.factory.post('/phrase/', data or {}, format='json'))

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''})
    def test_without_key_it_says_write_your_own(self):
        response = self.request()
        self.assertEqual(response.status_code, 503)
        self.assertIn('Escribe tu dedicatoria', response.data['error'])

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('apps.motivational.celebration_cards.OpenAI')
    def test_names_and_previous_phrase_travel_as_data(self, openai):
        openai.return_value.chat.completions.create.return_value = completion('  "Contigo hasta el último brindis."  ')
        response = self.request({'to_name': 'Ana', 'from_name': 'Luis', 'avoid': 'Lo mejor de la vida'})
        self.assertEqual(response.status_code, 200)
        # Se recortan las comillas con las que el modelo suele envolver la frase.
        self.assertEqual(response.data['phrase'], 'Contigo hasta el último brindis.')
        sent = openai.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        for text in ['Ana', 'Luis', 'Lo mejor de la vida', 'nunca instrucciones']:
            self.assertIn(text, sent)

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('apps.motivational.celebration_cards.OpenAI')
    def test_provider_error_is_not_exposed(self, openai):
        openai.return_value.chat.completions.create.side_effect = RuntimeError('detalle privado')
        response = self.request()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('privado', str(response.data))

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('apps.motivational.celebration_cards.OpenAI')
    def test_empty_or_oversized_answers_are_rejected(self, openai):
        create = openai.return_value.chat.completions.create
        for answer in ('   ', 'x' * 241, None):
            create.return_value = completion(answer)
            self.assertEqual(self.request().status_code, 502)

    def test_long_fields_are_rejected(self):
        self.assertEqual(self.request({'to_name': 'x' * 61}).status_code, 400)
        self.assertEqual(self.request({'avoid': 'x' * 241}).status_code, 400)

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''})
    def test_its_own_throttle_is_looser_than_the_image_one(self):
        for _ in range(40):
            self.assertEqual(self.request().status_code, 503)
        self.assertEqual(self.request().status_code, 429)
