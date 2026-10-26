"""Scheduler: campañas programadas, reintentos vencidos y reconciliación.

Ejecución: python -m app.workers.scheduler
"""
from __future__ import annotations

import time
from datetime import timezone as tz_module
from typing import Optional

from sqlalchemy import func

from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.core.logging_conf import get_logger, setup_logging
from app.models import Campaign, CampaignMessage
from app.services import campaigns as campaign_service
from app.services import queue as send_queue
from app.services import ses as ses_service
from app.workers.worker import reconcile_stale_sends

logger = get_logger("hj.scheduler")


def process_scheduled_campaigns(db) -> int:
    """Encola campañas programadas cuya fecha venció y que estén aprobadas."""
    now = utcnow()
    due = (
        db.query(Campaign)
        .filter(Campaign.status == "programada",
                Campaign.scheduled_at.isnot(None),
                Campaign.scheduled_at <= now,
                Campaign.approved_at.isnot(None))
        .all()
    )
    count = 0
    for campaign in due:
        try:
            campaign_service.enqueue_campaign(db, campaign)
            db.commit()
            count += 1
            logger.info("Campaña programada encolada", extra={"campana": campaign.name})
        except Exception as exc:
            db.rollback()
            logger.error("Fallo al encolar campaña programada",
                         extra={"campana": campaign.name, "error": type(exc).__name__})
            campaign_service.add_history(
                campaign, "error_programacion",
                detail={"motivo": type(exc).__name__},
            )
            campaign_service.transition(campaign, "fallida", detail={"motivo": str(exc)[:200]})
            db.commit()
    return count


def refresh_quota_cache() -> None:
    try:
        ses_service.get_sending_quota(force_refresh=True)
    except Exception:
        pass


def run_forever(interval_seconds: int = 20) -> None:
    """Bucle principal del scheduler."""
    setup_logging()
    logger.info("Scheduler iniciado")
    last_quota = 0.0
    last_reconcile = 0.0
    while True:
        try:
            db = SessionLocal()
            try:
                scheduled = process_scheduled_campaigns(db)
                send_queue.move_due_retries()

                now = time.time()
                if now - last_reconcile > 300:  # cada 5 minutos
                    stale = reconcile_stale_sends(db)
                    last_reconcile = now
                    if stale:
                        logger.warning("Reconciliación: mensajes inciertos", extra={"cantidad": stale})

                if now - last_quota > 600:  # cada 10 minutos
                    refresh_quota_cache()
                    last_quota = now

                if scheduled:
                    logger.info("Campañas programadas procesadas", extra={"cantidad": scheduled})
            finally:
                db.close()
        except Exception as exc:
            logger.error("Error en ciclo del scheduler", extra={"error": type(exc).__name__})
        time.sleep(interval_seconds)


if __name__ == "__main__":
    run_forever()
