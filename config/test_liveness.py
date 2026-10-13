from unittest.mock import patch

from django.test import SimpleTestCase

from config import liveness


class _Basta(Exception):
    """Corta el bucle infinito del vigía dentro del test."""


def _dormir_n(veces):
    llamadas = {"n": 0}

    def dormir(_segundos):
        llamadas["n"] += 1
        if llamadas["n"] > veces:
            raise _Basta

    return dormir


class VigiaDeVidaTests(SimpleTestCase):
    def test_healthz_responde_sin_tocar_la_base(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_muere_tras_los_fallos_seguidos(self):
        muertes = []
        liveness._vigilar(
            "http://x/healthz/",
            dormir=_dormir_n(100),
            responde=lambda url: False,
            morir=lambda: muertes.append(1),
        )
        self.assertEqual(muertes, [1])

    def test_un_acierto_reinicia_la_cuenta(self):
        # falla, falla, falla, responde, falla, falla, falla: nunca 4 seguidos
        respuestas = iter([False, False, False, True, False, False, False])
        muertes = []
        with self.assertRaises(_Basta):
            liveness._vigilar(
                "http://x/healthz/",
                dormir=_dormir_n(7),
                responde=lambda url: next(respuestas),
                morir=lambda: muertes.append(1),
            )
        self.assertEqual(muertes, [])

    def test_sin_port_no_arranca(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(liveness.start())

    def test_fuera_de_daphne_no_arranca_aunque_haya_port(self):
        """Un test o un manage.py que importe config.asgi con PORT heredado."""
        with patch.dict("os.environ", {"PORT": "8080"}), patch(
            "sys.argv", ["manage.py", "test"]
        ), patch("threading.Thread.start") as arrancar:
            self.assertIsNone(liveness.start())
        arrancar.assert_not_called()

    def test_en_daphne_arranca(self):
        with patch.dict("os.environ", {"PORT": "8080"}), patch(
            "sys.argv", ["/opt/venv/bin/daphne", "-p", "8080"]
        ), patch("threading.Thread.start") as arrancar:
            self.assertIsNotNone(liveness.start())
        arrancar.assert_called_once()

    def test_se_puede_apagar(self):
        with patch.dict("os.environ", {"PORT": "8080", "DISABLE_LIVENESS": "1"}):
            self.assertIsNone(liveness.start())
