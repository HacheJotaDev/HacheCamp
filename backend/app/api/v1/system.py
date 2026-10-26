"""Sistema: salud, estado de SES, auditoría y ajustes de aplicación."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AppSetting, AuditLog, User
from app.services import ses as ses_service
from app.workers.redis_client import redis_ok

router = APIRouter(tags=["Sistema"])

# Claves de ajustes editables desde el panel
EDITABLE_SETTINGS = {
    "default_from_name": {"tipo": "texto", "descripcion": "Nombre de remitente por defecto"},
    "default_from_email": {"tipo": "correo", "descripcion": "Remitente por defecto"},
    "default_reply_to": {"tipo": "correo", "descripcion": "Responder a (por defecto)"},
}


@router.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    """Healthcheck para Docker/K8s: no requiere autenticación."""
    checks: Dict[str, Any] = {"api": "ok"}
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
    checks["redis"] = "ok" if redis_ok() else "error"
    ses_ok = True
    try:
        ses_service.get_sending_quota()
    except Exception:
        ses_ok = False
    checks["ses"] = "ok" if ses_ok else "sin_verificar"
    overall = "degradado" if "error" in checks.values() else "ok"
    return {"estado": overall, "comprobaciones": checks, "version": "1.0.0"}


@router.get("/ses/status", dependencies=[Depends(get_current_user)])
def ses_status(db: Session = Depends(get_db)):
    """Estado de SES: cuotas reales, identidad del remitente y configuración."""
    identities, err = ses_service.list_verified_identities() if settings.ENV != "development" else ([], "desarrollo")
    try:
        quota = ses_service.get_sending_quota()
    except Exception as exc:
        quota = {"error": type(exc).__name__, "region": settings.AWS_REGION}
    return {
        "region": settings.AWS_REGION,
        "cuota": quota,
        "identidades": identities,
        "error_identidades": err,
        "remitente_inicial": "notificaciones@leisureparts4u.store",
        "configuration_set": settings.SES_CONFIGURATION_SET,
    }


@router.get("/audit", response_model=List[dict], dependencies=[Depends(require_admin)])
def list_audit(db: Session = Depends(get_db), page: int = Query(1, ge=1),
               per_page: int = Query(50, ge=1, le=200), accion: Optional[str] = None):
    query = db.query(AuditLog)
    if accion:
        query = query.filter(AuditLog.action == accion)
    total = query.count()
    items = query.order_by(AuditLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return [
        {
            "id": a.id, "usuario": a.user_email, "accion": a.action,
            "entidad": a.entity_type, "entidad_id": a.entity_id,
            "detalle": a.detail, "ip": a.ip, "fecha": a.created_at.isoformat(),
        }
        for a in items
    ]


@router.get("/settings", dependencies=[Depends(get_current_user)])
def get_settings_panel(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stored = {s.key: s.value for s in db.query(AppSetting).all()}
    return {
        "ajustes": {key: (stored.get(key) or {}).get("valor") for key in EDITABLE_SETTINGS},
        "catalogo": EDITABLE_SETTINGS,
        "public_base_url": settings.PUBLIC_BASE_URL,
        "webhook_sns": f"{settings.API_BASE_URL}/api/v1/webhooks/sns?token=***",
    }


@router.put("/settings/{key}", dependencies=[Depends(require_admin)])
def update_setting(key: str, payload: Dict[str, Any], request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if key not in EDITABLE_SETTINGS:
        from fastapi import HTTPException, status as http_status
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Ajuste desconocido")
    value = payload.get("value")
    row = db.get(AppSetting, key)
    if row:
        row.value = {"valor": value}
    else:
        db.add(AppSetting(key=key, value={"valor": value}))
    audit(db, "ajuste_actualizado", user_id=admin.id, user_email=admin.email,
          entity_type="setting", entity_id=key, ip=security.client_ip(request))
    db.commit()
    return {"detalle": "Ajuste actualizado"}
