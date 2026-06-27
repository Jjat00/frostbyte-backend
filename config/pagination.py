from rest_framework.pagination import PageNumberPagination


class StandardResultsPagination(PageNumberPagination):
    """Paginación por defecto que además permite pedir un tamaño mayor.

    Mantiene el comportamiento previo (50 por página) pero deja que un cliente
    pida más con ?page_size=N (tope max_page_size). Lo usa, por ejemplo, la
    pantalla de tomar pedido para cargar TODOS los productos de los dos
    negocios en una sola página.
    """

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 1000
