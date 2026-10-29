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
    Suppression,
    contact_lists_rel,
)
from app.services import queue as send_queue
from app.services.contacts import normalize_email
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


def _recipients_query(db: Session, campaign: Campaign):
    """Consulta base de destinatarios elegibles (única fuente de verdad).

    La usan select_recipients, count_recipients y cualquier estimación: así
    las reglas de elegibilidad NO pueden divergir entre 'seleccionar' y
    'contar' (antes el conteo del panel materializaba toda la lista).

    Reglas aplicadas:
    - Estado 'suscripto' (consentimiento vigente) para TODAS las categorías.
    - Pertenencia a lista o filtros de segmento cuando aplique.
    - Campañas promocionales: excluye suprimidos con NOT EXISTS correlacionado
      en SQL (la exclusión la hace la BD, sin cargar 50k filas en memoria).
      Suppression.email se almacena siempre normalizado (suppress_email usa
      normalize_email); se compara contra lower(Contact.email) para ser
      inmune a contactos heredados con mayúsculas, y al no transformar la
      columna de Suppression el índice único de email sigue utilizable.
    """
    query = db.query(Contact).filter(Contact.status == "suscripto",
                                     Contact.deleted_at.is_(None))

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

    if campaign.category == "promocional":
        query = query.filter(
            ~db.query(Suppression.id)
            .filter(Suppression.email == func.lower(Contact.email))
            .exists()
        )
    return query


def select_recipients(db: Session, campaign: Campaign) -> List[Contact]:
    """Selecciona destinatarios (mismas reglas que count_recipients)."""
    return _recipients_query(db, campaign).all()


def count_recipients(db: Session, campaign: Campaign) -> int:
    """Cuenta destinatarios en SQL (COUNT *): nunca materializa la lista.

    Los endpoints de estimación/preview con 50k+ contactos pasaron de cargar
    todas las filas y filtrar en Python a un único COUNT con la exclusión de
    supresiones dentro de la propia consulta.
    """
    return int(_recipients_query(db, campaign).count())


def enqueue_campaign(db: Session, campaign: Campaign) -> Dict[str, Any]:
    """Crea los mensajes por destinatario (idempotente) y los encola.

    No inicia envíos automáticamente al desplegar: solo se invoca desde
    acciones explícitas (enviar ahora / programación vencida aprobada).

    Escalado a 50k+: existencia comprobada por TROZOS de 1000 (una consulta
    IN por trozo en vez de una por contacto) e inserción masiva
    (insertmanyvalues) con fallback fila a fila si un trozo choca con una
    carrera externa.
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
    TROZO = 1000
    for i in range(0, len(recipients), TROZO):
        trozo = recipients[i:i + TROZO]
        ids_contacto = [c.id for c in trozo]
        # 1 consulta por trozo (antes: 1 por contacto)
        pares_existentes = {
            (cid, email)
            for cid, email in db.query(CampaignMessage.contact_id, CampaignMessage.email)
            .filter(CampaignMessage.campaign_id == campaign.id,
                    CampaignMessage.contact_id.in_(ids_contacto))
            .all()
        }
        nuevos: List[CampaignMessage] = []
        for contact in trozo:
            email = normalize_email(contact.email)
            if (contact.id, email) in pares_existentes:
                existing += 1
                continue
            nuevos.append(CampaignMessage(
                campaign_id=campaign.id, contact_id=contact.id, email=email,
                status="pendiente",
            ))
        if not nuevos:
            continue
        try:
            db.add_all(nuevos)
            db.flush()  # insertmanyvalues: un INSERT por trozo cuando el driver lo soporta
        except Exception:
            # Carrera externa (duplicado insertado a la vez): fallback fila a
            # fila; un contacto problemático no tira el trozo.
            db.rollback()
            for msg in nuevos:
                try:
                    dup = db.query(CampaignMessage.id).filter_by(
                        campaign_id=campaign.id, contact_id=msg.contact_id).first()
                    if dup:
                        existing += 1
                        continue
                    db.add(msg)
                    db.flush()
                except Exception:
                    db.rollback()
                    existing += 1
                    continue
        message_ids.extend(m.id for m in nuevos if m.id)

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


# Papelera / archivo (V5) --------------------------------------------------- #

ESTADOS_ACTIVOS = ("en_cola", "enviando", "pausada")
ESTADOS_CON_POBLACION_VIVA = ("borrador", "en_revision", "programada")


def cancel_for_delete(db: Session, campaign: Campaign, **user) -> int:
    """Cancela la campaña antes de llevarla a la papelera.

    - Marca pendientes como cancelados (los aceptados/entregados/incierto NO
      se tocan: cualquier operación ya aceptada por SES se respeta y jamás se
      reenvía; el worker ya ignora campañas canceladas en process_one).
    - Quitar la programación evita que el scheduler la lance más tarde.
    - Devuelve el número de mensajes cancelados.
    """
    if campaign.status in ("completada", "cancelada"):
        return 0
    updated = (
        db.query(CampaignMessage)
        .filter(CampaignMessage.campaign_id == campaign.id, CampaignMessage.status == "pendiente")
        .update({"status": "cancelado"}, synchronize_session=False)
    )
    previous = campaign.status
    campaign.status = "cancelada"
    campaign.scheduled_at = None
    add_history(campaign, "cancelada_por_eliminacion",
                detail={"mensajes_cancelados": updated, "estado_anterior": previous}, **user)
    return int(updated or 0)


def soft_delete_campaign(db: Session, campaign: Campaign, *, user_id: Optional[str] = None,
                         user_email: Optional[str] = None) -> int:
    """Lleva la campaña a la Papelera (borrado lógico).

    Si la campaña estaba activa se cancela antes (ningún envío queda suelto).
    Mensajes, eventos SES, historial y estadísticas quedan intactos: el
    borrado solo marca la fila de campaña.
    """
    if campaign.deleted_at is not None:
        raise CampaignError("La campaña ya está en la papelera")
    cancelados = cancel_for_delete(db, campaign,
                                   user_id=user_id, user_email=user_email)
    campaign.deleted_at = utcnow()
    campaign.deleted_by = user_id
    add_history(campaign, "eliminada", detail={
        "mensajes_cancelados": cancelados,
        "nota": "borrado lógico: los mensajes y eventos se conservan",
    }, user_id=user_id, user_email=user_email)
    return cancelados


def restore_campaign(db: Session, campaign: Campaign, *, user_id: Optional[str] = None,
                     user_email: Optional[str] = None) -> None:
    """Saca la campaña de la Papelera conservando toda su información.

    Restaurar NO reanuda el envío: si estaba activa al eliminarse quedó
    cancelada (comportamiento deliberado para no reenviar nada).
    """
    if campaign.deleted_at is None:
        raise CampaignError("La campaña no está en la papelera")
    campaign.deleted_at = None
    campaign.deleted_by = None
    add_history(campaign, "restaurada", user_id=user_id, user_email=user_email)


def archive_campaign(db: Session, campaign: Campaign, *, user_id: Optional[str] = None,
                     user_email: Optional[str] = None) -> None:
    if campaign.deleted_at is not None:
        raise CampaignError("No se puede archivar una campaña eliminada; restáurala primero")
    if campaign.archived_at is not None:
        raise CampaignError("La campaña ya está archivada")
    campaign.archived_at = utcnow()
    add_history(campaign, "archivada", user_id=user_id, user_email=user_email)


def unarchive_campaign(db: Session, campaign: Campaign, *, user_id: Optional[str] = None,
                       user_email: Optional[str] = None) -> None:
    if campaign.archived_at is None:
        raise CampaignError("La campaña no está archivada")
    campaign.archived_at = None
    add_history(campaign, "desarchivada", user_id=user_id, user_email=user_email)


def campaigns_query(db: Session, *, papelera: bool = False,
                    incluir_archivadas: bool = True):
    """Consulta base de campañas para el listado.

    - Papelera desactivada (defecto): excluye las eliminadas.
    - papelera=True: SOLO eliminadas.
    Las archivadas siguen en el listado (con su fecha) salvo que se pida lo
    contrario: eliminarlas de la vista rompería la búsqueda del histórico.
    """
    query = db.query(Campaign)
    if papelera:
        return query.filter(Campaign.deleted_at.isnot(None))
    query = query.filter(Campaign.deleted_at.is_(None))
    if not incluir_archivadas:
        query = query.filter(Campaign.archived_at.is_(None))
    return query
