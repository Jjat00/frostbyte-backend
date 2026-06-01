"""Señales de la Polla.

Garantizan que todo cliente que inicia sesión aparezca en el ranking de
inmediato (con 0 puntos), aunque todavía no haya hecho ningún pronóstico.
"""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserScore


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="polla_ensure_user_score")
def ensure_user_score(sender, instance, created, **kwargs):
    """Crea el ``UserScore`` (en 0) cuando se registra un cliente nuevo.

    Solo aplica a usuarios con rol ``customer`` (los que entran por Google a
    la Polla); el personal interno no participa del ranking.
    """
    if not created:
        return
    if getattr(instance, "role", None) != "customer":
        return
    UserScore.objects.get_or_create(user=instance)
