from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Serializer para ver información del usuario."""

    role_display = serializers.CharField(source="get_role_display", read_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "role_display",
            "phone",
            "provider",
            "avatar_url",
            "email_opt_out",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "provider", "avatar_url", "created_at"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Campos que un usuario puede editar de SU propio perfil (PATCH me).

    Nunca role/email/username/is_active: el perfil es autoservicio y no
    puede servir para escalar privilegios.
    """

    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "email_opt_out"]


class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear usuarios (solo admin)."""

    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "phone",
            "password",
            "password_confirm",
        ]

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Las contraseñas no coinciden."}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer para cambiar contraseña."""

    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(
        required=True,
        write_only=True,
        validators=[validate_password],
    )

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Contraseña actual incorrecta.")
        return value


class LoginSerializer(serializers.Serializer):
    """Serializer para login."""

    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class GoogleAuthSerializer(serializers.Serializer):
    """Serializer para login/registro de clientes con Google.

    Recibe el ``credential`` (id_token JWT) que devuelve Google Identity
    Services en el frontend.
    """

    credential = serializers.CharField(required=True, write_only=True)

