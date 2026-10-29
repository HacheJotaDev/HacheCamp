"""Contactos: alta, edición, búsqueda, filtros, importación/exportación CSV,
historial de consentimiento y bajas.

La importación masiva es ASÍNCRONA: el API prevalida el archivo (en un hilo,
sin bloquear el event loop), persiste el plan y encola un trabajo que procesa
un worker dedicado por lotes con commits cortos. Nunca se importan 50k filas
dentro del request: eso bloqueaba PostgreSQL (idle in transaction) y el API.
"""
from __future__ import annotations

import csv
import io
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool

from app.core import security
from app.core.audit import audit
from app.core.db import get_db, utcnow
from app.core.deps import get_current_user, require_admin
from app.models import (
    BulkOperation,
    Campaign,
    Contact,
    ImportJob,
    User,
    contact_lists_rel,
    contact_tags_rel,
)
from app.schemas import (
    BulkDeleteFiltered,
    BulkOperationOut,
    ConsentEventOut,
    ContactBulkDelete,
    ContactCreate,
    ContactDetailOut,
    ContactUpdate,
    ImportJobProgress,
    ImportResult,
    PurgarRequest,
    VaciarTodoRequest,
)
from app.services import bulk_ops
from app.services import contacts as contact_service
from app.services import import_jobs as import_service
from app.services import queue as queues
from app.services.csv_import import CSVImportError, build_import_plan, parse_csv_bytes

router = APIRouter(prefix="/contacts", tags=["Contactos"])


@router.get("", response_model=dict)
def list_contacts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    q: Optional[str] = None,
    estado: Optional[str] = None,
    list_id: Optional[str] = None,
    tag_id: Optional[str] = None,
    papelera: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
):
    """Listado de contactos. Por defecto EXCLUYE los de la papelera; con
    `papelera=true` devuelve SOLO los eliminados lógicamente."""
    query = bulk_ops.contacts_query(db, q=q, estado=estado, list_id=list_id,
                                    tag_id=tag_id, papelera=papelera)
    total = query.count()
    items = (
        query.options(joinedload(Contact.tags), joinedload(Contact.lists))
        .order_by(Contact.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [ContactDetailOut.model_validate(c).model_dump(mode="json") for c in items],
    }


@router.post("", response_model=ContactDetailOut, status_code=201)
def create_contact(payload: ContactCreate, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Alta simplificada: el correo es el único dato obligatorio.

    No se vuelve a pedir consentimiento a contactos ya suscriptos; el estado
    y las bajas existentes se conservan tal cual.
    """
    contact, created, notice = contact_service.upsert_contact(
        db, payload.email,
        first_name=payload.first_name, last_name=payload.last_name,
        attributes=payload.attributes,
        consent_source=payload.consent_source, consent_text=payload.consent_text,
        consent_at=utcnow(), source="manual",
        list_ids=payload.list_ids, tag_ids=payload.tag_ids,
    )
    audit(db, "contacto_alta" if created else "contacto_actualizado",
          user_id=user.id, user_email=user.email, entity_type="contact",
          entity_id=contact.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/bulk-delete", response_model=dict)
def bulk_delete_contacts(payload: ContactBulkDelete, request: Request,
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Eliminación de una selección explícita (≤1000 IDs, requiere admin).

    V5: modo=eliminar es BORRADO LÓGICO (papelera): un solo UPDATE atómico;
    mensajes, eventos y consentimientos NO se tocan y la operación queda
    registrada para poder restaurarla (restaurar por operación o por IDs).
    modo=dar_baja conserva el comportamiento V4 (supresión + baja).
    """
    if not payload.confirmar:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Falta la confirmación explícita de la eliminación masiva")
    if payload.modo == "eliminar" and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "La eliminación de contactos requiere permisos de administrador")
    ids = list(dict.fromkeys(payload.ids))  # deduplica conservando orden
    encontrados = db.query(Contact).filter(Contact.id.in_(ids)).all()
    if not encontrados:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ningún contacto pertenece a la selección")

    if payload.modo == "dar_baja":
        for contact in encontrados:
            if contact.status != "suscripto" or contact.deleted_at is not None:
                continue
            contact_service.suppress_email(db, contact.email, reason="baja", source="manual")
            contact.status = "dado_baja"
            contact.unsubscribed_at = utcnow()
            contact_service.add_consent_event(db, contact, "baja", source="manual",
                                              detail={"por": user.email, "masivo": True})
        procesados = [c.id for c in encontrados if c.status == "dado_baja"]
        audit(db, "contactos_dados_baja_masivo",
              user_id=user.id, user_email=user.email, entity_type="contact",
              detail={"cantidad": len(procesados), "modo": payload.modo},
              ip=security.client_ip(request))
        db.commit()
        return {"procesados": len(procesados), "solicitados": len(ids), "modo": payload.modo}

    # modo=eliminar -> papelera (borrado lógico) con registro para restaurar
    operacion = bulk_ops.create_operation(
        db, "eliminar_ids", {"ids": ids, "cantidad": len(ids)},
        user_id=user.id, user_email=user.email, ip=security.client_ip(request))
    afectados = (
        db.query(Contact)
        .filter(Contact.id.in_(ids), Contact.deleted_at.is_(None))
        .update({"deleted_at": utcnow(), "deleted_by": user.id,
                 "deleted_by_operation": operacion.id}, synchronize_session=False)
    )
    operacion.status = "completada"
    operacion.total_objetivo = len(ids)
    operacion.procesados = len(ids)
    operacion.afectados = int(afectados or 0)
    operacion.omitidos = len(ids) - operacion.afectados
    operacion.started_at = utcnow()
    operacion.finished_at = utcnow()
    audit(db, "contactos_eliminados_masivo",
          user_id=user.id, user_email=user.email, entity_type="contact",
          detail={"cantidad": operacion.afectados, "modo": "eliminar",
                  "operacion": operacion.id},
          ip=security.client_ip(request))
    db.commit()
    return {"procesados": len(ids), "solicitados": len(ids), "modo": payload.modo,
            "afectados": operacion.afectados, "operacion_id": operacion.id}


@router.get("/papelera", response_model=dict)
def contacts_papelera(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                      q: Optional[str] = None, page: int = Query(1, ge=1),
                      per_page: int = Query(25, ge=1, le=100)):
    """Papelera de contactos: SOLO eliminados lógicamente (ruta estática)."""
    query = bulk_ops.contacts_query(db, q=q, papelera=True)
    total = query.count()
    items = (
        query.options(joinedload(Contact.tags), joinedload(Contact.lists))
        .order_by(Contact.deleted_at.desc())
        .offset((page - 1) * per_page).limit(per_page).all()
    )
    return {"total": total, "page": page, "per_page": per_page,
            "items": [ContactDetailOut.model_validate(c).model_dump(mode="json") for c in items]}


@router.post("/restore-lote", response_model=dict)
def restore_lote(payload: dict, request: Request,
                 db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Restaura contactos concretos de la papelera (admin; máx. 5000 IDs)."""
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Falta la lista de IDs a restaurar")
    restaurados = bulk_ops.restore_ids(db, [str(x) for x in ids])
    audit(db, "contactos_restaurados", user_id=user.id, user_email=user.email,
          entity_type="contact", detail={"cantidad": restaurados},
          ip=security.client_ip(request))
    db.commit()
    return {"restaurados": restaurados}


@router.post("/bulk-delete-filtrado", response_model=BulkOperationOut, status_code=202)
def bulk_delete_filtrado(payload: BulkDeleteFiltered, request: Request,
                         db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Eliminación lógica de TODOS los contactos que coinciden con los filtros
    del listado (asíncrona: 202 + progreso consultable).

    El API solo crea la operación y la encola: nunca procesa 50k+ filas en la
    petición (eso bloquearía PostgreSQL y el login de los usuarios).
    """
    if not payload.confirmar:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Falta la confirmación explícita (?/confirmar=true)")
    if bulk_ops.hay_operacion_en_curso(db):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Ya hay una operación masiva en curso: espera o cancelala")
    filtros = {k: getattr(payload, k) for k in ("q", "estado", "list_id", "tag_id")}
    objetivo = bulk_ops.count_filtered(db, **filtros)
    if objetivo == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Ningún contacto activo coincide con esos filtros")
    op = bulk_ops.create_operation(
        db, "eliminar_filtro", filtros,
        user_id=user.id, user_email=user.email, ip=security.client_ip(request))
    audit(db, "contactos_eliminacion_filtrada_solicitada",
          user_id=user.id, user_email=user.email, entity_type="bulk_operation",
          entity_id=op.id, detail={"objetivo": objetivo, **filtros},
          ip=security.client_ip(request))
    db.commit()
    try:
        queues.enqueue_bulk_operation(op.id)
    except Exception:
        pass  # la recuperación del worker reencolará la operación 'pendiente'
    return bulk_ops.get_progress(op)


@router.get("/vaciar-todo/preview", response_model=dict)
def vaciar_todo_preview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Cuántos registros activos afectaría el vaciado global + dependencias.

    Informa de campañas activas e importaciones en curso ANTES de pedir la
    confirmación reforzada.
    """
    activos = bulk_ops.count_filtered(db)
    campanas_activas = (
        db.query(func.count(Campaign.id))
        .filter(Campaign.status.in_(["en_cola", "enviando", "pausada"]),
                Campaign.deleted_at.is_(None))
        .scalar() or 0
    )
    importaciones = (
        db.query(func.count(ImportJob.id))
        .filter(ImportJob.status.in_(["pendiente", "procesando"]))
        .scalar() or 0
    )
    en_papelera = (
        db.query(func.count(Contact.id)).filter(Contact.deleted_at.isnot(None)).scalar() or 0
    )
    return {
        "contactos_activos": activos,
        "en_papelera": en_papelera,
        "campanas_activas": campanas_activas,
        "importaciones_en_curso": importaciones,
        "frase_confirmacion": "VACIAR CONTACTOS",
        "aviso": (
            "El vaciado es un BORRADO LÓGICO: los contactos pasan a la papelera "
            "y pueden restaurarse. Supresiones, bajas, consentimientos, "
            "mensajes y eventos SES se conservan íntegros."
        ),
    }


@router.post("/vaciar-todo", response_model=BulkOperationOut, status_code=202)
def vaciar_todo(payload: VaciarTodoRequest, request: Request,
                db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """VACIAR TODOS los contactos activos (admin, confirmación reforzada).

    Requisitos cumplidos: permisos admin, advertencia con conteo exacto
    (GET /vaciar-todo/preview), indicación de dependencias, frase escrita
    exacta «VACIAR CONTACTOS», confirmación booleana, bloqueo si hay
    operaciones/colas incompatibles y registro de auditoría.
    Borrado lógico por lotes con progreso persistente y cancelación.
    """
    if not payload.confirmar or payload.confirmar_texto.strip() != "VACIAR CONTACTOS":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Confirmación reforzada incompleta: escribe VACIAR CONTACTOS")
    if bulk_ops.hay_operacion_en_curso(db):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Ya hay una operación masiva en curso: espera o cancelala")
    activos = bulk_ops.count_filtered(db)
    if activos == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay contactos activos que vaciar")
    op = bulk_ops.create_operation(
        db, "vaciar_todo", {"frase": "VACIAR CONTACTOS"},
        user_id=user.id, user_email=user.email, ip=security.client_ip(request))
    audit(db, "contactos_vaciado_global_solicitado",
          user_id=user.id, user_email=user.email, entity_type="bulk_operation",
          entity_id=op.id, detail={"objetivo": activos}, ip=security.client_ip(request))
    db.commit()
    try:
        queues.enqueue_bulk_operation(op.id)
    except Exception:
        pass
    return bulk_ops.get_progress(op)


@router.post("/purgar", response_model=BulkOperationOut, status_code=202)
def purgar(payload: PurgarRequest, request: Request,
           db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Purga FÍSICA irreversible de contactos ya borrados lógicamente.

    - Autorización SEPARADA: frase exacta «PURGAR CONTACTOS».
    - Solo contactos en papelera con antigüedad >= antiguedad_minima_dias.
    - OMITE contactos con mensajes de campaña: sus estadísticas y eventos SES
      históricos NUNCA se destruyen (los omitidos quedan contados).
    - Consentimientos y asociaciones del contacto purgado se eliminan con él;
      la lista de supresión NUNCA se toca (opción suprimir=True añade el
      correo a supresión para evitar reimportaciones accidentales).
    """
    if not payload.confirmar or payload.confirmar_texto.strip() != "PURGAR CONTACTOS":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Confirmación incompleta: escribe PURGAR CONTACTOS")
    if bulk_ops.hay_operacion_en_curso(db):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Ya hay una operación masiva en curso: espera o cancelala")
    op = bulk_ops.create_operation(
        db, "purgar", {"antiguedad_minima_dias": payload.antiguedad_minima_dias,
                        "suprimir": bool(getattr(payload, "suprimir", False))},
        user_id=user.id, user_email=user.email, ip=security.client_ip(request))
    audit(db, "contactos_purga_fisica_solicitada",
          user_id=user.id, user_email=user.email, entity_type="bulk_operation",
          entity_id=op.id,
          detail={"antiguedad_minima_dias": payload.antiguedad_minima_dias},
          ip=security.client_ip(request))
    db.commit()
    try:
        queues.enqueue_bulk_operation(op.id)
    except Exception:
        pass
    return bulk_ops.get_progress(op)


@router.get("/bulk/jobs", response_model=List[BulkOperationOut])
def bulk_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user),
              limit: int = Query(10, ge=1, le=50)):
    """Últimas operaciones masivas (sondeo del panel)."""
    ops = db.query(BulkOperation).order_by(BulkOperation.created_at.desc()).limit(limit).all()
    return [bulk_ops.get_progress(o) for o in ops]


@router.get("/bulk/{op_id}", response_model=BulkOperationOut)
def bulk_progress(op_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Progreso persistente de una operación masiva (sondeo del frontend)."""
    op = db.get(BulkOperation, op_id)
    if not op:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Operación no encontrada")
    return bulk_ops.get_progress(op)


@router.post("/bulk/{op_id}/cancel", response_model=BulkOperationOut)
def bulk_cancel(op_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    """Solicita la cancelación (efectiva entre lotes; safe: solo faltan por
    hacer lotes completos, el progreso confirmado se conserva)."""
    op = db.get(BulkOperation, op_id)
    if not op:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Operación no encontrada")
    if not bulk_ops.request_cancel(db, op):
        raise HTTPException(status.HTTP_409_CONFLICT, "La operación ya terminó")
    audit(db, "operacion_masiva_cancelada", user_id=user.id, user_email=user.email,
          entity_type="bulk_operation", entity_id=op.id, ip=security.client_ip(request))
    db.commit()
    return bulk_ops.get_progress(db.get(BulkOperation, op_id))


@router.post("/bulk/{op_id}/restore", response_model=dict)
def bulk_restore(op_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    """Restaura TODOS los contactos eliminados por esta operación (admin).

    UPDATE preciso por deleted_by_operation: no recalcula filtros.
    """
    op = db.get(BulkOperation, op_id)
    if not op:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Operación no encontrada")
    restaurados = bulk_ops.restore_by_operation(db, op.id, user_id=user.id)
    audit(db, "contactos_restaurados_por_operacion",
          user_id=user.id, user_email=user.email, entity_type="bulk_operation",
          entity_id=op.id, detail={"restaurados": restaurados},
          ip=security.client_ip(request))
    db.commit()
    return {"restaurados": restaurados, "operacion_id": op.id}


@router.get("/{contact_id}", response_model=ContactDetailOut)
def get_contact(contact_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    contact = (
        db.query(Contact).options(joinedload(Contact.tags), joinedload(Contact.lists))
        .filter(Contact.id == contact_id).first()
    )
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    return contact


@router.patch("/{contact_id}", response_model=ContactDetailOut)
def update_contact(contact_id: str, payload: ContactUpdate, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    if payload.first_name is not None:
        contact.first_name = payload.first_name
    if payload.last_name is not None:
        contact.last_name = payload.last_name
    if payload.attributes is not None:
        contact.attributes = payload.attributes
    if payload.list_ids is not None:
        db.query(contact_lists_rel).filter(contact_lists_rel.c.contact_id == contact.id).delete()
        for lid in payload.list_ids:
            db.execute(contact_lists_rel.insert().values(contact_id=contact.id, list_id=lid))
    if payload.tag_ids is not None:
        db.query(contact_tags_rel).filter(contact_tags_rel.c.contact_id == contact.id).delete()
        for tid in payload.tag_ids:
            db.execute(contact_tags_rel.insert().values(contact_id=contact.id, tag_id=tid))
    audit(db, "contacto_editado", user_id=user.id, user_email=user.email,
          entity_type="contact", entity_id=contact.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/{contact_id}", response_model=dict)
def delete_contact(contact_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_admin)):
    """Elimina un contacto individual (V5: borrado lógico, requiere admin).

    - Pasa a la PAPELERA: desaparece del listado, deja de ser elegible para
      campañas y las importaciones NO lo reactivan.
    - NO toca supresiones ni historial: consentimientos, mensajes y eventos
      se conservan; es restaurable.
    - La baja con supresión (comportamiento V4 del DELETE) ahora es la
      acción aparte POST /contacts/{id}/baja.
    """
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    contact.deleted_at = utcnow()
    contact.deleted_by = user.id
    audit(db, "contacto_eliminado", user_id=user.id, user_email=user.email,
          entity_type="contact", entity_id=contact.id, ip=security.client_ip(request))
    db.commit()
    return {"papelera": True, "id": contact.id}


@router.post("/{contact_id}/baja", response_model=ContactDetailOut)
def dar_baja_contacto(contact_id: str, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Da de baja un contacto y lo añade a supresión (comportamiento V4:
    nunca volverá a recibir campañas). Conserva todo su historial."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    contact_service.suppress_email(db, contact.email, reason="baja", source="manual")
    contact.status = "dado_baja"
    contact.unsubscribed_at = utcnow()
    contact_service.add_consent_event(db, contact, "baja", source="manual",
                                      detail={"por": user.email})
    audit(db, "contacto_baja", user_id=user.id, user_email=user.email,
          entity_type="contact", entity_id=contact.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/{contact_id}/restore", response_model=ContactDetailOut)
def restore_contact(contact_id: str, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Restaura un contacto de la papelera (requiere admin)."""
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    if contact.deleted_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El contacto no está en la papelera")
    contact.deleted_at = None
    contact.deleted_by = None
    contact.deleted_by_operation = None
    audit(db, "contacto_restaurado", user_id=user.id, user_email=user.email,
          entity_type="contact", entity_id=contact.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(contact)
    return contact


@router.get("/{contact_id}/consent", response_model=List[ConsentEventOut])
def contact_consent_history(contact_id: str, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    return contact.consent_history


@router.post("/import", status_code=202, response_model=ImportResult)
async def import_csv(
    request: Request,
    archivo: UploadFile = File(...),
    consent_source: str = Form("", max_length=60),
    consent_text: str = Form("", max_length=2000),
    list_ids: str = Form("[]"),
    tag_ids: str = Form("[]"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Importación masiva CSV/TXT ASÍNCRONA (respuesta inmediata, 202).

    1. Prevalida el archivo en un HILO (parse + validación + deduplicación en
       archivo): el event loop queda libre y el API sigue respondiendo.
    2. Persiste el plan (import_job_data) y el trabajo (import_jobs).
    3. Encola el trabajo en la cola Redis de importaciones (independiente de
       la de envíos); un worker dedicado lo procesa por lotes.

    El progreso se consulta con GET /contacts/import/{job_id}.
    """
    try:
        list_id_list = json.loads(list_ids)
        tag_id_list = json.loads(tag_ids)
        if not isinstance(list_id_list, list) or not isinstance(tag_id_list, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Formato de listas/etiquetas inválido")

    raw = await archivo.read()
    filename = archivo.filename or "import.csv"

    # Parseo pesado FUERA del event loop (50k filas ≈ segundos de CPU)
    try:
        plan = await run_in_threadpool(build_import_plan, raw)
    except CSVImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    job = import_service.create_job(
        db,
        filename=filename,
        plan=plan,
        consent_source=consent_source or None,
        consent_text=consent_text or None,
        list_ids=[str(x) for x in list_id_list],
        tag_ids=[str(x) for x in tag_id_list],
        user_id=user.id, user_email=user.email,
        ip=security.client_ip(request),
    )
    db.commit()

    # Encolar tras confirmar en BD (si Redis falla, el worker reencola al
    # arrancar gracias a recover_pending_jobs)
    try:
        import_service.enqueue(job.id)
    except Exception:
        pass  # recuperación al arrancar el worker; el trabajo queda "pendiente"

    audit(db, "importacion_solicitada", user_id=user.id, user_email=user.email,
          entity_type="import_job", entity_id=job.id,
          detail={"archivo": filename, "filas_validas": plan["filas_validas"],
                  "filas_invalidas": plan["filas_invalidas"]},
          ip=security.client_ip(request))
    db.commit()

    return ImportResult(
        job_id=job.id, estado=job.status,
        total_filas=plan["filas_validas"] + plan["filas_invalidas"],
        filas_validas=plan["filas_validas"],
        filas_invalidas=plan["filas_invalidas"],
        duplicados_en_archivo=plan["duplicados_en_archivo"],
        codificacion=plan["encoding"],
    )


@router.get("/import/jobs", response_model=List[ImportJobProgress])
def list_import_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                     limit: int = Query(10, ge=1, le=50)):
    """Últimas importaciones (para consultar progreso desde el panel)."""
    jobs = db.query(ImportJob).order_by(ImportJob.created_at.desc()).limit(limit).all()
    return [import_service.get_progress(j) for j in jobs]


@router.get("/import/{job_id}", response_model=ImportJobProgress)
def import_job_progress(job_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Progreso persistente de una importación (sondeo del frontend)."""
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Importación no encontrada")
    return import_service.get_progress(job)


@router.post("/import/{job_id}/cancel", response_model=ImportJobProgress)
def cancel_import_job(job_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Solicita la cancelación (efectiva al momento o entre lotes)."""
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Importación no encontrada")
    if not import_service.request_cancel(db, job):
        raise HTTPException(status.HTTP_409_CONFLICT, "La importación ya terminó")
    return import_service.get_progress(db.get(ImportJob, job_id))


@router.post("/import/preview")
async def import_csv_preview(archivo: UploadFile = File(...)):
    """Previsualiza el CSV/TXT (encabezados detectados y primeras filas)."""
    raw = await archivo.read()
    try:
        headers, rows, invalid, encoding = await run_in_threadpool(parse_csv_bytes, raw)
    except CSVImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return {
        "encabezados": headers,
        "codificacion": encoding,
        "filas_validas": len(rows),
        "filas_invalidas": invalid,
        "muestra": rows[:5],
    }


@router.get("/export/csv")
def export_contacts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Exportación autorizada de contactos (auditoría registrada)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "first_name", "last_name", "status", "consent_source",
                     "consent_at", "created_at"])
    for c in db.query(Contact).filter(Contact.deleted_at.is_(None)).order_by(Contact.created_at).yield_per(500):
        writer.writerow([c.email, c.first_name or "", c.last_name or "", c.status,
                         c.consent_source or "",
                         c.consent_at.isoformat() if c.consent_at else "",
                         c.created_at.isoformat()])
    audit(db, "contactos_exportados", user_id=user.id, user_email=user.email)
    db.commit()
    from fastapi.responses import StreamingResponse
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contactos_hj_campaigns.csv"},
    )
