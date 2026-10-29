"""Pruebas de regresión de la INTEGRACIÓN de hotfixes de producción en V4.

Cubre los cinco archivos modificados en producción (rama deploy/pro-v2,
commit 0ecdbee + parche local) integrados sobre la auditoría V4:

- HOTFIX 1: conteo eficiente de destinatarios (count_recipients en SQL,
  exclusión de supresiones con NOT EXISTS) en api/v1/campaigns.py y
  services/campaigns.py.
- HOTFIX 2: seguridad SNS en services/events.py (host estricto del
  certificado, cadena canónica oficial, Subject opcional, certificados
  PEM y DER).
- HOTFIX 3: ses_configured() sin llamadas AWS en services/ses.py.
- HOTFIX 4: consumidor SQS condicional en workers/worker.py y consumidor
  dedicado events_worker.py.

Las pruebas usan SQLite en memoria + fakeredis + SES simulado (ver
conftest.py): son pruebas funcionales/de contrato, NO pruebas de carga
contra PostgreSQL/Redis reales ni envíos reales a SES.
"""
from __future__ import annotations

import base64
import sys
import uuid

import pytest

from app.core.config import settings
from app.models import Campaign, Contact, Suppression
from tests.conftest import SessionLocal
from test_webhooks_tracking import _get_rsa_key, _make_sns_notification

# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #


def _contacto(email: str, status: str = "suscripto") -> Contact:
    return Contact(email=email, status=status, consent_source="prueba")


def _lista_con_contactos(client, emails: list) -> str:
    resp = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    list_id = resp.json()["id"]
    for email in emails:
        client.post("/api/v1/contacts", json={
            "email": email, "consent_source": "prueba", "list_ids": [list_id]})
    return list_id


def _campana(client, list_id: str, categoria: str = "promocional") -> str:
    resp = client.post("/api/v1/campaigns", json={
        "name": f"C-{uuid.uuid4().hex[:5]}", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "category": categoria, "list_id": list_id})
    return resp.json()["id"]


# --------------------------------------------------------------------------- #
# HOTFIX 1 — conteo eficiente y reglas de elegibilidad
# --------------------------------------------------------------------------- #


def test_count_y_select_aplican_las_mismas_reglas(client, admin_auth):
    """count_recipients y select_recipients devuelven el MISMO conjunto.

    Elegibilidad: suscripto (todas las categorías) y sin supresión
    (promocionales). El único suscripto válido queda suprimido: count y
    select coinciden en 0 y el envío real fallaría sin destinatarios.
    """
    from app.models import contact_lists_rel
    from app.services import campaigns as cs
    from app.services import contacts as contact_service

    lst = _lista_con_contactos(client, [
        f"a-{uuid.uuid4().hex[:5]}@t.local",
        f"b-{uuid.uuid4().hex[:5]}@t.local",
    ])
    cid = _campana(client, lst, "promocional")
    db2 = SessionLocal()
    try:
        camp = db2.get(Campaign, cid)
        contactos = (
            db2.query(Contact)
            .join(contact_lists_rel, contact_lists_rel.c.contact_id == Contact.id)
            .filter(contact_lists_rel.c.list_id == lst)
            .all()
        )
        db2.query(Suppression).delete(synchronize_session=False)
        contact_service.suppress_email(db2, contactos[0].email, reason="manual", source="manual")
        contactos[1].status = "dado_baja"
        db2.commit()
        emails = {c.email for c in contactos}
        db2.expire_all()

        seleccion = cs.select_recipients(db2, camp)
        total = cs.count_recipients(db2, camp)
    finally:
        db2.close()
    assert total == len(seleccion) == 0
    assert not (emails & {c.email for c in seleccion})


def test_select_y_count_sin_supresion_coinciden(client, admin_auth):
    """Caso sano: dos suscriptos, ninguno suprimido -> count == select == 2."""
    from app.services import campaigns as cs
    emails = [f"s1-{uuid.uuid4().hex[:5]}@t.local", f"s2-{uuid.uuid4().hex[:5]}@t.local"]
    lst = _lista_con_contactos(client, emails)
    cid = _campana(client, lst, "promocional")
    db2 = SessionLocal()
    try:
        camp = db2.get(Campaign, cid)
        seleccion = cs.select_recipients(db2, camp)
        total = cs.count_recipients(db2, camp)
    finally:
        db2.close()
    assert total == len(seleccion) == 2


def test_supresion_sql_ignora_mayusculas_del_contacto(db):
    """NOT EXISTS en SQL es inmune a contactos heredados con mayúsculas.

    suppress_email almacena SIEMPRE normalizado (minúsculas); el filtro
    compara contra lower(Contact.email) para no perder exclusiones. Conteo
    acotado a una lista para que sea determinista ante datos de otras pruebas.
    """
    from app.models import ContactList
    from app.services import campaigns as cs
    from app.services import contacts as contact_service

    db2 = SessionLocal()
    try:
        db2.query(Suppression).delete(synchronize_session=False)
        lista = ContactList(name=f"L-{uuid.uuid4().hex[:6]}")
        db2.add(lista)
        db2.flush()
        raro = f"MAYUS-{uuid.uuid4().hex[:5]}@T.Local"
        contacto_raro = _contacto(raro)
        db2.add(contacto_raro)
        normal = f"minus-{uuid.uuid4().hex[:5]}@t.local"
        contacto_normal = _contacto(normal)
        db2.add(contacto_normal)
        db2.flush()
        # La relación Contact.lists es viewonly: la asociación se inserta directo
        from app.models import contact_lists_rel
        db2.execute(contact_lists_rel.insert().values([
            {"contact_id": contacto_raro.id, "list_id": lista.id},
            {"contact_id": contacto_normal.id, "list_id": lista.id},
        ]))
        db2.commit()
        contact_service.suppress_email(db2, raro.lower(), reason="manual", source="manual")
        db2.commit()

        camp = Campaign(name="p", subject="p", from_name="p", from_email="p@local",
                        category="promocional", list_id=lista.id)
        seleccion = {c.email for c in cs.select_recipients(db2, camp)}
        total = cs.count_recipients(db2, camp)
    finally:
        db2.close()
    assert raro not in seleccion
    assert normal in seleccion
    assert total == 1


def test_transaccional_no_aplica_exclusion_sql_de_supresion(client, admin_auth):
    """Semántica V4 preservada: la exclusión por supresión es de promocionales.

    (El worker además bloquea suprimidos en el envío, así que el
    comportamiento de extremo a extremo no cambia.) Conteo acotado a una
    lista para que sea determinista ante datos de otras pruebas.
    """
    from app.models import ContactList
    from app.services import campaigns as cs
    from app.services import contacts as contact_service

    db2 = SessionLocal()
    try:
        db2.query(Suppression).delete(synchronize_session=False)
        lista = ContactList(name=f"L-{uuid.uuid4().hex[:6]}")
        db2.add(lista)
        db2.flush()
        email = f"trx-{uuid.uuid4().hex[:5]}@t.local"
        contacto = _contacto(email)
        db2.add(contacto)
        db2.flush()
        # La relación Contact.lists es viewonly: la asociación se inserta directo
        from app.models import contact_lists_rel
        db2.execute(contact_lists_rel.insert().values(
            contact_id=contacto.id, list_id=lista.id))
        db2.commit()
        contact_service.suppress_email(db2, email, reason="manual", source="manual")
        db2.commit()

        camp = Campaign(name="t", subject="t", from_name="t", from_email="t@local",
                        category="transaccional", list_id=lista.id)
        total = cs.count_recipients(db2, camp)
    finally:
        db2.close()
    assert total == 1


def test_estimate_api_usa_conteo_sql_sin_materializar(client, admin_auth, monkeypatch):
    """recipients-estimate llama a count_recipients, NO a select_recipients.

    Con 50k+ contactos el endpoint anterior cargaba la lista completa solo
    para hacer len(); si alguien lo restaura, esta prueba lo detecta.
    """
    from app.api.v1 import campaigns as api_campaigns
    emails = [f"e{i}-{uuid.uuid4().hex[:5]}@t.local" for i in range(3)]
    lst = _lista_con_contactos(client, emails)
    cid = _campana(client, lst, "promocional")

    def _no_materializar(*a, **k):
        raise AssertionError("recipients-estimate no debe materializar destinatarios")

    monkeypatch.setattr(api_campaigns.campaign_service, "select_recipients", _no_materializar)
    resp = client.get(f"/api/v1/campaigns/{cid}/recipients-estimate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["destinatarios"] == 3


def test_preview_api_cuenta_con_segmento(client, admin_auth):
    """recipients-preview aplica los filtros del segmento en el COUNT."""
    email_ok = f"seg-ok-{uuid.uuid4().hex[:5]}@t.local"
    email_fuera = f"seg-no-{uuid.uuid4().hex[:5]}@t.local"
    lst = _lista_con_contactos(client, [email_ok, email_fuera])
    seg = client.post("/api/v1/segments", json={
        "name": f"S-{uuid.uuid4().hex[:5]}",
        "rules": [{"campo": "email", "op": "contiene", "valor": "seg-ok"}],
    })
    assert seg.status_code in (200, 201), seg.text
    resp = client.post("/api/v1/campaigns/recipients-preview", json={
        "segment_id": seg.json()["id"], "category": "promocional"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["destinatarios"] == 1
    # y coherentemente por lista (mismas reglas base)
    resp2 = client.post("/api/v1/campaigns/recipients-preview", json={
        "list_id": lst, "category": "promocional"})
    assert resp2.json()["destinatarios"] == 2


# --------------------------------------------------------------------------- #
# HOTFIX 2 — seguridad y verificación SNS
# --------------------------------------------------------------------------- #


@pytest.fixture
def firma_local_der(monkeypatch):
    """Certificado de prueba servido en DER desde el host oficial de SNS."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    from app.services import events as events_service

    def fake_fetch(url, max_bytes=65536):
        assert url.startswith(f"https://{events_service._sns_host_valido()}"), url
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
    events_service._CERT_CACHE.clear()
    yield
    events_service._CERT_CACHE.clear()


def _pem_bytes() -> bytes:
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
    return cert.public_bytes(serialization.Encoding.PEM)


def test_canonical_string_formato_oficial_aws():
    """La cadena a firmar es exactamente la de la especificación SNS v1.

    Cada campo: NOMBRE\\nVALOR\\n (documentación AWS "Verifying the
    signatures of Amazon SNS messages"). Regresión: la versión anterior
    firmaba solo los valores concatenados.
    """
    from app.services import events as events_service
    sns = {"Message": "hola", "MessageId": "mid-1", "Timestamp": "2026-01-01T00:00:00Z",
           "TopicArn": "arn:aws:sns:us-east-2:123456789012:t", "Type": "Notification"}
    esperada = (
        "Message\nhola\n"
        "MessageId\nmid-1\n"
        "Timestamp\n2026-01-01T00:00:00Z\n"
        "TopicArn\narn:aws:sns:us-east-2:123456789012:t\n"
        "Type\nNotification\n"
    )
    assert events_service._canonical_string(
        sns, ["Message", "MessageId", "Timestamp", "TopicArn", "Type"]) == esperada


def test_sns_firma_valida_sin_subject(firma_local_der):
    from app.services import events as events_service
    sns = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
    events_service.verify_sns_signature(sns)  # no lanza


def test_sns_firma_valida_con_subject(firma_local_der):
    """Subject presente: participa en la firma entre MessageId y Timestamp."""
    from app.services import events as events_service
    sns = _make_sns_notification({"eventType": "bounce", "mail": {"messageId": "x"}},
                                 subject="Entrega retrasada")
    events_service.verify_sns_signature(sns)  # no lanza


def test_sns_subject_alterado_rechazado(firma_local_der):
    """Un Subject manipulado invalida la firma (forma parte del mensaje firmado)."""
    from app.services import events as events_service
    sns = _make_sns_notification({"eventType": "bounce", "mail": {"messageId": "x"}},
                                 subject="Original")
    sns["Subject"] = "Manipulado"
    with pytest.raises(events_service.SNSSecurityError):
        events_service.verify_sns_signature(sns)


def test_sns_firma_formato_antiguo_solo_valores_rechazada(firma_local_der):
    """Una firma con la cadena canónica antigua (solo valores) se rechaza.

    Guarda la corrección contra la especificación oficial: si alguien
    restaura la implementación anterior, las firmas reales de SNS fallarían
    y esta prueba (firmada al estilo antiguo) pasaría: debe ser al revés.
    """
    import json as _json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    from app.services import events as events_service
    sns = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
    keys = ["Message", "MessageId", "Timestamp", "TopicArn", "Type"]
    valores = [str(sns[k]) for k in keys]
    canonico_viejo = "\n".join(valores) + "\n"
    firma_vieja = base64.b64encode(
        _get_rsa_key().sign(canonico_viejo.encode(), padding.PKCS1v15(), hashes.SHA1())
    ).decode()
    assert _json.dumps({}) is not None
    sns["Signature"] = firma_vieja
    with pytest.raises(events_service.SNSSecurityError):
        events_service.verify_sns_signature(sns)


def test_sns_certificado_pem_aceptado(monkeypatch):
    """SNS publica certificados PEM: deben verificarse (regresión DER-only)."""
    from app.services import events as events_service
    pem = _pem_bytes()

    def fake_fetch(url, max_bytes=65536):
        return pem

    monkeypatch.setattr(events_service, "_safe_fetch", fake_fetch)
    events_service._CERT_CACHE.clear()
    try:
        sns = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
        events_service.verify_sns_signature(sns)  # no lanza
    finally:
        events_service._CERT_CACHE.clear()


def test_safe_fetch_rechaza_hosts_no_sns():
    """Anti-SSRF: solo el host sns.<región>.amazonaws.com está permitido.

    La validación por sufijo anterior aceptaba subdominios controlables por
    terceros (p. ej. buckets S3) y hosts de otras regiones.
    """
    from app.services import events as events_service
    with pytest.raises(events_service.SNSSecurityError):
        events_service._safe_fetch("https://bucket-atacante.s3.amazonaws.com/cert.pem")
    with pytest.raises(events_service.SNSSecurityError):
        events_service._safe_fetch("https://sns.us-east-1.amazonaws.com/cert.pem")  # otra región
    with pytest.raises(events_service.SNSSecurityError):
        events_service._safe_fetch("http://sns.us-east-2.amazonaws.com/cert.pem")  # no HTTPS
    # El host oficial de la región configurada SÍ pasa la comprobación de host
    host = events_service._sns_host_valido()
    assert host == f"sns.{settings.AWS_REGION}.amazonaws.com"


def test_sns_mensaje_manipulado_rechazado(firma_local_der):
    from app.services import events as events_service
    sns = _make_sns_notification({"eventType": "delivery", "mail": {"messageId": "x"}})
    sns["Message"] = '{"eventType":"complaint","mail":{}}'  # manipulado tras firmar
    with pytest.raises(events_service.SNSSecurityError):
        events_service.verify_sns_signature(sns)


def test_topicarn_en_lista_blanca_comparacion_exacta(monkeypatch):
    """La lista blanca de tópicos compara el TopicArn EXACTO, no por prefijo."""
    from app.services import sqs_events
    autorizado = "arn:aws:sns:us-east-2:123456789012:hj-events"
    monkeypatch.setattr(settings, "SNS_TOPIC_ARNS", autorizado)
    assert sqs_events._topic_authorized(autorizado) is True
    assert sqs_events._topic_authorized(autorizado + "-malicioso") is False
    assert sqs_events._topic_authorized("arn:aws:sns:us-east-1:123456789012:hj-events") is False


# --------------------------------------------------------------------------- #
# HOTFIX 3 — ses_configured() sin llamadas AWS
# --------------------------------------------------------------------------- #


@pytest.fixture
def cadena_boto3_limpia():
    from app.services import ses as ses_service
    ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)
    yield
    ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)


def _boto3_stub(get_credentials_result, captura: dict | None = None):
    class _Sesion:
        def get_credentials(self):
            if captura is not None:
                captura["llamadas"] = captura.get("llamadas", 0) + 1
            return get_credentials_result

    class _Modulo:
        def Session(self):
            return _Sesion()

    return _Modulo()  # instancia: boto3.Session() es una llamada sobre el módulo


def test_ses_configured_credenciales_explicitas_sin_tocar_boto3(monkeypatch, cadena_boto3_limpia):
    from app.services import ses as ses_service
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", "AKIAPRUEBA")
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", "secreta")

    class _Explota:
        def Session(self):
            raise AssertionError("con credenciales explícitas no se consulta la cadena")

    monkeypatch.setitem(sys.modules, "boto3", _Explota())
    assert ses_service.ses_configured() is True


def test_ses_configured_cadena_boto3_presente_y_ausente(monkeypatch, cadena_boto3_limpia):
    from app.services import ses as ses_service
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", None)
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", None)

    captura: dict = {}
    monkeypatch.setitem(sys.modules, "boto3", _boto3_stub(object(), captura))
    assert ses_service.ses_configured() is True
    assert captura["llamadas"] == 1

    ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)  # reinicia caché
    monkeypatch.setitem(sys.modules, "boto3", _boto3_stub(None, captura))
    assert ses_service.ses_configured() is False


def test_ses_configured_error_de_boto3_fail_closed(monkeypatch, cadena_boto3_limpia):
    from app.services import ses as ses_service
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", None)
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", None)

    class _Roto:
        def Session(self):
            raise RuntimeError("cadena de credenciales ilegible")

    monkeypatch.setitem(sys.modules, "boto3", _Roto())
    assert ses_service.ses_configured() is False


def test_ses_configured_cache_de_proceso_evita_relecturas(monkeypatch, cadena_boto3_limpia):
    """Dos comprobaciones seguidas -> una sola lectura de la cadena (60 s)."""
    from app.services import ses as ses_service
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", None)
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", None)
    captura: dict = {}
    stub = _boto3_stub(object(), captura)
    monkeypatch.setitem(sys.modules, "boto3", stub)
    assert ses_service.ses_configured() is True
    assert ses_service.ses_configured() is True
    assert captura["llamadas"] == 1


def test_send_raw_email_produccion_sin_credenciales_aborta(monkeypatch):
    """Fail-closed en producción: sin credenciales -> SESNotConfigured."""
    from app.services import ses as ses_service
    from app.core.config import settings as s
    monkeypatch.setattr(s, "ENV", "production")
    monkeypatch.setattr(s, "AWS_ACCESS_KEY_ID", None)
    monkeypatch.setattr(s, "AWS_SECRET_ACCESS_KEY", None)
    ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)
    msg = ses_service.build_raw_message(
        from_email="a@b.c", from_name="A", to_email="d@e.f",
        subject="s", html="<p></p>", text="t")
    with pytest.raises(ses_service.SESNotConfigured):
        ses_service.send_raw_email(msg)
    ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)


# --------------------------------------------------------------------------- #
# HOTFIX 4 — consumidor SQS: worker condicional + events-worker dedicado
# --------------------------------------------------------------------------- #


def test_worker_no_arranca_consumidor_por_defecto(monkeypatch):
    """Los 14 workers de envío NO consumen SQS salvo habilitación explícita."""
    from app.services import sqs_events
    from app.workers import worker
    monkeypatch.delenv("HJ_SQS_IN_SEND_WORKER", raising=False)
    llamadas: list = []
    monkeypatch.setattr(sqs_events, "start_consumer", lambda: llamadas.append(1))
    assert worker.iniciar_consumidor_si_habilitado() is None
    assert llamadas == []


def test_worker_arranca_consumidor_con_variable(monkeypatch):
    """HJ_SQS_IN_SEND_WORKER=1 conserva el comportamiento antiguo (escape)."""
    from app.services import sqs_events
    from app.workers import worker
    monkeypatch.setenv("HJ_SQS_IN_SEND_WORKER", "1")
    llamadas: list = []
    monkeypatch.setattr(sqs_events, "start_consumer", lambda: llamadas.append(1))
    worker.iniciar_consumidor_si_habilitado()
    assert llamadas == [1]


def test_events_worker_inactivo_sin_cola(monkeypatch):
    """Sin AWS_SQS_EVENTS_QUEUE_URL: latido 'inactivo' y bucle vivo (no crash)."""
    from app.workers import events_worker
    monkeypatch.setattr(settings, "AWS_SQS_EVENTS_QUEUE_URL", None)
    latidos: list = []
    monkeypatch.setattr(events_worker.heartbeat, "beat",
                        lambda name, **k: latidos.append((name, k.get("extra"))))
    monkeypatch.setattr(events_worker, "_espera_con_apagado",
                        lambda *a, **k: events_worker.stop())
    monkeypatch.setattr(events_worker, "_running", True)
    events_worker.run_forever()
    assert latidos and latidos[0][0] == "sqs_consumer"
    assert latidos[0][1] and "inactivo" in str(latidos[0][1].get("estado"))


def test_events_worker_arranca_bucle_consumidor(monkeypatch):
    """Con cola configurada, el events-worker lanza el bucle de consumo SQS."""
    from app.services import sqs_events
    from app.workers import events_worker
    monkeypatch.setattr(settings, "AWS_SQS_EVENTS_QUEUE_URL",
                        "https://sqs.us-east-2.amazonaws.com/123/hola")
    ejecutado: list = []
    import threading

    def _bucle_falso(stop):
        ejecutado.append(1)

    monkeypatch.setattr(sqs_events, "_bucle_consumidor", _bucle_falso)
    # Detiene run_forever en cuanto el bucle consumidor haya arrancado y
    # terminado (el hilo muere al volver _bucle_falso).
    def _espera(segundos, *a, **k):
        if ejecutado:
            events_worker.stop()

    monkeypatch.setattr(events_worker, "_espera_con_apagado", _espera)
    monkeypatch.setattr(events_worker, "_running", True)
    events_worker.run_forever()
    assert ejecutado == [1]


def test_heartbeat_acepta_nombre_sqs_consumer():
    """El nombre usado por el healthcheck del compose es válido para la CLI."""
    from app.workers import heartbeat
    assert heartbeat._key("sqs_consumer") == "hj:hb:sqs_consumer"
