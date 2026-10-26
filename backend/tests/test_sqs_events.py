"""Pruebas del consumidor de eventos SES vía SQS (canal privado).

Simula la cola SQS con un cliente falso y verifica:
- procesamiento del sobre SNS con firma válida,
- procesamiento de eventos SES directos (raw),
- idempotencia (duplicados ignorados y borrados),
- retención ante firma inválida (no se borra),
- descarte de mensajes venenosos y de cuerpos no JSON,
- confirmación de suscripción SNS,
- lista blanca de tópicos.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.core.config import settings
from app.models import CampaignMessage, SesEvent
from tests.conftest import SessionLocal
# Nota: pytest importa los módulos de prueba como top-level (tests/ sin __init__.py);
# usar el mismo nombre garantiza compartir la clave RSA y evitar módulos duplicados.
from test_webhooks_tracking import _make_sns_notification, _mk_message_with_ses_id, _get_rsa_key

QUEUE_URL = "https://sqs.us-east-2.amazonaws.com/123456789012/hj-campaigns-ses-events"


class FakeSqs:
    """Simulador mínimo del cliente SQS de Boto3."""

    def __init__(self, mensajes: list[dict] | None = None):
        self.incoming = list(mensajes or [])
        self.deleted: list[list[dict]] = []
        self.receives = 0

    def receive_message(self, **kwargs):
        self.receives += 1
        if self.receives == 1:
            return {"Messages": list(self.incoming)}
        return {}

    def delete_message_batch(self, QueueUrl, Entries):
        self.deleted.append(Entries)
        return {"Successful": [{"Id": e["Id"]} for e in Entries]}


def _msg_sqs(cuerpo: str, receive_count: int = 1) -> dict:
    return {
        "MessageId": str(uuid.uuid4()),
        "ReceiptHandle": f"rh-{uuid.uuid4().hex[:10]}",
        "Body": cuerpo,
        "Attributes": {"ApproximateReceiveCount": str(receive_count)},
    }


@pytest.fixture
def sqs_on(monkeypatch):
    monkeypatch.setattr(settings, "AWS_SQS_EVENTS_QUEUE_URL", QUEUE_URL)
    from app.services import events as events_service
    # Aislamiento de la caché de certificados SNS (antes y después de la prueba)
    events_service._CERT_CACHE.clear()
    yield
    events_service._CERT_CACHE.clear()


@pytest.fixture
def firma_local(monkeypatch):
    """Sustituye la descarga del certificado SNS por uno firmado con la clave de pruebas."""
    from app.services import events as events_service

    def fake_fetch(url, max_bytes=65536):
        if not url.startswith("https://sns.us-east-2.amazonaws.com"):
            raise events_service.SNSSecurityError("host no permitido")
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "sns.amazonaws.com")]))
            .issuer_name(x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "sns.amazonaws.com")]))
            .public_key(_get_rsa_key().public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(__import__("datetime").datetime.utcnow() - __import__("datetime").timedelta(days=1))
            .not_valid_after(__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(days=365))
            .sign(_get_rsa_key(), __import__("cryptography.hazmat.primitives.hashes", fromlist=["SHA256"]).SHA256())
        )
        return cert.public_bytes(serialization.Encoding.DER)

    monkeypatch.setattr(events_service, "_safe_fetch", fake_fetch)
    return


def _cleanup_ses_events():
    db = SessionLocal()
    db.query(SesEvent).delete(synchronize_session=False)
    db.commit()
    db.close()


# --------------------------------------------------------------------------- #
# Pruebas
# --------------------------------------------------------------------------- #


def test_sobre_sns_con_firma_valida_se_procesa(client, admin_auth, fake_ses, sqs_on, firma_local):
    cid, mid = _mk_message_with_ses_id(client, admin_auth, fake_ses, f"sq1-{uuid.uuid4().hex[:6]}@test.local")
    ses_id = SessionLocal().get(CampaignMessage, mid).ses_message_id  # type: ignore[union-attr]
    sobre = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": ses_id}})
    fake = FakeSqs([_msg_sqs(json.dumps(sobre))])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["procesados"] == 1
    assert fake.deleted and len(fake.deleted[0]) == 1
    db = SessionLocal()
    assert db.get(CampaignMessage, mid).status == "entregado"  # type: ignore[union-attr]
    db.close()
    _cleanup_ses_events()


def test_evento_directo_sin_sobre_se_procesa(client, admin_auth, fake_ses, sqs_on, firma_local):
    cid, mid = _mk_message_with_ses_id(client, admin_auth, fake_ses, f"sq2-{uuid.uuid4().hex[:6]}@test.local")
    ses_id = SessionLocal().get(CampaignMessage, mid).ses_message_id  # type: ignore[union-attr]
    raw = {"eventType": "delivery", "mail": {"messageId": ses_id}}
    fake = FakeSqs([_msg_sqs(json.dumps(raw))])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["procesados"] == 1
    assert len(fake.deleted) == 1
    db = SessionLocal()
    assert db.get(CampaignMessage, mid).status == "entregado"  # type: ignore[union-attr]
    db.close()
    _cleanup_ses_events()


def test_duplicado_se_ignora_y_se_borra(client, admin_auth, fake_ses, sqs_on, firma_local):
    cid, mid = _mk_message_with_ses_id(client, admin_auth, fake_ses, f"sq3-{uuid.uuid4().hex[:6]}@test.local")
    ses_id = SessionLocal().get(CampaignMessage, mid).ses_message_id  # type: ignore[union-attr]
    sobre = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": ses_id}})
    fake = FakeSqs([_msg_sqs(json.dumps(sobre)), _msg_sqs(json.dumps(sobre))])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["procesados"] == 1
    assert res["duplicados"] == 1
    # Ambos mensajes se borran (el duplicado también, ya fue gestionado)
    assert len(fake.deleted[0]) == 2
    _cleanup_ses_events()


def test_firma_invalida_no_se_borra(client, admin_auth, fake_ses, sqs_on, firma_local):
    import base64
    sobre = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
    sobre["Signature"] = base64.b64encode(b"firma-falsa").decode()
    fake = FakeSqs([_msg_sqs(json.dumps(sobre))])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["errores"] == 1
    assert res["procesados"] == 0
    assert fake.deleted == []  # retenido para reintentar/inspección


def test_mensaje_venenoso_se_descarta(client, sqs_on, firma_local):
    sobre = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
    # Firma manipulada: no es procesable y supera el número máximo de lecturas
    import base64
    sobre["Signature"] = base64.b64encode(b"x").decode()
    fake = FakeSqs([_msg_sqs(json.dumps(sobre), receive_count=settings.SQS_MAX_RECEIVE_COUNT + 1)])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["descartados"] == 1
    assert len(fake.deleted[0]) == 1
    db = SessionLocal()
    marcado = db.query(SesEvent).filter(SesEvent.type == "sqs_descartado").first()
    assert marcado is not None
    db.close()
    _cleanup_ses_events()


def test_cuerpo_no_json_se_descarta(client, sqs_on, firma_local):
    fake = FakeSqs([_msg_sqs("esto no es json")])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["descartados"] == 1
    assert len(fake.deleted[0]) == 1


def test_formato_desconocido_se_descarta(client, sqs_on, firma_local):
    fake = FakeSqs([_msg_sqs(json.dumps({"hello": "world"}))])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["descartados"] == 1


def test_confirmacion_de_suscripcion(client, sqs_on, firma_local):
    sobre = _make_sns_notification({"mensaje": "confirmar"}, msg_type="SubscriptionConfirmation")
    fake = FakeSqs([_msg_sqs(json.dumps(sobre))])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["descartados"] == 1  # gestionada y borrada
    db = SessionLocal()
    ack = db.query(SesEvent).filter(SesEvent.type == "sns_ack").first()
    assert ack is not None
    db.close()
    _cleanup_ses_events()


def test_topico_no_autorizado_se_retiene(client, admin_auth, fake_ses, sqs_on, firma_local, monkeypatch):
    monkeypatch.setattr(settings, "SNS_TOPIC_ARNS", "arn:aws:sns:us-east-2:123456789012:otro-topico")
    sobre = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
    # Firma válida pero tópico distinto al de la lista blanca
    fake = FakeSqs([_msg_sqs(json.dumps(sobre))])

    from app.services import sqs_events
    res = sqs_events.poll_once(fake)

    assert res["errores"] == 1
    assert fake.deleted == []  # no se borra: posible incidente de seguridad


def test_sqs_deshabilitado_no_arranca_hilo():
    from app.services import sqs_events
    monkey = None
    # Sin URL configurada (por defecto en pruebas) no debe crear hilo
    assert sqs_events.start_consumer() is None
    assert monkey is None
