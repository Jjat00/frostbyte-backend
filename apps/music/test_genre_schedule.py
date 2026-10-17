from datetime import datetime

from django.test import SimpleTestCase
from django.utils import timezone

from .genre_schedule import next_run


def local(*args):
    return timezone.make_aware(datetime(*args))


class ProximaCorridaTests(SimpleTestCase):
    def test_desde_un_sabado_va_al_lunes_siguiente(self):
        self.assertEqual(next_run(local(2026, 10, 17, 16, 0)), local(2026, 10, 19, 10, 0))

    def test_lunes_antes_de_la_hora_es_ese_mismo_dia(self):
        self.assertEqual(next_run(local(2026, 10, 19, 9, 59)), local(2026, 10, 19, 10, 0))

    def test_lunes_a_la_hora_justa_pasa_a_la_semana_siguiente(self):
        self.assertEqual(next_run(local(2026, 10, 19, 10, 0)), local(2026, 10, 26, 10, 0))
