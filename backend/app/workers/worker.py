"""Worker de envío: procesa la cola con control de velocidad, reintentos
con backoff exponencial, idempotencia y recuperación tras reinicios.

Ejecución: python -m app.workers.worker
"""
from __future__ import annotations

import json
import signal
import time
from datetime import timedelta
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.core.logging_conf import get_logger, setup_logging
from app.models import Campaign, CampaignMessage, Contact
from app.services import campaigns as campaign_service
from app.services import mail_config
from app.services import queue as send_queue
from app.services import ses as ses_service
from app.services import events as events_service
from app.services import sqs_events
from app.services import contacts as contact_service
from app.services.renderer import build_context, render_campaign_email
from app.core.tokens import click_token, unsubscribe_token
from app.workers.redis_client import redis_ok
from app.workers import heartbeat

logger = get_logger("hj.worker")

_running = True


def stop() -> None:
    global _running
    _running = False


def _render_and_send(db, msg: CampaignMessage, campaign: Campaign) -> str:
    """Renderiza el mensaje para el contacto y lo envía. Devuelve MessageId SES.

    Remitente y asunto SIEMPRE de "Configuración de correo" (valores globales
    vigentes en el momento del envío), aunque la campaña antigua tenga otros
    almacenados; sus registros históricos no se reescriben.
    """
    contact = db.get(Contact, msg.contact_id)
    if not contact:
        raise ValueError(f"Contacto inexistente {msg.contact_id}")

    cfg = mail_config.valores_para_envio(db, subject_fallback=campaign.subject)

    unsub_token = unsubscribe_token(contact.id, campaign.id)
    unsub_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/baja?token={unsub_token}"
    unsub_post = f"{settings.API_BASE_URL.rstrip('/')}/api/v1/public/unsubscribe/one-click?token={unsub_token}"
    context = build_context(
        contact={
            "email": contact.email, "first_name": contact.first_name,
            "last_name": contact.last_name, "attributes": contact.attributes,
        },
        campaign={"name": campaign.name, "subject": cfg["subject"]},
        unsubscribe_url=unsub_url,
    )

    def signer(url: str) -> str:
        tok = click_token(campaign.id, msg.id, url)
        return f"{settings.API_BASE_URL.rstrip('/')}/api/v1/t/click/{tok}"

    subject, html_final, plain = render_campaign_email(
        mode=campaign.mode,
        blocks=campaign.blocks,
        html_source=campaign.html,
        subject=cfg["subject"],
        context=context,
        click_signer=signer if campaign.category == "promocional" else None,
    )

    raw = ses_service.build_raw_message(
        from_email=cfg["from_email"], from_name=cfg["from_name"],
        to_email=msg.email, subject=subject, html=html_final, text=plain,
        reply_to=campaign.reply_to,
        unsubscribe_url=unsub_url if campaign.category == "promocional" else None,
        unsubscribe_post_url=unsub_post if campaign.category == "promocional" else None,
        campaign_id=campaign.id, message_id=msg.id, category=campaign.category,
    )
    return ses_service.send_raw_email(raw)


def process_one(db, mid: str) -> Dict[str, Any]:
    """Procesa un trabajo de envío. Devuelve resultado para métricas del ciclo."""
    msg = db.get(CampaignMessage, mid)
    if not msg:
        return {"accion": "descartado", "motivo": "mensaje_inexistente"}

    campaign = db.get(Campaign, msg.campaign_id)
    if not campaign:
        return {"accion": "descartado", "motivo": "campana_inexistente"}

    # Pausa: reencolar con retardo corto
    if campaign.status == "pausada":
        delay = send_queue.schedule_retry(mid, 1)
        return {"accion": "reencolado", "motivo": "campana_pausada", "espera": delay}
    # Cancelada: el mensaje pendiente queda cancelado
    if campaign.status == "cancelada":
        if msg.status == "pendiente":
            msg.status = "cancelado"
        return {"accion": "cancelado"}
    # Estados que no requieren envío
    if campaign.status in ("completada", "fallida"):
        if msg.status == "pendiente":
            msg.status = "cancelado"
        return {"accion": "cancelado", "motivo": "campana_finalizada"}

    # Idempotencia: si ya fue aceptado/entregado, no reenviar
    if msg.status not in ("pendiente", "incierto", "rebote_temporal"):
        return {"accion": "omitido", "motivo": f"estado_{msg.status}"}

    # Supresión: bloqueo de nuevos envíos a contactos suprimidos
    if contact_service.is_suppressed(db, msg.email):
        msg.status = "cancelado"
        msg.last_error = "Contacto en lista de supresión"
        return {"accion": "cancelado", "motivo": "supresion"}

    # Puerta de cuota 24 h (solo lectura de caché: 0 llamadas AWS por mensaje).
    # Si SES ya no acepta más hoy, reencolar con reintento corto; el mensaje NO
    # se marca error (la cuota se restablece con la ventana rodante).
    restante = ses_service.cuota_24h_restante()
    if restante is not None and restante <= settings.QUOTA_24H_MIN_REMAINING:
        delay = send_queue.schedule_retry(mid, 1)
        return {"accion": "reencolado", "motivo": "cuota_24h_agotada",
                "espera": delay, "restante": restante}

    # Claim atómico: UPDATE con guarda de estado. Si el mismo mid aparece dos
    # veces en la cola (o dos réplicas lo sacan), solo UNA reclamación tiene
    # éxito: el segundo worker lo omite en vez de enviar duplicado.
    reclamado = (
        db.query(CampaignMessage)
        .filter(CampaignMessage.id == mid,
                CampaignMessage.status.in_(("pendiente", "incierto", "rebote_temporal")))
        .update({"status": "enviando", "attempts": CampaignMessage.attempts + 1},
                synchronize_session=False)
    )
    db.commit()
    if not reclamado:
        return {"accion": "omitido", "motivo": "reclamado_por_otro_worker"}
    msg = db.get(CampaignMessage, mid)
    db.refresh(msg)

    # Control de velocidad (respeta cuota SES; GLOBAL entre réplicas)
    try:
        send_queue.wait_for_rate_slot(ses_service.effective_rate_per_second())
    except send_queue.RateLimiterUnavailable:
        # Fail-closed: Redis sin servicio => nada sale sin autorización global.
        # Se repone 'pendiente' con commit INMEDIATO (no depende de Redis). El
        # reintento en cola es opcional; si Redis sigue caído, la recuperación
        # de huérfanos del scheduler lo reencolará cuando vuelva.
        msg.status = "pendiente"
        msg.last_error = "rate_limiter_no_disponible"
        db.commit()
        try:
            delay = send_queue.schedule_retry(msg.id, 1)
        except Exception:
            delay = settings.RETRY_BACKOFF_SECONDS
        return {"accion": "reencolado", "motivo": "rate_limiter_no_disponible", "espera": delay}

    try:
        ses_message_id = _render_and_send(db, msg, campaign)
    except ses_service.SESError as exc:
        if exc.retryable and msg.attempts < settings.RETRY_MAX_ATTEMPTS:
            msg.status = "pendiente"
            msg.last_error = exc.detail
            delay = send_queue.schedule_retry(msg.id, msg.attempts)
            db.commit()
            return {"accion": "reintento_programado", "espera": delay, "error": exc.detail}
        msg.status = "error"
        msg.last_error = exc.detail
        db.commit()
        campaign_service.check_campaign_completion(db, campaign)
        db.commit()
        return {"accion": "error", "tipo": exc.kind, "detalle": exc.detail}
    except Exception as exc:
        # Error inesperado: reintento si quedan intentos
        if msg.attempts < settings.RETRY_MAX_ATTEMPTS:
            msg.status = "pendiente"
            msg.last_error = f"error_interno: {type(exc).__name__}"
            delay = send_queue.schedule_retry(msg.id, msg.attempts)
            db.commit()
            return {"accion": "reintento_programado", "espera": delay, "error": type(exc).__name__}
        msg.status = "error"
        msg.last_error = f"error_interno: {type(exc).__name__}"
        db.commit()
        return {"accion": "error", "tipo": "interno"}

    # Éxito: SES aceptó el mensaje
    msg.status = "aceptado"
    msg.ses_message_id = ses_message_id
    msg.sent_at = utcnow()
    msg.last_error = None
    if campaign.status == "en_cola":
        campaign_service.transition(campaign, "enviando")
    db.commit()

    events_service.record_event(
        db, type="envio_local", event_uid=f"local:{msg.id}:{msg.attempts}",
        campaign_id=campaign.id, message_id=msg.id, ses_message_id=ses_message_id,
        email=msg.email,
    )
    db.commit()
    campaign_service.check_campaign_completion(db, campaign)
    db.commit()
    return {"accion": "enviado", "ses_message_id": ses_message_id}


def recover_orphaned_messages(db, *, min_age_seconds: Optional[int] = None,
                              limit: int = 10000) -> int:
    """Reencola mensajes 'pendiente' que perdieron su lugar en la cola Redis.

    Un crash del worker entre BRPOP y el envío deja el mensaje 'pendiente' en
    PostgreSQL pero FUERA de la cola: nadie lo procesaría jamás. Cuando no hay
    nada en cola ni en reintentos (nada en vuelo) y el mensaje supera la edad
    mínima, se reencola; el claim atómico de process_one hace el proceso
    idempotente (una copia extra en cola nunca duplica el envío).

    'incierto' queda excluido a PROPÓSITO: ese estado requiere reconciliación
    manual para no duplicar correos que SES ya pudo aceptar.
    """
    edad = settings.ORPHAN_MIN_AGE_SECONDS if min_age_seconds is None else min_age_seconds
    try:
        if send_queue.outbound_count() > 0 or send_queue.pending_retries_count() > 0:
            return 0  # hay trabajo en vuelo: no tocar nada
    except Exception:
        return 0  # Redis caído: la recuperación no puede comprobar el estado
    limite = utcnow() - timedelta(seconds=max(edad, 0))
    ids = (
        db.query(CampaignMessage.id)
        .join(Campaign, Campaign.id == CampaignMessage.campaign_id)
        .filter(CampaignMessage.status == "pendiente",
                Campaign.status.in_(("en_cola", "enviando")),
                CampaignMessage.updated_at < limite)
        .limit(limit)
        .all()
    )
    mids = [r[0] for r in ids]
    if mids:
        send_queue.enqueue_messages(mids)
        logger.warning("Mensajes huérfanos reencolados", extra={"cantidad": len(mids)})
    return len(mids)


def reconcile_stale_sends(db) -> int:
    """Marca como 'incierto' los mensajes 'enviando' antiguos (posible pérdida
    de respuesta de SES tras una interrupción). Requiere reconciliación manual."""
    threshold = utcnow().timestamp() - settings.STALE_SENDING_MINUTES * 60
    stale = (
        db.query(CampaignMessage)
        .filter(CampaignMessage.status == "enviando",
                CampaignMessage.updated_at < utcnow().fromtimestamp(threshold, tz=utcnow().tzinfo))
        .all()
    )
    for msg in stale:
        msg.status = "incierto"
        msg.last_error = "Interrumpido tras el envío; resultado desconocido. Reconciliar manualmente."
    if stale:
        db.commit()
        logger.warning("Mensajes marcados como inciertos", extra={"cantidad": len(stale)})
    return len(stale)


def run_forever(poll_interval: int = 5) -> None:
    """Bucle principal del worker."""
    setup_logging()
    logger.info("Worker de envío iniciado", extra={"region": settings.AWS_REGION})
    # Eventos de SES vía SQS (canal privado) en hilo independiente
    sqs_events.start_consumer()
    while _running:
        try:
            # Latido de actividad real (healthcheck de Docker): se registra al
            # principio de cada ciclo, incluso cuando la cola está vacía.
            heartbeat.beat("worker", extra={"region": settings.AWS_REGION})
            if not redis_ok():
                logger.error("Redis no disponible; reintentando en 5s")
                time.sleep(poll_interval)
                continue
            # Reintentos vencidos -> cola principal
            send_queue.move_due_retries()
            mid = send_queue.pop_message(timeout_seconds=3)
            if not mid:
                continue
            db = SessionLocal()
            try:
                result = process_one(db, mid)
                if result.get("accion") == "reencolado":
                    time.sleep(min(result.get("espera", 30), 30))
                logger.info("Trabajo procesado", extra={"resultado": json.dumps(result)[:400]})
            finally:
                db.close()
        except KeyboardInterrupt:
            logger.info("Worker detenido por señal")
            break
        except Exception as exc:
            logger.error("Error en ciclo del worker", extra={"error": type(exc).__name__})
            time.sleep(poll_interval)


if __name__ == "__main__":
    # Apagado limpio con SIGTERM (docker stop) y SIGINT
    signal.signal(signal.SIGTERM, lambda *_: (stop(), sqs_events.stop_consumer()))
    signal.signal(signal.SIGINT, lambda *_: (stop(), sqs_events.stop_consumer()))
    run_forever()
