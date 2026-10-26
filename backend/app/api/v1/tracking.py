"""Seguimiento público de aperturas y clics (tokens firmados HMAC)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.db import get_db
from app.core.logging_conf import get_logger
from app.core.tokens import parse_click_token, parse_open_token
from app.services import events as events_service

logger = get_logger("hj.tracking")
router = APIRouter(prefix="/t", tags=["Seguimiento"])

GIF_1X1 = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000"
    "010001000002024401003b"
)


@router.get("/open/{token}.gif")
async def track_open(token: str, db: Session = Depends(get_db)):
    """Píxel de apertura. Token firmado: {campaña, mensaje}."""
    parsed = parse_open_token(token)
    if not parsed:
        return Response(content=GIF_1X1, media_type="image/gif")
    campaign_id, message_id = parsed

    def _record():
        created = events_service.record_open(db, campaign_id, message_id)
        db.commit()
        return created

    try:
        await run_in_threadpool(_record)
    except Exception:
        logger.warning("Fallo al registrar apertura", extra={"mensaje": message_id[:8]})
    return Response(
        content=GIF_1X1,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, private"},
    )


@router.get("/click/{token}")
async def track_click(token: str, request: Request, db: Session = Depends(get_db)):
    """Redirección de clic. URL firmada -> sin redirecciones abiertas."""
    parsed = parse_click_token(token)
    if not parsed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enlace inválido o expirado")
    campaign_id, message_id, url = parsed

    def _record():
        events_service.record_click(db, campaign_id, message_id, url)
        db.commit()

    try:
        await run_in_threadpool(_record)
    except Exception:
        logger.warning("Fallo al registrar clic", extra={"mensaje": message_id[:8]})
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=url, status_code=302)
