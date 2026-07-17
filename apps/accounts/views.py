from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate, get_user_model

from .serializers import (
    UserSerializer,
    UserCreateSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    GoogleAuthSerializer,
    ProfileUpdateSerializer,
)
from .google_auth import (
    GoogleAuthError,
    verify_google_credential,
    get_or_create_google_user,
)

User = get_user_model()


class IsAdminUser(permissions.BasePermission):
    """Permiso que solo permite acceso a administradores."""

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_admin


class IsAdminOrReadOnly(permissions.BasePermission):
    """Permiso que permite lectura a todos los autenticados, escritura solo a admins."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_admin


class LoginView(APIView):
    """Vista para iniciar sesión y obtener tokens JWT."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )

        if not user:
            return Response(
                {"error": "Credenciales inválidas"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": "Usuario desactivado"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "user": UserSerializer(user).data,
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
            }
        )


class GoogleLoginView(APIView):
    """Login/registro de clientes con Google.

    Recibe el ``credential`` (id_token) de Google Identity Services, lo
    verifica, resuelve o crea el usuario cliente y devuelve el mismo shape
    que ``LoginView``: ``{user, tokens}``.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            idinfo = verify_google_credential(
                serializer.validated_data["credential"]
            )
            user, _created = get_or_create_google_user(idinfo)
        except GoogleAuthError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": "Usuario desactivado"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "user": UserSerializer(user).data,
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
            }
        )


class LogoutView(APIView):
    """Vista para cerrar sesión (invalidar refresh token)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            return Response({"message": "Sesión cerrada exitosamente"})
        except Exception:
            return Response({"message": "Sesión cerrada"})


class MeView(APIView):
    """Vista para obtener información del usuario actual."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    def patch(self, request):
        # Solo campos de autoservicio: nunca role/email/username/is_active
        serializer = ProfileUpdateSerializer(
            request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class UserViewSet(viewsets.ModelViewSet):
    """ViewSet para gestión de usuarios (solo admin)."""

    queryset = User.objects.all()
    permission_classes = [IsAdminUser]

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        return UserSerializer

    def get_queryset(self):
        queryset = User.objects.all()
        role = self.request.query_params.get("role")
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    @action(detail=True, methods=["post"])
    def toggle_active(self, request, pk=None):
        """Activar/desactivar usuario."""
        user = self.get_object()
        user.is_active = not user.is_active
        user.save()
        return Response(
            {
                "message": f"Usuario {'activado' if user.is_active else 'desactivado'}",
                "is_active": user.is_active,
            }
        )

    @action(detail=True, methods=["post"])
    def reset_password(self, request, pk=None):
        """Reset de contraseña por admin."""
        user = self.get_object()
        new_password = request.data.get("new_password")
        if not new_password:
            return Response(
                {"error": "Se requiere new_password"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(new_password)
        user.save()
        return Response({"message": "Contraseña actualizada"})


class ChangePasswordView(APIView):
    """Vista para que el usuario cambie su propia contraseña."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save()
        return Response({"message": "Contraseña cambiada exitosamente"})

