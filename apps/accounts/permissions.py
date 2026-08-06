from rest_framework import permissions


class IsAuthenticated(permissions.IsAuthenticated):
    """
    Allows access only to authenticated users.
    Wrapper around DRF's IsAuthenticated for consistency.
    """
    pass


class IsAdminUser(permissions.BasePermission):
    """
    Allows access only to admin users (authenticated + role admin).
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_admin)


class IsEmployeeOrAdmin(permissions.BasePermission):
    """
    Allows access to authenticated users with role employee or admin.
    Used for operations that any staff member can perform (orders, etc).
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class IsStaffMember(permissions.BasePermission):
    """
    Allows access only to internal staff (admin or employee), never customers.

    A diferencia de IsEmployeeOrAdmin (que solo valida is_authenticated), esta
    clase excluye a los clientes autenticados con Google. Úsala para acciones
    operativas del local (abrir/cerrar, activar domicilios).
    """
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "is_staff_member", False)
        )


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Public read access, write access only for admin users.
    
    - GET, HEAD, OPTIONS: Allow anyone (public)
    - POST, PUT, PATCH, DELETE: Only authenticated admin users
    
    Use this for public-facing content like products catalog.
    """
    def has_permission(self, request, view):
        # Read permissions are allowed for any request (public)
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions only for admin users
        return bool(request.user and request.user.is_authenticated and request.user.is_admin)


class IsEmployeeOrAdminWithAdminWrite(permissions.BasePermission):
    """
    Authenticated employees+admins can read.
    Only admins can write (create/update/delete).

    Use this for internal content that employees need to
    view but only admins can manage.
    """
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_admin


class IsAuthenticatedOrReadOnly(permissions.BasePermission):
    """
    Public read access, write access for any authenticated user.
    
    - GET, HEAD, OPTIONS: Allow anyone (public)
    - POST, PUT, PATCH, DELETE: Only authenticated users
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated)
