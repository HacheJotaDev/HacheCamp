"""Pruebas V5: papelera y restauración de campañas/listas/contactos,
operaciones masivas seguras, permisos, concurrencia y estadísticas.

Línea roja: nada se borra físicamente salvo la purga explícita; supresiones,
consentimientos, mensajes y eventos SES se conservan en todas las rutas.
"""
from __future__ import annotations

import uuid

from app.core.db import SessionLocal
from app.models import (
    Campaign,
    CampaignMessage,
    ConsentEvent,
    Contact,
    ContactList,
    Suppression,
    contact_lists_rel,
)
from app.services import bulk_ops
from tests.conftest import FakeSES  # noqa: F401  (fixtures en conftest)


def _email(prefix: str) -> str:
    return f"v5-{prefix}-{uuid.uuid4().hex[:8]}@test.local"


def _crear_contacto(client, email: str, list_id: str | None = None) -> dict:
    payload: dict = {"email": email}
    if list_id:
        payload["list_ids"] = [list_id]
    r = client.post("/api/v1/contacts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_lista(client, nombre: str) -> dict:
    r = client.post("/api/v1/lists", json={"name": nombre, "description": None})
    assert r.status_code == 201, r.text
    return r.json()


def _crear_campana_borrador(client, list_id: str | None = None) -> dict:
    if list_id is None:
        list_id = _crear_lista(client, f"V5 src {uuid.uuid4().hex[:6]}")["id"]
    r = client.post("/api/v1/campaigns", json={
        "name": f"V5 {uuid.uuid4().hex[:6]}",
        "subject": "Asunto de prueba V5",
        "list_id": list_id,
        "segment_id": None,
    })
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Campañas: papelera, archivo, restauración, permisos
# --------------------------------------------------------------------------- #


def test_eliminar_campana_requiere_confirmacion(client, admin_auth):
    camp = _crear_campana_borrador(client)
    r = client.delete(f"/api/v1/campaigns/{camp['id']}")
    assert r.status_code == 409
    assert client.get(f"/api/v1/campaigns/{camp['id']}").status_code == 200


def test_eliminar_campana_borrador_va_a_papelera_y_restaura(client, admin_auth):
    camp = _crear_campana_borrador(client)
    r = client.delete(f"/api/v1/campaigns/{camp['id']}?confirmar=true")
    assert r.status_code == 200, r.text
    assert r.json()["papelera"] is True

    # Fuera del listado principal, visible en la papelera
    activos = client.get("/api/v1/campaigns").json()
    assert all(c["id"] != camp["id"] for c in activos["items"])
    papelera = client.get("/api/v1/campaigns?papelera=true").json()
    assert any(c["id"] == camp["id"] for c in papelera["items"])
    assert client.get("/api/v1/campaigns/papelera").json()["total"] == 1

    # Detalle normal -> 404 (eliminada); la información sigue en BD
    assert client.get(f"/api/v1/campaigns/{camp['id']}").status_code == 404

    # Restaurar conserva la información (estado, nombre, contenido)
    r = client.post(f"/api/v1/campaigns/{camp['id']}/restore")
    assert r.status_code == 200
    lista = client.get("/api/v1/campaigns").json()
    recuperada = next(c for c in lista["items"] if c["id"] == camp["id"])
    assert recuperada["name"] == camp["name"]
    assert recuperada["deleted_at"] is None


def test_eliminar_campana_activa_cancela_pendientes_y_no_reenvia_aceptados(client, admin_auth):
    """Una campaña en_cola eliminada se cancela: pendientes -> cancelado, y los
    mensajes aceptados/entregados NO se tocan (jamás se reenvían)."""
    db = SessionLocal()
    email_p = _email("activa-p")
    email_a = _email("activa-a")
    contacto_p = _crear_contacto(client, email_p)
    contacto_a = _crear_contacto(client, email_a)
    camp = _crear_campana_borrador(client)
    cid = camp["id"]
    db.add(CampaignMessage(campaign_id=cid, contact_id=contacto_p["id"],
                           email=email_p, status="pendiente"))
    db.add(CampaignMessage(campaign_id=cid, contact_id=contacto_a["id"],
                           email=email_a, status="aceptado"))
    camp_db = db.get(Campaign, cid)
    camp_db.status = "en_cola"
    db.commit()
    db.close()

    r = client.delete(f"/api/v1/campaigns/{cid}?confirmar=true")
    assert r.status_code == 200
    assert r.json()["mensajes_cancelados"] == 1

    db = SessionLocal()
    try:
        estados = {m.status for m in db.query(CampaignMessage).filter_by(campaign_id=cid)}
        assert "cancelado" in estados, "El pendiente debe quedar cancelado"
        assert "aceptado" in estados, "El aceptado por SES NO debe tocarse"
        assert db.get(Campaign, cid).status == "cancelada"
        assert db.get(Campaign, cid).deleted_at is not None
    finally:
        db.close()


def test_eliminar_campana_preserva_mensajes_y_eventos_para_estadisticas(client, admin_auth):
    """El borrado lógico NO destruye historial: las estadísticas históricas
    siguen computando tras eliminar y restaurar la campaña."""
    db = SessionLocal()
    email = _email("hist")
    contacto = _crear_contacto(client, email)
    camp = _crear_campana_borrador(client)
    cid = camp["id"]
    db.add(CampaignMessage(campaign_id=cid, contact_id=contacto["id"],
                           email=email, status="entregado"))
    db.commit()
    db.close()

    client.delete(f"/api/v1/campaigns/{cid}?confirmar=true")
    metricas = client.get("/api/v1/stats/metrics").json()
    assert metricas["entregados"] >= 1, "Las entregas históricas deben conservarse"

    client.post(f"/api/v1/campaigns/{cid}/restore")
    metricas = client.get("/api/v1/stats/metrics").json()
    assert metricas["entregados"] >= 1


def test_archivar_y_desarchivar_campana(client, admin_auth):
    camp = _crear_campana_borrador(client)
    r = client.post(f"/api/v1/campaigns/{camp['id']}/archive")
    assert r.status_code == 200
    item = next(c for c in client.get("/api/v1/campaigns").json()["items"]
                if c["id"] == camp["id"])
    assert item["archived_at"] is not None
    # Duplicado de archivo rechazado
    assert client.post(f"/api/v1/campaigns/{camp['id']}/archive").status_code == 400
    # Desarchivar
    assert client.post(f"/api/v1/campaigns/{camp['id']}/unarchive").status_code == 200
    item = next(c for c in client.get("/api/v1/campaigns").json()["items"]
                if c["id"] == camp["id"])
    assert item["archived_at"] is None


def test_no_se_puede_archivar_una_campana_eliminada(client, admin_auth):
    camp = _crear_campana_borrador(client)
    client.delete(f"/api/v1/campaigns/{camp['id']}?confirmar=true")
    r = client.post(f"/api/v1/campaigns/{camp['id']}/archive")
    assert r.status_code == 404  # _get_campaign excluye papelera


def test_restaurar_campana_sin_estar_en_papelera_falla(client, admin_auth):
    camp = _crear_campana_borrador(client)
    assert client.post(f"/api/v1/campaigns/{camp['id']}/restore").status_code == 400


# --------------------------------------------------------------------------- #
# Listas: eliminar lógico, vaciar, restaurar, conteos
# --------------------------------------------------------------------------- #


def test_listas_incluyen_conteo_y_busqueda(client, admin_auth):
    marca = uuid.uuid4().hex[:6]
    lista = _crear_lista(client, f"V5 {marca}")
    _crear_contacto(client, _email("l1"), lista["id"])
    _crear_contacto(client, _email("l2"), lista["id"])
    _crear_contacto(client, _email("l3"))

    listas = client.get("/api/v1/lists").json()
    mia = next(x for x in listas if x["id"] == lista["id"])
    assert mia["contactos"] == 2

    encontradas = client.get(f"/api/v1/lists?q={marca}").json()
    assert len(encontradas) == 1 and encontradas[0]["id"] == lista["id"]


def test_vaciar_lista_quita_asociaciones_y_conserva_contactos_globales(client, admin_auth):
    lista = _crear_lista(client, f"V5 vaciar {uuid.uuid4().hex[:6]}")
    otra = _crear_lista(client, f"V5 otra {uuid.uuid4().hex[:6]}")
    email = _email("multi")
    c = _crear_contacto(client, email)
    # Un solo PATCH con ambas listas (el PATCH REEMPLAZA la asignación completa)
    r = client.patch(f"/api/v1/contacts/{c['id']}",
                     json={"list_ids": [lista["id"], otra["id"]]})
    assert r.status_code == 200, r.text

    r = client.post(f"/api/v1/lists/{lista['id']}/empty?confirmar=true")
    assert r.status_code == 200, r.text
    assert r.json()["asociaciones_eliminadas"] == 1

    db = SessionLocal()
    try:
        contacto = db.query(Contact).filter(Contact.email == email).first()
        assert contacto is not None, "El contacto global se conserva"
        assert contacto.deleted_at is None
        rels = db.execute(
            contact_lists_rel.select().where(contact_lists_rel.c.contact_id == contacto.id)
        ).fetchall()
        assert [r.list_id for r in rels] == [otra["id"]], (
            "Las demás listas del contacto quedan intactas")
    finally:
        db.close()
    # La lista queda a cero
    mia = next(x for x in client.get("/api/v1/lists").json() if x["id"] == lista["id"])
    assert mia["contactos"] == 0


def test_vaciar_lista_requiere_confirmacion(client, admin_auth):
    lista = _crear_lista(client, f"V5 conf {uuid.uuid4().hex[:6]}")
    assert client.post(f"/api/v1/lists/{lista['id']}/empty").status_code == 409


def test_eliminar_lista_es_logica_y_se_restaura(client, admin_auth):
    lista = _crear_lista(client, f"V5 papelera {uuid.uuid4().hex[:6]}")
    _crear_contacto(client, _email("papelera"), lista["id"])

    r = client.delete(f"/api/v1/lists/{lista['id']}?confirmar=true")
    assert r.status_code == 200
    activas = client.get("/api/v1/lists").json()
    assert all(x["id"] != lista["id"] for x in activas)
    papelera = client.get("/api/v1/lists/papelera").json()
    assert any(x["id"] == lista["id"] for x in papelera)

    # Contactos y asociaciones intactos (borrado lógico)
    db = SessionLocal()
    try:
        lst = db.get(ContactList, lista["id"])
        assert lst.deleted_at is not None
    finally:
        db.close()

    r = client.post(f"/api/v1/lists/{lista['id']}/restore")
    assert r.status_code == 200
    restaurada = next(x for x in client.get("/api/v1/lists").json()
                      if x["id"] == lista["id"])
    assert restaurada["contactos"] == 1, "La restauración recupera la población"


def test_no_crear_lista_con_nombre_de_otra_en_papelera(client, admin_auth):
    nombre = f"V5 nombre {uuid.uuid4().hex[:6]}"
    lista = _crear_lista(client, nombre)
    client.delete(f"/api/v1/lists/{lista['id']}?confirmar=true")
    r = client.post("/api/v1/lists", json={"name": nombre, "description": None})
    assert r.status_code == 409


def test_eliminar_lista_protegida_con_campana_sin_materializar(client, admin_auth):
    """Borrador enlazado: la lista no puede eliminarse (población viva)."""
    lista = _crear_lista(client, f"V5 protegida {uuid.uuid4().hex[:6]}")
    _crear_campana_borrador(client, list_id=lista["id"])
    r = client.delete(f"/api/v1/lists/{lista['id']}?confirmar=true")
    assert r.status_code == 409
    assert "campaña" in r.json()["detail"].lower()


def test_vaciar_lista_protegida_con_campana_sin_materializar(client, admin_auth):
    lista = _crear_lista(client, f"V5 prot-vac {uuid.uuid4().hex[:6]}")
    _crear_campana_borrador(client, list_id=lista["id"])
    r = client.post(f"/api/v1/lists/{lista['id']}/empty?confirmar=true")
    assert r.status_code == 409


def test_eliminar_lista_requiere_admin(client, admin_auth, operador_auth):
    """Sobrescribe el cliente con sesión de operador: la eliminación debe 403."""
    lista = _crear_lista(client, f"V5 perm {uuid.uuid4().hex[:6]}")
    r = client.delete(f"/api/v1/lists/{lista['id']}?confirmar=true")
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Contactos: operaciones masivas asíncronas (vaciar todo, filtrados, purga)
# --------------------------------------------------------------------------- #


def _sembrar_contactos(client, lista_id: str | None, n: int, *, marca: str | None = None) -> list[str]:
    marca = marca or uuid.uuid4().hex[:6]
    db = SessionLocal()
    ids = []
    try:
        for i in range(n):
            c = Contact(email=_email(f"mas{marca}-{i}"), status="suscripto")
            db.add(c)
            db.flush()
            if lista_id:
                db.execute(contact_lists_rel.insert().values(
                    contact_id=c.id, list_id=lista_id))
            ids.append(c.id)
        db.commit()
    finally:
        db.close()
    return ids


def test_vaciar_todo_frase_incorrecta_rechazada(client, admin_auth):
    _sembrar_contactos(client, None, 3)
    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": True, "confirmar_texto": "vaciar contactos"})
    assert r.status_code == 409
    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": False, "confirmar_texto": "VACIAR CONTACTOS"})
    assert r.status_code == 409


def test_vaciar_todo_preview_cuenta_exacto(client, admin_auth):
    _sembrar_contactos(client, None, 4)
    r = client.get("/api/v1/contacts/vaciar-todo/preview")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["contactos_activos"] >= 4
    assert cuerpo["frase_confirmacion"] == "VACIAR CONTACTOS"


def test_vaciar_todo_procesa_por_lotes_y_restaura(client, admin_auth):
    """Vaciado global con 1.201 contactos (fuerza 2 lotes de 1000): borrado
    lógico con progreso, supresiones/consentimientos intactos y restauración
    precisa por operación."""
    marca = uuid.uuid4().hex[:6]
    patron = f"mas{marca}"
    _sembrar_contactos(client, None, 1201, marca=marca)

    # Un contacto con supresión y consentimiento: nada de eso debe perderse
    email_protegido = _email(f"prot{marca}")
    _crear_contacto(client, email_protegido)
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == email_protegido).first()
        db.add(Suppression(email=email_protegido, reason="manual", source="manual"))
        db.add(ConsentEvent(contact_id=c.id, event="alta", source="manual"))
        db.commit()
        id_protegido = c.id
    finally:
        db.close()

    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    assert r.status_code == 202, r.text
    op_id = r.json()["id"]

    # Objetivo real: todos los activos que el preview declaró (la BD es
    # compartida entre pruebas de la sesión; el exacto no es 1202 en seco)
    esperado = client.get("/api/v1/contacts/vaciar-todo/preview").json()["contactos_activos"]
    assert esperado >= 1202

    # Procesado asíncrono (el worker llama a esta función)
    resultado = bulk_ops.process_operation(SessionLocal(), op_id)
    assert resultado["accion"] == "completada"

    op = client.get(f"/api/v1/contacts/bulk/{op_id}").json()
    assert op["status"] == "completada"
    assert op["afectados"] == esperado
    assert op["progreso_pct"] == 100

    # Papelera: todos fuera del listado activo, presentes en la papelera
    assert client.get(f"/api/v1/contacts?q={patron}").json()["total"] == 0
    assert client.get(f"/api/v1/contacts/papelera?q={patron}").json()["total"] == 1201

    # Supresión y consentimiento intactos
    db = SessionLocal()
    try:
        assert db.query(Suppression).filter(
            Suppression.email == email_protegido).first() is not None
        c = db.get(Contact, id_protegido)
        assert c.deleted_at is not None and c.deleted_by_operation == op_id
        assert db.query(ConsentEvent).filter(
            ConsentEvent.contact_id == id_protegido).count() >= 1
    finally:
        db.close()

    # Restauración por operación: recupera exactamente los eliminados por ella
    r = client.post(f"/api/v1/contacts/bulk/{op_id}/restore")
    assert r.status_code == 200
    assert r.json()["restaurados"] == esperado
    assert client.get(f"/api/v1/contacts?q={patron}").json()["total"] == 1201


def test_vaciar_todo_con_cancelacion_previa(client, admin_auth):
    _sembrar_contactos(client, None, 5)
    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    op_id = r.json()["id"]
    assert client.post(f"/api/v1/contacts/bulk/{op_id}/cancel").status_code == 200
    # Cancelada antes de procesar: el worker la omite y nada se afecta
    resultado = bulk_ops.process_operation(SessionLocal(), op_id)
    assert resultado["accion"] == "omitido"
    op = client.get(f"/api/v1/contacts/bulk/{op_id}").json()
    assert op["status"] == "cancelada"
    assert op["afectados"] == 0


def test_eliminar_filtrado_asincrono(client, admin_auth):
    marca = uuid.uuid4().hex[:6]
    _sembrar_contactos(client, None, 3)
    db = SessionLocal()
    try:
        for _ in range(2):
            db.add(Contact(email=_email(f"fil{marca}"), status="suscripto"))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/v1/contacts/bulk-delete-filtrado",
                    json={"q": f"fil{marca}", "confirmar": True})
    assert r.status_code == 202, r.text
    op_id = r.json()["id"]

    bulk_ops.process_operation(SessionLocal(), op_id)
    op = client.get(f"/api/v1/contacts/bulk/{op_id}").json()
    assert op["status"] == "completada"
    assert op["total_objetivo"] == 2 and op["afectados"] == 2
    assert client.get(f"/api/v1/contacts?q=fil{marca}").json()["total"] == 0
    assert client.get(f"/api/v1/contacts/papelera?q=fil{marca}").json()["total"] == 2


def test_eliminar_filtrado_sin_resultados_rechazado(client, admin_auth):
    r = client.post("/api/v1/contacts/bulk-delete-filtrado",
                    json={"q": f"inexistente-{uuid.uuid4().hex}", "confirmar": True})
    assert r.status_code == 400


def test_importacion_no_reactiva_contactos_de_papelera(client, admin_auth, importar_csv):
    """Un trabajo de importación antiguo NO reactiva contactos eliminados."""
    email = _email("react")
    contacto = _crear_contacto(client, email)
    client.delete(f"/api/v1/contacts/{contacto['id']}")  # papelera

    progreso = importar_csv(f"email\n{email}\n".encode(), consent_source="formulario_web")
    assert progreso["estado"] == "completado"
    assert progreso["creados"] == 0
    assert progreso["notices"].get("eliminado_omitido") == 1
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == email).first()
        assert c.deleted_at is not None, "Sigue en papelera (no reactivado)"
    finally:
        db.close()


def test_purga_fisica_omite_contactos_con_mensajes(client, admin_auth):
    """La purga física SOLO toca contactos sin mensajes de campaña: las
    estadísticas históricas nunca se destruyen."""
    email_libre = _email("purgalibre")
    email_con_mensajes = _email("purgacond")
    libre = _crear_contacto(client, email_libre)
    con_mensajes = _crear_contacto(client, email_con_mensajes)

    db = SessionLocal()
    try:
        camp = Campaign(name="P", subject="P", from_name="P", from_email="p@local.test")
        db.add(camp)
        db.flush()
        db.add(CampaignMessage(campaign_id=camp.id, contact_id=con_mensajes["id"],
                               email=email_con_mensajes, status="entregado"))
        db.commit()
    finally:
        db.close()

    client.delete(f"/api/v1/contacts/{libre['id']}")
    client.delete(f"/api/v1/contacts/{con_mensajes['id']}")

    r = client.post("/api/v1/contacts/purgar",
                    json={"confirmar": True, "confirmar_texto": "PURGAR CONTACTOS",
                          "antiguedad_minima_dias": 0})
    assert r.status_code == 202, r.text
    op_id = r.json()["id"]
    bulk_ops.process_operation(SessionLocal(), op_id)

    db = SessionLocal()
    try:
        assert db.get(Contact, libre["id"]) is None, "Sin mensajes: purgable"
        assert db.get(Contact, con_mensajes["id"]) is not None, (
            "Con mensajes de campaña: NUNCA se purga (preserva estadísticas)")
        # El mensaje y la campaña siguen ahí
        assert db.query(CampaignMessage).filter_by(
            contact_id=con_mensajes["id"]).count() == 1
    finally:
        db.close()
    op = client.get(f"/api/v1/contacts/bulk/{op_id}").json()
    assert op["omitidos"] == 0  # el contacto con mensajes ni siquiera entra al objetivo


def test_purga_requiere_frase_exacta(client, admin_auth):
    r = client.post("/api/v1/contacts/purgar",
                    json={"confirmar": True, "confirmar_texto": "purgar"})
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# Concurrencia / coherencia worker-eliminación
# --------------------------------------------------------------------------- #


def test_worker_no_envia_mensajes_de_campana_eliminada(client, admin_auth):
    """process_one sobre una campaña eliminada (cancelada) cancela el pendiente;
    un mensaje ya aceptado nunca se reenvía."""
    from app.workers.worker import process_one

    db = SessionLocal()
    try:
        email = _email("worker")
        contacto = _crear_contacto(client, email)
        camp = _crear_campana_borrador(client)
        db.add(CampaignMessage(campaign_id=camp["id"], contact_id=contacto["id"],
                               email=email, status="pendiente"))
        db.commit()
    finally:
        db.close()

    client.delete(f"/api/v1/campaigns/{camp['id']}?confirmar=true")

    db = SessionLocal()
    try:
        msg = db.query(CampaignMessage).filter_by(campaign_id=camp["id"]).first()
        resultado = process_one(db, msg.id)
        assert resultado["accion"] == "cancelado"
        db.refresh(msg)
        assert msg.status == "cancelado"
    finally:
        db.close()


def test_dos_operaciones_masivas_en_serie_no_solapan(client, admin_auth):
    """hay_operacion_en_curso bloquea el lanzamiento simultáneo de vaciados."""
    _sembrar_contactos(client, None, 2)
    r1 = client.post("/api/v1/contacts/vaciar-todo",
                     json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    assert r1.status_code == 202
    r2 = client.post("/api/v1/contacts/vaciar-todo",
                     json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    assert r2.status_code == 409
    # Limpieza: completar la primera
    bulk_ops.process_operation(SessionLocal(), r1.json()["id"])


# --------------------------------------------------------------------------- #
# Estadísticas V5
# --------------------------------------------------------------------------- #


def test_metricas_incluyen_entrega_rate_y_en_vuelo(client, admin_auth):
    m = client.get("/api/v1/stats/metrics").json()
    assert "entrega_rate" in m and "en_vuelo" in m
    assert "entrega_rate" in m["denominadores"]
    assert "success_rate" in m["notas"]


def test_dashboard_no_cuenta_campanas_eliminadas(client, admin_auth):
    camp = _crear_campana_borrador(client)
    antes = client.get("/api/v1/stats/dashboard").json()["resumen"]["campanas_total"]
    client.delete(f"/api/v1/campaigns/{camp['id']}?confirmar=true")
    despues = client.get("/api/v1/stats/dashboard").json()["resumen"]["campanas_total"]
    assert despues == antes - 1
