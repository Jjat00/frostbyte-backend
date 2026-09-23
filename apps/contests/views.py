"""API de concursos.

Dos superficies, como en reservas:
- Cliente (/contests/current/...): la página pública del concurso vigente y
  la inscripción propia, que exige sesión de Google.
- Staff (/contests/admin/...): la lista de inscritos para marcar en la barra
  el pago y el seguimiento en Instagram, y la configuración del concurso.
"""
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.accounts.permissions import IsStaffMember

from .models import Contest, ContestEntry
from .serializers import (
    EntryCreateSerializer,
    MyEntrySerializer,
    PublicContestSerializer,
    StaffContestSerializer,
    StaffEntrySerializer,
    StaffEntryUpdateSerializer,
)


class ContestEntryThrottle(UserRateThrottle):
    """Anti-spam de inscripciones; leer la propia no cuenta."""
    scope = "contest_entries"
    rate = "10/hour"

    def allow_request(self, request, view):
        if request.method != "POST":
            return True
        return super().allow_request(request, view)


def _public_contest():
    contest = Contest.current_public()
    if not contest:
        raise Http404("No hay concurso vigente.")
    return contest


def _active_entry(contest, user):
    return (
        ContestEntry.objects
        .filter(contest=contest, user=user, cancelled=False)
        .first()
    )


# ---------------------------------------------------------------- cliente

@api_view(["GET"])
@permission_classes([AllowAny])
def current_contest(request):
    """Concurso vigente, o null si no hay ninguno publicado.

    Sin 404 a propósito: la carta lo consulta en cada visita para decidir si
    pinta el anuncio, y "no hay concurso" es el estado normal."""
    contest = Contest.current_public()
    return Response(PublicContestSerializer(contest).data if contest else None)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([ContestEntryThrottle])
def my_entry(request):
    """GET: mi inscripción al concurso vigente (null si no tengo).
    POST: me inscribo. Queda pendiente hasta que el staff marque el pago y
    el seguimiento en Instagram."""
    contest = _public_contest()

    if request.method == "GET":
        entry = _active_entry(contest, request.user)
        return Response(MyEntrySerializer(entry).data if entry else None)

    if not contest.registrations_open:
        raise ValidationError("Las inscripciones están cerradas.")
    if _active_entry(contest, request.user):
        raise ValidationError("Ya estás inscrito en este concurso.")

    serializer = EntryCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        with transaction.atomic():
            # Bloquea el concurso para que dos inscripciones simultáneas no
            # saquen el mismo número
            Contest.objects.select_for_update().get(pk=contest.pk)
            last = contest.entries.aggregate(n=Max("number"))["n"] or 0
            entry = serializer.save(
                contest=contest, user=request.user, number=last + 1)
    except IntegrityError:
        raise ValidationError("Ya estás inscrito en este concurso.")

    # El celular queda en el perfil para no volver a escribirlo
    if not request.user.phone:
        request.user.phone = entry.phone
        request.user.save(update_fields=["phone"])

    return Response(MyEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_my_entry(request):
    """Cancela mi inscripción mientras no esté pagada."""
    contest = _public_contest()
    entry = _active_entry(contest, request.user)
    if not entry:
        raise ValidationError("No tienes una inscripción activa.")
    if entry.paid:
        raise ValidationError(
            "Tu inscripción ya está pagada: para cancelarla habla con la barra.")
    entry.cancelled = True
    entry.save(update_fields=["cancelled"])
    return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ staff

def _staff_contest():
    contest = Contest.current_for_staff()
    if not contest:
        raise Http404("No hay concursos creados.")
    return contest


@api_view(["GET"])
@permission_classes([IsStaffMember])
def staff_overview(request):
    """Concurso vigente con todos sus inscritos (cancelados al final)."""
    contest = _staff_contest()
    entries = (
        contest.entries
        .select_related("user", "paid_by", "instagram_checked_by")
        .order_by("cancelled", "number")
    )
    counts = {s: 0 for s in ContestEntry.Status.values}
    for e in entries:
        counts[e.status] += 1
    return Response({
        "contest": StaffContestSerializer(contest).data,
        "counts": counts,
        "entries": StaffEntrySerializer(entries, many=True).data,
    })


@api_view(["PATCH"])
@permission_classes([IsStaffMember])
def staff_update_contest(request):
    """Abre/cierra inscripciones y edita fecha, premio o valor (solo admin)."""
    if not request.user.is_admin:
        return Response(
            {"detail": "Solo un administrador cambia la configuración."},
            status=status.HTTP_403_FORBIDDEN,
        )
    contest = _staff_contest()
    serializer = StaffContestSerializer(contest, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(["PATCH"])
@permission_classes([IsStaffMember])
def staff_update_entry(request, pk):
    """Marca pago, seguimiento en Instagram, cancelación o notas."""
    try:
        entry = ContestEntry.objects.select_related("contest").get(pk=pk)
    except ContestEntry.DoesNotExist:
        raise Http404
    serializer = StaffEntryUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    if "paid" in data and data["paid"] != entry.paid:
        entry.set_paid(data["paid"], request.user)
    if "follows_instagram" in data and data["follows_instagram"] != entry.follows_instagram:
        entry.set_follows_instagram(data["follows_instagram"], request.user)
    if "cancelled" in data:
        entry.cancelled = data["cancelled"]
    if "staff_notes" in data:
        entry.staff_notes = data["staff_notes"]

    try:
        with transaction.atomic():
            entry.save()
    except IntegrityError:
        raise ValidationError(
            "Esta persona ya tiene otra inscripción activa: no se puede reactivar.")

    entry.refresh_from_db()
    return Response(StaffEntrySerializer(entry).data)
