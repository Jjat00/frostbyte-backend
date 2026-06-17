from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Modelo de usuario personalizado con roles."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrador"
        EMPLOYEE = "employee", "Empleado"
        CUSTOMER = "customer", "Cliente"

    class Provider(models.TextChoices):
        LOCAL = "local", "Local"
        GOOGLE = "google", "Google"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.EMPLOYEE,
        verbose_name="Rol",
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name="Teléfono",
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.LOCAL,
        verbose_name="Proveedor de autenticación",
    )
    google_sub = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
        verbose_name="Google ID (sub)",
        help_text="Identificador único de la cuenta de Google",
    )
    avatar_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        verbose_name="Foto de perfil",
    )
    email_opt_out = models.BooleanField(
        default=False,
        verbose_name="No recibir correos",
        help_text="Si está activo, el usuario se dio de baja de los correos de la Polla.",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de creación")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última actualización")

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_employee(self):
        return self.role == self.Role.EMPLOYEE

    @property
    def is_customer(self):
        return self.role == self.Role.CUSTOMER

    @property
    def is_staff_member(self):
        """True para personal interno (admin o empleado), no para clientes."""
        return self.role in (self.Role.ADMIN, self.Role.EMPLOYEE) or self.is_superuser

