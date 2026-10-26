"""Contactos: alta, edición, búsqueda, filtros, importación/exportación CSV,
historial de consentimiento y bajas."""
from __future__ import annotations

import csv
import io
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core import security
from app.core.audit import audit
from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.deps import get_current_user
from app.models import Contact, ContactList, Tag, User, contact_lists_rel, contact_tags_rel
from app.schemas import (
    ConsentEventOut,
    ContactCreate,
    ContactDetailOut,
    ContactOut,
    ContactUpdate,
    ImportResult,
)
from app.services import contacts as contact_service
from app.services.csv_import import CSVImportError, import_contacts_from_csv, parse_csv_bytes

router = APIRouter(prefix="/contacts", tags=["Contactos"])


@router.get("", response_model=dict)
def list_contacts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    q: Optional[str] = None,
    estado: Optional[str] = None,
    list_id: Optional[str] = None,
    tag_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
):
    query = db.query(Contact)
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


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    # Baja + supresión: el correo nunca volverá a recibir campañas
    contact_service.suppress_email(db, contact.email, reason="baja", source="manual")
    contact.status = "dado_baja"
    contact.unsubscribed_at = utcnow()
    contact_service.add_consent_event(db, contact, "baja", source="manual",
                                      detail={"por": user.email})
    audit(db, "contacto_baja", user_id=user.id, user_email=user.email,
          entity_type="contact", entity_id=contact.id, ip=security.client_ip(request))
    db.commit()


@router.get("/{contact_id}/consent", response_model=List[ConsentEventOut])
def contact_consent_history(contact_id: str, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado")
    return contact.consent_history


@router.post("/import", response_model=ImportResult)
async def import_csv(
    request: Request,
    archivo: UploadFile = File(...),
    consent_source: str = Form(..., min_length=2, max_length=60),
    consent_text: str = Form("", max_length=2000),
    list_ids: str = Form("[]"),
    tag_ids: str = Form("[]"),
    dry_run: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Importación CSV con validación, duplicados y registro de consentimiento."""
    try:
        list_id_list = json.loads(list_ids)
        tag_id_list = json.loads(tag_ids)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Formato de listas/etiquetas inválido")

    raw = await archivo.read()
    try:
        result = import_contacts_from_csv(
            db, raw,
            consent_source=consent_source,
            consent_text=consent_text or None,
            list_ids=list_id_list, tag_ids=tag_id_list,
            dry_run=dry_run,
        )
    except CSVImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if not dry_run:
        audit(db, "contactos_importados", user_id=user.id, user_email=user.email,
              entity_type="contact", detail={"creados": result["creados"],
                                             "actualizados": result["actualizados"],
                                             "fuente": consent_source},
              ip=security.client_ip(request))
        db.commit()
    return ImportResult(**result)


@router.post("/import/preview")
async def import_csv_preview(archivo: UploadFile = File(...)):
    """Previsualiza el CSV (encabezados detectados y primeras filas)."""
    raw = await archivo.read()
    try:
        headers, rows, invalid, encoding = parse_csv_bytes(raw)
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
    for c in db.query(Contact).order_by(Contact.created_at).yield_per(500):
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
