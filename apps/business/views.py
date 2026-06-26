from rest_framework import filters, viewsets

from apps.accounts.permissions import IsAdminOrReadOnly

from .models import Business
from .serializers import BusinessSerializer


class BusinessViewSet(viewsets.ModelViewSet):
    """CRUD de negocios.

    Lectura publica (la usa el menu del cliente para mostrar los negocios),
    escritura solo admin. Sin paginacion: son pocos registros.
    """

    queryset = Business.objects.all()
    serializer_class = BusinessSerializer
    permission_classes = [IsAdminOrReadOnly]
    lookup_field = "slug"
    pagination_class = None
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["display_order", "name"]
    ordering = ["display_order", "name"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        is_admin = bool(user and user.is_authenticated and user.is_admin)
        # Los visitantes no-admin solo ven negocios activos (no exponer un
        # negocio en preparacion antes de su lanzamiento).
        if not is_admin:
            return queryset.filter(is_active=True)
        active_only = self.request.query_params.get("active_only", "false")
        if active_only.lower() == "true":
            queryset = queryset.filter(is_active=True)
        return queryset
