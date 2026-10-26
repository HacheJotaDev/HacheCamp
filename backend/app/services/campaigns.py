"""Lógica de campañas: máquina de estados, selección de destinatarios,
encolado idempotente, pausa/reanudación/cancelación.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.logging_conf import get_logger
from app.models import (
    CAMPAIGN_TRANSITIONS,
    Campaign,
    CampaignHistory,
    CampaignMessage,
    Contact,
    Segment,
    contact_lists_rel,
)
from app.services import queue as send_queue
from app.services.contacts import can_send_promotional, normalize_email
from app.services.renderer import html_to_plain_text, wrap_plain_with_unsubscribe

logger = get_logger("hj.campaigns")


class CampaignError(ValueError):
    pass


def can_transition(current: str, target: str) -> bool:
    return target in CAMPAIGN_TRANSITIONS.get(current, [])


def transition(campaign: Campaign, target: str, *, user_id: Optional[str] = None,
               user_email: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
    if not can_transition(campaign.status, target):
        raise CampaignError(f"Transición de estado inválida: {campaign.status} → {target}")
    previous = campaign.status
    campaign.status = target
    campaign.history.append(CampaignHistory(
        campaign_id=campaign.id,
        user_id=user_id,
        user_email=user_email,
        action=f"estado:{previous}->{target}",
        detail=detail or {},
    ))


def add_history(campaign: Campaign, action: str, *, user_id: Optional[str] = None,
                user_email: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
    campaign.history.append(CampaignHistory(
        campaign_id=campaign.id, user_id=user_id, user_email=user_email,
        action=action, detail=detail or {},
    ))


def select_recipients(db: Session, campaign: Campaign) -> List[Contact]:
    """Selecciona destinatarios según lista o segmento, con consentimiento válido
    para campañas promocionales y sin supresión."""
    query = db.query(Contact).filter(Contact.status == "suscripto")

    if campaign.list_id:
        query = query.join(contact_lists_rel, contact_lists_rel.c.contact_id == Contact.id).filter(
            contact_lists_rel.c.list_id == campaign.list_id
        )
    elif campaign.segment_id:
        segment = db.get(Segment, campaign.segment_id)
        if segment and segment.rules:
            for rule in segment.rules:
                field = (rule.get("campo") or "").strip()
                op = (rule.get("op") or "contiene").strip()
                value = str(rule.get("valor") or "")
                if field == "email":
                    col = Contact.email
                elif field == "first_name":
                    col = Contact.first_name
                elif field == "last_name":
                    col = Contact.last_name
                elif field.startswith("atributo:"):
                    key = field.split(":", 1)[1]
                    # Filtrado por atributo JSON (aproximación por coincidencia de texto)
                    query = query.filter(Contact.attributes.cast(func.text()).like(f'%"{key}"%'))
                    continue
                else:
                    continue
                if op == "igual":
                    query = query.filter(col == value)
                elif op == "empieza":
                    query = query.filter(col.like(f"{value}%"))
                else:  # contiene
                    query = query.filter(col.like(f"%{value}%"))

    recipients = query.all()
    # Filtro de supresión y consentimiento para campañas promocionales
    if campaign.category == "promocional":
        recipients = [c for c in recipients if can_send_promotional(db, c)[0]]
    else:
        recipients = [c for c in recipients if c.status == "suscripto"]
    return recipients


def enqueue_campaign(db: Session, campaign: Campaign) -> Dict[str, Any]:
    """Crea los mensajes por destinatario (idempotente) y los encola.

    No inicia envíos automáticamente al desplegar: solo se invoca desde
    acciones explícitas (enviar ahora / programación vencida aprobada).
    """
    if campaign.status not in ("borrador", "en_revision", "programada", "pausada", "en_cola"):
        raise CampaignError(f"No se puede encolar una campaña en estado {campaign.status}")
    if not campaign.approved_at:
        raise CampaignError("La campaña requiere aprobación administrativa antes del envío")

    recipients = select_recipients(db, campaign)
    if not recipients:
        raise CampaignError("La campaña no tiene destinatarios con consentimiento válido")

    # Prepara texto plano congelado (sin variables; se renderiza por destinatario)
    if campaign.mode == "html" and campaign.html and not campaign.plain_text:
        campaign.plain_text = wrap_plain_with_unsubscribe(html_to_plain_text(campaign.html))

    message_ids: List[str] = []
    existing = 0
    for contact in recipients:
        email = normalize_email(contact.email)
        exists = db.query(CampaignMessage.id).filter_by(
            campaign_id=campaign.id, contact_id=contact.id
        ).first()
        if exists:
            existing += 1
            continue
        msg = CampaignMessage(
            campaign_id=campaign.id, contact_id=contact.id, email=email,
            status="pendiente",
        )
        db.add(msg)
        db.flush()
        message_ids.append(msg.id)

    if campaign.status in ("borrador", "en_revision", "programada", "pausada"):
        transition(campaign, "en_cola")

    if message_ids:
        send_queue.enqueue_messages(message_ids)

    add_history(campaign, "encolado", detail={
        "destinatarios": len(recipients), "nuevos": len(message_ids), "existentes": existing,
    })
    return {"destinatarios": len(recipients), "nuevos": len(message_ids), "existentes": existing}


def campaign_progress(db: Session, campaign: Campaign) -> Dict[str, Any]:
    """Conteos por estado de mensaje + totales."""
    rows = (
        db.query(CampaignMessage.status, func.count(CampaignMessage.id))
        .filter(CampaignMessage.campaign_id == campaign.id)
        .group_by(CampaignMessage.status)
        .all()
    )
    counts: Dict[str, int] = {status: 0 for status in [
        "pendiente", "enviando", "aceptado", "entregado", "rebote_permanente",
        "rebote_temporal", "queja", "error", "cancelado", "incierto",
    ]}
    for status, total in rows:
        if status in counts:
            counts[status] = total
    total = sum(counts.values())
    processed = total - counts["pendiente"] - counts["enviando"]
    return {
        "total": total,
        "procesados": processed,
        "por_estado": counts,
        "pendientes": counts["pendiente"],
        "errores": counts["error"],
    }


def check_campaign_completion(db: Session, campaign: Campaign) -> None:
    """Marca completada/fallida cuando no quedan mensajes activos."""
    if campaign.status not in ("enviando", "en_cola"):
        return
    pending = (
        db.query(func.count(CampaignMessage.id))
        .filter(
            CampaignMessage.campaign_id == campaign.id,
            CampaignMessage.status.in_(["pendiente", "enviando"]),
        )
        .scalar()
    )
    if pending and pending > 0:
        return
    remaining_retries = send_queue.pending_retries_count()
    errored = (
        db.query(func.count(CampaignMessage.id))
        .filter(CampaignMessage.campaign_id == campaign.id, CampaignMessage.status == "error")
        .scalar()
    )
    total = (
        db.query(func.count(CampaignMessage.id))
        .filter(CampaignMessage.campaign_id == campaign.id)
        .scalar()
    )
    campaign.completed_at = utcnow()
    if total and errored == total:
        transition(campaign, "fallida", detail={"motivo": "todos_los_envios_fallaron"})
    else:
        transition(campaign, "completada", detail={"reintentos_pendientes": remaining_retries})


def pause_campaign(db: Session, campaign: Campaign, **user) -> None:
    transition(campaign, "pausada", **user)


def resume_campaign(db: Session, campaign: Campaign, **user) -> None:
    if campaign.status != "pausada":
        raise CampaignError("Solo se pueden reanudar campañas pausadas")
    # Reencola los pendientes que hubieran quedado sin procesar
    pendientes = (
        db.query(CampaignMessage)
        .filter(CampaignMessage.campaign_id == campaign.id, CampaignMessage.status == "pendiente")
        .all()
    )
    transition(campaign, "enviando", **user)
    send_queue.enqueue_messages([m.id for m in pendientes])


def cancel_campaign(db: Session, campaign: Campaign, **user) -> None:
    target = "cancelada"
    if campaign.status in ("completada", "cancelada"):
        raise CampaignError("La campaña ya finalizó")
    # Marca pendientes como cancelados
    updated = (
        db.query(CampaignMessage)
        .filter(CampaignMessage.campaign_id == campaign.id, CampaignMessage.status == "pendiente")
        .update({"status": "cancelado"}, synchronize_session=False)
    )
    campaign.status = target
    add_history(campaign, "cancelada", detail={"mensajes_cancelados": updated}, **user)
