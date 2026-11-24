"""Pruebas de los mensajes que Meta rechaza después de aceptarlos (rechazos.py).

Lo que no se puede romper: que el rechazo quede anotado, que al dueño le
llegue por la otra vía (texto -> plantilla, plantilla -> texto) una sola vez,
y que a un cliente no se le reintente nada.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from . import rechazos
from .models import AgentSettings, SentMessage, WebhookEvent

DUENO = "573001112233"
CLIENTE = "573009998877"


def _rechazo(wamid, to, kind="text", code=131047, content="🔔 Ana (57300) está esperando a una persona.\nMotivo: pide domicilio"):
    """Webhook de rechazo con la forma que manda Kapso (visto en producción)."""
    message = {
        "id": wamid,
        "to": to,
        "type": kind,
        "kapso": {
            "origin": "cloud_api",
            "status": "failed",
            "content": content,
            "direction": "outbound",
            "statuses": [
                {
                    "id": wamid,
                    "status": "failed",
                    "errors": [
                        {
                            "code": code,
                            "title": "Re-engagement message",
                            "error_data": {"details": "more than 24 hours have passed"},
                        }
                    ],
                }
            ],
        },
    }
    if kind == "text":
        message["text"] = {"body": content}
    return {"message": message, "phone_number_id": "PNID"}


def _envio(wamid):
    """Lo que devuelve Kapso al aceptar un envío; registra el wamid como haría _post_message."""

    def send(*args, fallback=False):
        SentMessage.objects.create(wamid=wamid, to_phone=DUENO, is_fallback=fallback)
        return {"messages": [{"id": wamid}]}

    return send


@override_settings(KAPSO_PHONE_NUMBER_IDS=["PNID"], WHATSAPP_OWNER_ALERT_TEMPLATE="aviso_dueno")
@patch("apps.whatsapp.rechazos.close_old_connections", lambda: None)
@patch("apps.whatsapp.rechazos.time.sleep")
class RechazosTests(TestCase):
    def setUp(self):
        config = AgentSettings.load()
        config.owner_phones = DUENO
        config.save()

    def test_extrae_solo_los_rechazados(self, _sleep):
        enviado = _rechazo("wamid.B", DUENO)
        enviado["message"]["kapso"]["status"] = "sent"
        enviado["message"]["kapso"]["statuses"] = []
        fallos = rechazos.extract_failures([_rechazo("wamid.A", DUENO), enviado])
        self.assertEqual(len(fallos), 1)
        self.assertEqual(fallos[0]["wamid"], "wamid.A")
        self.assertEqual(fallos[0]["code"], 131047)
        self.assertEqual(fallos[0]["to"], DUENO)
        self.assertIn("está esperando", fallos[0]["content"])

    @patch("apps.whatsapp.rechazos.kapso.send_text")
    @patch("apps.whatsapp.rechazos.kapso.send_template", side_effect=_envio("wamid.R"))
    def test_aviso_de_texto_al_dueno_sale_como_plantilla(self, send_template, send_text, _sleep):
        SentMessage.objects.create(wamid="wamid.A", to_phone=DUENO)
        rechazos.handle_failures(rechazos.extract_failures([_rechazo("wamid.A", DUENO)]))

        send_text.assert_not_called()
        pnid, to, name, language, params = send_template.call_args.args
        self.assertEqual((pnid, to, name), ("PNID", DUENO, "aviso_dueno"))
        # Las variables de plantilla no aceptan saltos de línea.
        self.assertEqual(params, ["🔔 Ana (57300) está esperando a una persona. Motivo: pide domicilio"])

        original = SentMessage.objects.get(wamid="wamid.A")
        self.assertIsNotNone(original.failed_at)
        self.assertEqual(original.error_code, 131047)
        self.assertFalse(original.is_fallback)
        self.assertTrue(SentMessage.objects.get(wamid="wamid.R").is_fallback)

    @patch("apps.whatsapp.rechazos.kapso.send_template")
    @patch("apps.whatsapp.rechazos.kapso.send_text", side_effect=_envio("wamid.R"))
    def test_plantilla_rechazada_sale_como_texto(self, send_text, send_template, _sleep):
        SentMessage.objects.create(wamid="wamid.P", to_phone=DUENO)
        fallo = _rechazo("wamid.P", DUENO, kind="template", code=131042, content="📊 Cierre del sábado\nVentas: $1")
        rechazos.handle_failures(rechazos.extract_failures([fallo]))

        send_template.assert_not_called()
        self.assertEqual(send_text.call_args.args, ("PNID", DUENO, "📊 Cierre del sábado\nVentas: $1"))
        self.assertEqual(send_text.call_args.kwargs, {"fallback": True})

    @patch("apps.whatsapp.rechazos.kapso.send_template", side_effect=_envio("wamid.R"))
    def test_el_mismo_rechazo_repetido_reenvia_una_vez(self, send_template, _sleep):
        SentMessage.objects.create(wamid="wamid.A", to_phone=DUENO)
        for _ in range(3):
            rechazos.handle_failures(rechazos.extract_failures([_rechazo("wamid.A", DUENO)]))
        self.assertEqual(send_template.call_count, 1)

    @patch("apps.whatsapp.rechazos.kapso.send_text")
    @patch("apps.whatsapp.rechazos.kapso.send_template")
    def test_si_rechazan_el_reenvio_no_hay_tercer_intento(self, send_template, send_text, _sleep):
        SentMessage.objects.create(wamid="wamid.R", to_phone=DUENO, is_fallback=True)
        fallo = _rechazo("wamid.R", DUENO, kind="template", code=131042)
        rechazos.handle_failures(rechazos.extract_failures([fallo]))

        send_template.assert_not_called()
        send_text.assert_not_called()
        self.assertEqual(SentMessage.objects.get(wamid="wamid.R").error_code, 131042)

    @patch("apps.whatsapp.rechazos.kapso.send_text")
    @patch("apps.whatsapp.rechazos.kapso.send_template")
    def test_a_un_cliente_solo_se_anota(self, send_template, send_text, _sleep):
        SentMessage.objects.create(wamid="wamid.C", to_phone=CLIENTE)
        rechazos.handle_failures(rechazos.extract_failures([_rechazo("wamid.C", CLIENTE)]))

        send_template.assert_not_called()
        send_text.assert_not_called()
        self.assertIsNotNone(SentMessage.objects.get(wamid="wamid.C").failed_at)

    @patch("apps.whatsapp.rechazos.kapso.send_template")
    def test_lo_que_no_mando_el_backend_no_se_reintenta(self, send_template, _sleep):
        # Un mensaje del inbox o de la app: no tiene fila en SentMessage.
        rechazos.handle_failures(rechazos.extract_failures([_rechazo("wamid.X", DUENO)]))
        send_template.assert_not_called()

    @patch("apps.whatsapp.rechazos.kapso.send_template", side_effect=_envio("wamid.R"))
    def test_el_aviso_largo_se_recorta(self, send_template, _sleep):
        SentMessage.objects.create(wamid="wamid.L", to_phone=DUENO)
        rechazos.handle_failures(rechazos.extract_failures([_rechazo("wamid.L", DUENO, content="x " * 900)]))
        (texto,) = send_template.call_args.args[4]
        self.assertEqual(len(texto), rechazos.MAX_ALERT_CHARS)
        self.assertTrue(texto.endswith("…"))

    @patch("apps.whatsapp.rechazos.kapso.send_template", side_effect=_envio("wamid.R"))
    def test_el_worker_trata_el_rechazo_del_webhook(self, send_template, _sleep):
        from .worker import _process_event

        SentMessage.objects.create(wamid="wamid.A", to_phone=DUENO)
        event = WebhookEvent.objects.create(idempotency_key="rechazo-1", payload=_rechazo("wamid.A", DUENO))
        _process_event(event)

        self.assertEqual(send_template.call_count, 1)
        self.assertIsNotNone(SentMessage.objects.get(wamid="wamid.A").failed_at)
        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.Status.IGNORED)

    @patch("apps.whatsapp.rechazos.kapso.send_template")
    def test_el_rechazo_que_llega_antes_del_registro_espera(self, send_template, sleep):
        # El webhook gana la carrera a _record_sent: durante la espera aparece la fila.
        sleep.side_effect = lambda _s: SentMessage.objects.create(wamid="wamid.T", to_phone=DUENO)
        send_template.side_effect = _envio("wamid.R")
        rechazos.handle_failures(rechazos.extract_failures([_rechazo("wamid.T", DUENO)]))

        sleep.assert_called_once()
        self.assertEqual(send_template.call_count, 1)
        self.assertIsNotNone(SentMessage.objects.get(wamid="wamid.T").failed_at)

    @patch("apps.whatsapp.kapso.requests.post")
    def test_el_reenvio_nace_marcado(self, post, _sleep):
        from . import kapso

        post.return_value.status_code = 200
        post.return_value.json.return_value = {"messages": [{"id": "wamid.F"}]}
        with override_settings(TESTING=False, KAPSO_API_KEY="k"):
            kapso.send_template("PNID", DUENO, "aviso_dueno", "es", ["hola"], fallback=True)
            kapso.send_text("PNID", DUENO, "hola")
        self.assertTrue(SentMessage.objects.get(wamid="wamid.F").is_fallback)
