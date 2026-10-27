"""Importación asíncrona de contactos: trabajos, lotes, progreso y reanudación.

Diseño (motivado por el fallo de producción con 50.656 correos):

- El API NUNCA importa dentro del request: prevalida el archivo (parse +
  deduplicación, en un hilo), persiste el plan en import_job_data y encola un
  import_job. Responde 202 en segundos.
- Un worker dedicado (app.workers.import_worker, servicio aparte del worker de
  envíos) procesa los trabajos POR LOTES con COMMIT CORTO por lote: sin
  transacciones largas ("idle in transaction"), sin bloquear INSERTs ajenos.
- Progreso persistente en import_jobs (creados/actualizados/errores/notices y
  rows_processed): el frontend consulta la fila y el worker REANUDA tras un
  reinicio desde el último lote confirmado.
- Idempotencia: upsert_contact es idempotente (email único) y el conteo
  creado/actualizado se decide con el estado real de la BD por lote, así que
  reintentar un lote nunca duplica ni infla las cifras.
- Invariantes intactos: supresión (nunca reactiva bajas/rebotes/quejas),
  eventos de consentimiento, asociaciones a listas/etiquetas, auditoría.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.logging_conf import get_logger
from app.models import ImportJob, ImportJobData
from app.services import contacts as contact_service
from app.services import queue as queues

logger = get_logger("hj.import_jobs")

MAX_ERROR_SAMPLES = 20


# --------------------------------------------------------------------------- #
# Creación y consulta (las usa el API)
# --------------------------------------------------------------------------- #


def create_job(
    db: Session,
    *,
    filename: str,
    plan: Dict[str, Any],
    consent_source: Optional[str],
    consent_text: Optional[str],
    list_ids: List[str],
    tag_ids: List[str],
    user_id: str,
    user_email: str,
    ip: Optional[str] = None,
) -> ImportJob:
    """Persiste el trabajo con su plan prevalidado (SIN procesar nada).

    La fila de datos va en import_job_data (payload JSON); import_jobs queda
    ligera para actualizarse por lote sin reescribir megabytes.
    """
    job = ImportJob(
        filename=(filename or "import.csv")[:255],
        status="pendiente",
        total_rows=int(plan["filas_validas"]),
        invalid_rows=int(plan["filas_invalidas"]),
        duplicates_in_file=int(plan["duplicados_en_archivo"]),
        encoding=plan.get("encoding"),
        headers=plan.get("headers"),
        consent_source=(consent_source or None),
        consent_text=consent_text,
        list_ids=list_ids,
        tag_ids=tag_ids,
        user_id=user_id,
        user_email=user_email,
        ip=ip,
        notices={},
        error_samples=[],
    )
    db.add(job)
    db.flush()
    db.add(ImportJobData(job_id=job.id, payload=json.dumps(plan["rows"], ensure_ascii=False)))
    db.flush()
    return job


def enqueue(job_id: str) -> None:
    """Encola el trabajo en la cola Redis de importaciones."""
    queues.enqueue_import_job(job_id)


def get_progress(job: ImportJob) -> Dict[str, Any]:
    """Serializa el estado y progreso de un trabajo para el API/frontend."""
    total = max(job.total_rows, 0)
    done = min(job.rows_processed, total)
    pct = int(done * 100 / total) if total else (100 if job.status == "completado" else 0)
    return {
        "job_id": job.id,
        "filename": job.filename,
        "estado": job.status,
        "progreso_pct": pct,
        "total_filas": total,
        "filas_procesadas": done,
        "filas_invalidas": job.invalid_rows,
        "duplicados_en_archivo": job.duplicates_in_file,
        "creados": job.creados,
        "actualizados": job.actualizados,
        "errores": job.errores,
        "notices": job.notices or {},
        "error_samples": job.error_samples or [],
        "last_error": job.last_error,
        "attempts": job.attempts,
        "cancel_requested": job.cancel_requested,
        "encoding": job.encoding,
        "headers": job.headers,
        "list_ids": job.list_ids or [],
        "tag_ids": job.tag_ids or [],
        "user_email": job.user_email,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def request_cancel(db: Session, job: ImportJob) -> bool:
    """Marca un trabajo para cancelación.

    - Si aún no empezó: queda cancelado al momento.
    - Si está procesando: el worker lo aborta entre lotes (cancel_requested).
    Devuelve True si la solicitud se registró.
    """
    if job.status in ("completado", "fallido", "cancelado"):
        return False
    job.cancel_requested = True
    if job.status == "pendiente":
        job.status = "cancelado"
        job.finished_at = utcnow()
    db.commit()
    return True


def recover_pending_jobs(db: Session) -> List[str]:
    """Reencola trabajos vivos tras un reinicio del worker importador.

    - pendiente: se encoló pero nadie lo procesó (Redis sin AOF, caída, etc.).
    - procesando: el worker murió a mitad de trabajo; se reanuda desde
      rows_processed (idempotente).
    Devuelve los ids reencolados.
    """
    vivos = db.query(ImportJob).filter(ImportJob.status.in_(["pendiente", "procesando"])).all()
    encolados: List[str] = []
    for job in vivos:
        try:
            enqueue(job.id)
            encolados.append(job.id)
        except Exception as exc:  # Redis caído: el siguiente intento de arranque lo reintenta
            logger.error("No se pudo reencolar import %s: %s", job.id, type(exc).__name__)
    if encolados:
        logger.warning("Importaciones reencoladas tras reinicio", extra={"cantidad": len(encolados)})
    return encolados


# --------------------------------------------------------------------------- #
# Procesamiento (lo usa el worker importador)
# --------------------------------------------------------------------------- #


def _contar_notices(notices: Dict[str, int], notice: str) -> None:
    if notice:
        notices[notice] = notices.get(notice, 0) + 1


def _upsert_fila(db: Session, job: ImportJob, row: Dict[str, Any]) -> str:
    """Procesa una fila del plan. Devuelve el notice ("" si no hay)."""
    _, _created, notice = contact_service.upsert_contact(
        db,
        row["email"],
        first_name=row.get("first_name"),
        last_name=row.get("last_name"),
        attributes=row.get("attributes"),
        consent_source=job.consent_source,
        consent_at=utcnow(),
        consent_text=job.consent_text,
        source="csv_import",
        list_ids=job.list_ids or [],
        tag_ids=job.tag_ids or [],
    )
    return notice


def process_batch(db: Session, job: ImportJob, rows: List[Dict[str, Any]]) -> None:
    """Procesa UN lote del plan con UN commit (transacción corta).

    - Ruta rápida: todas las filas del lote en una transacción y commit. El
      conteo creado/actualizado se decide contra el estado REAL de la BD al
      iniciar el lote (consulta por emails del lote): así un reintento del
      mismo lote tras un crash no infla cifras ni duplica filas.
    - Ruta lenta (solo si el lote falla): rollback y re-proceso fila a fila con
      commit individual; una fila problemática no pierde el resto del lote ni
      tira el trabajo. (No se usan SAVEPOINTs: rotos con el driver sqlite.)
    """
    if not rows:
        return
    emails = [r["email"] for r in rows]
    existentes = {
        c.email
        for c in db.query(contact_service.Contact).filter(
            contact_service.Contact.email.in_(emails)
        ).all()
    }

    notices: Dict[str, int] = dict(job.notices or {})
    error_samples: List[Dict[str, Any]] = list(job.error_samples or [])
    lote_creados = lote_actualizados = lote_errores = 0

    try:
        # --- ruta rápida: lote completo en una transacción -----------------
        for row in rows:
            notice = _upsert_fila(db, job, row)
            _contar_notices(notices, notice)
            if row["email"] in existentes:
                lote_actualizados += 1
            else:
                lote_creados += 1
        db.commit()  # fin de la transacción corta: nada queda "idle in transaction"
    except Exception as fast_exc:
        db.rollback()
        logger.warning("Lote con fallo; reprocesando fila a fila", extra={
            "job": job.id, "error": type(fast_exc).__name__, "filas": len(rows)})
        # --- ruta lenta: fila a fila, commit individual --------------------
        lote_creados = lote_actualizados = lote_errores = 0
        notices = dict(job.notices or {})
        for row in rows:
            try:
                ya_estaba = db.query(contact_service.Contact).filter(
                    contact_service.Contact.email == row["email"]).first() is not None
                notice = _upsert_fila(db, job, row)
                db.commit()
                _contar_notices(notices, notice)
                if ya_estaba:
                    lote_actualizados += 1
                else:
                    lote_creados += 1
            except Exception as exc:  # una fila mala no tira el trabajo entero
                db.rollback()
                lote_errores += 1
                if len(error_samples) < MAX_ERROR_SAMPLES:
                    error_samples.append({
                        "line": row.get("line"),
                        "email": row["email"][:120],
                        "error": f"{type(exc).__name__}: {exc}"[:200],
                    })
                logger.warning("Fila de importación fallida", extra={
                    "job": job.id, "email": row["email"][:60], "error": type(exc).__name__})

    # Progreso persistente (transacción aparte, fila ligera)
    job = db.get(ImportJob, job.id)
    job.rows_processed += len(rows)
    job.creados += lote_creados
    job.actualizados += lote_actualizados
    job.errores += lote_errores
    job.notices = notices
    job.error_samples = error_samples
    db.commit()


def process_job(db: Session, job_id: str, *, batch_size: Optional[int] = None) -> Dict[str, Any]:
    """Procesa un trabajo completo (o lo reanuda) por lotes.

    Devuelve un resumen para métricas del worker. Errores de lote completos
    (p. ej. BD caída) elevan la excepción tras IMPORT_LOT_RETRIES intentos:
    el trabajo queda fallido y podrá reanudarse después desde su offset.
    """
    batch = batch_size or settings.IMPORT_BATCH_SIZE
    job = db.get(ImportJob, job_id)
    if not job:
        return {"accion": "descartado", "motivo": "trabajo_inexistente"}
    if job.status in ("completado", "cancelado", "fallido"):
        return {"accion": "omitido", "motivo": f"estado_{job.status}"}

    data = db.get(ImportJobData, job_id)
    if not data:
        job.status = "fallido"
        job.last_error = "payload_perdido"
        job.finished_at = utcnow()
        db.commit()
        return {"accion": "error", "motivo": "payload_perdido"}

    filas: List[Dict[str, Any]] = json.loads(data.payload)
    offset = min(job.rows_processed, len(filas))
    total = len(filas)

    # Cada vez que el worker toma el trabajo se cuenta (límite de reencolados)
    job.status = "procesando"
    job.started_at = job.started_at or utcnow()
    job.attempts += 1
    db.commit()

    logger.info("Importación procesando", extra={
        "job": job.id, "total": total, "desde": offset, "lote": batch})

    reintento_lote = 0
    while offset < total:
        # Cancelación solicitada: abortar entre lotes conservando el progreso
        db.refresh(job)
        if job.cancel_requested:
            job.status = "cancelado"
            job.finished_at = utcnow()
            db.commit()
            logger.warning("Importación cancelada", extra={
                "job": job.id, "procesadas": job.rows_processed})
            return {"accion": "cancelado", "procesadas": job.rows_processed}

        lote = filas[offset:offset + batch]
        try:
            process_batch(db, job, lote)
            reintento_lote = 0
        except Exception as exc:
            db.rollback()
            reintento_lote += 1
            logger.error("Fallo de lote de importación", extra={
                "job": job.id, "lote": offset, "intento": reintento_lote,
                "error": type(exc).__name__})
            if reintento_lote >= settings.IMPORT_LOT_RETRIES:
                job = db.get(ImportJob, job_id)
                job.status = "fallido"
                job.last_error = f"error_lote_{offset}: {type(exc).__name__}"
                job.finished_at = utcnow()
                db.commit()
                return {"accion": "error", "motivo": type(exc).__name__, "offset": offset}
            continue  # reintenta el mismo lote (idempotente)

        offset += len(lote)

    # Completado
    job = db.get(ImportJob, job_id)
    if job.cancel_requested and job.rows_processed < total:
        job.status = "cancelado"
    else:
        job.status = "completado"
    job.finished_at = utcnow()
    db.commit()

    # Auditoría en nombre del usuario que la solicitó
    contact_service_contact_audit(job)
    logger.info("Importación completada", extra={
        "job": job.id, "creados": job.creados, "actualizados": job.actualizados,
        "errores": job.errores, "filas": job.rows_processed})
    return {
        "accion": job.status,
        "creados": job.creados, "actualizados": job.actualizados,
        "errores": job.errores, "filas": job.rows_processed,
    }


def contact_service_contact_audit(job: ImportJob) -> None:
    """Auditoría de la importación completada (la hace el worker, no el API)."""
    from app.core.audit import audit
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        trabajo = db.get(ImportJob, job.id)
        audit(
            db, "contactos_importados",
            user_id=trabajo.user_id, user_email=trabajo.user_email,
            entity_type="import_job", entity_id=trabajo.id,
            detail={
                "archivo": trabajo.filename,
                "creados": trabajo.creados,
                "actualizados": trabajo.actualizados,
                "invalidos": trabajo.invalid_rows,
                "duplicados_en_archivo": trabajo.duplicates_in_file,
                "errores": trabajo.errores,
                "estado": trabajo.status,
            },
            ip=trabajo.ip,
        )
        db.commit()
    finally:
        db.close()
