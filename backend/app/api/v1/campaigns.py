"""Campañas: CRUD, aprobación, programación, pruebas, pausa/reanudación,
cancelación, progreso en tiempo real e historial."""
from __future__ import annotations

import zoneinfo
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.db import get_db, utcnow
from app.core.deps import get_current_user, require_admin
from app.models import Campaign, CampaignMessage, SesEvent, Template, User
from app.services import contacts as contact_service
from app.schemas import (
    CampaignAction,
    CampaignCreate,
    CampaignDetailOut,
    CampaignHistoryOut,
    CampaignMessageOut,
    CampaignOut,
    CampaignUpdate,
    RecipientsEstimate,
    RecipientsPreview,
)
from app.services import campaigns as campaign_service
from app.services import mail_config
from app.services import ses as ses_service
from app.services.renderer import (
    html_to_plain_text,
    render_campaign_email,
    render_template_string,
    validate_template_source,
)
from app.services.sanitizer import SanitizationError, sanitize_editor_blocks, sanitize_email_html

router = APIRouter(prefix="/campaigns", tags=["Campañas"])


def _validate_campaign_content(mode: str, blocks, html) -> None:
    if mode == "html" and html:
        err = validate_template_source(html)
        if err:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Sintaxis inválida: {err}")
        if len(html) > 500_000:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "HTML demasiado extenso")
    if blocks and len(blocks) > 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Demasiados bloques")


def _get_campaign(db: Session, campaign_id: str) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaña no encontrada")
    return campaign


@router.get("", response_model=dict)
def list_campaigns(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                   estado: Optional[str] = None, page: int = Query(1, ge=1),
                   per_page: int = Query(20, ge=1, le=100)):
    query = db.query(Campaign)
    if estado:
        query = query.filter(Campaign.status == estado)
    total = query.count()
    items = query.order_by(Campaign.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    counts = dict(db.query(Campaign.status, func.count(Campaign.id)).group_by(Campaign.status).all())
    return {
        "total": total, "page": page, "per_page": per_page,
        "por_estado": counts,
        "items": [CampaignOut.model_validate(c).model_dump(mode="json") for c in items],
    }


# Rutas estáticas (deben declararse antes que /{campaign_id} para que
# FastAPI no interprete "queue", "scheduled" o "recipients" como un id).


@router.post("/recipients-preview", response_model=RecipientsEstimate)
def recipients_preview(payload: RecipientsPreview, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Cuenta los destinatarios elegibles ANTES de crear la campaña.

    Aplica las mismas reglas que el envío real: estado suscripto, sin
    supresión (campañas promocionales) y pertenencia a lista/segmento.
    """
    if not payload.list_id and not payload.segment_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Debe seleccionar una lista o un segmento")
    probe = Campaign(
        name="preview", subject="preview", from_name="preview", from_email="preview@local",
        category=payload.category, list_id=payload.list_id, segment_id=payload.segment_id,
    )
    recipients = campaign_service.select_recipients(db, probe)
    return RecipientsEstimate(destinatarios=len(recipients))


@router.get("/queue/active", response_model=dict)
def queue_active(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Estado en tiempo real de la cola de envíos, por campaña.

    Incluye progreso, velocidad reciente (mensajes/min) y último error.
    """
    from app.core.db import utcnow as _utcnow

    activos = (
        db.query(Campaign)
        .filter(Campaign.status.in_(["en_cola", "enviando", "pausada"]))
        .order_by(Campaign.enqueued_at.asc().nullslast())
        .all()
    )
    ahora = _utcnow()
    items = []
    for c in activos:
        progreso = campaign_service.campaign_progress(db, c)
        estados = progreso["por_estado"]
        # Velocidad: mensajes aceptados en los últimos 60 segundos
        minuto = (
            db.query(func.count(CampaignMessage.id))
            .filter(
                CampaignMessage.campaign_id == c.id,
                CampaignMessage.sent_at.isnot(None),
                CampaignMessage.sent_at >= ahora - timedelta(seconds=60),
            )
            .scalar() or 0
        )
        reintentos = (
            db.query(func.count(CampaignMessage.id))
            .filter(CampaignMessage.campaign_id == c.id, CampaignMessage.attempts > 1)
            .scalar() or 0
        )
        ultimo_error = (
            db.query(CampaignMessage)
            .filter(
                CampaignMessage.campaign_id == c.id,
                CampaignMessage.last_error.isnot(None),
                CampaignMessage.status.in_(["error", "incierto", "rebote_temporal"]),
            )
            .order_by(CampaignMessage.updated_at.desc())
            .first()
        )
        items.append({
            "id": c.id,
            "name": c.name,
            "subject": c.subject,
            "status": c.status,
            "total": progreso["total"],
            "procesados": progreso["procesados"],
            "por_estado": estados,
            "enviados": estados.get("aceptado", 0) + estados.get("entregado", 0),
            "pendientes": progreso["pendientes"],
            "entregados": estados.get("entregado", 0),
            "fallidos": estados.get("error", 0),
            "rebotes": estados.get("rebote_permanente", 0) + estados.get("rebote_temporal", 0),
            "reintentos": reintentos,
            "velocidad_por_minuto": minuto,
            "hora_inicio": c.enqueued_at.isoformat() if c.enqueued_at else None,
            "hora_fin": c.completed_at.isoformat() if c.completed_at else None,
            "ultimo_error": (ultimo_error.last_error[:200] if ultimo_error and ultimo_error.last_error else None),
        })
    return {"items": items, "actualizado": ahora.isoformat()}


@router.get("/scheduled/upcoming", response_model=dict)
def scheduled_upcoming(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                       page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100)):
    """Próximos envíos programados (persisten tras reinicios del VPS)."""
    query = (
        db.query(Campaign)
        .filter(Campaign.status == "programada", Campaign.scheduled_at.isnot(None))
        .order_by(Campaign.scheduled_at.asc())
    )
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "total": total, "page": page, "per_page": per_page,
        "items": [CampaignOut.model_validate(c).model_dump(mode="json") for c in items],
    }


@router.get("/scheduled/history", response_model=dict)
def scheduled_history(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                      page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100)):
    """Historial de envíos que estuvieron programados (completadas, canceladas,
    fallidas, o ya en cola/enviando)."""
    query = (
        db.query(Campaign)
        .filter(Campaign.scheduled_at.isnot(None),
                Campaign.status.in_(["completada", "cancelada", "fallida", "en_cola", "enviando", "pausada"]))
        .order_by(Campaign.scheduled_at.desc())
    )
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "total": total, "page": page, "per_page": per_page,
        "items": [CampaignOut.model_validate(c).model_dump(mode="json") for c in items],
    }


def _resolver_remitente(db: Session, payload: CampaignCreate) -> tuple[str, str]:
    """Remitente automático: usa el configurado por defecto salvo override.

    Fuentes: "Configuración de correo" (app_settings: default_from_name /
    default_from_email), con fallback a las constantes del proyecto.
    El correo del remitente debe pertenecer al dominio verificado en SES.
    """
    guardado = mail_config.leer_guardado(db)
    from_name = (payload.from_name or "").strip() or \
        guardado[mail_config.KEY_FROM_NAME] or mail_config.DEFAULT_FROM_NAME
    from_email = (payload.from_email or "").strip() or \
        guardado[mail_config.KEY_FROM_EMAIL] or mail_config.DEFAULT_FROM_EMAIL
    if not contact_service.is_valid_email(from_email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"El remitente por defecto no es un correo válido: {from_email}")
    try:
        from_email = mail_config.validar_from_email(from_email)
    except mail_config.MailConfigError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return from_name, from_email


@router.post("", response_model=CampaignDetailOut, status_code=201)
def create_campaign(payload: CampaignCreate, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _validate_campaign_content(payload.mode, payload.blocks, payload.html)
    if payload.list_id is None and payload.segment_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Debe seleccionar una lista o un segmento de destinatarios")

    from_name, from_email = _resolver_remitente(db, payload)

    # Asunto: el propio de la campaña tiene prioridad (nunca se sobrescribe);
    # si se omite, se usa el asunto predeterminado de "Configuración de correo".
    subject = (payload.subject or "").strip()
    if not subject:
        subject = mail_config.config_efectiva(db)["subject"]
    if not subject:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "El asunto es obligatorio: indícalo o define un asunto "
                            "predeterminado en 'Configuración de correo'")

    blocks = None
    html = None
    plain_text = None
    try:
        if payload.mode == "html":
            html = sanitize_email_html(payload.html or "")
            plain_text = html_to_plain_text(html)
        else:
            blocks = sanitize_editor_blocks(payload.blocks or [])
    except SanitizationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    campaign = Campaign(
        name=payload.name, subject=subject,
        from_name=from_name, from_email=from_email,
        reply_to=payload.reply_to, template_id=payload.template_id,
        mode=payload.mode, blocks=blocks, html=html, plain_text=plain_text,
        category=payload.category, list_id=payload.list_id, segment_id=payload.segment_id,
        scheduled_at=payload.scheduled_at, timezone=payload.timezone,
        created_by=user.id,
    )
    db.add(campaign)
    db.flush()
    campaign_service.add_history(campaign, "creada", user_id=user.id, user_email=user.email)
    audit(db, "campana_creada", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=campaign.id,
          detail={"nombre": payload.name}, ip=security.client_ip(request))
    db.commit()
    db.refresh(campaign)
    detail = CampaignDetailOut.model_validate(campaign)
    detail.progreso = campaign_service.campaign_progress(db, campaign)
    return detail


@router.get("/{campaign_id}", response_model=CampaignDetailOut)
def get_campaign(campaign_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    detail = CampaignDetailOut.model_validate(campaign)
    detail.progreso = campaign_service.campaign_progress(db, campaign)
    from app.services.stats import metrics_summary
    detail.metricas = metrics_summary(db, campaign_id=campaign.id)
    return detail


@router.put("/{campaign_id}", response_model=CampaignDetailOut)
def update_campaign(campaign_id: str, payload: CampaignUpdate, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    if campaign.status not in ("borrador", "en_revision", "programada", "fallida"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"No se puede editar una campaña en estado {campaign.status}")
    _validate_campaign_content(payload.mode or campaign.mode, payload.blocks, payload.html)

    content_changed = False
    if payload.name is not None:
        campaign.name = payload.name
    if payload.subject is not None:
        campaign.subject = payload.subject
    if payload.from_name is not None:
        campaign.from_name = payload.from_name
    if payload.from_email is not None:
        campaign.from_email = payload.from_email
    if "reply_to" in payload.model_fields_set:
        campaign.reply_to = payload.reply_to
    if payload.category is not None:
        campaign.category = payload.category
    if payload.list_id is not None:
        campaign.list_id = payload.list_id
        campaign.segment_id = None
    if payload.segment_id is not None:
        campaign.segment_id = payload.segment_id
        campaign.list_id = None
    if payload.mode is not None and payload.mode != campaign.mode:
        campaign.mode = payload.mode
        content_changed = True
    if payload.blocks is not None:
        campaign.blocks = sanitize_editor_blocks(payload.blocks)
        content_changed = True
    if payload.html is not None:
        campaign.html = sanitize_email_html(payload.html)
        content_changed = True
    if payload.scheduled_at is not None:
        campaign.scheduled_at = payload.scheduled_at
    if payload.timezone is not None:
        campaign.timezone = payload.timezone
    if content_changed:
        if campaign.mode == "html" and campaign.html:
            campaign.plain_text = html_to_plain_text(campaign.html)
    campaign_service.add_history(campaign, "editada", user_id=user.id, user_email=user.email)
    audit(db, "campana_editada", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=campaign.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(campaign)
    detail = CampaignDetailOut.model_validate(campaign)
    detail.progreso = campaign_service.campaign_progress(db, campaign)
    return detail


@router.post("/{campaign_id}/duplicate", response_model=CampaignDetailOut, status_code=201)
def duplicate_campaign(campaign_id: str, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    copy = Campaign(
        name=f"{campaign.name} (copia)", subject=campaign.subject,
        from_name=campaign.from_name, from_email=campaign.from_email,
        reply_to=campaign.reply_to, template_id=campaign.template_id,
        mode=campaign.mode, blocks=campaign.blocks, html=campaign.html,
        plain_text=campaign.plain_text, category=campaign.category,
        list_id=campaign.list_id, segment_id=campaign.segment_id,
        timezone=campaign.timezone, created_by=user.id,
    )
    db.add(copy)
    db.flush()
    campaign_service.add_history(copy, "duplicada_desde", user_id=user.id,
                                 user_email=user.email, detail={"origen": campaign.id})
    audit(db, "campana_duplicada", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=copy.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(copy)
    return copy


# Acciones de estado ------------------------------------------------------------ #


@router.post("/{campaign_id}/submit-review")
def submit_for_review(campaign_id: str, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    campaign_service.transition(campaign, "en_revision", user_id=user.id, user_email=user.email)
    db.commit()
    return {"estado": campaign.status}


@router.post("/{campaign_id}/approve", dependencies=[Depends(require_admin)])
def approve_campaign(campaign_id: str, request: Request, db: Session = Depends(get_db),
                     admin: User = Depends(require_admin)):
    """Aprobación administrativa obligatoria antes del envío masivo."""
    campaign = _get_campaign(db, campaign_id)
    if campaign.approved_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La campaña ya está aprobada")
    campaign.approved_by = admin.id
    campaign.approved_at = utcnow()
    campaign_service.add_history(campaign, "aprobada", user_id=admin.id, user_email=admin.email)
    audit(db, "campana_aprobada", user_id=admin.id, user_email=admin.email,
          entity_type="campaign", entity_id=campaign.id, ip=security.client_ip(request))
    db.commit()
    return {"estado": campaign.status, "aprobada": True}


@router.post("/{campaign_id}/schedule")
def schedule_campaign(campaign_id: str, payload: CampaignAction, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    if not campaign.approved_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "La campaña requiere aprobación administrativa antes de programar")
    if not payload.scheduled_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Debe indicar fecha y hora")
    tz_name = payload.timezone or campaign.timezone or "UTC"
    try:
        zoneinfo.ZoneInfo(tz_name)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Zona horaria inválida: {tz_name}")
    campaign.scheduled_at = payload.scheduled_at
    campaign.timezone = tz_name
    campaign_service.transition(campaign, "programada", user_id=user.id, user_email=user.email,
                                detail={"programada_para": payload.scheduled_at.isoformat()})
    db.commit()
    return {"estado": campaign.status}


@router.put("/{campaign_id}/schedule")
def reschedule_campaign(campaign_id: str, payload: CampaignAction, request: Request,
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Modifica la programación de una campaña ya programada (fecha, hora o
    zona horaria). Los mensajes pendientes aún no se han encolado."""
    campaign = _get_campaign(db, campaign_id)
    if campaign.status != "programada":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Solo se puede reprogramar una campaña en estado programada")
    if payload.scheduled_at is not None:
        tz_name = payload.timezone or campaign.timezone or "UTC"
        try:
            zoneinfo.ZoneInfo(tz_name)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Zona horaria inválida: {tz_name}")
        campaign.scheduled_at = payload.scheduled_at
        campaign.timezone = tz_name
    elif payload.timezone is not None:
        try:
            zoneinfo.ZoneInfo(payload.timezone)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Zona horaria inválida: {payload.timezone}")
        campaign.timezone = payload.timezone
    campaign_service.add_history(campaign, "reprogramada", user_id=user.id,
                                 user_email=user.email,
                                 detail={"nueva_fecha": (
                                     campaign.scheduled_at.isoformat() if campaign.scheduled_at else None)})
    audit(db, "campana_reprogramada", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=campaign.id, ip=security.client_ip(request))
    db.commit()
    return {"estado": campaign.status, "scheduled_at": (
        campaign.scheduled_at.isoformat() if campaign.scheduled_at else None),
        "timezone": campaign.timezone}


@router.post("/{campaign_id}/schedule/cancel")
def cancel_schedule(campaign_id: str, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Cancela la programación y devuelve la campaña a borrador (sin cancelar
    la campaña en sí)."""
    campaign = _get_campaign(db, campaign_id)
    if campaign.status != "programada":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Solo se puede cancelar la programación de campañas programadas")
    campaign_service.transition(campaign, "borrador", user_id=user.id, user_email=user.email,
                                detail={"motivo": "programacion_cancelada"})
    campaign.scheduled_at = None
    audit(db, "campana_desprogramada", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=campaign.id, ip=security.client_ip(request))
    db.commit()
    return {"estado": campaign.status}


@router.post("/{campaign_id}/send-now", response_model=dict)
def send_now(campaign_id: str, request: Request, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """Encola el envío masivo ahora (requiere aprobación previa)."""
    campaign = _get_campaign(db, campaign_id)
    if not campaign.approved_at:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "La campaña requiere aprobación administrativa antes del envío")
    if campaign.category == "promocional":
        # Verificación de flujo de bajas probado: siempre activo (requisito)
        pass
    try:
        result = campaign_service.enqueue_campaign(db, campaign)
    except campaign_service.CampaignError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    audit(db, "campana_enviada", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=campaign.id,
          detail={"destinatarios": result["destinatarios"]}, ip=security.client_ip(request))
    db.commit()
    return result


@router.post("/{campaign_id}/pause")
def pause(campaign_id: str, request: Request, db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    try:
        campaign_service.pause_campaign(db, campaign, user_id=user.id, user_email=user.email)
    except campaign_service.CampaignError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return {"estado": campaign.status}


@router.post("/{campaign_id}/resume")
def resume(campaign_id: str, request: Request, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    try:
        campaign_service.resume_campaign(db, campaign, user_id=user.id, user_email=user.email)
    except campaign_service.CampaignError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return {"estado": campaign.status}


@router.post("/{campaign_id}/cancel")
def cancel(campaign_id: str, request: Request, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    try:
        campaign_service.cancel_campaign(db, campaign, user_id=user.id, user_email=user.email)
    except campaign_service.CampaignError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    audit(db, "campana_cancelada", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=campaign.id, ip=security.client_ip(request))
    db.commit()
    return {"estado": campaign.status}


@router.get("/{campaign_id}/recipients-estimate", response_model=RecipientsEstimate)
def recipients_estimate(campaign_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    recipients = campaign_service.select_recipients(db, campaign)
    return RecipientsEstimate(destinatarios=len(recipients))


# Seguimiento ------------------------------------------------------------------- #


@router.get("/{campaign_id}/messages", response_model=dict)
def campaign_messages(campaign_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user),
                      estado: Optional[str] = None, page: int = Query(1, ge=1),
                      per_page: int = Query(25, ge=1, le=100)):
    campaign = _get_campaign(db, campaign_id)
    query = db.query(CampaignMessage).filter(CampaignMessage.campaign_id == campaign.id)
    if estado:
        query = query.filter(CampaignMessage.status == estado)
    total = query.count()
    items = (
        query.order_by(CampaignMessage.updated_at.desc())
        .offset((page - 1) * per_page).limit(per_page).all()
    )
    return {
        "total": total, "page": page, "per_page": per_page,
        "items": [CampaignMessageOut.model_validate(m).model_dump(mode="json") for m in items],
    }


@router.get("/{campaign_id}/events", response_model=dict)
def campaign_events(campaign_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user),
                    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200)):
    campaign = _get_campaign(db, campaign_id)
    query = db.query(SesEvent).filter(SesEvent.campaign_id == campaign.id)
    total = query.count()
    items = (
        query.order_by(SesEvent.occurred_at.desc())
        .offset((page - 1) * per_page).limit(per_page).all()
    )
    return {
        "total": total, "page": page, "per_page": per_page,
        "items": [
            {
                "id": e.id, "tipo": e.type, "correo": e.email,
                "url": e.url, "ocurrido": e.occurred_at.isoformat(),
                "ses_message_id": e.ses_message_id,
            }
            for e in items
        ],
    }


@router.get("/{campaign_id}/history", response_model=List[CampaignHistoryOut])
def campaign_history(campaign_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    return campaign.history


@router.get("/{campaign_id}/plain-text")
def campaign_plain_text(campaign_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    campaign = _get_campaign(db, campaign_id)
    return {"plain_text": campaign.plain_text or ""}


@router.post("/{campaign_id}/test-send")
def campaign_test_send(campaign_id: str, payload: Dict[str, Any], request: Request,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Envío de prueba de la campaña a una dirección autorizada."""
    campaign = _get_campaign(db, campaign_id)
    to_email = str(payload.get("to_email", "")).strip()
    if not to_email or "@" not in to_email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Correo de destino inválido")
    to_email = to_email.lower()

    context = {
        "contact": {"email": to_email, "first_name": "Prueba", "last_name": "",
                    "attributes": {}},
        "campaign": {"name": campaign.name, "subject": campaign.subject},
        "unsubscribe_url": "https://ejemplo.local/baja?token=PRUEBA",
        "web_view_url": "",
    }
    try:
        subject, html_final, plain = render_campaign_email(
            mode=campaign.mode, blocks=campaign.blocks, html_source=campaign.html,
            subject=campaign.subject, context=context,
        )
        ses_id = ses_service.send_test_email(
            to_email=to_email, subject=f"[PRUEBA] {subject}", html=html_final, text=plain,
            from_email=campaign.from_email, from_name=campaign.from_name,
            reply_to=campaign.reply_to,
        )
    except ses_service.SESNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except ses_service.SESError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"SES: {exc.detail}")
    except SanitizationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    campaign_service.add_history(campaign, "prueba_enviada",
                                 user_id=user.id, user_email=user.email,
                                 detail={"destinatario": to_email[:3] + "***"})
    audit(db, "prueba_campana", user_id=user.id, user_email=user.email,
          entity_type="campaign", entity_id=campaign.id, ip=security.client_ip(request))
    db.commit()
    return {"detalle": "Prueba enviada", "ses_message_id": ses_id}
