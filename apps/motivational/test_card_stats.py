from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .card_stats import build_card_stats
from .models import CardGeneration

URL = "/api/v1/motivational/celebration-card/stats/"


def card(provider=CardGeneration.GEMINI, status=CardGeneration.OK, was_fallback=False, days_ago=0):
    row = CardGeneration.objects.create(provider=provider, status=status, was_fallback=was_fallback)
    if days_ago:
        CardGeneration.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago))
    return row


class CardStatsTests(APITestCase):
    def test_counts_separate_deliveries_from_attempts(self):
        card()
        card(status=CardGeneration.FAILED)
        card(provider=CardGeneration.OPENAI, was_fallback=True)

        stats = build_card_stats()
        self.assertEqual(stats["generated"], 2)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["total_attempts"], 3)
        gemini, openai = stats["by_provider"]
        self.assertEqual((gemini["generated"], gemini["failed"]), (1, 1))
        self.assertEqual((openai["generated"], openai["as_fallback"]), (1, 1))

    def test_window_counters_ignore_the_range(self):
        card(days_ago=40)
        card()
        stats = build_card_stats(days_param="7")
        # El rango recorta el detalle, pero el histórico sigue completo.
        self.assertEqual(stats["generated"], 1)
        self.assertEqual(stats["all_time"], 2)
        self.assertEqual(stats["last_7_days"], 1)
        self.assertEqual(len(stats["daily"]), 1)

    def test_endpoint_is_for_the_team_only(self):
        self.assertEqual(self.client.get(URL).status_code, 401)
        user = get_user_model().objects.create_user(
            username="staff", email="staff@frostbyte.test", password="clave-de-prueba")
        self.client.force_authenticate(user=user)
        card()
        self.assertEqual(self.client.get(URL).data["generated"], 1)
