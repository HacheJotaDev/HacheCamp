"""Agregaciones estadísticas reales (sin métricas inventadas)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Campaign, CampaignMessage, SesEvent, Suppression
from app.services import contacts as contact_service


def dashboard_summary(db: Session) -> Dict[str, Any]:
    """Resumen para el panel principal. Solo datos reales."""
    total_campaigns = db.query(func.count(Campaign.id)).scalar() or 0
    active_campaigns = (
        db.query(func.count(Campaign.id))
        .filter(Campaign.status.in_(["en_cola", "enviando", "programada", "pausada"]))
        .scalar()
    ) or 0

    message_counts: Dict[str, int] = {}
    for status, total in (
        db.query(CampaignMessage.status, func.count(CampaignMessage.id))
        .group_by(CampaignMessage.status).all()
    ):
        message_counts[status] = total
    accepted = sum(message_counts.get(s, 0) for s in ("aceptado", "entregado", "rebote_permanente",
                                                      "rebote_temporal", "queja", "incierto"))
    delivered = message_counts.get("entregado", 0)
    bounces = message_counts.get("rebote_permanente", 0)
    soft_bounces = message_counts.get("rebote_temporal", 0)

    event_counts: Dict[str, int] = {}
    for etype, total in (
        db.query(SesEvent.type, func.count(SesEvent.id)).group_by(SesEvent.type).all()
    ):
        event_counts[etype] = total

    return {
        "campanas_total": total_campaigns,
        "campanas_activas": active_campaigns,
        "aceptados_ses": accepted,
        "entregas": delivered,
        "rebotes": bounces,
        "rebotes_temporales": soft_bounces,
        "quejas": message_counts.get("queja", 0) or event_counts.get("complaint", 0),
        "bajas": event_counts.get("unsubscribe", 0),
        "aperturas": event_counts.get("open", 0),
        "clics": event_counts.get("click", 0),
        "errores_envio": message_counts.get("error", 0),
        "contactos": contact_service.count_contacts(db),
        "suprimidos": db.query(func.count(Suppression.id)).scalar() or 0,
    }


def daily_series(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Evolución diaria de eventos (entregas, rebotes, quejas, bajas, aperturas, clics)."""
    since = utcnow() - timedelta(days=days)
    rows = (
        db.query(SesEvent.type, func.date(SesEvent.occurred_at), func.count(SesEvent.id))
        .filter(SesEvent.occurred_at >= since, SesEvent.type.in_(
            ["delivery", "bounce", "complaint", "unsubscribe", "open", "click"]
        ))
        .group_by(SesEvent.type, func.date(SesEvent.occurred_at))
        .all()
    )
    by_date: Dict[str, Dict[str, int]] = {}
    for etype, date, total in rows:
        key = str(date)
        by_date.setdefault(key, {})[etype] = total

    # Envíos por día (aceptados por SES) desde campaign_messages
    send_rows = (
        db.query(func.date(CampaignMessage.sent_at), func.count(CampaignMessage.id))
        .filter(CampaignMessage.sent_at >= since, CampaignMessage.status.notin_(["pendiente"]))
        .group_by(func.date(CampaignMessage.sent_at))
        .all()
    )
    for date, total in send_rows:
        if date:
            by_date.setdefault(str(date), {})["envios"] = total

    series: List[Dict[str, Any]] = []
    day = (utcnow() - timedelta(days=days - 1)).date()
    for _ in range(days):
        key = day.isoformat()
        entry = {"fecha": key}
        entry.update({k: by_date.get(key, {}).get(k, 0) for k in
                      ["envios", "delivery", "bounce", "complaint", "unsubscribe", "open", "click"]})
        for k in ["envios", "delivery", "bounce", "complaint", "unsubscribe", "open", "click"]:
            entry.setdefault(k, 0)
        series.append(entry)
        day += timedelta(days=1)
    return series


def campaign_events(db: Session, campaign_id: str, limit: int = 200) -> List[SesEvent]:
    return (
        db.query(SesEvent)
        .filter(SesEvent.campaign_id == campaign_id)
        .order_by(SesEvent.occurred_at.desc())
        .limit(limit)
        .all()
    )
