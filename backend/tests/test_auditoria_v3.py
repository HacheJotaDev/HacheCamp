"""Pruebas de la auditoría v3:

1. Flujo completo de plantillas HTML en campañas (bug crítico del selector).
2. Rate limiter atómico compartido entre N "workers".
3. Contadores SQS correctos (sns_acks != descartados) y rastro de descartes.
4. Métricas reales de rendimiento (/system/ses/rendimiento).
5. Diagnóstico del pipeline de eventos (/system/ses/diagnostico).

SES simulado con fake_ses; nunca se envían correos reales.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

from app.core.config import settings
from tests.conftest import SessionLocal

QUEUE_URL = "https://sqs.us-east-2.amazonaws.com/123456789012/hj-eventos-pruebas"


@pytest.fixture
def sqs_on(monkeypatch):
    """Activa la cola SQS de eventos (igual que en test_sqs_events)."""
    monkeypatch.setattr(settings, "AWS_SQS_EVENTS_QUEUE_URL", QUEUE_URL)
    from app.services import events as events_service
    events_service._CERT_CACHE.clear()
    yield
    events_service._CERT_CACHE.clear()


@pytest.fixture
def firma_local(monkeypatch):
    """Certificado SNS firmado con la clave de pruebas (igual que en test_sqs_events).

    IMPORTANTE: se importa por el nombre de módulo top-level (test_webhooks_tracking)
    para obtener la MISMA instancia/clave que usa _make_sns_notification; importarlo
    como tests.test_webhooks_tracking crearía un módulo distinto con otra clave RSA.
    """
    from test_webhooks_tracking import _get_rsa_key
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


# --------------------------------------------------------------------------- #
# 1. Plantillas HTML: crear, listar, usar en campaña, conservar y previsualizar
# --------------------------------------------------------------------------- #

HTML_PLANTILLA = (
    "<html><head><style>.titulo{color:#e11d48;font-size:24px}</style></head>"
    "<body><h1 class=\"titulo\">Hola {{contact.first_name}}</h1>"
    "<p>Contenido <b>HTML</b> de la plantilla</p></body></html>"
)


def _crear_plantilla_html(client, nombre=None):
    r = client.post("/api/v1/templates", json={
        "name": nombre or f"HTML-{uuid.uuid4().hex[:6]}",
        "mode": "html",
        "html": HTML_PLANTILLA,
        "category": "newsletter",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_plantilla_html_se_crea_y_se_lista_con_html(client, admin_auth):
    tpl = _crear_plantilla_html(client)
    assert tpl["mode"] == "html"
    assert "titulo" in (tpl["html"] or "")

    lista = client.get("/api/v1/templates")
    assert lista.status_code == 200
    items = lista.json()
    encontrada = [t for t in items if t["id"] == tpl["id"]]
    assert encontrada, "la plantilla HTML debe aparecer en el listado"
    # El listado incluye el HTML: el wizard de campañas lo carga desde aquí
    assert encontrada[0]["mode"] == "html"
    assert "titulo" in (encontrada[0]["html"] or "")


def test_campana_desde_plantilla_html_se_crea_y_conserva_plantilla(client, admin_auth):
    tpl = _crear_plantilla_html(client)
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    lid = lst.json()["id"]

    camp = client.post("/api/v1/campaigns", json={
        "name": f"C-HTML-{uuid.uuid4().hex[:6]}",
        "subject": "Asunto de prueba",
        "from_name": "HJ Test", "from_email": "notificaciones@leisureparts4u.store",
        "template_id": tpl["id"],
        "mode": "html",
        "blocks": None,
        "html": HTML_PLANTILLA,
        "category": "promocional", "list_id": lid,
    })
    assert camp.status_code == 201, camp.text
    cid = camp.json()["id"]

    # Al reabrir la campaña se conserva plantilla y HTML real (no texto plano)
    detalle = client.get(f"/api/v1/campaigns/{cid}")
    assert detalle.status_code == 200
    data = detalle.json()
    assert data["mode"] == "html"
    assert data["template_id"] == tpl["id"]
    assert "titulo" in (data["html"] or "")

    # La plantilla original queda intacta (no conversión destructiva)
    tpl_despues = client.get(f"/api/v1/templates/{tpl['id']}")
    assert tpl_despues.status_code == 200
    assert "titulo" in (tpl_despues.json()["html"] or "")
    assert tpl_despues.json()["mode"] == "html"


def test_campana_html_renderiza_antes_del_envio(client, admin_auth, fake_ses):
    """El envío real de una campaña HTML renderiza el HTML fuente (no bloques)."""
    tpl = _crear_plantilla_html(client)
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    lid = lst.json()["id"]
    email = f"html-{uuid.uuid4().hex[:6]}@test.local"
    client.post("/api/v1/contacts", json={
        "email": email, "first_name": "Ana", "consent_source": "prueba",
        "list_ids": [lid]})

    camp = client.post("/api/v1/campaigns", json={
        "name": f"C-HTML-{uuid.uuid4().hex[:6]}",
        "subject": "Hola {{contact.first_name}}",
        "from_name": "HJ Test", "from_email": "notificaciones@leisureparts4u.store",
        "template_id": tpl["id"], "mode": "html", "blocks": None,
        "html": HTML_PLANTILLA, "category": "promocional", "list_id": lid,
    })
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    resp = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert resp.status_code == 200, resp.text

    from app.services import queue as send_queue
    from app.workers.worker import process_one
    processed = 0
    for _ in range(10):
        db = SessionLocal()
        mid = send_queue.pop_message(timeout_seconds=1)
        if not mid:
            db.close()
            break
        process_one(db, mid)
        processed += 1
        db.close()
    assert processed == 1

    raw = fake_ses.sent[0]["Content"]["Raw"]["Data"].decode()
    # HTML de la plantilla presente y personalizado por variable
    assert "titulo" in raw or 'class="titulo"' in raw
    assert "Hola Ana" in raw


def test_preview_html_funciona(client, admin_auth):
    r = client.post("/api/v1/templates/preview", json={
        "mode": "html", "html": HTML_PLANTILLA})
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "titulo" in html or "Hola" in html


def test_plantilla_bloques_sigue_funcionando(client, admin_auth):
    """Compatibilidad: el flujo de bloques (texto plano renderizado) no se rompe."""
    r = client.post("/api/v1/templates", json={
        "name": f"BLOQ-{uuid.uuid4().hex[:6]}", "mode": "bloques",
        "blocks": [
            {"type": "titulo", "props": {"text": "Hola", "level": 1}},
            {"type": "texto", "props": {"content": "<p>Cuerpo</p>"}},
        ]})
    assert r.status_code == 201, r.text
    tpl = r.json()
    assert tpl["mode"] == "bloques"
    lista = client.get("/api/v1/templates")
    assert any(t["id"] == tpl["id"] for t in lista.json())


# --------------------------------------------------------------------------- #
# 2. Rate limiter atómico compartido
# --------------------------------------------------------------------------- #

def test_rate_limiter_respetado_por_hilos_concurrentes():
    """N 'workers' que toman slot a la vez no superan la velocidad conjunta."""
    from app.services import queue as send_queue
    from app.workers.redis_client import get_redis
    get_redis().delete("hj:q:last_send_ns")

    rate = 50.0  # 20 ms de intervalo
    n = 10
    resultados = [None] * n

    def _tomar(i):
        t0 = time.monotonic()
        send_queue.wait_for_rate_slot(rate)
        resultados[i] = time.monotonic() - t0

    hilos = [threading.Thread(target=_tomar, args=(i,)) for i in range(n)]
    inicio = time.monotonic()
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    total = time.monotonic() - inicio

    # Todos obtuvieron slot y el conjunto respetó el intervalo global:
    # n envíos a rate/s requieren al menos (n-1)/rate segundos.
    assert all(r is not None for r in resultados)
    minimo_esperado = (n - 1) / rate * 0.8  # tolerancia del scheduler
    assert total >= minimo_esperado, (
        f"se enviaron {n} slots en {total:.3f}s (mínimo esperado "
        f"{minimo_esperado:.3f}s): el límite global no se respetó")


def test_rate_limiter_redis_caido_no_bloquea_envios(monkeypatch):
    """Si Redis cae, el envío continúa (degradación) sin reventar."""
    from app.services import queue as send_queue

    def _redis_muerto():
        raise ConnectionError("redis caído")

    monkeypatch.setattr(send_queue, "get_redis", _redis_muerto)
    t0 = time.monotonic()
    espera = send_queue.wait_for_rate_slot(100.0)
    assert espera == 0.0
    assert time.monotonic() - t0 < 2.0


# --------------------------------------------------------------------------- #
# 3. Contadores SQS correctos y rastro de descartes en BD
# --------------------------------------------------------------------------- #

class FakeSqs:
    def __init__(self, mensajes):
        self.mensajes = list(mensajes)
        self.borrados: list[dict] = []

    def receive_message(self, **kwargs):
        msgs = self.mensajes[:kwargs.get("MaxNumberOfMessages", 10)]
        self.mensajes = self.mensajes[len(msgs):]
        return {"Messages": msgs} if msgs else {}

    def delete_message_batch(self, **kwargs):
        self.borrados.extend(kwargs.get("Entries", []))
        return {"Successful": [{"Id": e["Id"]} for e in kwargs.get("Entries", [])]}


def _msg_sqs(cuerpo: str, msg_id=None):
    return {"MessageId": msg_id or f"sqs-{uuid.uuid4().hex[:12]}",
            "ReceiptHandle": f"rh-{uuid.uuid4().hex[:8]}",
            "Body": cuerpo, "Attributes": {"ApproximateReceiveCount": "1"}}


def _limpiar_eventos():
    from app.models import SesEvent
    db = SessionLocal()
    db.query(SesEvent).delete()
    db.commit()
    db.close()


def test_formato_desconocido_se_cuenta_como_descarte_y_deja_rastro(client, sqs_on):
    from app.services import sqs_events
    fake = FakeSqs([_msg_sqs(json.dumps({"cosa": "rara"}))])

    res = sqs_events.poll_once(fake)

    assert res["recibidos"] == 1
    assert res["procesados"] == 0
    assert res["descartados"] == 1
    assert len(fake.borrados) == 1  # borrado de la cola

    from app.models import SesEvent
    db = SessionLocal()
    rastro = db.query(SesEvent).filter(SesEvent.type == "sqs_descartado").first()
    db.close()
    assert rastro is not None, "todo descarte debe dejar rastro consultable"
    assert (rastro.payload or {}).get("motivo") == "formato_desconocido"
    _limpiar_eventos()


def test_sns_ack_no_falsea_descartes(client, sqs_on, firma_local):
    """Suscripción SNS confirmada: sns_acks=1 y descartados=0."""
    from test_sqs_events import _make_sns_notification
    from app.services import sqs_events
    sobre = _make_sns_notification({"mensaje": "ok"}, msg_type="SubscriptionConfirmation")
    fake = FakeSqs([_msg_sqs(json.dumps(sobre))])

    res = sqs_events.poll_once(fake)

    assert res["sns_acks"] == 1
    assert res["descartados"] == 0
    assert res["procesados"] == 0
    _limpiar_eventos()


def test_evento_delivery_se_procesa_y_contadores_correctos(client, sqs_on, firma_local):
    """Un delivery real: procesados=1, descartados=0, sns_acks=0."""
    from test_sqs_events import _make_sns_notification
    from app.services import sqs_events
    ses_msg_id = f"ses-{uuid.uuid4().hex[:10]}"
    evento = {"eventType": "delivery", "mail": {"messageId": ses_msg_id}}
    sobre = _make_sns_notification(evento)
    fake = FakeSqs([_msg_sqs(json.dumps(sobre))])

    res = sqs_events.poll_once(fake)

    assert res["procesados"] == 1
    assert res["descartados"] == 0
    assert res["sns_acks"] == 0
    _limpiar_eventos()


# --------------------------------------------------------------------------- #
# 4. Métricas reales de rendimiento
# --------------------------------------------------------------------------- #

def test_rendimiento_refleja_envios_reales(client, admin_auth, fake_ses):
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    lid = lst.json()["id"]
    email = f"rend-{uuid.uuid4().hex[:6]}@test.local"
    client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "prueba", "list_ids": [lid]})
    camp = client.post("/api/v1/campaigns", json={
        "name": f"C-{uuid.uuid4().hex[:6]}", "subject": "Hola",
        "from_name": "HJ", "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques",
        "blocks": [{"type": "texto", "props": {"content": "<p>Hola</p>"}}],
        "category": "promocional", "list_id": lid})
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    # Antes de procesar: campaña activa con pendientes reales
    r = client.get("/api/v1/system/ses/rendimiento")
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["pendientes"]["base_datos"] >= 1
    activas = [c for c in datos["campanas_activas"] if c["id"] == cid]
    assert activas, "la campaña en curso debe figurar con progreso real"
    assert activas[0]["pendientes"] == 1
    assert activas[0]["progreso_pct"] == 0.0

    # Velocidad efectiva = mínimo(configurada, límite SES=14 del fake)
    assert datos["velocidad"]["efectiva"] <= 14.0
    assert datos["cuota_24h"]["max_24h"] == 50000.0

    # Tras procesar un mensaje hay envio_local -> por_minuto refleja realidad
    from app.services import queue as send_queue
    from app.workers.worker import process_one
    db = SessionLocal()
    mid = send_queue.pop_message(timeout_seconds=1)
    process_one(db, mid)
    db.close()

    r2 = client.get("/api/v1/system/ses/rendimiento")
    datos2 = r2.json()
    assert datos2["por_minuto"] >= 1, "los envíos aceptados deben contar"
    assert datos2["aceptados_60s"] >= 1
    activa = [c for c in datos2["campanas_activas"] if c["id"] == cid]
    if activa:
        assert activa[0]["aceptados"] >= 1


def test_rendimiento_requiere_sesion(client):
    r = client.get("/api/v1/system/ses/rendimiento")
    assert r.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# 5. Diagnóstico del pipeline de eventos
# --------------------------------------------------------------------------- #

def test_diagnostico_admin_muestra_configuration_set_y_consumidor(client, admin_auth):
    r = client.get("/api/v1/system/ses/diagnostico")
    assert r.status_code == 200, r.text
    datos = r.json()
    # En pruebas SES_CONFIGURATION_SET está vacío: el diagnóstico debe avisar
    assert datos["configuration_set_aplicado"] is False
    assert datos["nota"] and "configuration set" in datos["nota"].lower()
    assert "consumidor" in datos and "vivo" in datos["consumidor"]
    assert "eventos" in datos and "por_tipo_24h" in datos["eventos"]
    assert "mensajes_por_estado" in datos


def test_diagnostico_requiere_admin(client, admin_auth):
    # admin_auth es admin: permitido; un operador normal: denegado
    import uuid as _uuid
    from app.core import security
    from app.models import User
    email = f"op-{_uuid.uuid4().hex[:8]}@test.local"
    db = SessionLocal()
    db.add(User(email=email, name="Op", role="operador",
                password_hash=security.hash_password("ClaveSegura123")))
    db.commit()
    db.close()
    r = client.post("/api/v1/auth/logout")
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveSegura123"})
    assert resp.status_code == 200
    client.headers.update({"X-CSRF-Token": resp.json()["csrf_token"]})
    r2 = client.get("/api/v1/system/ses/diagnostico")
    assert r2.status_code == 403
