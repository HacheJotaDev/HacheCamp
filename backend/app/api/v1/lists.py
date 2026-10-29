"""Listas, etiquetas y segmentos de contactos.

V5: las listas usan BORRADO LÓGICO (papelera + restauración), el listado
incluye el Nº DE CONTACTOS de cada lista (una sola consulta GROUP BY, sin
N+1) y hay búsqueda. Vaciar una lista elimina únicamente las asociaciones:
la base global de contactos queda intacta.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import ContactList, Segment, Tag, User
from app.schemas import ListCreate, ListOut, SegmentCreate, SegmentOut, TagCreate, TagOut
from app.services import lists as list_service

router = APIRouter(tags=["Listas y etiquetas"])


# --------------------------------------------------------------------------- #
# Listas
# --------------------------------------------------------------------------- #


def _listar(db: Session, *, papelera: bool, q: Optional[str]):
    query = db.query(ContactList)
    if papelera:
        query = query.filter(ContactList.deleted_at.isnot(None))
    else:
        query = query.filter(ContactList.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            ContactList.name.ilike(like) | ContactList.description.ilike(like))
    return query.order_by(ContactList.name).all()


def _con_conteos(db: Session, listas) -> List[ListOut]:
    conteos = list_service.counts_por_lista(db)
    salida = []
    for lst in listas:
        out = ListOut.model_validate(lst)
        out.contactos = conteos.get(lst.id, 0)
        salida.append(out)
    return salida


@router.get("/lists/papelera", response_model=List[ListOut])
def lists_papelera(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Listas eliminadas (papelera). Ruta estática: antes de /lists/{list_id}."""
    return _con_conteos(db, _listar(db, papelera=True, q=None))


@router.get("/lists", response_model=List[ListOut])
def list_lists(db: Session = Depends(get_db), user: User = Depends(get_current_user),
               q: Optional[str] = None):
    """Listas activas con su número de contactos; búsqueda opcional por nombre
    o descripción."""
    return _con_conteos(db, _listar(db, papelera=False, q=q))


@router.post("/lists", response_model=ListOut, status_code=201)
def create_list(payload: ListCreate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    existe = (
        db.query(ContactList)
        .filter(ContactList.name == payload.name, ContactList.deleted_at.is_(None))
        .first()
    )
    if existe:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una lista con ese nombre")
    en_papelera = (
        db.query(ContactList)
        .filter(ContactList.name == payload.name, ContactList.deleted_at.isnot(None))
        .first()
    )
    if en_papelera:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Existe una lista eliminada con ese nombre: restáurala o renómbrala primero",
        )
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
    if not lst or lst.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    # Igual que en la creación: el nombre queda reservado también por las
    # listas de la PAPELERA (UNIQUE en BD); renombrar a un nombre en papelera
    # bloquearía después la restauración de esa otra lista.
    colision = list_service.nombre_en_uso(db, payload.name, exclude_id=lst.id)
    if colision:
        estado = "está en la papelera" if colision.deleted_at is not None else "ya existe"
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Ya {estado} una lista con ese nombre")
    lst.name = payload.name
    lst.description = payload.description
    db.commit()
    db.refresh(lst)
    out = ListOut.model_validate(lst)
    out.contactos = list_service.counts_por_lista(db).get(lst.id, 0)
    return out


@router.post("/lists/{list_id}/empty", response_model=dict)
def empty_list(list_id: str, request: Request, db: Session = Depends(get_db),
               user: User = Depends(get_current_user),
               confirmar: bool = Query(False)):
    """VACIAR lista: elimina solo las asociaciones contacto-lista.

    Los contactos globales se conservan (y sus otras listas). Protegido:
    rechaza si campañas sin materializar usan la lista. Exige confirmación.
    """
    lst = db.get(ContactList, list_id)
    if not lst or lst.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    if not confirmar:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Falta confirmación (?confirmar=true) para vaciar la lista")
    try:
        resultado = list_service.empty_list(db, lst, user_id=user.id, user_email=user.email)
    except list_service.ListError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    audit(db, "lista_vaciada", user_id=user.id, user_email=user.email,
          entity_type="list", entity_id=lst.id, detail=resultado,
          ip=security.client_ip(request))
    db.commit()
    return resultado


@router.delete("/lists/{list_id}", response_model=dict)
def delete_list(list_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_admin),
                confirmar: bool = Query(False)):
    """Lleva la lista a la PAPELERA (borrado lógico, requiere admin).

    NO borra contactos ni asociaciones: la restauración recupera la lista tal
    cual. Protegido contra campañas sin materializar que la usen.
    """
    lst = db.get(ContactList, list_id)
    if not lst:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    if not confirmar:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Falta confirmación (?confirmar=true) para eliminar la lista")
    try:
        list_service.soft_delete_list(db, lst, user_id=user.id, user_email=user.email)
    except list_service.ListError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    audit(db, "lista_eliminada", user_id=user.id, user_email=user.email,
          entity_type="list", entity_id=lst.id, ip=security.client_ip(request))
    db.commit()
    return {"papelera": True, "id": lst.id}


@router.post("/lists/{list_id}/restore", response_model=dict)
def restore_list(list_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin), payload: Optional[dict] = None):
    """Restaura una lista desde la papelera (requiere admin).

    Si otra lista tomó su nombre mientras estaba eliminada (el nombre es
    UNIQUE), la restauración exige un nombre nuevo: cuerpo opcional
    {"nuevo_nombre": "..."}; sin él se responde 409 con el mensaje accionable.
    """
    lst = db.get(ContactList, list_id)
    if not lst:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    nuevo = None
    if isinstance(payload, dict):
        candidato = payload.get("nuevo_nombre")
        if candidato is not None:
            nuevo = str(candidato)
    try:
        nombre_final = list_service.restore_list(db, lst, nuevo_nombre=nuevo)
        db.commit()
    except list_service.ListError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except IntegrityError:
        # Carrera: otra petición restauró/renombró con el mismo nombre entre
        # nuestra comprobación y el commit (UNIQUE de BD). Nunca un 500.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Otra operación acaba de usar ese nombre: reintenta con uno distinto",
        )
    audit(db, "lista_restaurada", user_id=user.id, user_email=user.email,
          entity_type="list", entity_id=lst.id,
          detail={"nombre_final": nombre_final}, ip=security.client_ip(request))
    db.commit()
    return {"id": lst.id, "restaurada": True, "nombre": nombre_final}


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
