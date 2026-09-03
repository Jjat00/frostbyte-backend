"""API del módulo de Frosty dentro del panel de Frostbyte.

Separada del webhook a propósito: `views.py` es lo que Kapso y Meta llaman sin
autenticación, y esto es lo contrario —solo el dueño, con su token del panel—.

Todo lo de aquí ya existía en el admin de Django; la diferencia es que el
panel se abre desde el celular, que es donde está el dueño cuando quiere
cambiarle el tono al agente o subirle un sticker.
"""

from rest_framework import status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminUser

from .models import AgentSettings, Sticker
from .serializers import AgentSettingsSerializer, StickerSerializer
from .stickers import StickerError, from_upload, has_transparency

# Un sticker sale de una imagen o de un video corto grabado en el celular; más
# que esto es un archivo que se subió por error.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class AgentSettingsView(APIView):
    """Configuración del agente (fila única): quién es y qué puede mandar.

    Reservada al admin: es el dueño quien decide cómo habla su negocio, y un
    empleado con el turno abierto no tiene por qué poder cambiarlo.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(AgentSettingsSerializer(AgentSettings.load()).data)

    def patch(self, request):
        serializer = AgentSettingsSerializer(
            AgentSettings.load(), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


def _read_upload(request):
    """El archivo que llegó del formulario, ya en bytes, y de qué tipo es.

    Devuelve (None, None) si no venía ninguno: al editar un sticker existente
    se puede cambiar solo el nombre o el "cuándo usarlo" sin volver a subirlo.
    """
    upload = request.FILES.get("archivo")
    if not upload:
        return None, None
    if upload.size > MAX_UPLOAD_BYTES:
        raise StickerError(
            "El archivo pesa demasiado. Manda una imagen o un video de pocos segundos."
        )
    kind = "video" if (upload.content_type or "").startswith("video/") else "image"
    return upload.read(), kind


class StickerViewSet(viewsets.ModelViewSet):
    """El banco de stickers del agente.

    La subida acepta lo que tenga a mano quien la hace —PNG, JPG, GIF o un
    video corto— y la conversión al WebP que exige Meta ocurre en el servidor:
    quien llena el banco no tiene por qué saber que existe un límite de 100 KB.
    """

    queryset = Sticker.objects.all()
    serializer_class = StickerSerializer
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = None

    def create(self, request, *args, **kwargs):
        try:
            raw, kind = _read_upload(request)
        except StickerError as exc:
            return Response({"archivo": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not raw:
            return Response(
                {"archivo": "Sube la imagen del sticker."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            data, animated = from_upload(raw, kind)
        except StickerError as exc:
            return Response({"archivo": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer.save(data=data, byte_size=len(data), is_animated=animated)
        return Response(
            self._with_warning(serializer.data, raw, animated),
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        try:
            raw, kind = _read_upload(request)
        except StickerError as exc:
            return Response({"archivo": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=kwargs.pop("partial", False)
        )
        serializer.is_valid(raise_exception=True)

        animated = instance.is_animated
        if raw:
            try:
                data, animated = from_upload(raw, kind)
            except StickerError as exc:
                return Response({"archivo": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            serializer.save(data=data, byte_size=len(data), is_animated=animated)
        else:
            serializer.save()
        return Response(self._with_warning(serializer.data, raw, animated))

    @staticmethod
    def _with_warning(data, raw, animated):
        """Avisa del fondo plano sin bloquear la subida.

        Rechazar el archivo por esto sería peor que aceptarlo diciendo cómo va
        a verse: el sticker funciona igual, solo se ve como un cuadro pegado
        sobre el fondo del chat.
        """
        if raw and not animated and not has_transparency(raw):
            data = dict(data)
            data["warning"] = (
                "La imagen no tiene fondo transparente: en el chat se verá como un cuadro "
                "sobre el fondo, no como un sticker. Vuelve a subirla en PNG con "
                "transparencia si quieres arreglarlo."
            )
        return data
