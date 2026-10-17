from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import SongRequest
from .stats import build_stats


def pedida(cuando, floor=2):
    row = SongRequest.objects.create(
        floor=floor, song_name="x", artist_name="y", spotify_track_uri=f"uri:{cuando}",
    )
    then = timezone.make_aware(cuando)
    SongRequest.objects.filter(pk=row.pk).update(created_at=then, updated_at=then)
    return row


class RangoPorNocheTests(TestCase):
    """Un rango de fechas trae noches enteras: el corte es a las 6 a. m., no a medianoche."""

    def setUp(self):
        pedida(datetime(2026, 10, 10, 5, 30))  # madrugada: es de la noche del 9
        pedida(datetime(2026, 10, 10, 21, 0))  # noche del 10
        pedida(datetime(2026, 10, 11, 1, 30))  # madrugada: sigue siendo la noche del 10
        pedida(datetime(2026, 10, 11, 6, 0))  # ya es el día 11

    def test_un_solo_dia_trae_la_noche_completa(self):
        stats = build_stats(start=date(2026, 10, 10), end=date(2026, 10, 10))
        self.assertEqual(stats["summary"]["total"], 2)
        self.assertEqual(stats["summary"]["nights"], 1)
        self.assertEqual((stats["range"]["start"], stats["range"]["end"]), ("2026-10-10", "2026-10-10"))

    def test_rango_de_varios_dias(self):
        stats = build_stats(start=date(2026, 10, 9), end=date(2026, 10, 11))
        self.assertEqual(stats["summary"]["total"], 4)
        self.assertEqual(stats["summary"]["nights"], 3)

    def test_vista_ordena_un_rango_invertido(self):
        user = get_user_model().objects.create_user(username="staff", password="x")
        client = APIClient()
        client.force_authenticate(user)
        res = client.get(reverse("music-stats"), {"start": "2026-10-10", "end": "2026-10-09"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["range"]["start"], "2026-10-09")
        self.assertEqual(res.data["summary"]["total"], 3)
