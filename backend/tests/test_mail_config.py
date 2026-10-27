"""Pruebas de regresión: Configuración de correo (remitente y asunto predeterminados).

Cubre:
- Persistencia y recarga de los tres valores (AppSetting, sin migraciones).
- Permisos: solo administradores guardan; cualquier usuario autenticado lee.
- Validación: dominio exclusivo, anti inyección de cabeceras (CRLF), longitudes.
- Integración: creación de campañas usa la configuración y no sobrescribe
  campañas con remitente/asunto propios.
- Envíos de prueba: sin reintroducir remitente/asunto (config por defecto),
  y las pruebas de campaña usan el remitente propio de la campaña.
"""
from __future__ import annotations

import uuid
from email import message_from_bytes
from email.header import decode_header, make_header

import pytest

from app.core import security
from app.core.db import SessionLocal
from app.models import AppSetting, User

DOMINIO = "leisureparts4u.store"
CONFIG_VALIDA = {
    "from_name": "HJ Duro",
    "from_email": f"notificaciones@{DOMINIO}",
    "subject": "Novedades especiales para ti",
}
CLAVES = ("default_from_name", "default_from_email", "default_subject")


# --------------------------------------------------------------------------- #
# Ayudantes y aislamiento
# --------------------------------------------------------------------------- #


@pytest.fixture
def limpiar_mail_config():
    """Deja la configuración de correo como estaba tras cada prueba."""
    yield
    db = SessionLocal()
    db.query(AppSetting).filter(AppSetting.key.in_(CLAVES)).delete(
        synchronize_session=False
    )
    db.commit()
    db.close()


def _crear_y_login(client, role: str) -> str:
    """Crea un usuario del rol indicado y abre sesión; devuelve su CSRF."""
    db = SessionLocal()
    email = f"{role}-{uuid.uuid4().hex[:8]}@test.local"
    user = User(email=email, name=role.title(), role=role,
                password_hash=security.hash_password("ClaveSegura123"))
    db.add(user)
    db.commit()
    db.close()
    resp = client.post("/api/v1/auth/login", json={"email": email,
                                                   "password": "ClaveSegura123"})
    assert resp.status_code == 200, resp.text
    csrf = resp.json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": csrf})
    return csrf


def _configurar(client, **sobrescrituras):
    payload = {**CONFIG_VALIDA, **sobrescrituras}
    resp = client.put("/api/v1/system/mail-config", json=payload)
    assert resp.status_code == 200, resp.text
    return payload


def _crear_lista(client) -> str:
    r = client.post("/api/v1/lists", json={"name": f"Lista {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _cabecera_decodificada(mensaje_bytes: bytes, nombre: str) -> str:
    """Lee una cabecera RFC 5322 decodificando la codificación RFC 2047.

    Así se verifica lo que un cliente (Gmail, Outlook) mostraría realmente.
    """
    mensaje = message_from_bytes(mensaje_bytes)
    valor = mensaje.get(nombre, "")
    return str(make_header(decode_header(valor)))


def _crear_campana(client, lista: str, **campos):
    payload = {
        "name": campos.pop("name", f"Campaña {uuid.uuid4().hex[:6]}"),
        "mode": "html",
        "html": "<html><body><p>Contenido de prueba</p></body></html>",
        "list_id": lista,
        **campos,
    }
    return client.post("/api/v1/campaigns", json=payload)


# --------------------------------------------------------------------------- #
# 1. Lectura, persistencia y permisos
# --------------------------------------------------------------------------- #


def test_get_mail_config_requiere_sesion(client):
    assert client.get("/api/v1/system/mail-config").status_code == 401


def test_valores_por_defecto_sin_configuracion(client, admin_auth, limpiar_mail_config):
    resp = client.get("/api/v1/system/mail-config")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["from_name"] == "HJ Campaigns"
    assert data["from_email"] == f"notificaciones@{DOMINIO}"
    assert data["subject"] == ""
    assert data["dominio"] == DOMINIO
    assert data["guardado"]["default_from_name"] is None


def test_guardar_y_recargar_persistente(client, admin_auth):
    _configurar(client)
    resp = client.get("/api/v1/system/mail-config")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["from_name"] == CONFIG_VALIDA["from_name"]
    assert data["from_email"] == CONFIG_VALIDA["from_email"]
    assert data["subject"] == CONFIG_VALIDA["subject"]
    assert data["guardado"]["default_subject"] == CONFIG_VALIDA["subject"]
    # Persistido realmente en app_settings
    db = SessionLocal()
    filas = {s.key: (s.value or {}).get("valor")
             for s in db.query(AppSetting).filter(AppSetting.key.in_(CLAVES)).all()}
    db.close()
    assert filas["default_from_name"] == CONFIG_VALIDA["from_name"]
    assert filas["default_from_email"] == CONFIG_VALIDA["from_email"]


def test_operador_lee_pero_no_guarda(client, admin_auth, limpiar_mail_config):
    _configurar(client)
    _crear_y_login(client, "operador")
    resp = client.get("/api/v1/system/mail-config")
    assert resp.status_code == 200
    resp = client.put("/api/v1/system/mail-config", json=CONFIG_VALIDA)
    assert resp.status_code == 403, "Solo un administrador puede modificar la configuración"


def test_sin_sesion_no_guarda(client):
    resp = client.put("/api/v1/system/mail-config", json=CONFIG_VALIDA)
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 2. Validación: dominio, inyección de cabeceras y longitudes
# --------------------------------------------------------------------------- #


def test_rechaza_dominio_ajeno(client, admin_auth, limpiar_mail_config):
    resp = client.put("/api/v1/system/mail-config",
                      json={**CONFIG_VALIDA, "from_email": "usuario@gmail.com"})
    assert resp.status_code == 400, resp.text
    assert DOMINIO in resp.json()["detail"]


def test_rechaza_inyeccion_de_cabeceras(client, admin_auth, limpiar_mail_config):
    # Salto de línea en el nombre (permite inyectar cabeceras como Bcc)
    r1 = client.put("/api/v1/system/mail-config",
                    json={**CONFIG_VALIDA, "from_name": "HJ\nBcc: victima@example.com"})
    assert r1.status_code == 400
    # Retorno de carro en el asunto
    r2 = client.put("/api/v1/system/mail-config",
                    json={**CONFIG_VALIDA, "subject": "Asunto\r\nBcc: victima@example.com"})
    assert r2.status_code == 400
    # Salto de línea en el correo
    r3 = client.put("/api/v1/system/mail-config",
                    json={**CONFIG_VALIDA, "from_email": "a@leisureparts4u.store\nBcc: x"})
    assert r3.status_code == 400


def test_rechaza_valores_fuera_de_rango(client, admin_auth, limpiar_mail_config):
    assert client.put("/api/v1/system/mail-config",
                      json={**CONFIG_VALIDA, "from_name": "A"}).status_code == 400
    assert client.put("/api/v1/system/mail-config",
                      json={**CONFIG_VALIDA, "from_name": "X" * 121}).status_code == 400
    assert client.put("/api/v1/system/mail-config",
                      json={**CONFIG_VALIDA, "subject": ""}).status_code == 400
    assert client.put("/api/v1/system/mail-config",
                      json={**CONFIG_VALIDA, "subject": "Y" * 256}).status_code == 400


def test_ajustes_genericos_validan_claves_de_correo(client, admin_auth, limpiar_mail_config):
    """El endpoint genérico /settings comparte las mismas reglas estrictas."""
    r1 = client.put("/api/v1/settings/default_from_name",
                    json={"value": "Mal\nNombre"})
    assert r1.status_code == 400
    r2 = client.put("/api/v1/settings/default_from_email",
                    json={"value": "trampas@gmail.com"})
    assert r2.status_code == 400
    r3 = client.put("/api/v1/settings/default_subject",
                    json={"value": "Asunto válido"})
    assert r3.status_code == 200


# --------------------------------------------------------------------------- #
# 3. Integración con campañas
# --------------------------------------------------------------------------- #


def test_campana_carga_configuracion_automaticamente(client, admin_auth, limpiar_mail_config):
    _configurar(client)
    lista = _crear_lista(client)
    # Sin subject, sin from_name, sin from_email: todo sale de la configuración
    resp = _crear_campana(client, lista)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["subject"] == CONFIG_VALIDA["subject"]
    assert data["from_name"] == CONFIG_VALIDA["from_name"]
    assert data["from_email"] == CONFIG_VALIDA["from_email"]


def test_campana_con_datos_propios_no_se_sobrescribe(client, admin_auth, limpiar_mail_config):
    _configurar(client)
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista,
                          name="Campaña propia",
                          subject="Mi asunto propio",
                          from_name="Equipo LP",
                          from_email=f"equipo@{DOMINIO}")
    assert resp.status_code == 201, resp.text
    campana_id = resp.json()["id"]
    # Cambiar la configuración global NO debe alterar la campaña existente
    _configurar(client, from_name="Otro Nombre", subject="Otro asunto global")
    detalle = client.get(f"/api/v1/campaigns/{campana_id}")
    assert detalle.status_code == 200
    data = detalle.json()
    assert data["subject"] == "Mi asunto propio"
    assert data["from_name"] == "Equipo LP"
    assert data["from_email"] == f"equipo@{DOMINIO}"


def test_campana_rechaza_remitente_de_otro_dominio(client, admin_auth, limpiar_mail_config):
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista, from_email="yo@gmail.com")
    assert resp.status_code == 400
    assert DOMINIO in resp.json()["detail"]


def test_campana_rechaza_from_name_con_salto_de_linea(client, admin_auth):
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista, from_name="HJ\nBcc: victima@example.com")
    assert resp.status_code == 422, "El esquema debe rechazar saltos de línea"


def test_campana_sin_asunto_y_sin_configurar_falla_con_mensaje_claro(
        client, admin_auth, limpiar_mail_config):
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista)
    assert resp.status_code == 400
    assert "asunto" in resp.json()["detail"].lower()


def test_actualizacion_campana_rechaza_inyeccion(client, admin_auth):
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista, subject="Asunto ok")
    assert resp.status_code == 201
    campana_id = resp.json()["id"]
    r = client.put(f"/api/v1/campaigns/{campana_id}",
                   json={"subject": "Trampa\r\nBcc: x@example.com"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# 4. Envíos de prueba
# --------------------------------------------------------------------------- #


def test_prueba_de_plantilla_usa_config_sin_reintroducir(
        client, admin_auth, fake_ses, limpiar_mail_config):
    _configurar(client)
    resp = client.post("/api/v1/templates/test-send", json={
        "to_email": f"destino-{uuid.uuid4().hex[:6]}@test.local",
        "mode": "html",
        "html": "<html><body><p>Prueba</p></body></html>",
        # Sin from_email, from_name ni subject
    })
    assert resp.status_code == 200, resp.text
    assert fake_ses.sent, "SES debe haber recibido el mensaje simulado"
    enviado = fake_ses.sent[0]
    esperado = f"{CONFIG_VALIDA['from_name']} <{CONFIG_VALIDA['from_email']}>"
    assert enviado["FromEmailAddress"] == esperado
    cuerpo = enviado["Content"]["Raw"]["Data"].decode("utf-8", errors="replace")
    assert CONFIG_VALIDA["subject"] in cuerpo


def test_prueba_de_plantilla_con_valores_propios_se_respetan(
        client, admin_auth, fake_ses, limpiar_mail_config):
    _configurar(client)
    resp = client.post("/api/v1/templates/test-send", json={
        "to_email": f"destino-{uuid.uuid4().hex[:6]}@test.local",
        "mode": "html",
        "html": "<html><body><p>Prueba</p></body></html>",
        "from_name": "Nombre Específico",
        "subject": "Asunto específico",
    })
    assert resp.status_code == 200, resp.text
    enviado = fake_ses.sent[0]
    assert "Nombre Específico" in enviado["FromEmailAddress"]
    cuerpo = enviado["Content"]["Raw"]["Data"]
    assert _cabecera_decodificada(cuerpo, "Subject") == "Asunto específico"


def test_prueba_de_campana_usa_remitente_de_la_campana(
        client, admin_auth, fake_ses, limpiar_mail_config):
    _configurar(client)
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista)
    assert resp.status_code == 201
    campana_id = resp.json()["id"]
    r = client.post(f"/api/v1/campaigns/{campana_id}/test-send",
                    json={"to_email": f"prueba-{uuid.uuid4().hex[:6]}@test.local"})
    assert r.status_code == 200, r.text
    enviado = fake_ses.sent[0]
    esperado = f"{CONFIG_VALIDA['from_name']} <{CONFIG_VALIDA['from_email']}>"
    assert enviado["FromEmailAddress"] == esperado
