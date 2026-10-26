"""Pruebas de contactos, CSV, consentimiento, duplicados y supresión."""
from __future__ import annotations

import io
import uuid

import pytest

from app.models import Contact, Suppression
from tests.conftest import SessionLocal

CSV_VALIDO = (
    "email,nombre,apellido,ciudad\n"
    "maria.garcia@example.com,María,García,Madrid\n"
    "juan.perez@example.com,Juan,Pérez,Bogotá\n"
    "ANA.LOPEZ@Example.COM,Ana,López,Lima\n"
    "correo-invalido,José,Bad,X\n"
    "maria.garcia@example.com,Duplicada,X,Y\n"
)


def test_normalizacion_y_validacion_email():
    from app.services.contacts import is_valid_email, normalize_email
    assert normalize_email("  ANA LOPEZ@Example.COM ") == "ana lopez@example.com"
    # Los espacios internos invalidan
    assert not is_valid_email("ana lopez@example.com")
    assert is_valid_email("Maria.Garcia@example.com")
    assert not is_valid_email("sin-arroba.com")
    assert not is_valid_email("@dominio.com")


def test_import_csv_crea_actualiza_y_deduplica(client, admin_auth):
    files = {"archivo": ("contactos.csv", CSV_VALIDO.encode("utf-8"), "text/csv")}
    data = {"consent_source": "formulario_web", "consent_text": "Acepto recibir comunicaciones"}
    resp = client.post("/api/v1/contacts/import", files=files, data=data)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["creados"] == 3
    assert result["filas_invalidas"] == 1
    assert result["duplicados_en_archivo"] == 1
    assert result["notices"] == {}

    # Reimportar: duplicados contra BD -> actualizados, no creados
    resp2 = client.post("/api/v1/contacts/import", files=files, data=data)
    result2 = resp2.json()
    assert result2["creados"] == 0
    assert result2["actualizados"] == 3

    # El correo se normalizó (minúsculas)
    db = SessionLocal()
    contacto = db.query(Contact).filter(Contact.email == "ana.lopez@example.com").first()
    db.close()
    assert contacto is not None
    assert contacto.consent_source == "formulario_web"


def test_import_csv_simulacion_no_persiste(client, admin_auth):
    csv_data = f"email,nombre\nuniq-{uuid.uuid4().hex[:6]}@x.com,Test\n".encode()
    files = {"archivo": ("nuevo.csv", csv_data, "text/csv")}
    resp = client.post("/api/v1/contacts/import", files=files,
                       data={"consent_source": "prueba", "dry_run": "true"})
    assert resp.status_code == 200
    assert resp.json()["simulacion"] is True
    db = SessionLocal()
    count = db.query(Contact).filter(Contact.first_name == "Test").count()
    db.close()
    assert count == 0


def test_import_csv_sin_columna_email_falla(client, admin_auth):
    files = {"archivo": ("mal.csv", b"nombre,apellido\nAna,Garcia\n", "text/csv")}
    resp = client.post("/api/v1/contacts/import", files=files,
                       data={"consent_source": "prueba"})
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"]


def test_alta_manual_con_consentimiento(client, admin_auth):
    email = f"manual-{uuid.uuid4().hex[:6]}@test.local"
    resp = client.post("/api/v1/contacts", json={
        "email": email, "first_name": "Luis", "consent_source": "alta_manual",
        "consent_text": "Consentimiento explícito en tienda"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "suscripto"

    # Historial de consentimiento registrado
    cid = resp.json()["id"]
    hist = client.get(f"/api/v1/contacts/{cid}/consent")
    events = [e["event"] for e in hist.json()]
    assert "alta" in events and "consentimiento" in events


def test_baja_impide_reactivacion_por_import(client, admin_auth):
    """Un contacto dado de baja NO se reactiva al reimportar el CSV."""
    email = f"baja-{uuid.uuid4().hex[:6]}@test.local"
    resp = client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "formulario_web"})
    cid = resp.json()["id"]
    client.delete(f"/api/v1/contacts/{cid}")  # baja + supresión

    csv_data = f"email\n{email}\n".encode()
    files = {"archivo": ("re.csv", csv_data, "text/csv")}
    resp2 = client.post("/api/v1/contacts/import", files=files,
                        data={"consent_source": "formulario_web"})
    result = resp2.json()
    assert result["creados"] == 0
    assert result["notices"].get("existente_estado_dado_baja") == 1

    db = SessionLocal()
    c = db.query(Contact).filter(Contact.email == email).first()
    db.close()
    assert c.status == "dado_baja"


def test_supresion_bloquea_y_se_puede_quitar(client, admin_auth):
    email = f"sup-{uuid.uuid4().hex[:6]}@test.local"
    resp = client.post("/api/v1/suppression", json={"email": email, "reason": "manual"})
    assert resp.status_code == 201

    db = SessionLocal()
    assert db.query(Suppression).filter(Suppression.email == email).first() is not None
    db.close()

    # Eliminación de la supresión
    del_resp = client.delete(f"/api/v1/suppression/{email}")
    assert del_resp.status_code == 204
    db = SessionLocal()
    assert db.query(Suppression).filter(Suppression.email == email).first() is None
    db.close()


def test_listas_tags_segmentos_crud(client, admin_auth):
    lst = client.post("/api/v1/lists", json={"name": f"Lista-{uuid.uuid4().hex[:5]}"})
    assert lst.status_code == 201
    tag = client.post("/api/v1/tags", json={"name": f"vip-{uuid.uuid4().hex[:5]}", "color": "#10b981"})
    assert tag.status_code == 201

    seg = client.post("/api/v1/segments", json={
        "name": f"Seg-{uuid.uuid4().hex[:5]}",
        "rules": [{"campo": "email", "op": "contiene", "valor": "@test.local"}]})
    assert seg.status_code == 201

    # Filtro por lista en contactos
    lid = lst.json()["id"]
    email = f"lst-{uuid.uuid4().hex[:6]}@test.local"
    c = client.post("/api/v1/contacts", json={
        "email": email, "consent_source": "prueba", "list_ids": [lid]})
    assert c.status_code == 201
    filtered = client.get(f"/api/v1/contacts?list_id={lid}")
    assert filtered.status_code == 200
    assert any(item["email"] == email for item in filtered.json()["items"])


def test_export_csv_autorizado(client, admin_auth):
    resp = client.get("/api/v1/contacts/export/csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    body = resp.text
    assert body.startswith("email,first_name,last_name,status")


def test_busqueda_y_filtro_por_estado(client, admin_auth):
    email = f"busq-{uuid.uuid4().hex[:6]}@test.local"
    client.post("/api/v1/contacts", json={"email": email, "consent_source": "prueba"})
    resp = client.get(f"/api/v1/contacts?q={email}&estado=suscripto")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
