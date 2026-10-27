"""Estadísticas reales para el dashboard."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import User
from app.services import ses as ses_service
from app.services.stats import daily_series, dashboard_summary, deliverability_summary, metrics_summary

router = APIRouter(prefix="/stats", tags=["Estadísticas"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    summary = dashboard_summary(db)
    metricas = metrics_summary(db)
    # Cuota SES (cacheada; no bloquea el panel si falla)
    try:
        quota = ses_service.get_sending_quota()
    except Exception:
        quota = {"error": "no_disponible", "region": ses_service.settings.AWS_REGION}
    return {"resumen": summary, "metricas": metricas, "cuota_ses": quota,
            "nota_aperturas": ("Las aperturas son aproximadas: bloqueadores de imágenes, "
                               "proxies y mecanismos de privacidad pueden distorsionarlas.")}


@router.get("/series")
def series(db: Session = Depends(get_db), user: User = Depends(get_current_user),
           dias: int = Query(30, ge=7, le=90), campana: Optional[str] = Query(None)):
    """Evolución diaria con filtro opcional por campaña."""
    return {"dias": dias, "campana": campana, "puntos": daily_series(db, dias, campaign_id=campana)}


@router.get("/metrics")
def metrics(db: Session = Depends(get_db), user: User = Depends(get_current_user),
            campana: Optional[str] = Query(None)):
    """Tasas con denominadores explícitos: global o por campaña."""
    return metrics_summary(db, campaign_id=campana)


@router.get("/deliverability")
def deliverability(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Entregabilidad: lo que SES confirma y lo que no puede saberse."""
    return deliverability_summary(db)
