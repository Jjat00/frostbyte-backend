from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Cuentas de Usuario"

    def ready(self):
        # Registra el transform ``__plain`` (búsqueda sin tildes ni mayúsculas)
        # en CharField/TextField antes de que corra cualquier consulta.
        import apps.search  # noqa: F401
