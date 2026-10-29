"""Operaciones masivas sobre contactos (V5): eliminación lógica por lotes,
vaciado global, purga física controlada, progreso persistente y restauración.

Diseño (heredado de import_jobs):
- El API crea la operación (respuesta inmediata) y la encola en hj:q:bulk.
- El worker importador la procesa por LOTES con commits cortos: nunca hay una
  transacción gigante sobre 50k+ filas.
- El progreso se persiste en bulk_operations: el frontend sondea esa fila y el
  worker puede reanudar tras un reinicio desde `procesados`.

Seguridad de datos:
- SOLO borrado lógico (deleted_at): supresiones, bajas, quejas, evidencia de
  consentimiento y eventos SES NUNCA se destruyen.
- La purga física es un tipo aparte (purgar), requiere frase de confirmación
  escrita y OMITE contactos con mensajes de campaña (preserva estadísticas).
- Los contactos eliminados quedan excluidos de destinatarios y de las
  importaciones (upsert_contact no reactiva eliminados).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.logging_conf import get_logger
from app.models import (
    BulkOperation,
    CampaignMessage,
    Contact,
    contact_lists_rel,
    contact_tags_rel,
)
from app.services import queue as queues

logger = get_logger("hj.bulk_ops")

LOTE = 1000  # contactos por lote (commit corto por lote)

TIPOS = ("eliminar_ids", "eliminar_filtro", "vaciar_todo", "purgar")


class BulkError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Creación y progreso
# --------------------------------------------------------------------------- #


def hay_operacion_en_curso(db: Session) -> bool:
    """True si hay una operación masiva pendiente o procesando.

    Las operaciones se ejecutan en SERIE (una cola): evitar solapes protege
    contra carreras vaciar+importar o vaciar+vaciar.
    """
    return (
        db.query(BulkOperation.id)
        .filter(BulkOperation.status.in_(("pendiente", "procesando")))
        .first()
        is not None
    )


def recover_pending_operations(db: Session, *, min_age_seconds: int = 120) -> int:
    """Reencola operaciones 'pendiente'/'procesando' estancadas (arranque y
    recuperación periódica del worker). Idempotente: el procesado continúa
    desde el último lote confirmado y el lock Redis evita solapes."""
    from datetime import timedelta as _td
    edad = utcnow() - _td(seconds=max(min_age_seconds, 0))
    atascadas = (
        db.query(BulkOperation)
        .filter(BulkOperation.status.in_(("pendiente", "procesando")),
                BulkOperation.updated_at < edad)
        .all()
    )
    reencoladas = 0
    for op in atascadas:
        try:
            queues.enqueue_bulk_operation(op.id)
            reencoladas += 1
        except Exception:
            pass  # Redis caído: se reintentará en la próxima pasada
    return reencoladas


def create_operation(db: Session, tipo: str, criterios: Dict[str, Any], *,
                     user_id: Optional[str] = None, user_email: Optional[str] = None,
                     ip: Optional[str] = None) -> BulkOperation:
    if tipo not in TIPOS:
        raise BulkError(f"Tipo de operación desconocido: {tipo}")
    op = BulkOperation(tipo=tipo, status="pendiente", criterios=criterios,
                       user_id=user_id, user_email=user_email, ip=ip)
    db.add(op)
    db.flush()
    return op


def get_progress(op: BulkOperation) -> Dict[str, Any]:
    progreso = 0
    if op.total_objetivo > 0:
        progreso = min(100, round(100.0 * op.procesados / op.total_objetivo))
    elif op.status in ("completada", "cancelada", "cancelada_parcial"):
        progreso = 100
    return {
        "id": op.id, "tipo": op.tipo, "status": op.status,
        "total_objetivo": op.total_objetivo, "procesados": op.procesados,
        "afectados": op.afectados, "omitidos": op.omitidos,
        "progreso_pct": progreso, "cancel_requested": op.cancel_requested,
        "last_error": op.last_error, "user_email": op.user_email,
        "criterios": op.criterios,
        "created_at": op.created_at, "started_at": op.started_at,
        "finished_at": op.finished_at,
    }


def request_cancel(db: Session, op: BulkOperation) -> bool:
    """Pide cancelación (efectiva entre lotes). False si ya terminó."""
    if op.status not in ("pendiente", "procesando"):
        return False
    op.cancel_requested = True
    if op.status == "pendiente":  # aún no empezó: termina aquí mismo
        op.status = "cancelada"
        op.finished_at = utcnow()
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# Filtros compartidos con el listado de contactos
# --------------------------------------------------------------------------- #


def contacts_query(db: Session, *, q: Optional[str] = None, estado: Optional[str] = None,
                   list_id: Optional[str] = None, tag_id: Optional[str] = None,
                   papelera: bool = False, hasta_created=None):
    """Mismos filtros que GET /contacts (fuente única de verdad).

    - Papelera desactivada (defecto): solo contactos NO eliminados.
    - papelera=True: SOLO eliminados lógicamente.
    - hasta_created: límite de población para operaciones masivas (un contacto
      creado después del inicio de la operación queda fuera del objetivo).
    """
    query = db.query(Contact)
    if papelera:
        query = query.filter(Contact.deleted_at.isnot(None))
    else:
        query = query.filter(Contact.deleted_at.is_(None))
    if hasta_created is not None:
        query = query.filter(Contact.created_at <= hasta_created)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Contact.email.ilike(like),
                                 Contact.first_name.ilike(like),
                                 Contact.last_name.ilike(like)))
    if estado:
        query = query.filter(Contact.status == estado)
    if list_id:
        query = query.join(contact_lists_rel, contact_lists_rel.c.contact_id == Contact.id).filter(
            contact_lists_rel.c.list_id == list_id)
    if tag_id:
        query = query.join(contact_tags_rel, contact_tags_rel.c.contact_id == Contact.id).filter(
            contact_tags_rel.c.tag_id == tag_id)
    return query


def count_filtered(db: Session, **filtros) -> int:
    """COUNT en SQL de los contactos que afectaría una operación filtrada."""
    return int(contacts_query(db, **filtros).count())


def _count_activos(db: Session, filtros: Dict[str, Any], *, hasta=None) -> int:
    """COUNT de contactos ACTIVOS que coinciden con los filtros (objetivo del
    trabajo). `hasta` congela la población al inicio de la operación."""
    query = contacts_query(db, **filtros)
    if hasta is not None:
        query = query.filter(Contact.created_at <= hasta)
    return int(query.count())


# --------------------------------------------------------------------------- #
# Borrado lógico por lotes (worker)
# --------------------------------------------------------------------------- #


def _eliminar_lote(db: Session, op: BulkOperation, contactos: List[Contact]) -> int:
    """Marca un lote como eliminado (borrado lógico) y confirma el commit."""
    ahora = utcnow()
    ids = [c.id for c in contactos]
    actualizados = (
        db.query(Contact)
        .filter(Contact.id.in_(ids), Contact.deleted_at.is_(None))
        .update({"deleted_at": ahora, "deleted_by": op.user_id,
                 "deleted_by_operation": op.id}, synchronize_session=False)
    )
    db.commit()
    return int(actualizados or 0)


def _lotes_activos(db: Session, filtros: Dict[str, Any], *, hasta=None, incluir_eliminados: bool = False):
    """Itera LOTES de contactos candidatos, SIEMPRE desde el principio (OFFSET 0).

    Por qué offset-0: el borrado lógico saca las filas del filtro (deleted_at
    IS NULL), así que un OFFSET creciente SALTARÍA contactos sin procesar.
    Con offset-0 cada iteración re-consulta "los primeros LOTE candidatos":
    el conjunto se encoge por nuestra propia acción, el bucle termina y la
    reanudación tras un reinicio es trivial (vuelve a empezar donde quedó).
    `hasta` (created_at <= hasta) congela el objetivo: contactos creados
    DESPUÉS del inicio quedan fuera (una importación concurrente no es
    barrida por el vaciado a medias).
    """
    while True:
        filtros_lote = dict(filtros)
        if hasta is not None and not incluir_eliminados:
            filtros_lote["hasta_created"] = hasta
        query = contacts_query(db, papelera=incluir_eliminados, **filtros_lote)
        lote = query.order_by(Contact.created_at, Contact.id).limit(LOTE).all()
        if not lote:
            return
        yield lote


def _lotes_para_purgar(db: Session, cutoff, *, suprimir: bool = False):
    """Lotes de contactos borrados lógicamente ANTES de `cutoff` y SIN mensajes
    de campaña (la purga nunca destruye estadísticas ni eventos históricos).

    OFFSET 0 en cada iteración: las filas purgadas salen del conjunto y la
    siguiente consulta devuelve el siguiente bloque. Reanudable tras reinicio.
    El borrado es EXPLÍCITO por tabla (consent_events y relaciones) para ser
    portable (SQLite/PostgreSQL) y auditable.
    suprimir=True añade los correos a la lista de supresión (razón «purga»)
    para que una importación antigua no los vuelva a crear como suscriptos.
    """
    from app.models import ConsentEvent  # importación tardía
    from app.services.contacts import normalize_email, suppress_email
    while True:
        con_mensajes = (
            db.query(CampaignMessage.contact_id)
            .filter(CampaignMessage.contact_id == Contact.id)
            .exists()
        )
        candidatos = (
            db.query(Contact)
            .filter(Contact.deleted_at.isnot(None),
                    Contact.deleted_at <= cutoff,
                    ~con_mensajes)
            .order_by(Contact.created_at, Contact.id)
            .limit(LOTE)
            .all()
        )
        if not candidatos:
            return
        ids = [c.id for c in candidatos]
        if suprimir:
            for c in candidatos:
                suppress_email(db, normalize_email(c.email), reason="purga", source="manual")
        # Borrado explícito y portable
        db.execute(contact_tags_rel.delete().where(contact_tags_rel.c.contact_id.in_(ids)))
        db.execute(contact_lists_rel.delete().where(contact_lists_rel.c.contact_id.in_(ids)))
        db.query(ConsentEvent).filter(ConsentEvent.contact_id.in_(ids)).delete(
            synchronize_session=False)
        borrados = (
            db.query(Contact)
            .filter(Contact.id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()
        yield borrados


def process_operation(db: Session, op_id: str) -> Dict[str, Any]:
    """Procesa una operación masiva (lado worker). Reanudable e idempotente.

    - Commit por lote: interrumpir (reinicio, cancelación) deja progreso
      confirmado y consistente.
    - `omitidos` cuenta contactos que ya estaban eliminados (carrera con otra
      operación) o excluidos de la purga.
    """
    op = db.get(BulkOperation, op_id)
    if not op:
        return {"accion": "descartado", "motivo": "operacion_inexistente"}
    if op.status not in ("pendiente", "procesando"):
        return {"accion": "omitido", "motivo": f"estado_{op.status}"}

    # Lock lógico en Redis: un solo worker procesa operaciones en serie; si el
    # worker murió a mitad, el lock expira y la recuperación periódica reencola.
    try:
        r = queues.get_redis()
        lock = r.lock("hj:lock:bulk", timeout=900, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            return {"accion": "omitido", "motivo": "otra_operacion_en_proceso"}
    except Exception:
        lock = None  # Redis caído: seguimos sin lock (operación en serie por diseño)

    db2 = None
    try:
        # Sesión propia: los commits por lote no tocan la sesión del llamador
        from app.core.db import SessionLocal
        db2 = SessionLocal()
        op = db2.get(BulkOperation, op.id)  # refrescar en la nueva sesión
        if op.status == "pendiente":
            op.status = "procesando"
            op.started_at = utcnow()
            db2.commit()
        elif op.status == "procesando":
            pass  # reanudación

        criterios = dict(op.criterios or {})
        if op.tipo == "purgar":
            dias = int(criterios.get("antiguedad_minima_dias", 30) or 0)
            cutoff = utcnow() - timedelta(days=dias)
            # Objetivo total (una sola vez, estable: los candidatos salen del
            # conjunto solo al ser purgados por esta misma operación)
            if op.total_objetivo == 0:
                con_mensajes = (
                    db2.query(CampaignMessage.contact_id)
                    .filter(CampaignMessage.contact_id == Contact.id)
                    .exists()
                )
                op.total_objetivo = int(
                    db2.query(func.count(Contact.id))
                    .filter(Contact.deleted_at.isnot(None),
                            Contact.deleted_at <= cutoff,
                            ~con_mensajes)
                    .scalar() or 0
                )
                db2.commit()
            for borrados in _lotes_para_purgar(db2, cutoff,
                                               suprimir=bool(criterios.get("suprimir"))):
                # refresh OBLIGATORIO: la sesión usa expire_on_commit=False y
                # db2.get devolvería la copia cacheada sin ver la cancelación
                # pedida por otra sesión (el API). import_jobs hace lo mismo.
                db2.refresh(op)
                if op.cancel_requested:
                    op.status = "cancelada_parcial" if op.afectados else "cancelada"
                    op.finished_at = utcnow()
                    db2.commit()
                    return {"accion": "cancelada", "procesados": op.procesados}
                op.procesados += borrados
                op.afectados += borrados
                db2.commit()
        else:
            if op.tipo == "vaciar_todo":
                filtros: Dict[str, Any] = {}
            elif op.tipo == "eliminar_filtro":
                filtros = {k: criterios.get(k) for k in ("q", "estado", "list_id", "tag_id")}
            else:  # eliminar_ids: SOLO sus ids explícitos (defensa en profundidad)
                ids = [str(x) for x in (criterios.get("ids") or [])][:5000]
                op.total_objetivo = len(ids)
                db2.commit()
                if ids:
                    actualizados = (
                        db2.query(Contact)
                        .filter(Contact.id.in_(ids), Contact.deleted_at.is_(None))
                        .update({"deleted_at": utcnow(), "deleted_by": op.user_id,
                                 "deleted_by_operation": op.id}, synchronize_session=False)
                    )
                    op.procesados = len(ids)
                    op.afectados = int(actualizados or 0)
                    op.omitidos = len(ids) - op.afectados
                    db2.commit()
                op = db2.get(BulkOperation, op.id)
                # Terminal SIEMPRE: el trabajo en línea ya acabó; una
                # cancelación pedida a estas alturas llegó tarde (sin efecto).
                db2.refresh(op)
                op.status = "completada"
                op.finished_at = utcnow()
                db2.commit()
                return {"accion": "completada", "afectados": op.afectados,
                        "omitidos": op.omitidos}
            # `hasta`: congela el objetivo en los contactos existentes al inicio
            hasta = op.started_at
            # Objetivo total (una sola vez; con offset-0 el conteo de ACTIVOS
            # solo decrece por nuestra propia acción)
            if op.total_objetivo == 0:
                op.total_objetivo = _count_activos(db2, filtros, hasta=hasta)
                db2.commit()
            for lote in _lotes_activos(db2, filtros, hasta=hasta):
                # refresh OBLIGATORIO: la sesión usa expire_on_commit=False y
                # db2.get devolvería la copia cacheada sin ver la cancelación
                # pedida por otra sesión (el API). import_jobs hace lo mismo.
                db2.refresh(op)
                if op.cancel_requested:
                    op.status = "cancelada_parcial" if op.afectados else "cancelada"
                    op.finished_at = utcnow()
                    db2.commit()
                    return {"accion": "cancelada", "procesados": op.procesados}
                afectados = _eliminar_lote(db2, op, lote)
                op.procesados += afectados
                op.afectados += afectados
                db2.commit()

        op = db2.get(BulkOperation, op.id)
        # Terminal SIEMPRE: la cancelación se evalúa al INICIO de cada lote;
        # si llegamos aquí el objetivo ya se procesó por completo (el flag
        # llegó tarde, durante el último lote). Dejar 'procesando' con el flag
        # activo dejaría la operación huérfana: el estado honesto es
        # 'completada' con procesados == total_objetivo, y cancel_requested
        # queda registrado para la auditoría.
        db2.refresh(op)
        op.status = "completada"
        op.finished_at = utcnow()
        db2.commit()
        return {"accion": "completada", "afectados": op.afectados, "omitidos": op.omitidos}
    except Exception as exc:
        if db2 is not None:
            try:
                db2.rollback()
                op = db2.get(BulkOperation, op_id)
                if op and op.status in ("pendiente", "procesando"):
                    op.status = "fallida"
                    op.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    op.finished_at = utcnow()
                    db2.commit()
            except Exception:
                pass
        raise
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass
        if db2 is not None:
            db2.close()


# --------------------------------------------------------------------------- #
# Restauración
# --------------------------------------------------------------------------- #


def restore_by_operation(db: Session, op_id: str, *, user_id: Optional[str] = None) -> int:
    """Restaura TODOS los contactos eliminados por una operación masiva.

    UPDATE preciso por deleted_by_operation: no recalcula filtros (una
    restauración por criterios podría recuperar filas equivocadas si los datos
    cambiaron entre medias).
    """
    restaurados = (
        db.query(Contact)
        .filter(Contact.deleted_by_operation == op_id, Contact.deleted_at.isnot(None))
        .update({"deleted_at": None, "deleted_by": None,
                 "deleted_by_operation": None}, synchronize_session=False)
    )
    return int(restaurados or 0)


def restore_ids(db: Session, ids: List[str]) -> int:
    """Restaura contactos concretos de la papelera (uno o varios)."""
    ids = list(dict.fromkeys(ids))[:5000]
    if not ids:
        return 0
    restaurados = (
        db.query(Contact)
        .filter(Contact.id.in_(ids), Contact.deleted_at.isnot(None))
        .update({"deleted_at": None, "deleted_by": None,
                 "deleted_by_operation": None}, synchronize_session=False)
    )
    return int(restaurados or 0)
