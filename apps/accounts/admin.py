from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth import get_user_model

User = get_user_model()


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = [
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "is_active",
        "created_at",
    ]
    list_filter = ["role", "provider", "is_active", "is_staff", "created_at"]
    search_fields = ["username", "email", "first_name", "last_name", "google_sub"]
    ordering = ["-created_at"]
    readonly_fields = ["provider", "google_sub", "avatar_url"]

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Información adicional", {"fields": ("role", "phone")}),
        ("Cuenta externa", {"fields": ("provider", "google_sub", "avatar_url")}),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Información adicional", {"fields": ("role", "phone", "first_name", "last_name", "email")}),
    )

