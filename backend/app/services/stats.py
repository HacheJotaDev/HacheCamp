"""Agregaciones estadísticas reales (sin métricas inventadas).

Definiciones de las tasas (denominadores explícitos):
- Success Rate  = entregas confirmadas / mensajes con resultado definitivo.
  Definitivo = entregado + rebote permanente + error + queja + incierto.
- Bounce Rate   = rebotes (permanentes + temporales) / mensajes aceptados
  por SES (enviados).
- Open Rate     = destinatarios únicos con al menos una apertura /
  entregas confirmadas. Varias aperturas del mismo destinatario cuentan
  como una sola persona.
- Click Rate    = destinatarios únicos con al menos un clic /
  entregas confirmadas.
- Failed        = mensajes que no pudieron enviarse o fueron rechazados
  definitivamente (estado 'error').
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Campaign, CampaignMessage, SesEvent, Suppression
from app.services import contacts as contact_service


def _conteo_mensajes(db: Session, campaign_id: Optional[str] = None) -> Dict[str, int]:
    query = db.query(CampaignMessage.status, func.count(CampaignMessage.id))
    if campaign_id:
        query = query.filter(CampaignMessage.campaign_id == campaign_id)
    result: Dict[str, int] = {}
    for status, total in query.group_by(CampaignMessage.status).all():
        result[status] = total
    return result


def _unicos_con_evento(db: Session, tipo: str, campaign_id: Optional[str] = None) -> int:
    """Mensajes únicos con al menos un evento del tipo dado (una persona, no
    varios eventos)."""
    query = (
        db.query(func.count(func.distinct(SesEvent.message_id)))
        .filter(SesEvent.type == tipo, SesEvent.message_id.isnot(None))
    )
    if campaign_id:
        query = query.filter(SesEvent.campaign_id == campaign_id)
    return int(query.scalar() or 0)


def metrics_summary(db: Session, campaign_id: Optional[str] = None) -> Dict[str, Any]:
    """Tasas y totales con denominadores explícitos, por campaña o globales."""
    m = _conteo_mensajes(db, campaign_id)
    aceptados = sum(m.get(s, 0) for s in ("aceptado", "entregado", "rebote_permanente",
                                           "rebote_temporal", "queja", "incierto"))
    entregado = m.get("entregado", 0)
    rebotes_duros = m.get("rebote_permanente", 0)
    rebotes_suaves = m.get("rebote_temporal", 0)
    errores = m.get("error", 0)
    quejas = m.get("queja", 0)
    inciertos = m.get("incierto", 0)
    pendientes = m.get("pendiente", 0) + m.get("enviando", 0) + m.get("cancelado", 0)

    # Resultado definitivo: el mensaje ya tiene resolución de entrega o fallo
    definitivos = entregado + rebotes_duros + errores + quejas + inciertos
    aperturas_unicas = _unicos_con_evento(db, "open", campaign_id)
    clics_unicos = _unicos_con_evento(db, "click", campaign_id)

    # Señales de SES que son EVENTOS, no estados de mensaje:
    # - delivery_delay: SES avisó de demora (buzón lento, greylisting). NO es
    #   entrega ni error; el delivery definitivo llega después.
    # - reject: SES rechazó el mensaje antes de aceptarlo (contenido, remitente).
    def _eventos(tipo: str) -> int:
        q = db.query(func.count(SesEvent.id)).filter(SesEvent.type == tipo)
        if campaign_id:
            q = q.filter(SesEvent.campaign_id == campaign_id)
        return int(q.scalar() or 0)

    retrasos = _eventos("delivery_delay")
    rechazados = _eventos("reject")

    def _pct(numerador: int, denominador: int) -> Optional[float]:
        if denominador <= 0:
            return None  # no calculable: no se inventa un cero
        return round(100.0 * numerador / denominador, 1)

    return {
        "total": sum(m.values()),
        "aceptados_ses": aceptados,
        "entregados": entregado,
        "pendientes": pendientes,
        "fallidos": errores,
        "rebotes": rebotes_duros,
        "rebotes_temporales": rebotes_suaves,
        "quejas": quejas,
        "rechazados": rechazados,
        "retrasos": retrasos,
        "incierto": inciertos,
        "success_rate": _pct(entregado, definitivos),
        "bounce_rate": _pct(rebotes_duros + rebotes_suaves, aceptados),
        "open_rate": _pct(aperturas_unicas, entregado),
        "click_rate": _pct(clics_unicos, entregado),
        "aperturas_unicas": aperturas_unicas,
        "clics_unicos": clics_unicos,
        "denominadores": {
            "success_rate": "entregados / (entregados + rebotes permanentes + errores + quejas + inciertos)",
            "bounce_rate": "(rebotes permanentes + temporales) / aceptados por SES",
            "open_rate": "destinatarios únicos con apertura / entregas confirmadas",
            "click_rate": "destinatarios únicos con clic / entregas confirmadas",
        },
        "notas": {
            "retrasos": ("Eventos DeliveryDelay de SES: el mensaje sigue en tránsito "
                         "(no es entrega ni rebote); el resultado definitivo llega después."),
            "rechazados": ("Mensajes que SES rechazó al procesarlos, antes de aceptarlos "
                           "(cuenta como fallidos)."),
            "incierto": ("Envíos sin confirmación de SES (timeout en la API): requieren "
                         "reconciliación; no se cuentan ni como entrega ni como fallo."),
        },
    }


def deliverability_summary(db: Session) -> Dict[str, Any]:
    """Resumen de entregabilidad honesto.

    SES no informa de la ubicación final (Inbox/Spam) de cada mensaje: solo
    confirma la entrega al servidor receptor y notifica rebotes y quejas.
    Las ubicaciones se marcan como no disponibles salvo que se configuren
    pruebas con buzones semilla (seed testing).
    """
    m = _conteo_mensajes(db)
    eventos: Dict[str, int] = {}
    for etype, total in (
        db.query(SesEvent.type, func.count(SesEvent.id)).group_by(SesEvent.type).all()
    ):
        eventos[etype] = total
    return {
        "aceptados_por_ses": m.get("aceptado", 0) + m.get("entregado", 0) + m.get("rebote_permanente", 0)
                             + m.get("rebote_temporal", 0) + m.get("queja", 0) + m.get("incierto", 0),
        "entregado_servidor_receptor": m.get("entregado", 0) or eventos.get("delivery", 0),
        "rebote_permanente": m.get("rebote_permanente", 0),
        "rebote_temporal": m.get("rebote_temporal", 0),
        "rechazados": eventos.get("reject", 0),
        "retrasos_entrega": eventos.get("delivery_delay", 0),
        "quejas_spam": eventos.get("complaint", 0),
        "ubicacion_desconocida": m.get("incierto", 0),
        "inbox_confirmado": None,
        "spam_confirmado": None,
        "inbox_testing": {
            "disponible": False,
            "motivo": (
                "Amazon SES no informa de la ubicación final de cada mensaje. Para medir "
                "Inbox/Spam hay que configurar pruebas con buzones semilla autorizados "
                "(seed testing); no se muestran cifras estimadas."
            ),
        },
        "nota": (
            "«Entregado al servidor receptor» es la confirmación de SES (evento delivery); "
            "no equivale a haber llegado a la bandeja de entrada."
        ),
    }


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
        "rechazados": event_counts.get("reject", 0),
        "retrasos": event_counts.get("delivery_delay", 0),
        "incierto": message_counts.get("incierto", 0),
        "bajas": event_counts.get("unsubscribe", 0),
        "aperturas": event_counts.get("open", 0),
        "clics": event_counts.get("click", 0),
        "errores_envio": message_counts.get("error", 0),
        "contactos": contact_service.count_contacts(db),
        "suprimidos": db.query(func.count(Suppression.id)).scalar() or 0,
    }


def daily_series(db: Session, days: int = 30, campaign_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Evolución diaria de eventos (entregas, rebotes, quejas, bajas, aperturas, clics)."""
    since = utcnow() - timedelta(days=days)
    ev_query = db.query(SesEvent.type, func.date(SesEvent.occurred_at), func.count(SesEvent.id))
    if campaign_id:
        ev_query = ev_query.filter(SesEvent.campaign_id == campaign_id)
    rows = (
        ev_query.filter(SesEvent.occurred_at >= since, SesEvent.type.in_(
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
    send_query = db.query(func.date(CampaignMessage.sent_at), func.count(CampaignMessage.id))
    if campaign_id:
        send_query = send_query.filter(CampaignMessage.campaign_id == campaign_id)
    send_rows = (
        send_query.filter(CampaignMessage.sent_at >= since, CampaignMessage.status.notin_(["pendiente"]))
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
