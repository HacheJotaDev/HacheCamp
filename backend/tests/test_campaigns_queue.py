"""Pruebas de campañas, cola, worker, pausas, reintentos y errores SES.

SES está simulado (fake_ses); nunca se envían correos reales.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.models import Campaign, CampaignMessage, Contact, Suppression
from app.services import queue as send_queue
from app.workers.worker import process_one
from tests.conftest import SessionLocal


def _setup_campaign(client, admin_auth, *, contact_emails, category="promocional",
                    with_contact_list=True):
    """Crea lista + contactos + campaña aprobada y devuelve (campaign_id, contact_ids)."""
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    lid = lst.json()["id"]
    cids = []
    for email in contact_emails:
        c = client.post("/api/v1/contacts", json={
            "email": email, "consent_source": "prueba",
            "list_ids": [lid] if with_contact_list else []})
        cids.append(c.json()["id"])

    camp = client.post("/api/v1/campaigns", json={
        "name": f"C-{uuid.uuid4().hex[:6]}",
        "subject": "Hola {{contact.first_name}}",
        "from_name": "HJ Test", "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques",
        "blocks": [
            {"type": "titulo", "props": {"text": "Hola {{contact.first_name}}", "level": 1}},
            {"type": "texto", "props": {"content": "<p>Contenido de prueba</p>"}},
            {"type": "pie", "props": {"text": "Nos vemos"}},
        ],
        "category": category, "list_id": lid})
    cid = camp.json()["id"]
    # Aprobación (admin)
    client.post(f"/api/v1/campaigns/{cid}/approve")
    return cid, cids


def test_flujo_completo_campana_envio(client, admin_auth, fake_ses):
    emails = [f"c1-{uuid.uuid4().hex[:6]}@test.local",
              f"c2-{uuid.uuid4().hex[:6]}@test.local"]
    cid, _ = _setup_campaign(client, admin_auth, contact_emails=emails)

    # Estimación de destinatarios
    est = client.get(f"/api/v1/campaigns/{cid}/recipients-estimate")
    assert est.json()["destinatarios"] == 2

    # Enviar ahora
    resp = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert resp.status_code == 200, resp.text
    assert resp.json()["nuevos"] == 2

    # Procesar la cola con el worker (real)
    from app.core.db import SessionLocal as S
    processed = 0
    for _ in range(20):
        db = S()
        mid = send_queue.pop_message(timeout_seconds=1)
        if not mid:
            db.close()
            break
        result = process_one(db, mid)
        processed += 1
        db.close()
    assert processed == 2

    # Estado final: completada, mensajes aceptados
    db = S()
    campaign = db.get(Campaign, cid)
    msgs = db.query(CampaignMessage).filter(CampaignMessage.campaign_id == cid).all()
    db.close()
    assert campaign.status == "completada"
    assert all(m.status == "aceptado" for m in msgs)
    assert all(m.ses_message_id for m in msgs)
    # SES recibió 2 envíos con contenido renderizado
    assert len(fake_ses.sent) == 2
    # El asunto se personalizó por destinatario
    raw_bodies = [s["Content"]["Raw"]["Data"].decode() for s in fake_ses.sent]
    assert any("Hola " in b for b in raw_bodies)
    # Cabeceras de baja RFC 8058 presentes en promocionales
    assert any("List-Unsubscribe" in b for b in raw_bodies)
    assert any("List-Unsubscribe-Post" in b for b in raw_bodies)


def test_envio_sin_aprobacion_rechazado(client, admin_auth, fake_ses):
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    client.post("/api/v1/contacts", json={
        "email": f"x-{uuid.uuid4().hex[:6]}@test.local",
        "consent_source": "prueba", "list_ids": [lst.json()["id"]]})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Sin aprobar", "subject": "S",
        "from_name": "HJ", "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "category": "promocional",
        "list_id": lst.json()["id"]})
    cid = camp.json()["id"]
    resp = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert resp.status_code == 403
    assert "aprobación" in resp.json()["detail"].lower()


def test_operador_no_puede_aprobar(client, admin_auth):
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Xx", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    # Operador
    email = f"op2-{uuid.uuid4().hex[:5]}@test.local"
    from app.core import security
    from app.models import User
    db = SessionLocal()
    db.add(User(email=email, name="Op", role="operador",
                password_hash=security.hash_password("ClaveOperador123")))
    db.commit()
    db.close()
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveOperador123"})
    client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    resp = client.post(f"/api/v1/campaigns/{camp.json()['id']}/approve")
    assert resp.status_code == 403


def test_suprimidos_y_bajas_excluidos_del_envio(client, admin_auth, fake_ses):
    """Bajas y suprimidos no reciben la campaña promocional."""
    ok_email = f"ok-{uuid.uuid4().hex[:6]}@test.local"
    baja_email = f"baja-{uuid.uuid4().hex[:6]}@test.local"
    sup_email = f"sup-{uuid.uuid4().hex[:6]}@test.local"
    cid, cids = _setup_campaign(client, admin_auth,
                                contact_emails=[ok_email, baja_email, sup_email])
    # Dar de baja al segundo contacto
    client.delete(f"/api/v1/contacts/{cids[1]}")
    # Suprimir al tercero
    client.post("/api/v1/suppression", json={"email": sup_email, "reason": "manual"})

    resp = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert resp.status_code == 200
    assert resp.json()["nuevos"] == 1  # solo el contacto suscripto y no suprimido


def test_pausa_y_reanudacion(client, admin_auth, fake_ses):
    emails = [f"p{i}-{uuid.uuid4().hex[:6]}@test.local" for i in range(3)]
    cid, _ = _setup_campaign(client, admin_auth, contact_emails=emails)
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    # Pausa ANTES de procesar
    client.post(f"/api/v1/campaigns/{cid}/pause")
    detail = client.get(f"/api/v1/campaigns/{cid}").json()
    assert detail["status"] == "pausada"

    # El worker reencola en vez de enviar
    from app.core.db import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    assert mid
    db = S()
    result = process_one(db, mid)
    db.close()
    assert result["accion"] == "reencolado"

    # Reanudar y procesar todo
    client.post(f"/api/v1/campaigns/{cid}/resume")
    for _ in range(10):
        mid = send_queue.pop_message(timeout_seconds=1)
        if not mid:
            break
        db = S()
        process_one(db, mid)
        db.close()
    db = S()
    campaign = db.get(Campaign, cid)
    estados = [m.status for m in db.query(CampaignMessage).filter_by(campaign_id=cid).all()]
    db.close()
    assert campaign.status == "completada"
    assert all(e == "aceptado" for e in estados)


def test_cancelacion_marca_pendientes(client, admin_auth, fake_ses):
    emails = [f"c-{i}-{uuid.uuid4().hex[:5]}@test.local" for i in range(2)]
    cid, _ = _setup_campaign(client, admin_auth, contact_emails=emails)
    client.post(f"/api/v1/campaigns/{cid}/send-now")
    client.post(f"/api/v1/campaigns/{cid}/cancel")
    detail = client.get(f"/api/v1/campaigns/{cid}").json()
    assert detail["status"] == "cancelada"
    from app.core.db import SessionLocal as S
    db = S()
    estados = [m.status for m in db.query(CampaignMessage).filter_by(campaign_id=cid).all()]
    db.close()
    assert all(e in ("pendiente", "cancelado") for e in estados)


def test_reintento_por_throttling_y_exito(client, admin_auth, fake_ses):
    emails = [f"t-{uuid.uuid4().hex[:6]}@test.local"]
    cid, _ = _setup_campaign(client, admin_auth, contact_emails=emails)
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    # Primera vez: SES lanza throttling
    fake_ses.behavior = "throttling"
    from app.core.db import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    db = S()
    result = process_one(db, mid)
    db.close()
    assert result["accion"] == "reintento_programado"
    assert result["espera"] >= 60  # backoff exponencial

    # Segunda vez: SES funciona -> aceptado
    fake_ses.behavior = "ok"
    moved = send_queue.move_due_retries()  # fuerza el paso a la cola principal
    # El reintento aún no vence; procesamos el id directamente
    db = S()
    result2 = process_one(db, mid)
    db.close()
    assert result2["accion"] == "enviado"


def test_error_permanente_marca_error(client, admin_auth, fake_ses):
    emails = [f"err-{uuid.uuid4().hex[:6]}@test.local"]
    cid, _ = _setup_campaign(client, admin_auth, contact_emails=emails)
    client.post(f"/api/v1/campaigns/{cid}/send-now")
    fake_ses.behavior = "rejected"
    from app.core.db import SessionLocal as S
    mid = send_queue.pop_message(timeout_seconds=2)
    db = S()
    result = process_one(db, mid)
    db.close()
    assert result["accion"] == "error"

    detail = client.get(f"/api/v1/campaigns/{cid}").json()
    assert detail["progreso"]["por_estado"]["error"] == 1


def test_idempotencia_sin_duplicados(client, admin_auth, fake_ses):
    """Encolar dos veces no crea mensajes duplicados."""
    emails = [f"i-{uuid.uuid4().hex[:6]}@test.local"]
    cid, _ = _setup_campaign(client, admin_auth, contact_emails=emails)
    client.post(f"/api/v1/campaigns/{cid}/send-now")
    # Segunda encolada de la misma campaña (estado en_cola permite)
    client.post(f"/api/v1/campaigns/{cid}/send-now")
    detail = client.get(f"/api/v1/campaigns/{cid}").json()
    assert detail["progreso"]["total"] == 1
    assert detail["progreso"]["por_estado"]["pendiente"] == 1


def test_duplicar_campana(client, admin_auth):
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Original", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    dup = client.post(f"/api/v1/campaigns/{camp.json()['id']}/duplicate")
    assert dup.status_code == 201
    assert "(copia)" in dup.json()["name"]
    assert dup.json()["status"] == "borrador"


def test_transiciones_invalidas_rechazadas(client, admin_auth):
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Tt", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    cid = camp.json()["id"]
    # De borrador no se puede pausar
    resp = client.post(f"/api/v1/campaigns/{cid}/pause")
    assert resp.status_code == 400


def test_programacion_requiere_aprobacion(client, admin_auth):
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:6]}"})
    camp = client.post("/api/v1/campaigns", json={
        "name": "Pp", "subject": "S", "from_name": "HJ",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "list_id": lst.json()["id"]})
    resp = client.post(f"/api/v1/campaigns/{camp.json()['id']}/schedule",
                       json={"scheduled_at": "2027-01-01T10:00:00Z", "timezone": "UTC"})
    assert resp.status_code == 400


def test_segmento_en_campana(client, admin_auth, fake_ses):
    """Campaña por segmento en lugar de lista."""
    seg = client.post("/api/v1/segments", json={
        "name": f"Seg-{uuid.uuid4().hex[:5]}",
        "rules": [{"campo": "email", "op": "empieza", "valor": "seg-"}]})
    seg_id = seg.json()["id"]
    emails = [f"seg-{uuid.uuid4().hex[:6]}@test.local", f"otro-{uuid.uuid4().hex[:6]}@test.local"]
    for e in emails:
        client.post("/api/v1/contacts", json={"email": e, "consent_source": "prueba"})

    camp = client.post("/api/v1/campaigns", json={
        "name": f"SegCamp-{uuid.uuid4().hex[:4]}", "subject": "S",
        "from_name": "HJ", "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques", "blocks": [], "category": "promocional",
        "segment_id": seg_id})
    cid = camp.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    resp = client.post(f"/api/v1/campaigns/{cid}/send-now")
    # Solo el contacto seg-* entra (1)
    assert resp.json()["destinatarios"] == 1
