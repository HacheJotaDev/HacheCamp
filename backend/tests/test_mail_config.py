"""Pruebas de regresión: Configuración de correo (única fuente de verdad).

Cubre:
- Persistencia y recarga de los tres valores (AppSetting, sin migraciones).
- Permisos: solo administradores guardan; cualquier usuario autenticado lee.
- Validación: dominio exclusivo, anti inyección de cabeceras (CRLF), longitudes.
- Fuente única de verdad: TODOS los envíos (campañas nuevas, envíos inmediatos
  y programados vía worker, pruebas de campaña y de plantilla) usan los tres
  valores globales vigentes en el momento del envío, ignorando cualquier
  remitente/asunto enviado por el frontend o almacenado en campañas antiguas.
- Los registros históricos de las campañas nunca se reescriben.
- Cabeceras MIME From/Subject reales enviadas a SES (decodificadas como las
  mostraría Gmail), exactamente iguales a los valores configurados.
"""
from __future__ import annotations

import uuid
from email import message_from_bytes
from email.header import decode_header, make_header

import pytest

from app.core import security
from app.core.db import SessionLocal
from app.models import AppSetting, Campaign, User
from app.services import mail_config

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


def test_creacion_ignora_remitente_y_asunto_del_frontend(
        client, admin_auth, limpiar_mail_config):
    """Con configuración guardada, el payload no puede introducir remitente
    ni asunto individuales: la campaña nace con la configuración global."""
    _configurar(client)
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista,
                          name="Campaña con trampa",
                          subject="Asunto manipulado",
                          from_name="Equipo LP",
                          from_email=f"equipo@{DOMINIO}")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["subject"] == CONFIG_VALIDA["subject"]
    assert data["from_name"] == CONFIG_VALIDA["from_name"]
    assert data["from_email"] == CONFIG_VALIDA["from_email"]


def test_campana_existente_no_reescrita_y_envio_usa_config_vigente(
        client, admin_auth, limpiar_mail_config):
    """Los registros históricos nunca se reescriben: cambiar la configuración
    global no toca los datos almacenados de campañas existentes."""
    _configurar(client)
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista)
    assert resp.status_code == 201
    campana_id = resp.json()["id"]
    # Cambiar la configuración global NO debe alterar la fila histórica
    _configurar(client, from_name="Otro Nombre", subject="Otro asunto global")
    db = SessionLocal()
    fila = db.get(Campaign, campana_id)
    snap = (fila.subject, fila.from_name, fila.from_email)
    db.close()
    assert snap == (CONFIG_VALIDA["subject"], CONFIG_VALIDA["from_name"],
                    CONFIG_VALIDA["from_email"])


def test_campana_con_remitente_ajeno_en_payload_es_ignorado(
        client, admin_auth, limpiar_mail_config):
    """Sin config guardada el payload es el fallback, pero el dominio exclusivo
    se aplica igualmente: un correo de otro dominio se rechaza incluso si
    alguien manipula la solicitud HTTP."""
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
# 4. Envíos de prueba (plantillas y campañas): única fuente de verdad
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
    cuerpo = enviado["Content"]["Raw"]["Data"]
    assert _cabecera_decodificada(cuerpo, "Subject") == CONFIG_VALIDA["subject"]


def test_prueba_de_plantilla_ignora_valores_del_frontend(
        client, admin_auth, fake_ses, limpiar_mail_config):
    """Aunque un cliente antiguo (o una solicitud manipulada) envíe
    from_email/from_name/subject, el backend aplica los valores globales."""
    _configurar(client)
    resp = client.post("/api/v1/templates/test-send", json={
        "to_email": f"destino-{uuid.uuid4().hex[:6]}@test.local",
        "mode": "html",
        "html": "<html><body><p>Prueba</p></body></html>",
        "from_name": "HJ Campaigns",
        "from_email": f"antiguo@{DOMINIO}",
        "subject": "[Prueba] HJ Campaigns",
    })
    assert resp.status_code == 200, resp.text
    enviado = fake_ses.sent[0]
    esperado = f"{CONFIG_VALIDA['from_name']} <{CONFIG_VALIDA['from_email']}>"
    assert enviado["FromEmailAddress"] == esperado
    cuerpo = enviado["Content"]["Raw"]["Data"]
    assert _cabecera_decodificada(cuerpo, "Subject") == CONFIG_VALIDA["subject"]
    assert _cabecera_decodificada(cuerpo, "From") == esperado


def test_prueba_de_plantilla_sin_asunto_configurado_rechaza_con_mensaje_claro(
        client, admin_auth, fake_ses, limpiar_mail_config):
    """Ya no se inventa un asunto genérico: sin configurar no hay envío."""
    resp = client.post("/api/v1/templates/test-send", json={
        "to_email": f"destino-{uuid.uuid4().hex[:6]}@test.local",
        "mode": "html",
        "html": "<html><body><p>Prueba</p></body></html>",
    })
    assert resp.status_code == 400, resp.text
    assert "Configuración de correo" in resp.json()["detail"]
    assert not fake_ses.sent, "SES no debe recibir nada sin asunto configurado"


def test_prueba_de_campana_usa_config_vigente_no_snapshot(
        client, admin_auth, fake_ses, limpiar_mail_config):
    """La prueba de campaña usa la configuración global vigente EN EL MOMENTO
    del envío, aunque la campaña guarde un remitente/asunto anteriores; la
    fila histórica queda intacta."""
    _configurar(client)
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista)
    assert resp.status_code == 201
    campana_id = resp.json()["id"]

    # El administrador cambia la configuración tras crear la campaña
    nueva = _configurar(client, from_name="HacheJota",
                        from_email=f"hachejota@{DOMINIO}",
                        subject="Notificaciones")

    r = client.post(f"/api/v1/campaigns/{campana_id}/test-send",
                    json={"to_email": f"prueba-{uuid.uuid4().hex[:6]}@test.local"})
    assert r.status_code == 200, r.text
    enviado = fake_ses.sent[0]
    esperado = f"{nueva['from_name']} <{nueva['from_email']}>"
    assert enviado["FromEmailAddress"] == esperado
    cuerpo = enviado["Content"]["Raw"]["Data"]
    assert _cabecera_decodificada(cuerpo, "Subject") == nueva["subject"]
    assert _cabecera_decodificada(cuerpo, "From") == esperado

    # La campaña conserva sus datos históricos (snapshot de creación)
    db = SessionLocal()
    fila = db.get(Campaign, campana_id)
    snap = (fila.subject, fila.from_name, fila.from_email)
    db.close()
    assert snap == (CONFIG_VALIDA["subject"], CONFIG_VALIDA["from_name"],
                    CONFIG_VALIDA["from_email"])


def test_prueba_de_campana_asunto_exacto_sin_prefijos(
        client, admin_auth, fake_ses, limpiar_mail_config):
    """El Subject enviado a SES es exactamente el configurado, sin prefijos
    tipo '[PRUEBA]' ni '[Prueba] HJ Campaigns'."""
    _configurar(client)
    lista = _crear_lista(client)
    resp = _crear_campana(client, lista)
    assert resp.status_code == 201
    r = client.post(f"/api/v1/campaigns/{resp.json()['id']}/test-send",
                    json={"to_email": f"prueba-{uuid.uuid4().hex[:6]}@test.local"})
    assert r.status_code == 200, r.text
    cuerpo = fake_ses.sent[0]["Content"]["Raw"]["Data"]
    assert _cabecera_decodificada(cuerpo, "Subject") == CONFIG_VALIDA["subject"]
    assert "PRUEBA]" not in _cabecera_decodificada(cuerpo, "Subject")


# --------------------------------------------------------------------------- #
# 5. Worker (envíos inmediatos y programados): única fuente de verdad
# --------------------------------------------------------------------------- #


def _aprobar_y_encolar(client, campana_id: str) -> None:
    assert client.post(f"/api/v1/campaigns/{campana_id}/approve").status_code == 200
    r = client.post(f"/api/v1/campaigns/{campana_id}/send-now")
    assert r.status_code == 200, r.text


def test_worker_envio_usa_config_vigente(client, admin_auth, fake_ses,
                                         limpiar_mail_config):
    """Envío masivo (mismo código para inmediatos y programados): el worker
    resuelve From/Subject desde 'Configuración de correo' en el momento del
    envío, aunque la campaña tenga otros valores almacenados."""
    from app.services import queue as send_queue
    from app.workers.worker import process_one

    _configurar(client)
    lista = _crear_lista(client)
    client.post("/api/v1/contacts", json={
        "email": f"dest-{uuid.uuid4().hex[:6]}@test.local", "list_ids": [lista]})
    resp = _crear_campana(client, lista)
    campana_id = resp.json()["id"]
    _aprobar_y_encolar(client, campana_id)

    # La configuración cambia DESPUÉS de crear (y de programar) la campaña
    nueva = _configurar(client, from_name="HacheJota",
                        from_email=f"hachejota@{DOMINIO}",
                        subject="Notificaciones")

    mid = send_queue.pop_message(timeout_seconds=2)
    assert mid, "Debe haber un mensaje en cola"
    db = SessionLocal()
    try:
        resultado = process_one(db, mid)
        assert resultado["accion"] == "enviado", resultado
    finally:
        db.close()

    assert len(fake_ses.sent) == 1
    enviado = fake_ses.sent[0]
    esperado = f"{nueva['from_name']} <{nueva['from_email']}>"
    assert enviado["FromEmailAddress"] == esperado
    cuerpo = enviado["Content"]["Raw"]["Data"]
    assert _cabecera_decodificada(cuerpo, "Subject") == nueva["subject"]
    assert _cabecera_decodificada(cuerpo, "From") == esperado

    # Fila histórica intacta
    db = SessionLocal()
    fila = db.get(Campaign, campana_id)
    snap = (fila.subject, fila.from_name, fila.from_email)
    db.close()
    assert snap == (CONFIG_VALIDA["subject"], CONFIG_VALIDA["from_name"],
                    CONFIG_VALIDA["from_email"])


def test_worker_sin_config_usa_asunto_propio_de_la_campana(
        client, admin_auth, fake_ses, limpiar_mail_config):
    """Sin asunto configurado aún, el envío cae al asunto propio de la
    campaña (compatible con envíos históricos y sus variables)."""
    from app.services import queue as send_queue
    from app.workers.worker import process_one

    lista = _crear_lista(client)
    email = f"dest-{uuid.uuid4().hex[:6]}@test.local"
    client.post("/api/v1/contacts", json={"email": email, "list_ids": [lista]})
    resp = _crear_campana(client, lista, subject="Hola {{contact.first_name}}")
    assert resp.status_code == 201, resp.text
    _aprobar_y_encolar(client, resp.json()["id"])

    mid = send_queue.pop_message(timeout_seconds=2)
    db = SessionLocal()
    try:
        resultado = process_one(db, mid)
        assert resultado["accion"] == "enviado", resultado
    finally:
        db.close()

    enviado = fake_ses.sent[0]
    esperado = f"HJ Campaigns <notificaciones@{DOMINIO}>"
    assert enviado["FromEmailAddress"] == esperado
    cuerpo = enviado["Content"]["Raw"]["Data"]
    asunto = _cabecera_decodificada(cuerpo, "Subject")
    assert asunto.startswith("Hola"), asunto
    assert "{{" not in asunto, "Las variables del asunto propio deben renderizarse"


# --------------------------------------------------------------------------- #
# 6. Resolución central (unitaria)
# --------------------------------------------------------------------------- #


def test_valores_para_envio_resolucion(db, limpiar_mail_config):
    """Guardado gana; si no hay nada, constantes del proyecto; el asunto cae
    al fallback (asunto propio de la campaña) solo si no está configurado."""
    # Sin configuración: constantes + fallback
    valores = mail_config.valores_para_envio(db, subject_fallback="Asunto propio")
    assert valores == {"from_name": "HJ Campaigns",
                       "from_email": f"notificaciones@{DOMINIO}",
                       "subject": "Asunto propio"}

    # Con configuración: gana siempre, incluso con fallback disponible
    db.add(AppSetting(key="default_from_name", value={"valor": "HacheJota"}))
    db.add(AppSetting(key="default_from_email",
                      value={"valor": f"hachejota@{DOMINIO}"}))
    db.add(AppSetting(key="default_subject", value={"valor": "Notificaciones"}))
    db.flush()
    valores = mail_config.valores_para_envio(db, subject_fallback="Asunto propio")
    assert valores == {"from_name": "HacheJota",
                       "from_email": f"hachejota@{DOMINIO}",
                       "subject": "Notificaciones"}
    db.rollback()
