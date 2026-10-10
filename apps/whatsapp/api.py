"""API del módulo de Frosty dentro del panel de Frostbyte.

Separada del webhook a propósito: `views.py` es lo que Kapso y Meta llaman sin
autenticación, y esto es lo contrario —solo el dueño, con su token del panel—.

Todo lo de aquí ya existía en el admin de Django; la diferencia es que el
panel se abre desde el celular, que es donde está el dueño cuando quiere
cambiarle el tono al agente o subirle un sticker.
"""

from django.db.models import OuterRef, Subquery
from django.db.models.functions import Left
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminUser, IsStaffMember
from apps.search import PlainSearchFilter
from config.pagination import StandardResultsPagination

from .models import AgentSettings, AgentTone, ChatMessage, Sticker, WhatsAppContact
from .serializers import (
    AgentSettingsSerializer,
    AgentToneSerializer,
    ChatMessageSerializer,
    ConversationSerializer,
    StickerSerializer,
)
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


class AgentToneViewSet(viewsets.ModelViewSet):
    """El catálogo de personalidades: crear, afinar y descartar tonos.

    Lo que se edita aquí es texto de prompt —entra tal cual en el bloque QUIÉN
    ERES—, así que el catálogo se protege por los dos extremos: no se borra el
    tono con el que el agente está hablando ahora mismo, ni el último que
    quede. Quedarse sin catálogo sería quedarse sin manera de elegir.
    """

    queryset = AgentTone.objects.all()
    serializer_class = AgentToneSerializer
    permission_classes = [IsAdminUser]
    pagination_class = None

    def destroy(self, request, *args, **kwargs):
        tone = self.get_object()
        if AgentSettings.load().tone_preset == tone.key:
            return Response(
                {
                    "detail": (
                        f"«{tone.name}» es el tono con el que habla ahora mismo. Elige otro "
                        "antes de borrarlo."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if AgentTone.objects.count() <= 1:
            return Response(
                {"detail": "Es el único tono que queda: crea otro antes de borrar este."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Devuelve un tono de fábrica al texto con el que vino.

        Solo tiene sentido en los de fábrica: de los demás no hay original al
        que volver, y decir que sí sin hacer nada sería peor que negarse.
        """
        tone = self.get_object()
        if not tone.restore():
            return Response(
                {"detail": "Este tono lo creaste tú: no hay un original al que volver."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(tone).data)


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


# Lo que trae cada vistazo al chat. Una conversación de un pedido cabe de
# sobra; lo anterior se pide hacia atrás con ?before=.
MESSAGES_PAGE = 60


class ConversationPagination(StandardResultsPagination):
    page_size = 30


class ConversationViewSet(viewsets.ReadOnlyModelViewSet):
    """Los chats de WhatsApp para el equipo: la bandeja y cada conversación.

    Solo lectura y abierta a admin y empleados: quien está en turno necesita
    ver qué le prometió Frosty al cliente o qué le contestó un compañero, pero
    responder sigue siendo cosa de la app de WhatsApp Business (un mensaje que
    saliera de aquí no pasaría por la pausa humana ni por el agente).

    El archivo es `ChatMessage`, que se indexa por teléfono y no por FK: se
    une al contacto por `phone[:30]`, que es lo que cabe en esa columna.
    """

    serializer_class = ConversationSerializer
    permission_classes = [IsStaffMember]
    pagination_class = ConversationPagination
    filter_backends = [PlainSearchFilter]
    search_fields = ["customer_name", "profile_name", "username", "phone", "contact_phone"]

    def get_queryset(self):
        if self.action != "list":
            return WhatsAppContact.objects.all()
        last = ChatMessage.objects.filter(phone=OuterRef("phone_key")).order_by("-created_at")
        return (
            WhatsAppContact.objects.filter(last_message_at__isnull=False)
            .annotate(phone_key=Left("phone", 30))
            .annotate(
                last_body=Subquery(last.values("body")[:1]),
                last_author=Subquery(last.values("author")[:1]),
            )
            .order_by("-last_message_at")
        )

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        """Los últimos mensajes del chat en orden de lectura (viejo → nuevo).

        `?before=<fecha ISO>` trae los anteriores a esa fecha, para el botón
        de "ver mensajes anteriores". Junto con los mensajes van los pedidos
        de ese teléfono, que es lo segundo que se busca al leer el chat.
        """
        contact = get_object_or_404(WhatsAppContact, pk=pk)
        qs = ChatMessage.objects.filter(phone=contact.phone[:30])
        before = parse_datetime(request.query_params.get("before") or "")
        if before is not None:
            qs = qs.filter(created_at__lt=before)
        page = list(qs.order_by("-created_at")[: MESSAGES_PAGE + 1])
        has_more = len(page) > MESSAGES_PAGE
        page = list(reversed(page[:MESSAGES_PAGE]))
        return Response(
            {
                "contact": ConversationSerializer(contact).data,
                "messages": ChatMessageSerializer(page, many=True).data,
                "has_more": has_more,
                "orders": _orders_of(contact),
            }
        )


def _orders_of(contact, limit=5):
    """Los últimos pedidos de ese cliente, sin importar quién los creó.

    Mismo criterio que el admin (`pedidos_del_cliente`): por los últimos 10
    dígitos del teléfono, que es como quedan también los que el equipo cierra
    a mano. Un BSUID no tiene dígitos que comparar; ahí se usa el celular que
    dio el cliente, si lo dio.
    """
    from apps.orders.models import Order

    from .kapso import is_bsuid
    from .tools import normalize_phone

    source = contact.contact_phone if is_bsuid(contact.phone) else contact.phone
    digits = normalize_phone(source or "")[-10:]
    if len(digits) < 10:
        return []
    pedidos = Order.objects.filter(customer_phone__endswith=digits).order_by("-created_at")[:limit]
    return [
        {
            "id": o.id,
            "order_number": o.order_number,
            "status": o.status,
            "status_display": o.get_status_display(),
            "order_type_display": o.get_order_type_display(),
            "total": str(o.total),
            "created_at": o.created_at,
        }
        for o in pedidos
    ]
