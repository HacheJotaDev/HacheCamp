"""Pruebas de webhook SNS (firma real), tracking y bajas RFC 8058."""
from __future__ import annotations

import base64
import hashlib
import json
import uuid

import pytest

from app.models import Campaign, CampaignMessage, Contact, SesEvent
from app.core.tokens import click_token, open_token, unsubscribe_token
from tests.conftest import SessionLocal

# --------------------------------------------------------------------------- #
# Utilidades SNS: genera notificaciones FIRMADAS con una clave RSA de prueba
# --------------------------------------------------------------------------- #

_RSA_KEY = None


def _get_rsa_key():
    global _RSA_KEY
    if _RSA_KEY is None:
        from cryptography.hazmat.primitives.asymmetric import rsa
        _RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _RSA_KEY


def _ses_id_of(mid: str) -> str:
    """Lee el MessageId de SES real asignado al mensaje (determinista)."""
    from tests.conftest import SessionLocal as S
    db = S()
    ses_id = db.get(CampaignMessage, mid).ses_message_id
    db.close()
    return ses_id


def _make_sns_notification(message: dict, msg_type: str = "Notification") -> dict:
    """Construye una notificación SNS con firma válida (SignatureVersion 1)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    key = _get_rsa_key()
    message_str = json.dumps(message)
    msg_id = str(uuid.uuid4())
    timestamp = "2026-01-01T00:00:00Z"

    if msg_type == "Notification":
        keys = ["Message", "MessageId", "Timestamp", "TopicArn", "Type"]
    else:
        keys = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]

    parts = []
    for k in keys:
        value = {
            "Message": message_str, "MessageId": msg_id, "Timestamp": timestamp,
            "TopicArn": "arn:aws:sns:us-east-2:123456789012:hj-events", "Type": msg_type,
            "SubscribeURL": "https://sns.us-east-2.amazonaws.com/?Action=ConfirmSubscription",
            "Token": "tok",
        }[k]
        parts.append(str(value))
    canonical = "\n".join(parts) + "\n"
    signature = base64.b64encode(
        key.sign(canonical.encode(), padding.PKCS1v15(), hashes.SHA1())
    ).decode()
    return {
        "Type": msg_type, "MessageId": msg_id, "Message": message_str,
        "Timestamp": timestamp, "TopicArn": "arn:aws:sns:us-east-2:123456789012:hj-events",
        "SignatureVersion": "1", "Signature": signature,
        "SigningCertURL": "https://sns.us-east-2.amazonaws.com/cert.pem",
        "SubscribeURL": "https://sns.us-east-2.amazonaws.com/?Action=ConfirmSubscription",
        "Token": "tok",
    }


@pytest.fixture
def webhook_token(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "WEBHOOK_TOKEN", "test-webhook-token")
    # Sustituye la descarga del certificado por nuestro certificado local
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
    return "test-webhook-token"


def _mk_message_with_ses_id(client, admin_auth, fake_ses, email: str):
    """Crea campaña con un contacto, la envía y procesa -> devuelve (msg, campaign_id)."""
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "prueba", "list_ids": [lst.json()["id"]]})
    camp = client.post("/api/v1/campaigns", json={
        "name": f"E-{uuid.uuid4().hex[:5]}", "subject": "S",
        "from_name": "HJ", "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "category": "promocional",
        "list_id": lst.json()["id"]})
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    from app.services import queue as send_queue
    from app.workers.worker import process_one
    from tests.conftest import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    db = S()
    process_one(db, mid)
    db.close()
    return cid, mid


def test_webhook_token_invalido_rechazado(client, webhook_token):
    resp = client.post("/api/v1/webhooks/sns?token=INCORRECTO", json={})
    assert resp.status_code == 403


def test_webhook_firma_invalida_rechazada(client, webhook_token):
    """Una notificación con firma alterada debe rechazarse."""
    sns = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
    sns["Signature"] = base64.b64encode(b"firma-falsa").decode()
    resp = client.post(f"/api/v1/webhooks/sns?token={webhook_token}", json=sns)
    assert resp.status_code == 403


def test_webhook_delivery_entrega_confirmada(client, admin_auth, fake_ses, webhook_token):
    email = f"d-{uuid.uuid4().hex[:6]}@test.local"
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "prueba", "list_ids": [lst.json()["id"]]})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Delx", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    from app.services import queue as send_queue
    from app.workers.worker import process_one
    from tests.conftest import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    db = S()
    process_one(db, mid)
    db.close()

    from tests.conftest import SessionLocal as S
    ses_message_id = _ses_id_of(mid)

    sns = _make_sns_notification({
        "eventType": "delivery",
        "mail": {"messageId": ses_message_id, "destination": [email]},
        "delivery": {"processingTimeMillis": 123},
    })
    resp = client.post(f"/api/v1/webhooks/sns?token={webhook_token}", json=sns)
    assert resp.status_code == 200, resp.text
    assert resp.json()["procesado"] is True

    db = S()
    msg = db.get(CampaignMessage, mid)
    events = db.query(SesEvent).filter(SesEvent.campaign_id == cid).all()
    db.close()
    assert msg.status == "entregado"
    assert any(e.type == "delivery" for e in events)

    # Idempotencia: reenviar la misma notificación no duplica eventos
    resp2 = client.post(f"/api/v1/webhooks/sns?token={webhook_token}", json=sns)
    assert resp2.json()["detalle"] == "duplicado_ignorado"


def test_webhook_bounce_permanente_suprime(client, admin_auth, fake_ses, webhook_token):
    """Rebote permanente -> supresión global automática + estado del contacto."""
    email = f"bounce-{uuid.uuid4().hex[:6]}@test.local"
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "prueba", "list_ids": [lst.json()["id"]]})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Bb", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    from app.services import queue as send_queue
    from app.workers.worker import process_one
    from tests.conftest import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    db = S()
    process_one(db, mid)
    db.close()
    ses_message_id = _ses_id_of(mid)

    sns = _make_sns_notification({
        "eventType": "bounce",
        "mail": {"messageId": ses_message_id, "destination": [email]},
        "bounce": {"bounceType": "Permanent", "bounceSubType": "General",
                   "timestamp": "2026-01-01T00:00:00Z",
                   "bouncedRecipients": [{"emailAddress": email, "diagnosticCode": "550 user unknown"}]},
    })
    resp = client.post(f"/api/v1/webhooks/sns?token={webhook_token}", json=sns)
    assert resp.status_code == 200

    db = S()
    msg = db.get(CampaignMessage, mid)
    contact = db.query(Contact).filter(Contact.email == email).first()
    from app.models import Suppression
    supp = db.query(Suppression).filter(Suppression.email == email).first()
    db.close()
    assert msg.status == "rebote_permanente"
    assert contact.status == "rebote"
    assert supp is not None and supp.reason == "rebote_permanente"

    # El contacto suprimido no puede recibir la siguiente campaña
    camp2 = client.post("/api/v1/campaigns", json={
        "name": "B2x", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    cid2 = camp2.json()["id"]
    client.post(f"/api/v1/campaigns/{cid2}/approve")
    r2 = client.post(f"/api/v1/campaigns/{cid2}/send-now")
    # Sin destinatarios válidos: el contacto fue suprimido por el rebote
    assert r2.status_code == 400


def test_webhook_complaint_suprime(client, admin_auth, fake_ses, webhook_token):
    """Queja de spam -> supresión inmediata."""
    email = f"complaint-{uuid.uuid4().hex[:6]}@test.local"
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "prueba", "list_ids": [lst.json()["id"]]})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Qq", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    from app.services import queue as send_queue
    from app.workers.worker import process_one
    from tests.conftest import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    db = S()
    process_one(db, mid)
    db.close()
    ses_message_id = _ses_id_of(mid)

    sns = _make_sns_notification({
        "eventType": "complaint",
        "mail": {"messageId": ses_message_id, "destination": [email]},
        "complaint": {"complaintFeedbackType": "abuse", "timestamp": "2026-01-01T00:00:00Z"},
    })
    resp = client.post(f"/api/v1/webhooks/sns?token={webhook_token}", json=sns)
    assert resp.status_code == 200

    db = S()
    msg = db.get(CampaignMessage, mid)
    contact = db.query(Contact).filter(Contact.email == email).first()
    from app.models import Suppression
    supp = db.query(Suppression).filter(Suppression.email == email).first()
    db.close()
    assert msg.status == "queja"
    assert contact.status == "queja"
    assert supp is not None


def test_webhook_suscripcion_confirmada(client, webhook_token):
    sns = _make_sns_notification({"hola": "mundo"}, msg_type="SubscriptionConfirmation")
    resp = client.post(f"/api/v1/webhooks/sns?token={webhook_token}", json=sns)
    assert resp.status_code == 200
    assert resp.json()["detalle"] == "suscripcion_confirmada"


# --------------------------------------------------------------------------- #
# Tracking: aperturas y clics
# --------------------------------------------------------------------------- #


def _prepared_message(client, admin_auth, fake_ses):
    email = f"tr-{uuid.uuid4().hex[:6]}@test.local"
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "prueba", "list_ids": [lst.json()["id"]]})
    camp = client.post("/api/v1/campaigns", json={
        "name": "TRx", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    client.post(f"/api/v1/campaigns/{cid}/send-now")
    from app.services import queue as send_queue
    from app.workers.worker import process_one
    from tests.conftest import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    db = S()
    process_one(db, mid)
    db.close()
    return cid, mid


def test_pixel_apertura_registra_evento(client, admin_auth, fake_ses):
    cid, mid = _prepared_message(client, admin_auth, fake_ses)
    token = open_token(cid, mid)
    resp = client.get(f"/api/v1/t/open/{token}.gif")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/gif"
    assert resp.content[:4] == b"GIF8"

    from tests.conftest import SessionLocal as S
    db = S()
    opens = db.query(SesEvent).filter(SesEvent.type == "open", SesEvent.message_id == mid).count()
    db.close()
    assert opens == 1


def test_token_apertura_falso_rechazado(client, admin_auth, fake_ses):
    cid, mid = _prepared_message(client, admin_auth, fake_ses)
    resp = client.get("/api/v1/t/open/falso-token.gif")
    assert resp.status_code == 200  # devuelve gif silencioso
    from tests.conftest import SessionLocal as S
    db = S()
    events = db.query(SesEvent).filter(SesEvent.type == "open", SesEvent.message_id == mid).count()
    db.close()
    # No crea evento para el token inválido de este mensaje
    assert events == 0


def test_clic_firmado_redirige(client, admin_auth, fake_ses):
    cid, mid = _prepared_message(client, admin_auth, fake_ses)
    url = "https://leisureparts4u.store/oferta"
    token = click_token(cid, mid, url)
    resp = client.get(f"/api/v1/t/click/{token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == url

    from tests.conftest import SessionLocal as S
    db = S()
    clicks = db.query(SesEvent).filter(SesEvent.type == "click", SesEvent.message_id == mid).count()
    db.close()
    assert clicks == 1


# --------------------------------------------------------------------------- #
# Bajas públicas
# --------------------------------------------------------------------------- #


def test_baja_publica_sin_login(client, admin_auth):
    email = f"baja-{uuid.uuid4().hex[:6]}@test.local"
    c = client.post("/api/v1/contacts", json={"email": email, "consent_source": "prueba"})
    contact_id = c.json()["id"]
    token = unsubscribe_token(contact_id)

    # Información de la baja (página pública)
    info = client.get(f"/api/v1/public/unsubscribe/info?token={token}")
    assert info.status_code == 200
    assert email[0] in info.json()["email_enmascarado"]

    # Confirmación
    resp = client.post("/api/v1/public/unsubscribe/confirm", json={"token": token})
    assert resp.status_code == 200

    from tests.conftest import SessionLocal as S
    from app.models import Suppression
    db = S()
    contact = db.get(Contact, contact_id)
    supp = db.query(Suppression).filter(Suppression.email == email).first()
    db.close()
    assert contact.status == "dado_baja"
    assert contact.unsubscribed_at is not None
    assert supp is not None

    # Sin sesión: la baja funcionó (este test nunca usa admin_auth más allá de crear el contacto)


def test_baja_one_click_rfc8058(client, admin_auth):
    email = f"oc-{uuid.uuid4().hex[:6]}@test.local"
    c = client.post("/api/v1/contacts", json={"email": email, "consent_source": "prueba"})
    token = unsubscribe_token(c.json()["id"])
    resp = client.post(f"/api/v1/public/unsubscribe/one-click?token={token}")
    assert resp.status_code == 200
    from tests.conftest import SessionLocal as S
    db = S()
    contact = db.get(Contact, c.json()["id"])
    db.close()
    assert contact.status == "dado_baja"


def test_baja_token_invalido(client):
    resp = client.post("/api/v1/public/unsubscribe/confirm", json={"token": "x.y"})
    assert resp.status_code == 400


def test_estadisticas_dashboard(client, admin_auth, fake_ses):
    cid, mid = _prepared_message(client, admin_auth, fake_ses)
    resp = client.get("/api/v1/stats/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["resumen"]["aceptados_ses"] >= 1
    assert "cuota_ses" in data
    assert "max_24h" in data["cuota_ses"]

    series = client.get("/api/v1/stats/series?dias=7").json()
    assert len(series["puntos"]) == 7
