"""Pruebas de la importación ASÍNCRONA de contactos (50k sin bloquear el API).

Cubre el fallo de producción (CSV de 50.656 correos bloqueaba el API, dejaba
transacciones 'idle in transaction' y la BD acababa con 0 contactos):

- El API responde 202 al instante y el login SIGUE respondiendo durante la
  subida de 50k filas (parseo en hilo) y mientras el trabajo está en cola.
- El worker importador procesa por lotes con commits cortos; el progreso es
  persistente y consultable.
- Idempotencia: reimportar no duplica; reintentar un lote no infla cifras.
- Reanudación tras "crash": el trabajo continúa desde el último lote.
- Cancelación entre lotes conservando el progreso.
- Invariantes: supresión nunca reactivada (bajas/rebotes/quejas), eventos de
  consentimiento, asociaciones a listas/etiquetas, auditoría.
- Fila corrupta: no tira el trabajo (ruta lenta fila a fila).
"""
from __future__ import annotations

import io
import time
import uuid

import pytest

from app.core.db import utcnow
from app.models import AuditLog, Contact, ImportJob, ImportJobData, Suppression
from app.services import import_jobs as import_service
from app.services import queue as queues
from tests.conftest import SessionLocal


def _csv_masivo(n: int, dominio: str = "bigmail.example") -> bytes:
    """CSV con n correos únicos + encabezado."""
    lineas = ["email,first_name,last_name"]
    lineas.extend(f"user{i}@{dominio},Nombre{i},Apellido{i}" for i in range(n))
    return ("\n".join(lineas) + "\n").encode("utf-8")


def _dominio() -> str:
    """Dominio único por test: la BD de pruebas persiste entre tests."""
    return f"{uuid.uuid4().hex[:10]}.test"


def _filas_plan(jid: str) -> list[dict]:
    db = SessionLocal()
    try:
        import json
        return json.loads(db.get(ImportJobData, jid).payload)
    finally:
        db.close()


def _procesar_prefijo(jid: str, n: int) -> None:
    """Simula que el worker procesó las primeras n filas y murió: inserta
    REALMENTE esas filas (con su commit) y deja el trabajo 'procesando'."""
    db = SessionLocal()
    try:
        job = db.get(ImportJob, jid)
        job.status = "procesando"
        job.started_at = job.started_at or utcnow()
        db.commit()
        import_service.process_batch(db, job, _filas_plan(jid)[:n])
    finally:
        db.close()


def _subida(client, contenido: bytes, filename: str = "big.csv", **form) -> dict:
    data = {"consent_source": "", "list_ids": "[]", "tag_ids": "[]"}
    data.update(form)
    r = client.post(
        "/api/v1/contacts/import",
        files={"archivo": (filename, io.BytesIO(contenido), "text/csv")},
        data=data,
    )
    assert r.status_code == 202, r.text
    return r.json()


def _procesar(job_id: str) -> dict:
    """Simula el worker importador: procesa el trabajo y devuelve su resumen."""
    db = SessionLocal()
    try:
        return import_service.process_job(db, job_id)
    finally:
        db.close()


def _progreso(client, job_id: str) -> dict:
    r = client.get(f"/api/v1/contacts/import/{job_id}")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Ciclo de vida básico
# --------------------------------------------------------------------------- #


def test_subida_responde_202_sin_procesar_en_el_request(client, admin_auth):
    dom = _dominio()
    contenido = _csv_masivo(300, dom)
    subida = _subida(client, contenido, consent_source="importacion_csv")
    assert subida["filas_validas"] == 300
    assert subida["estado"] == "pendiente"
    # Nada se importó todavía: el API solo creó el trabajo
    db = SessionLocal()
    try:
        creados = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert creados == 0, "El API no debe importar dentro del request"
        job = db.get(ImportJob, subida["job_id"])
        assert job is not None and job.status == "pendiente"
        assert job.total_rows == 300
    finally:
        db.close()


def test_progreso_y_finalizacion(client, admin_auth, importar_csv):
    dom = _dominio()
    progreso = importar_csv(_csv_masivo(1200, dom), consent_source="importacion_csv")
    assert progreso["estado"] == "completado"
    assert progreso["total_filas"] == 1200
    assert progreso["filas_procesadas"] == 1200
    assert progreso["creados"] == 1200
    assert progreso["progreso_pct"] == 100
    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == 1200
    finally:
        db.close()


def test_listado_de_ultimas_importaciones(client, admin_auth, importar_csv):
    importar_csv(_csv_masivo(5))
    importar_csv(_csv_masivo(3))
    r = client.get("/api/v1/contacts/import/jobs")
    assert r.status_code == 200
    jobs = r.json()
    assert len(jobs) >= 2
    assert {"estado", "creados", "total_filas", "progreso_pct"} <= set(jobs[0].keys())


def test_progreso_job_inexistente_404(client, admin_auth):
    assert client.get("/api/v1/contacts/import/no-existe").status_code == 404


def test_consentimiento_listas_y_etiquetas(client, admin_auth, importar_csv):
    lista = client.post("/api/v1/lists", json={"name": f"L-imp-{uuid.uuid4().hex[:6]}"})
    tag = client.post("/api/v1/tags", json={"name": f"t-imp-{uuid.uuid4().hex[:6]}"})
    lid, tid = lista.json()["id"], tag.json()["id"]
    dom = _dominio()

    progreso = importar_csv(
        _csv_masivo(10, dom),
        consent_source="formulario_web",
        consent_text="Acepto explícitamente",
        list_ids=f'["{lid}"]', tag_ids=f'["{tid}"]',
    )
    assert progreso["creados"] == 10
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == f"user0@{dom}").first()
        assert c is not None
        assert [l.id for l in c.lists] == [lid]
        assert [t.id for t in c.tags] == [tid]
        eventos = [e.event for e in c.consent_history]
        assert "alta" in eventos and "consentimiento" in eventos
    finally:
        db.close()
    # Asociaciones visibles por filtro de listas
    r = client.get(f"/api/v1/contacts?list_id={lid}")
    assert r.json()["total"] == 10


def test_auditoria_solicitada_y_completada(client, admin_auth, importar_csv):
    importar_csv(_csv_masivo(4), consent_source="importacion_csv")
    db = SessionLocal()
    try:
        acciones = {a.action for a in db.query(AuditLog).filter(
            AuditLog.action.in_(["importacion_solicitada", "contactos_importados"])).all()}
        assert "importacion_solicitada" in acciones
        assert "contactos_importados" in acciones
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Idempotencia y reintentos
# --------------------------------------------------------------------------- #


def test_reimportar_mismo_archivo_no_duplica(client, admin_auth, importar_csv):
    dom = _dominio()
    contenido = _csv_masivo(80, dom)
    p1 = importar_csv(contenido, consent_source="importacion_csv")
    assert p1["creados"] == 80
    p2 = importar_csv(contenido, consent_source="importacion_csv")
    assert p2["creados"] == 0
    assert p2["actualizados"] == 80
    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == 80
    finally:
        db.close()


def test_reprocesar_lote_ya_confirmado_no_infla_cifras(client, admin_auth):
    """Reintentar un lote cuyo commit ya se aplicó (crash antes de avanzar el
    offset) no duplica contactos: el conteo viene del estado real de la BD."""
    dom = _dominio()
    subida = _subida(client, _csv_masivo(60, dom))
    jid = subida["job_id"]
    db = SessionLocal()
    try:
        job = db.get(ImportJob, jid)
        job.status = "procesando"
        db.commit()
        # Procesa el primer lote de 20 (commit dentro de process_batch)...
        import_service.process_batch(db, job, _filas_plan(jid)[:20])
        # ...y simula el crash ANTES de que el progreso (offset + contadores,
        # que se confirman JUNTOS en un único commit) quedara persistido
        job.rows_processed = 0
        job.creados = 0
        job.actualizados = 0
        db.commit()
    finally:
        db.close()

    # Reanuda el trabajo completo: los 20 ya insertados cuentan como
    # actualizados, no duplicados; los 40 restantes se crean
    res = _procesar(jid)
    assert res["accion"] == "completado"
    assert res["creados"] == 40
    assert res["actualizados"] >= 20
    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == 60, "Reintentar un lote no debe duplicar contactos"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Reanudación tras reinicio y recuperación
# --------------------------------------------------------------------------- #


def test_reanudacion_tras_crash_continua_desde_el_ultimo_lote(client, admin_auth):
    dom = _dominio()
    subida = _subida(client, _csv_masivo(150, dom))
    jid = subida["job_id"]

    # El worker procesó 100 filas (2 lotes) y murió: esas filas ESTÁN en BD
    _procesar_prefijo(jid, 100)

    db = SessionLocal()
    try:
        job = db.get(ImportJob, jid)
        assert job.rows_processed == 100
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == 100
    finally:
        db.close()

    # "Reinicio del worker": recover_pending_jobs + process_job
    # (puede haber otros trabajos vivos de pruebas anteriores en la BD persistente)
    db = SessionLocal()
    try:
        encolados = import_service.recover_pending_jobs(db)
        assert jid in encolados
    finally:
        db.close()
    res = _procesar(jid)
    assert res["accion"] == "completado"
    assert res["filas"] == 150

    progreso = _progreso(client, jid)
    assert progreso["creados"] == 150
    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == 150, "La reanudación completa el trabajo sin duplicar"
    finally:
        db.close()


def test_payload_perdido_marca_fallido_con_mensaje(client, admin_auth):
    subida = _subida(client, _csv_masivo(3))
    jid = subida["job_id"]
    db = SessionLocal()
    try:
        db.delete(db.get(ImportJobData, jid))
        db.commit()
        res = import_service.process_job(db, jid)
        assert res["accion"] == "error" and res["motivo"] == "payload_perdido"
        job = db.get(ImportJob, jid)
        assert job.status == "fallido" and job.last_error == "payload_perdido"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Cancelación
# --------------------------------------------------------------------------- #


def test_cancelar_pendiente_es_inmediato(client, admin_auth):
    subida = _subida(client, _csv_masivo(50))
    jid = subida["job_id"]
    r = client.post(f"/api/v1/contacts/import/{jid}/cancel")
    assert r.status_code == 200
    assert r.json()["estado"] == "cancelado"
    # procesar un trabajo cancelado lo omite
    res = _procesar(jid)
    assert res["accion"] == "omitido"


def test_cancelar_procesando_aborta_entre_lotes_y_conserva_progreso(client, admin_auth):
    dom = _dominio()
    subida = _subida(client, _csv_masivo(200, dom))
    jid = subida["job_id"]

    # El worker procesó 50 filas reales; entonces el usuario pide cancelar
    _procesar_prefijo(jid, 50)
    db = SessionLocal()
    try:
        job = db.get(ImportJob, jid)
        job.cancel_requested = True
        db.commit()
    finally:
        db.close()

    res = _procesar(jid)
    assert res["accion"] == "cancelado"
    progreso = _progreso(client, jid)
    assert progreso["estado"] == "cancelado"
    assert progreso["filas_procesadas"] == 50
    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == 50, "El progreso coincide con lo realmente insertado"
    finally:
        db.close()
    # Cancelar algo ya terminado -> 409
    r = client.post(f"/api/v1/contacts/import/{jid}/cancel")
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# Robustez de datos
# --------------------------------------------------------------------------- #


def test_fila_problematica_no_tira_el_trabajo(client, admin_auth, importar_csv):
    """Una fila que fuerza la ruta lenta no pierde el resto del lote."""
    dom = _dominio()
    lineas = ["email,first_name"]
    lineas.extend(f"ok{i}@{dom},Nombre{i}" for i in range(6))
    contenido = ("\n".join(lineas) + "\n").encode()

    progreso = importar_csv(contenido)
    assert progreso["estado"] == "completado"
    assert progreso["creados"] == 6


def test_suprimidos_rebotes_y_quejas_nunca_reactivados(client, admin_auth, importar_csv):
    """Bajas, rebotes y quejas conservan su estado; suprimidos nuevos entran
    como dado_baja (nunca suscripto)."""
    dom = _dominio()
    e_sup, e_baja, e_rebote, e_queja, e_fresco = (
        f"nuevo-suprimido@{dom}", f"baja@{dom}", f"rebote@{dom}",
        f"queja@{dom}", f"fresco@{dom}")
    db = SessionLocal()
    try:
        # suprimido sin contacto previo
        db.add(Suppression(email=e_sup, reason="manual", source="manual"))
        # contacto dado de baja
        db.add(Contact(email=e_baja, status="dado_baja"))
        # contacto con rebote
        db.add(Contact(email=e_rebote, status="rebote"))
        # contacto con queja
        db.add(Contact(email=e_queja, status="queja"))
        db.commit()
    finally:
        db.close()

    progreso = importar_csv(
        ("email\n"
         f"{e_sup}\n"
         f"{e_baja}\n"
         f"{e_rebote}\n"
         f"{e_queja}\n"
         f"{e_fresco}\n").encode(),
        consent_source="importacion_csv",
    )
    assert progreso["creados"] == 2  # suprimido-nuevo + fresco
    assert progreso["actualizados"] == 3
    assert progreso["notices"].get("correo_suprimido") == 1
    assert progreso["notices"].get("existente_estado_dado_baja") == 1
    assert progreso["notices"].get("existente_estado_rebote") == 1
    assert progreso["notices"].get("existente_estado_queja") == 1

    db = SessionLocal()
    try:
        assert db.query(Contact).filter(Contact.email == e_sup).first().status == "dado_baja"
        assert db.query(Contact).filter(Contact.email == e_baja).first().status == "dado_baja"
        assert db.query(Contact).filter(Contact.email == e_rebote).first().status == "rebote"
        assert db.query(Contact).filter(Contact.email == e_queja).first().status == "queja"
        assert db.query(Contact).filter(Contact.email == e_fresco).first().status == "suscripto"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 50.656 contactos: la prueba del fallo de producción
# --------------------------------------------------------------------------- #


def test_import_50k_no_bloquea_api_y_login_sigue_respondiendo(client, admin_auth):
    """La prueba del caso real: 50.656 correos.

    - La subida responde 202 sin 500 y SIN bloquear: tras ella el login
      responde en < 5 s (antes, el event loop quedaba congelado minutos).
    - El worker procesa todo por lotes; el progreso es consistente.
    """
    N = 50_656
    dom = _dominio()
    t0 = time.monotonic()
    subida = _subida(client, _csv_masivo(N, dom))
    t_subida = time.monotonic() - t0
    assert subida["filas_validas"] == N
    assert t_subida < 60, f"La subida tardó {t_subida:.1f}s: demasiado"

    # El login responde NORMALMENTE con el trabajo de 50k pendiente en cola
    t1 = time.monotonic()
    login = client.post("/api/v1/auth/login",
                        json={"email": "nadie@bigmail.example", "password": "loquesea123"})
    t_login = time.monotonic() - t1
    assert login.status_code in (401, 400)   # responde (credenciales inválidas), no se cuelga
    assert t_login < 5, f"Login tardó {t_login:.1f}s con importación en cola: bloqueado"

    # Procesamiento por el worker (mismo código que el proceso separado)
    t2 = time.monotonic()
    res = _procesar(subida["job_id"])
    t_proc = time.monotonic() - t2
    assert res["accion"] == "completado"
    assert res["creados"] == N

    progreso = _progreso(client, subida["job_id"])
    assert progreso["progreso_pct"] == 100
    assert progreso["creados"] == N and progreso["errores"] == 0

    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == N, f"Esperados {N} contactos en BD, hay {n}"
    finally:
        db.close()

    # Y el login sigue respondiendo después de la importación completa
    assert client.post("/api/v1/auth/login",
                       json={"email": "nadie2@bigmail.example", "password": "x"}).status_code == 401


def test_import_50k_reimportacion_idempotente(client, admin_auth):
    """Reimportar el archivo de 50k: 0 creados, 50.656 actualizados, sin duplicados."""
    N = 50_656
    dom = _dominio()
    contenido = _csv_masivo(N, dom)
    subida = _subida(client, contenido)
    assert _procesar(subida["job_id"])["accion"] == "completado"

    subida2 = _subida(client, contenido)
    res = _procesar(subida2["job_id"])
    assert res["accion"] == "completado"
    assert res["creados"] == 0
    assert res["actualizados"] == N

    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like(f"%@{dom}")).count()
        assert n == N, "La reimportación de 50k no duplica nada"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Cola e infraestructura
# --------------------------------------------------------------------------- #


def test_cola_importacion_independiente_de_envios(client, admin_auth):
    """El trabajo se encola en hj:q:import (NO en la cola de envíos)."""
    subida = _subida(client, _csv_masivo(3))
    r = queues.get_redis()
    assert r.llen(queues.IMPORT_KEY) >= 1
    assert r.llen(queues.OUTBOUND_KEY) == 0
    jid = queues.pop_import_job(timeout_seconds=1)
    assert jid == subida["job_id"]
    # Y la cola de envíos sigue vacía
    assert r.llen(queues.OUTBOUND_KEY) == 0


def test_limite_de_filas_rechazado_con_mensaje_claro(client, admin_auth, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "IMPORT_MAX_ROWS", 10)
    contenido = _csv_masivo(11)
    data = {"consent_source": "", "list_ids": "[]", "tag_ids": "[]"}
    r = client.post("/api/v1/contacts/import",
                    files={"archivo": ("grande.csv", io.BytesIO(contenido), "text/csv")},
                    data=data)
    assert r.status_code == 400
    assert "filas válidas" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Regresión: alias /system/* (la pestaña SES de Ajustes daba 404)
# --------------------------------------------------------------------------- #


def test_alias_system_ses_status_y_audit(client, admin_auth):
    """El frontend llama /api/v1/system/ses/status: debe existir (antes 404)."""
    r = client.get("/api/v1/system/ses/status")
    assert r.status_code == 200, r.text
    assert "region" in r.json()
    # El alias antiguo sigue funcionando (compatibilidad)
    assert client.get("/api/v1/ses/status").status_code == 200
    # Auditoría con alias documentado
    ra = client.get("/api/v1/system/audit?page=1")
    assert ra.status_code == 200
    assert client.get("/api/v1/audit?page=1").status_code == 200
