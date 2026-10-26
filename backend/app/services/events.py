"""Procesamiento de eventos de SES (SNS) y de seguimiento.

- Verificación criptográfica de notificaciones SNS (SignatureVersion 1).
- Idempotencia por event_uid (MessageId de SNS).
- Rebotes permanentes y quejas -> supresión global automática.
- Entregas, rechazos y otros eventos registrados por campaña y mensaje.
"""
from __future__ import annotations

import base64
import json
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.logging_conf import get_logger
from app.models import Campaign, CampaignMessage, Contact, SesEvent
from app.services import contacts as contact_service

logger = get_logger("hj.events")

AMAZON_SUFFIXES = (".amazonaws.com", ".amazon.com")

# Caché de certificados de firma SNS (evita descargar el certificado en cada
# notificación; necesario para volúmenes altos vía SQS).
_CERT_CACHE: Dict[str, Tuple[bytes, float]] = {}
_CERT_TTL_SEGUNDOS = 3600.0


class SNSSecurityError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Verificación de firmas SNS
# --------------------------------------------------------------------------- #


def _safe_fetch(url: str, max_bytes: int = 65536) -> bytes:
    """Descarga solo desde hosts Amazon (protección SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SNSSecurityError("La URL del certificado debe ser HTTPS")
    host = (parsed.hostname or "").lower()
    if not any(host.endswith(sfx) for sfx in AMAZON_SUFFIXES):
        raise SNSSecurityError("La URL del certificado no pertenece a Amazon")
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec: hosts restringidos
        return resp.read(max_bytes)


def _certificado_firmante(url: str, max_age: float = _CERT_TTL_SEGUNDOS) -> bytes:
    """Descarga (con caché temporal) el certificado de firma de Amazon."""
    import time
    cached = _CERT_CACHE.get(url)
    if cached:
        der, expira = cached
        if time.time() < expira:
            return der
    der = _safe_fetch(url)
    _CERT_CACHE[url] = (der, time.time() + max_age)
    return der


def _canonical_string(sns: Dict[str, Any], keys: list) -> str:
    parts = []
    for key in keys:
        value = sns.get(key, "")
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(str(value))
    return "\n".join(parts) + "\n"


def verify_sns_signature(sns: Dict[str, Any]) -> None:
    """Verifica la firma de una notificación SNS (SignatureVersion 1, RSA-SHA1)."""
    if sns.get("SignatureVersion") != "1":
        raise SNSSecurityError("Solo se acepta SignatureVersion 1")
    cert_url = sns.get("SigningCertURL", "")
    if not cert_url:
        raise SNSSecurityError("Falta SigningCertURL")

    msg_type = sns.get("Type", "Notification")
    if msg_type == "Notification":
        keys = ["Message", "MessageId", "Timestamp", "TopicArn", "Type"]
    elif msg_type == "SubscriptionConfirmation":
        keys = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]
    elif msg_type == "UnsubscribeConfirmation":
        keys = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]
    else:
        raise SNSSecurityError(f"Tipo SNS desconocido: {msg_type}")

    canonical = _canonical_string(sns, keys)
    signature = sns.get("Signature", "")
    if not signature:
        raise SNSSecurityError("Falta la firma")

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        cert_der = _certificado_firmante(cert_url)
        from cryptography import x509
        cert = x509.load_der_x509_certificate(cert_der)
        public_key = cert.public_key()
        public_key.verify(
            base64.b64decode(signature),
            canonical.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
    except SNSSecurityError:
        raise
    except Exception as exc:
        raise SNSSecurityError(f"Firma SNS inválida: {type(exc).__name__}")


def confirm_subscription(subscribe_url: str) -> bool:
    """Confirma la suscripción SNS visitando la URL (validada)."""
    try:
        _safe_fetch(subscribe_url)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Procesamiento de eventos
# --------------------------------------------------------------------------- #


def record_event(
    db: Session,
    *,
    type: str,
    event_uid: str,
    campaign_id: Optional[str] = None,
    message_id: Optional[str] = None,
    ses_message_id: Optional[str] = None,
    email: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> Tuple[SesEvent, bool]:
    """Registra un evento de forma idempotente. Devuelve (evento, nuevo)."""
    existing = db.query(SesEvent).filter(SesEvent.event_uid == event_uid).first()
    if existing:
        return existing, False
    event = SesEvent(
        event_uid=event_uid[:160],
        type=type,
        campaign_id=campaign_id,
        message_id=message_id,
        ses_message_id=ses_message_id,
        email=email,
        payload=payload,
        url=url,
        occurred_at=occurred_at or utcnow(),
    )
    db.add(event)
    return event, True


def _find_by_ses_message_id(db: Session, ses_message_id: Optional[str]) -> Optional[CampaignMessage]:
    if not ses_message_id:
        return None
    return db.query(CampaignMessage).filter(CampaignMessage.ses_message_id == ses_message_id).first()


def process_ses_notification(db: Session, ses_event: Dict[str, Any]) -> Dict[str, Any]:
    """Procesa el contenido de un evento SES dentro de una notificación SNS.

    Devuelve un resumen del procesamiento. Nunca lanza por datos inválidos.
    """
    event_type = (ses_event.get("eventType") or ses_event.get("notificationType") or "").lower()
    mail = ses_event.get("mail", {})
    ses_message_id = mail.get("messageId")
    destination = (mail.get("destination") or [None])[0] or ""
    tags = mail.get("tags", {}) or {}
    campaign_ids = tags.get("campaign_id") or []
    campaign_id = campaign_ids[0] if campaign_ids else None

    summary: Dict[str, Any] = {"tipo": event_type, "procesado": False}

    if event_type == "delivery":
        cm = _find_by_ses_message_id(db, ses_message_id)
        if cm:
            if cm.status in ("aceptado", "enviando"):
                cm.status = "entregado"
            record_event(db, type="delivery", event_uid=f"ses:{ses_message_id}:delivery",
                         campaign_id=cm.campaign_id, message_id=cm.id,
                         ses_message_id=ses_message_id, email=cm.email,
                         payload={"dsn": ses_event.get("delivery", {}).get("processingTimeMillis")})
            summary["procesado"] = True
            summary["mensaje"] = cm.id
        else:
            record_event(db, type="delivery", event_uid=f"ses:{ses_message_id}:delivery",
                         campaign_id=campaign_id, ses_message_id=ses_message_id, email=destination)
            summary["procesado"] = True

    elif event_type == "bounce":
        bounce = ses_event.get("bounce", {})
        bounce_type = bounce.get("bounceType", "")
        bounce_subtype = bounce.get("bounceSubType", "")
        bounced_uid = f"ses:{ses_message_id}:bounce:{bounce.get('bounceType')}:{bounce.get('timestamp', '')}"
        cm = _find_by_ses_message_id(db, ses_message_id)
        if cm:
            if bounce_type == "Permanent":
                cm.status = "rebote_permanente"
                contact_service.suppress_email(
                    db, cm.email, reason="rebote_permanente", source="sns",
                    detail=f"{bounce_subtype}: {json.dumps(bounce.get('bouncedRecipients', []))[:400]}",
                    campaign_id=cm.campaign_id,
                )
                contact = db.query(Contact).filter(Contact.email == cm.email).first()
                if contact and contact.status == "suscripto":
                    contact.status = "rebote"
                    contact_service.add_consent_event(db, contact, "rebote", source="sns",
                                                      detail={"tipo": bounce_subtype})
            else:
                cm.status = "rebote_temporal"
            cm.last_error = f"rebote {bounce_type}/{bounce_subtype}"
            record_event(db, type="bounce", event_uid=bounced_uid,
                         campaign_id=cm.campaign_id, message_id=cm.id,
                         ses_message_id=ses_message_id, email=cm.email,
                         payload={"tipo": bounce_type, "subtipo": bounce_subtype})
            summary["procesado"] = True
        else:
            record_event(db, type="bounce", event_uid=bounced_uid, campaign_id=campaign_id,
                         ses_message_id=ses_message_id, email=destination,
                         payload={"tipo": bounce_type, "subtipo": bounce_subtype})
            summary["procesado"] = True

    elif event_type == "complaint":
        complaint = ses_event.get("complaint", {})
        uid = f"ses:{ses_message_id}:complaint:{complaint.get('timestamp', '')}"
        cm = _find_by_ses_message_id(db, ses_message_id)
        email = (cm.email if cm else destination) or ""
        if cm:
            cm.status = "queja"
        if email:
            contact_service.suppress_email(db, email, reason="queja", source="sns",
                                           detail=complaint.get("complaintFeedbackType", ""),
                                           campaign_id=cm.campaign_id if cm else None)
            contact = db.query(Contact).filter(Contact.email == email).first()
            if contact and contact.status == "suscripto":
                contact.status = "queja"
                contact_service.add_consent_event(db, contact, "queja", source="sns")
        record_event(db, type="complaint", event_uid=uid,
                     campaign_id=cm.campaign_id if cm else campaign_id,
                     message_id=cm.id if cm else None,
                     ses_message_id=ses_message_id, email=email,
                     payload={"feedback": complaint.get("complaintFeedbackType", "")})
        summary["procesado"] = True

    elif event_type == "reject":
        record_event(db, type="reject", event_uid=f"ses:{ses_message_id}:reject",
                     campaign_id=campaign_id, ses_message_id=ses_message_id,
                     email=destination, payload={"motivo": ses_event.get("reject", {}).get("reason", "")})
        cm = _find_by_ses_message_id(db, ses_message_id)
        if cm:
            cm.status = "error"
            cm.last_error = "SES rechazó el mensaje"
        summary["procesado"] = True

    elif event_type == "send":
        record_event(db, type="send", event_uid=f"ses:{ses_message_id}:send",
                     campaign_id=campaign_id, ses_message_id=ses_message_id, email=destination)
        summary["procesado"] = True

    else:
        record_event(db, type=event_type or "desconocido",
                     event_uid=f"ses:{ses_message_id}:{event_type}",
                     campaign_id=campaign_id, ses_message_id=ses_message_id, email=destination)
        summary["procesado"] = True

    return summary


def record_open(db: Session, campaign_id: str, message_id: str) -> bool:
    """Registra apertura. Idempotente por mensaje/día (aproximación razonable)."""
    cm = db.get(CampaignMessage, message_id)
    if not cm or cm.campaign_id != campaign_id:
        return False
    day = utcnow().strftime("%Y%m%d")
    uid = f"open:{message_id}:{day}"
    _, created = record_event(db, type="open", event_uid=uid, campaign_id=campaign_id,
                              message_id=message_id, email=cm.email)
    return created


def record_click(db: Session, campaign_id: str, message_id: str, url: str) -> bool:
    """Registra clic. Cada clic se registra (comportamiento estándar)."""
    cm = db.get(CampaignMessage, message_id)
    if not cm or cm.campaign_id != campaign_id:
        return False
    import hashlib
    digest = hashlib.sha256(f"{url}".encode()).hexdigest()[:12]
    uid = f"click:{message_id}:{digest}"
    record_event(db, type="click", event_uid=uid, campaign_id=campaign_id,
                 message_id=message_id, email=cm.email, url=url[:2000])
    return True
