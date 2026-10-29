"""Pruebas de CIERRE V5 (revisión pre-push): cancelación determinista entre
lotes, reanudación sin omisiones/duplicados, colisión de nombres al restaurar
listas, protecciones con workers simultáneos, preservación de historial en
purga/restore y carreras importación↔eliminación.

Complementa test_v5_administracion.py: aquí cada caso es DETERMINISTA (sin
esperas ni carreras reales): los puntos de sincronización se inyectan con
monkeypatch sobre los límites de lote.
"""
from __future__ import annotations

import uuid

from app.core.db import SessionLocal, utcnow
from app.models import (
    BulkOperation,
    Campaign,
    CampaignMessage,
    ConsentEvent,
    Contact,
    ContactList,
    Suppression,
    contact_lists_rel,
    contact_tags_rel,
)
from app.services import bulk_ops
from tests.conftest import FakeSES  # noqa: F401  (fixtures en conftest)


def _email(prefix: str) -> str:
    return f"v5c-{prefix}-{uuid.uuid4().hex[:8]}@test.local"


def _crear_lista(client, nombre: str) -> dict:
    r = client.post("/api/v1/lists", json={"name": nombre, "description": None})
    assert r.status_code == 201, r.text
    return r.json()


def _sembrar(client, n: int, *, marca: str, lista_id: str | None = None) -> list[str]:
    db = SessionLocal()
    ids = []
    try:
        for i in range(n):
            c = Contact(email=_email(f"{marca}-{i}"), status="suscripto")
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


def _activos_now() -> int:
    db = SessionLocal()
    try:
        return int(db.query(Contact.id).filter(Contact.deleted_at.is_(None)).count())
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Punto 2: cancelación determinista ENTRE lotes (ni omisiones ni duplicados)
# --------------------------------------------------------------------------- #


def test_cancelacion_parcial_entre_lotes_determinista(client, admin_auth, monkeypatch):
    """El flag de cancelación se activa justo tras confirmarse el PRIMER lote.
    Garantías: exactamente los contactos del lote 1 quedan eliminados (una
    vez), el resto sigue activo e intacto, y la operación termina en
    cancelada_parcial con procesados == 1000."""
    marca = uuid.uuid4().hex[:6]
    _sembrar(client, 1201, marca=marca)  # fuerza >= 2 lotes
    activos_antes = _activos_now()

    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    assert r.status_code == 202, r.text
    op_id = r.json()["id"]

    original = bulk_ops._eliminar_lote
    lote1 = {"ids": None}

    def envoltura(db, op, contactos):
        afectados = original(db, op, contactos)
        if lote1["ids"] is None:
            lote1["ids"] = [c.id for c in contactos]
            # Cancelación determinista ENTRE lotes: flag confirmado en su
            # propia sesión ANTES de que el worker relea la operación.
            dbs = SessionLocal()
            try:
                fila = dbs.get(BulkOperation, op.id)
                fila.cancel_requested = True
                dbs.commit()
            finally:
                dbs.close()
        return afectados

    monkeypatch.setattr(bulk_ops, "_eliminar_lote", envoltura)
    resultado = bulk_ops.process_operation(SessionLocal(), op_id)
    monkeypatch.undo()

    assert resultado["accion"] == "cancelada"
    assert len(lote1["ids"]) == 1000, "el primer lote es de 1000 exactos"

    db = SessionLocal()
    try:
        op = db.get(BulkOperation, op_id)
        assert op.status == "cancelada_parcial"
        assert op.afectados == 1000 and op.procesados == 1000

        ids_lote1 = set(lote1["ids"])
        eliminados = {c.id for c in db.query(Contact)
                      .filter(Contact.deleted_by_operation == op_id).all()}
        assert eliminados == ids_lote1, (
            "Se eliminó EXACTAMENTE el primer lote: ni más ni menos")

        # Ni omisiones encubiertas ni dobles marcas: el resto sigue activo
        restantes_activos = db.query(Contact.id).filter(
            Contact.deleted_at.is_(None)).count()
        assert restantes_activos == activos_antes - 1000
        # Ningún contacto del lote 1 puede seguir activo
        assert db.query(Contact.id).filter(
            Contact.id.in_(ids_lote1), Contact.deleted_at.is_(None)).count() == 0
    finally:
        db.close()

    # El progreso persistido es coherente para el panel
    progreso = client.get(f"/api/v1/contacts/bulk/{op_id}").json()
    assert progreso["status"] == "cancelada_parcial"
    assert progreso["afectados"] == 1000
    assert progreso["cancel_requested"] is True


def test_cancelacion_durante_ultimo_lote_no_deja_operacion_huerfana(client, admin_auth, monkeypatch):
    """Cancelación pedida DURANTE el último lote (llega tarde): la operación
    termina 'completada' (todo el objetivo procesado), nunca 'procesando'
    eterno. Regresión del patrón 'if not cancel_requested: completada'."""
    marca = uuid.uuid4().hex[:6]
    _sembrar(client, 3, marca=marca)  # un solo lote

    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    op_id = r.json()["id"]

    original = bulk_ops._eliminar_lote

    def envoltura(db, op, contactos):
        afectados = original(db, op, contactos)
        dbs = SessionLocal()
        try:
            fila = dbs.get(BulkOperation, op.id)
            fila.cancel_requested = True  # llega durante el ÚNICO lote
            dbs.commit()
        finally:
            dbs.close()
        return afectados

    monkeypatch.setattr(bulk_ops, "_eliminar_lote", envoltura)
    resultado = bulk_ops.process_operation(SessionLocal(), op_id)
    monkeypatch.undo()

    assert resultado["accion"] == "completada"
    progreso = client.get(f"/api/v1/contacts/bulk/{op_id}").json()
    assert progreso["status"] == "completada", (
        "una cancelación tardía no puede dejar la operación en 'procesando'")
    assert progreso["cancel_requested"] is True  # queda registrado

    # Nueva operación posible: nada bloqueado por la cola (se resembran
    # contactos: el vaciado anterior dejó la base sin activos)
    _sembrar(client, 2, marca=uuid.uuid4().hex[:6])
    r2 = client.post("/api/v1/contacts/vaciar-todo",
                     json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    assert r2.status_code == 202, r2.text
    bulk_ops.process_operation(SessionLocal(), r2.json()["id"])


def test_reanudacion_tras_interrupcion_sin_omisiones_ni_duplicados(client, admin_auth):
    """Simula un reinicio del worker tras confirmarse el primer lote: la fila
    queda 'procesando' con progreso persistido. Al reanudar, el bucle offset-0
    completa el objetivo SIN re-procesar lo confirmado ni saltar candidatos."""
    marca = uuid.uuid4().hex[:6]
    total_marca = 1200
    _sembrar(client, total_marca, marca=marca)
    total_inicial = _activos_now()

    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    op_id = r.json()["id"]

    # --- estado tras un "crash" post-lote-1: primer lote confirmado en BD ----
    dbs = SessionLocal()
    try:
        op = dbs.get(BulkOperation, op_id)
        op.status = "procesando"
        op.started_at = utcnow()
        op.total_objetivo = total_inicial
        op.procesados = 1000
        op.afectados = 1000
        dbs.commit()
        primeros = (
            dbs.query(Contact)
            .filter(Contact.deleted_at.is_(None), Contact.created_at <= op.started_at)
            .order_by(Contact.created_at, Contact.id)
            .limit(1000).all()
        )
        ids_confirmados = [c.id for c in primeros]
        dbs.query(Contact).filter(Contact.id.in_(ids_confirmados)).update(
            {"deleted_at": utcnow(), "deleted_by": op.user_id,
             "deleted_by_operation": op_id}, synchronize_session=False)
        dbs.commit()
    finally:
        dbs.close()

    # --- el worker reiniciado reanuda (recover_pending -> process_operation)
    resultado = bulk_ops.process_operation(SessionLocal(), op_id)
    assert resultado["accion"] == "completada"

    db = SessionLocal()
    try:
        op = db.get(BulkOperation, op_id)
        assert op.status == "completada"
        assert op.procesados == total_inicial, (
            "procesados = confirmados (1000) + reanudados; ni más ni menos")
        assert op.afectados == total_inicial
        eliminados = db.query(Contact.id).filter(
            Contact.deleted_by_operation == op_id).count()
        assert eliminados == total_inicial, "cada contacto una única operación"
        # Ningún activo que existiera al inicio quedó sin procesar
        activos_restantes = db.query(Contact.id).filter(
            Contact.deleted_at.is_(None), Contact.created_at <= op.started_at).count()
        assert activos_restantes == 0, "sin omisiones"
        # Toda la marca está en papelera (1200 de los eliminados)
        en_papelera_marca = db.query(Contact.id).filter(
            Contact.deleted_at.isnot(None),
            Contact.email.like(f"%{marca}%")).count()
        assert en_papelera_marca == total_marca
        de_marca_eliminados = db.query(Contact.id).filter(
            Contact.deleted_by_operation == op_id,
            Contact.email.like(f"%{marca}%")).count()
        assert de_marca_eliminados == total_marca, (
            "ningún contacto de la marca quedó fuera de la operación")
    finally:
        db.close()

    # Restauración por operación recupera TODO el objetivo (confirmado+reanudado)
    r = client.post(f"/api/v1/contacts/bulk/{op_id}/restore")
    assert r.status_code == 200
    assert r.json()["restaurados"] == total_inicial
    assert client.get(f"/api/v1/contacts?q={marca}").json()["total"] == total_marca


# --------------------------------------------------------------------------- #
# Punto 6: importación y eliminación simultáneas
# --------------------------------------------------------------------------- #


def test_contacto_creado_durante_vaciado_no_es_barrido(client, admin_auth, monkeypatch):
    """Un contacto creado por una importación concurrente DESPUÉS de que la
    operación empezó a procesar queda FUERA del objetivo (población congelada
    con created_at <= started_at): no se elimina ni contamina el conteo."""
    marca = uuid.uuid4().hex[:6]
    _sembrar(client, 4, marca=marca)

    r = client.post("/api/v1/contacts/vaciar-todo",
                    json={"confirmar": True, "confirmar_texto": "VACIAR CONTACTOS"})
    op_id = r.json()["id"]
    objetivo = r.json()["total_objetivo"] or _activos_now()

    original = bulk_ops._eliminar_lote
    creado = {"id": None}

    def envoltura(db, op, contactos):
        afectados = original(db, op, contactos)
        if creado["id"] is None:
            # "importación" concurrente: alta a mitad de la operación
            nuevo = Contact(email=_email(f"durante-{marca}"), status="suscripto")
            db.add(nuevo)
            db.commit()
            creado["id"] = nuevo.id
        return afectados

    monkeypatch.setattr(bulk_ops, "_eliminar_lote", envoltura)
    resultado = bulk_ops.process_operation(SessionLocal(), op_id)
    monkeypatch.undo()

    assert resultado["accion"] == "completada"
    db = SessionLocal()
    try:
        assert creado["id"] is not None
        c = db.get(Contact, creado["id"])
        assert c is not None and c.deleted_at is None, (
            "el contacto creado durante el vaciado NO fue barrido")
        assert c.created_at > db.get(BulkOperation, op_id).started_at
        afectados = db.query(Contact.id).filter(
            Contact.deleted_by_operation == op_id).count()
        assert afectados == objetivo, "el objetivo original no cambia"
    finally:
        db.close()
    assert client.get(f"/api/v1/contacts?q=durante-{marca}").json()["total"] == 1


def test_importacion_tras_eliminacion_masiva_no_reactiva_y_restauracion_integral(
        client, admin_auth, importar_csv):
    """Job de importación lanzado DESPUÉS de una eliminación masiva: los
    correos de contactos en papelera se omiten (nunca se recrean/reactivan);
    la restauración por operación los recupera completos."""
    marca = uuid.uuid4().hex[:6]
    emails = [_email(f"imp{marca}-{i}") for i in range(3)]
    # Siembra con EXACTAMENTE esos correos (los mismos que irá al CSV)
    db = SessionLocal()
    ids = []
    try:
        for em in emails:
            c = Contact(email=em, status="suscripto")
            db.add(c)
            db.flush()
            ids.append(c.id)
        db.commit()
    finally:
        db.close()

    r = client.post("/api/v1/contacts/bulk-delete-filtrado",
                    json={"q": f"imp{marca}", "confirmar": True})
    assert r.status_code == 202, r.text
    op_id = r.json()["id"]
    bulk_ops.process_operation(SessionLocal(), op_id)
    assert client.get(f"/api/v1/contacts?q=imp{marca}").json()["total"] == 0

    # Importación posterior que incluye SOLO correos ya eliminados
    csv_body = ("email\n" + "\n".join(emails) + "\n").encode()
    progreso = importar_csv(csv_body, consent_source="formulario_web")
    assert progreso["estado"] == "completado"
    assert progreso["creados"] == 0, "ningún eliminado se recrea"
    assert progreso["notices"].get("eliminado_omitido") == 3

    db = SessionLocal()
    try:
        for cid in ids:
            c = db.get(Contact, cid)
            assert c.deleted_at is not None and c.deleted_by_operation == op_id
            assert c.status == "suscripto", "el estado previo se conserva"
    finally:
        db.close()

    # Restauración por operación: vuelven con su identidad y datos íntegros
    r = client.post(f"/api/v1/contacts/bulk/{op_id}/restore")
    assert r.json()["restaurados"] == 3
    db = SessionLocal()
    try:
        for cid in ids:
            c = db.get(Contact, cid)
            assert c.deleted_at is None and c.deleted_by_operation is None
    finally:
        db.close()
    assert client.get(f"/api/v1/contacts?q=imp{marca}").json()["total"] == 3


# --------------------------------------------------------------------------- #
# Punto 3: colisión de nombres al restaurar listas desde Papelera
# --------------------------------------------------------------------------- #


def test_renombrar_no_roba_nombre_de_lista_en_papelera(client, admin_auth):
    """Renombrar una lista activa a un nombre que está en la papelera está
    bloqueado: si no, la restauración de esa otra lista chocaría con la
    restricción UNIQUE (error 500) — regresión del hueco de update_list."""
    marca = uuid.uuid4().hex[:6]
    a = _crear_lista(client, f"V5C col-a {marca}")
    b = _crear_lista(client, f"V5C col-b {marca}")
    r = client.delete(f"/api/v1/lists/{a['id']}?confirmar=true")
    assert r.status_code == 200

    r = client.patch(f"/api/v1/lists/{b['id']}",
                     json={"name": a["name"], "description": None})
    assert r.status_code == 409
    assert "papelera" in r.json()["detail"]


def test_restaurar_lista_con_colision_409_accionable_y_renombre(client, admin_auth, monkeypatch):
    """Manejo de COLISIÓN de nombres al restaurar (defensa en profundidad).

    La restricción UNIQUE de contact_lists.name impide que dos filas compartan
    nombre, así que una colisión real solo podría existir en datos heredados,
    motores sin la constraint o cambios futuros de esquema. Se simula con
    monkeypatch de nombre_en_uso (determinista) para ejercitar la rama:
    409 accionable sin nombre nuevo y restauración RENOMBRADA con él."""
    from app.services import lists as list_service

    marca = uuid.uuid4().hex[:6]
    eliminada = _crear_lista(client, f"V5C choc {marca}")
    otra = _crear_lista(client, f"V5C otra {marca}")
    _sembrar(client, 2, marca=marca, lista_id=eliminada["id"])

    r = client.delete(f"/api/v1/lists/{eliminada['id']}?confirmar=true")
    assert r.status_code == 200

    real = list_service.nombre_en_uso

    def colision_heredada(db, name, *, exclude_id=None):
        # Simula un dato heredado: el nombre de la lista en papelera aparece
        # ocupado por OTRA fila (imposible hoy por UNIQUE, posible sin ella)
        if name == eliminada["name"]:
            return db.get(ContactList, otra["id"])
        return real(db, name, exclude_id=exclude_id)

    monkeypatch.setattr(list_service, "nombre_en_uso", colision_heredada)

    # 1) Restaurar sin nombre nuevo: 409 con mensaje accionable (nunca 500)
    r = client.post(f"/api/v1/lists/{eliminada['id']}/restore")
    assert r.status_code == 409, r.text
    assert "Indica un nombre nuevo" in r.json()["detail"]
    # La lista sigue en la papelera, intacta y sin restaurar
    assert any(x["id"] == eliminada["id"]
               for x in client.get("/api/v1/lists/papelera").json())

    # 2) Nombre nuevo también ocupado: 409 con el estado de la ocupante
    r = client.post(f"/api/v1/lists/{eliminada['id']}/restore",
                    json={"nuevo_nombre": f"V5C otra {marca}"})
    assert r.status_code == 409
    assert "V5C otra" in r.json()["detail"]

    # 3) Restauración renombrada: 200 + población conservada
    nombre_nuevo = f"V5C restaurada {marca}"
    r = client.post(f"/api/v1/lists/{eliminada['id']}/restore",
                    json={"nuevo_nombre": nombre_nuevo})
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == nombre_nuevo
    activas = client.get("/api/v1/lists").json()
    restaurada = next(x for x in activas if x["id"] == eliminada["id"])
    assert restaurada["name"] == nombre_nuevo
    assert restaurada["contactos"] == 2, "la restauración conserva asociaciones"

    monkeypatch.undo()
    # 4) La lista que 'chocaba' sigue activa con su nombre real
    db = SessionLocal()
    try:
        assert db.get(ContactList, otra["id"]).name == f"V5C otra {marca}"
    finally:
        db.close()


def test_restaurar_lista_sin_colision_mantiene_nombre(client, admin_auth):
    marca = uuid.uuid4().hex[:6]
    lst = _crear_lista(client, f"V5C simple {marca}")
    client.delete(f"/api/v1/lists/{lst['id']}?confirmar=true")
    r = client.post(f"/api/v1/lists/{lst['id']}/restore")
    assert r.status_code == 200
    assert r.json()["nombre"] == lst["name"]


# --------------------------------------------------------------------------- #
# Punto 4: protecciones con campañas activas y workers simultáneos
# --------------------------------------------------------------------------- #


def test_worker_no_envia_a_contacto_pasado_a_papelera(client, admin_auth):
    """Un mensaje PENDIENTE de un contacto que entró en la papelera no sale:
    el worker lo cancela (motivo contacto_en_papelera). Lo ya aceptado por SES
    jamás se toca (idempotencia de estados intacta)."""
    from app.workers.worker import process_one

    db = SessionLocal()
    try:
        email = _email("papelera-w")
        c = Contact(email=email, status="suscripto")
        db.add(c)
        db.flush()
        camp = Campaign(name="V5C W", subject="S", from_name="T",
                        from_email="t@local.test", status="en_cola")
        db.add(camp)
        db.flush()
        msg = CampaignMessage(campaign_id=camp.id, contact_id=c.id,
                              email=email, status="pendiente")
        db.add(msg)
        db.commit()
        cid_msg, c_id = msg.id, c.id
    finally:
        db.close()

    # El contacto entra en la papelera mientras el mensaje espera en cola
    r = client.delete(f"/api/v1/contacts/{c_id}")
    assert r.status_code == 200

    db = SessionLocal()
    try:
        resultado = process_one(db, cid_msg)
        assert resultado["accion"] == "cancelado"
        assert resultado["motivo"] == "contacto_en_papelera"
        # El bucle del worker confirma tras process_one (regresión: el cierre
        # sin commit hacía rollback silencioso de la cancelación)
        db.commit()
    finally:
        db.close()

    # Persistencia real: OTRO hilo/sesión ve el mensaje cancelado
    db2 = SessionLocal()
    try:
        assert db2.get(CampaignMessage, cid_msg).status == "cancelado"
    finally:
        db2.close()


def test_worker_respetando_papelera_no_cancela_aceptado(client, admin_auth):
    """El guard de papelera SOLO toca pendientes: un mensaje ya aceptado por
    SES de un contacto en papelera se devuelve omitido, nunca reenviado ni
    anulado (nada aceptado se destruye)."""
    from app.workers.worker import process_one

    db = SessionLocal()
    try:
        email = _email("aceptado-w")
        c = Contact(email=email, status="suscripto")
        db.add(c)
        db.flush()
        camp = Campaign(name="V5C A", subject="S", from_name="T",
                        from_email="t@local.test", status="enviando")
        db.add(camp)
        db.flush()
        msg = CampaignMessage(campaign_id=camp.id, contact_id=c.id,
                              email=email, status="aceptado",
                              ses_message_id="ses-ya-aceptado")
        db.add(msg)
        db.commit()
        cid_msg, c_id = msg.id, c.id
    finally:
        db.close()

    client.delete(f"/api/v1/contacts/{c_id}")

    db = SessionLocal()
    try:
        resultado = process_one(db, cid_msg)
        assert resultado["accion"] == "omitido"
        msg = db.get(CampaignMessage, cid_msg)
        assert msg.status == "aceptado", "lo aceptado no se anula"
        assert msg.ses_message_id == "ses-ya-aceptado"
    finally:
        db.close()


def test_lista_eliminada_no_frena_campana_ya_materializada(client, admin_auth, fake_ses):
    """Eliminar una lista usada por una campaña ya EN COLA está permitido:
    sus destinatarios quedaron materializados al encolar (instantánea). El
    worker sigue procesando esos mensajes y la campaña completa."""
    from app.services import queue as send_queue

    marca = uuid.uuid4().hex[:6]
    lst = _crear_lista(client, f"V5C mat {marca}")
    email = _email(f"mat-{marca}")
    r = client.post("/api/v1/contacts", json={"email": email, "list_ids": [lst["id"]]})
    assert r.status_code == 201

    camp = client.post("/api/v1/campaigns", json={
        "name": f"V5C mat-camp {marca}", "subject": "S",
        "from_name": "T", "from_email": "notificaciones@leisureparts4u.store",
        "category": "promocional", "list_id": lst["id"]})
    assert camp.status_code == 201, camp.text
    cid = camp.json()["id"]
    assert client.post(f"/api/v1/campaigns/{cid}/approve").status_code == 200
    r = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert r.status_code == 200 and r.json()["nuevos"] == 1

    # La lista pasa a la papelera DESPUÉS de encolar (protegida solo si la
    # población no está materializada: borrador/en_revision/programada)
    r = client.delete(f"/api/v1/lists/{lst['id']}?confirmar=true")
    assert r.status_code == 200, r.text

    # El worker procesa el mensaje materializado con normalidad
    db = SessionLocal()
    mid = send_queue.pop_message(timeout_seconds=1)
    db.close()
    assert mid is not None, "el mensaje materializado sigue en cola"
    from app.workers.worker import process_one
    db = SessionLocal()
    try:
        resultado = process_one(db, mid)
        assert resultado["accion"] == "enviado"
    finally:
        db.close()
    assert len(fake_ses.sent) == 1
    db = SessionLocal()
    try:
        assert db.get(CampaignMessage, mid).status == "aceptado"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Punto 5: historial, supresiones, consentimiento y estadísticas intactos
# --------------------------------------------------------------------------- #


def test_eliminar_y_restaurar_contacto_conserva_historial_completo(client, admin_auth):
    """Ciclo completo: contacto con consentimiento, listas, etiquetas, mensaje
    entregado y baja con supresión -> papelera -> restaurar. NADA se pierde."""
    marca = uuid.uuid4().hex[:6]
    email = _email(f"hist-{marca}")
    lst = _crear_lista(client, f"V5C hist {marca}")
    r = client.post("/api/v1/contacts",
                    json={"email": email, "consent_source": "web",
                          "list_ids": [lst["id"]]})
    cid = r.json()["id"]

    db = SessionLocal()
    try:
        c = db.get(Contact, cid)
        db.execute(contact_tags_rel.insert().values(contact_id=c.id,
                                                    tag_id=_tag_id(db)))
        db.add(CampaignMessage(campaign_id=_camp_id(db), contact_id=c.id,
                               email=email, status="entregado"))
        db.commit()
        eventos_antes = db.query(ConsentEvent).filter(
            ConsentEvent.contact_id == cid).count()
        assert eventos_antes >= 1
    finally:
        db.close()

    # Baja con supresión (histórico V4) y después papelera
    assert client.post(f"/api/v1/contacts/{cid}/baja").status_code == 200
    assert client.delete(f"/api/v1/contacts/{cid}").status_code == 200
    assert client.get(f"/api/v1/contacts?q=hist-{marca}").json()["total"] == 0

    # Restaurar
    assert client.post(f"/api/v1/contacts/{cid}/restore").status_code == 200

    db = SessionLocal()
    try:
        c = db.get(Contact, cid)
        assert c.deleted_at is None and c.deleted_by is None
        assert c.email == email, "identidad intacta"
        assert c.status == "dado_baja", "el estado de baja se conserva"
        assert c.unsubscribed_at is not None, "la fecha de baja se conserva"
        assert db.query(Suppression).filter(
            Suppression.email == email).first() is not None, (
            "la supresión NO se destruye con el borrado lógico")
        eventos_despues = db.query(ConsentEvent).filter(
            ConsentEvent.contact_id == cid).count()
        assert eventos_despues >= eventos_antes + 1, (
            "eventos de consentimiento íntegros (alta + baja)")
        assert db.query(CampaignMessage).filter_by(contact_id=cid).count() == 1, (
            "el historial de mensajes NO se destruye")
        listas = db.execute(contact_lists_rel.select().where(
            contact_lists_rel.c.contact_id == cid)).fetchall()
        assert [x.list_id for x in listas] == [lst["id"]], "listas intactas"
    finally:
        db.close()


def test_purga_con_suprimir_bloquea_reimportacion_para_siempre(client, admin_auth, importar_csv):
    """Purga física con suprimir=True: el contacto desaparece, la evidencia de
    supresión (reason='purga') permanece y una reimportación futura NO puede
    recrearlo como suscripto (entra dado_baja). Sin suprimir, la purga de un
    contacto con mensajes de campaña sigue estando prohibida."""
    marca = uuid.uuid4().hex[:6]
    email = _email(f"purgasup-{marca}")
    r = client.post("/api/v1/contacts", json={"email": email})
    cid = r.json()["id"]
    assert client.delete(f"/api/v1/contacts/{cid}").status_code == 200

    r = client.post("/api/v1/contacts/purgar",
                    json={"confirmar": True, "confirmar_texto": "PURGAR CONTACTOS",
                          "antiguedad_minima_dias": 0, "suprimir": True})
    assert r.status_code == 202
    op_id = r.json()["id"]
    bulk_ops.process_operation(SessionLocal(), op_id)

    db = SessionLocal()
    try:
        assert db.get(Contact, cid) is None, "purga física realizada"
        supp = db.query(Suppression).filter(Suppression.email == email).first()
        assert supp is not None and supp.reason == "purga", (
            "la evidencia de supresión impide reimportar como suscripto")
    finally:
        db.close()

    progreso = importar_csv(f"email\n{email}\n".encode(), consent_source="web")
    assert progreso["estado"] == "completado"
    assert progreso["notices"].get("correo_suprimido") == 1, (
        "reimportado SOLO como correo_suprimido, jamás suscripto")
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == email).first()
        assert c is not None
        assert c.status == "dado_baja", "nunca vuelve a suscripto tras purga"
        assert c.deleted_at is None
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Punto 7: segmentos y plantillas (revisión de cierre, sin reescrituras)
# --------------------------------------------------------------------------- #


def test_segmento_insensible_a_mayusculas(client, admin_auth):
    """Las reglas de segmento deben coincidir aunque el valor se escriba con
    MAYÚSCULAS: en PostgreSQL LIKE es case-sensitive (en SQLite no, por eso
    la suite clásica no lo detectaba). Regresión del hallazgo V5."""
    from app.services import campaigns as campaign_service

    marca = uuid.uuid4().hex[:6]
    _sembrar(client, 2, marca=marca)
    r = client.post("/api/v1/segments", json={
        "name": f"V5C seg {marca}",
        "rules": [{"campo": "email", "op": "contiene",
                   "valor": f"V5C-{marca}".upper()}]})
    assert r.status_code == 201, r.text
    seg_id = r.json()["id"]

    db = SessionLocal()
    try:
        camp = Campaign(name=f"V5C segcamp {marca}", subject="S", from_name="T",
                        from_email="t@local.test", category="informativa",
                        segment_id=seg_id)
        db.add(camp)
        db.flush()
        total = campaign_service.count_recipients(db, camp)
        assert total >= 2, (
            f"el segmento en MAYÚSCULAS debe coincidir con correos en "
            f"minúsculas (encontrados: {total})")
    finally:
        db.close()


def test_segmento_por_atributo_comportamiento_documentado(client, admin_auth):
    """Candado de comportamiento: las reglas 'atributo:clave' seleccionan los
    contactos que TIENEN la clave (coincidencia de texto sobre el JSON); el
    valor/op aún no filtran. Limitación conocida, documentada en el informe:
    si cambia, debe ser una decisión consciente con migración de segmentos."""
    from app.services import campaigns as campaign_service

    marca = uuid.uuid4().hex[:6]
    db = SessionLocal()
    try:
        db.add(Contact(email=_email(f"atrio-{marca}"), status="suscripto",
                       attributes={"ciudad": "Madrid"}))
        db.add(Contact(email=_email(f"atrio2-{marca}"), status="suscripto",
                       attributes={"ciudad": "Barcelona"}))
        db.add(Contact(email=_email(f"atrio3-{marca}"), status="suscripto",
                       attributes={"pais": "España"}))
        db.commit()
        # Segmento por atributo (reglas directas al modelo, sin API)
        from app.models import Segment
        seg = Segment(name=f"V5C atri {marca}",
                      rules=[{"campo": "atributo:ciudad", "op": "contiene",
                              "valor": "MADRID"}])
        db.add(seg)
        db.commit()
        camp2 = Campaign(name=f"V5C atr2 {marca}", subject="S", from_name="T",
                         from_email="t@local.test", category="informativa",
                         segment_id=seg.id)
        db.add(camp2)
        db.commit()
        total = campaign_service.count_recipients(db, camp2)
        assert total == 2, (
            "los contactos con la clave 'ciudad' se seleccionan (2), "
            "independientemente del valor (comportamiento documentado)")
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# utilidades internas
# --------------------------------------------------------------------------- #


def _tag_id(db) -> str:
    from app.models import Tag
    tag = Tag(name=f"v5c-{uuid.uuid4().hex[:6]}")
    db.add(tag)
    db.flush()
    return tag.id


def _camp_id(db) -> str:
    camp = Campaign(name=f"V5C hist-{uuid.uuid4().hex[:6]}", subject="S",
                    from_name="T", from_email="t@local.test")
    db.add(camp)
    db.flush()
    return camp.id
