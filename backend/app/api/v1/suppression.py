"""Lista de supresión global: nunca se envía a estas direcciones."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.deps import get_current_user
from app.models import Contact, Suppression, User
from app.schemas import SuppressionCreate, SuppressionOut
from app.services import contacts as contact_service
from app.core.db import get_db, utcnow

router = APIRouter(prefix="/suppression", tags=["Supresión"])


@router.get("", response_model=dict)
def list_suppression(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                     q: Optional[str] = None, motivo: Optional[str] = None,
                     page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=100)):
    query = db.query(Suppression)
    if q:
        query = query.filter(Suppression.email.ilike(f"%{q}%"))
    if motivo:
        query = query.filter(Suppression.reason == motivo)
    total = query.count()
    items = query.order_by(Suppression.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    counts = dict(db.query(Suppression.reason, func.count(Suppression.id)).group_by(Suppression.reason).all())
    return {
        "total": total, "page": page, "per_page": per_page,
        "por_motivo": counts,
        "items": [SuppressionOut.model_validate(s).model_dump(mode="json") for s in items],
    }


@router.post("", response_model=SuppressionOut, status_code=201)
def add_suppression(payload: SuppressionCreate, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    supp = contact_service.suppress_email(
        db, payload.email, reason=payload.reason, source="manual",
        detail=payload.detail,
    )
    contact = db.query(Contact).filter(Contact.email == payload.email).first()
    if contact and contact.status == "suscripto":
        contact.status = "dado_baja"
        contact.unsubscribed_at = utcnow()
        contact_service.add_consent_event(db, contact, "baja", source="manual",
                                          detail={"por": user.email, "motivo": payload.reason})
    audit(db, "supresion_agregada", user_id=user.id, user_email=user.email,
          entity_type="suppression", entity_id=supp.id, detail={"motivo": payload.reason},
          ip=security.client_ip(request))
    db.commit()
    db.refresh(supp)
    return supp


@router.delete("/{email}", status_code=204)
def remove_suppression(email: str, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    if not contact_service.unsuppress_email(db, email):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dirección no encontrada en supresión")
    audit(db, "supresion_eliminada", user_id=user.id, user_email=user.email,
          detail={"correo": email[:3] + "***"}, ip=security.client_ip(request))
    db.commit()
