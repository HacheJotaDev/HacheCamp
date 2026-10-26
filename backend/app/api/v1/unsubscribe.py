"""Bajas públicas (sin inicio de sesión).

- GET  /unsubscribe/one-click  -> información de la baja (para la página /baja)
- POST /unsubscribe/one-click  -> RFC 8058 List-Unsubscribe=One-Click
- POST /unsubscribe/confirm    -> confirmación desde la página pública

Ambas vías: supresión global + estado del contacto + registro del motivo.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.db import get_db, utcnow
from app.core.logging_conf import get_logger
from app.core.tokens import parse_unsub_token
from app.models import Campaign, Contact
from app.services import contacts as contact_service

logger = get_logger("hj.bajas")
router = APIRouter(prefix="/public", tags=["Bajas"])


def _process_unsubscribe(db: Session, token: str, *, campaign_id_hint=None) -> Dict[str, Any]:
    parsed = parse_unsub_token(token)
    if not parsed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enlace de baja inválido o caducado")
    contact_id, campaign_id = parsed[0], (campaign_id_hint or parsed[1])
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suscripción no encontrada")

    already = contact.status == "dado_baja" and contact.unsubscribed_at is not None
    if not already:
        contact.status = "dado_baja"
        contact.unsubscribed_at = utcnow()
        contact.unsubscribe_campaign_id = campaign_id
        contact_service.suppress_email(
            db, contact.email, reason="baja", source="baja", campaign_id=campaign_id
        )
        contact_service.add_consent_event(
            db, contact, "baja", source="enlace_baja",
            detail={"campana": campaign_id or "global"},
        )
        db.commit()
        logger.info("Baja procesada", extra={"contacto": contact.id[:8]})

    campaign_name = None
    if campaign_id:
        campaign = db.get(Campaign, campaign_id)
        campaign_name = campaign.name if campaign else None
    return {"email_enmascarado": contact_service.mask_email(contact.email),
            "campana": campaign_name, "ya_estaba_dada_de_baja": already}


@router.get("/unsubscribe/info")
async def unsubscribe_info(token: str, db: Session = Depends(get_db)):
    """Información para la página pública de baja (sin datos sensibles)."""
    return await run_in_threadpool(_process_info, db, token)


def _process_info(db: Session, token: str) -> Dict[str, Any]:
    parsed = parse_unsub_token(token)
    if not parsed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enlace de baja inválido o caducado")
    contact_id, campaign_id = parsed
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suscripción no encontrada")
    campaign_name = None
    if campaign_id:
        campaign = db.get(Campaign, campaign_id)
        campaign_name = campaign.name if campaign else None
    return {
        "email_enmascarado": contact_service.mask_email(contact.email),
        "campana": campaign_name,
        "estado": contact.status,
    }


@router.post("/unsubscribe/one-click")
async def unsubscribe_one_click(token: str, db: Session = Depends(get_db)):
    """Baja en un clic RFC 8058: los clientes de correo hacen POST automático."""
    return await run_in_threadpool(_process_unsubscribe, db, token)


class UnsubscribeConfirm(BaseModel):
    token: str


@router.post("/unsubscribe/confirm")
async def unsubscribe_confirm(payload: UnsubscribeConfirm, db: Session = Depends(get_db)):
    """Confirmación explícita desde la página pública de baja."""
    return await run_in_threadpool(_process_unsubscribe, db, payload.token)
