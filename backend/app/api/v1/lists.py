"""Listas, etiquetas y segmentos de contactos."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import ContactList, Segment, Tag, User
from app.schemas import ListCreate, ListOut, SegmentCreate, SegmentOut, TagCreate, TagOut

router = APIRouter(tags=["Listas y etiquetas"])


# --------------------------------------------------------------------------- #
# Listas
# --------------------------------------------------------------------------- #


@router.get("/lists", response_model=List[ListOut])
def list_lists(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(ContactList).order_by(ContactList.name).all()


@router.post("/lists", response_model=ListOut, status_code=201)
def create_list(payload: ListCreate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    if db.query(ContactList).filter(ContactList.name == payload.name).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una lista con ese nombre")
    lst = ContactList(name=payload.name, description=payload.description)
    db.add(lst)
    audit(db, "lista_creada", user_id=user.id, user_email=user.email,
          entity_type="list", entity_id=lst.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(lst)
    return lst


@router.patch("/lists/{list_id}", response_model=ListOut)
def update_list(list_id: str, payload: ListCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    lst = db.get(ContactList, list_id)
    if not lst:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    lst.name = payload.name
    lst.description = payload.description
    db.commit()
    db.refresh(lst)
    return lst


@router.delete("/lists/{list_id}", status_code=204)
def delete_list(list_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lst = db.get(ContactList, list_id)
    if not lst:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    db.delete(lst)
    db.commit()


# --------------------------------------------------------------------------- #
# Etiquetas
# --------------------------------------------------------------------------- #


@router.get("/tags", response_model=List[TagOut])
def list_tags(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Tag).order_by(Tag.name).all()


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(payload: TagCreate, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    if db.query(Tag).filter(Tag.name == payload.name).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe esa etiqueta")
    tag = Tag(name=payload.name, color=payload.color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Etiqueta no encontrada")
    db.delete(tag)
    db.commit()


# --------------------------------------------------------------------------- #
# Segmentos
# --------------------------------------------------------------------------- #


@router.get("/segments", response_model=List[SegmentOut])
def list_segments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Segment).order_by(Segment.name).all()


@router.post("/segments", response_model=SegmentOut, status_code=201)
def create_segment(payload: SegmentCreate, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if db.query(Segment).filter(Segment.name == payload.name).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un segmento con ese nombre")
    segment = Segment(
        name=payload.name, description=payload.description,
        rules=[r.model_dump() for r in payload.rules],
    )
    db.add(segment)
    audit(db, "segmento_creado", user_id=user.id, user_email=user.email,
          entity_type="segment", entity_id=segment.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(segment)
    return segment


@router.delete("/segments/{segment_id}", status_code=204)
def delete_segment(segment_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    segment = db.get(Segment, segment_id)
    if not segment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segmento no encontrado")
    db.delete(segment)
    db.commit()
