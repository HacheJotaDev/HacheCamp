"""Webhook de Amazon SNS para eventos de SES.

Seguridad:
- Token secreto en la URL (?token=...) configurado por WEBHOOK_TOKEN.
- Verificación criptográfica de la firma SNS (SignatureVersion 1) con el
  certificado oficial de Amazon (host restringido, anti-SSRF).
- Idempotencia por MessageId de SNS y por uid de evento.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.logging_conf import get_logger
from app.models import SesEvent
from app.services import events as events_service

logger = get_logger("hj.webhook")
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/sns")
async def sns_webhook(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    x_amz_sns_message_type: str = Header(default=None),
):
    if not settings.WEBHOOK_TOKEN or token != settings.WEBHOOK_TOKEN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token de webhook inválido")

    try:
        sns: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cuerpo JSON inválido")

    msg_type = x_amz_sns_message_type or sns.get("Type", "Notification")

    # Verificación criptográfica SIEMPRE (bloqueante por descarga del certificado)
    try:
        await run_in_threadpool(events_service.verify_sns_signature, sns)
    except events_service.SNSSecurityError as exc:
        logger.warning("Notificación SNS rechazada", extra={"motivo": str(exc)[:200]})
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Firma SNS inválida: {exc}")

    # Lista blanca opcional de tópicos
    topic_arn = sns.get("TopicArn", "")
    if settings.SNS_TOPIC_ARNS:
        allowed = [a.strip() for a in settings.SNS_TOPIC_ARNS.split(",") if a.strip()]
        if topic_arn not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "TopicArn no autorizado")

    if msg_type == "SubscriptionConfirmation":
        url = sns.get("SubscribeURL", "")
        ok = await run_in_threadpool(events_service.confirm_subscription, url)
        logger.info("Suscripción SNS confirmada", extra={"resultado": ok})
        return {"detalle": "suscripcion_confirmada"}

    if msg_type == "UnsubscribeConfirmation":
        logger.warning("SNS confirmó desuscripción del tópico")
        return {"detalle": "desuscripcion_registrada"}

    # Notification: Message contiene el evento SES en JSON
    try:
        ses_event = json.loads(sns.get("Message", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message no es JSON válido")

    sns_message_id = sns.get("MessageId", "")

    def _process() -> Dict[str, Any]:
        # Idempotencia por MessageId de SNS
        if sns_message_id:
            existing = db.query(SesEvent).filter(
                SesEvent.event_uid == f"sns:{sns_message_id}").first()
            if existing:
                return {"detalle": "duplicado_ignorado"}
        summary = events_service.process_ses_notification(db, ses_event)
        if sns_message_id:
            db.add(SesEvent(event_uid=f"sns:{sns_message_id}", type="sns_ack",
                            payload={"tipo_evento": summary.get("tipo")},
                            occurred_at=utcnow()))
        db.commit()
        return {"detalle": "procesado", **summary}

    return await run_in_threadpool(_process)
