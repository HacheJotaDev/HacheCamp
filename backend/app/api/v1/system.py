"""Sistema: salud, estado de SES, auditoría y ajustes de aplicación."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AppSetting, AuditLog, User
from app.services import mail_config
from app.services import ses as ses_service
from app.workers.redis_client import redis_ok

router = APIRouter(tags=["Sistema"])

# Claves de ajustes editables desde el panel
EDITABLE_SETTINGS = {
    "default_from_name": {"tipo": "texto", "descripcion": "Nombre de remitente por defecto"},
    "default_from_email": {"tipo": "correo", "descripcion": "Remitente por defecto"},
    "default_subject": {"tipo": "texto", "descripcion": "Asunto predeterminado de campañas"},
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
@router.get("/system/ses/status", dependencies=[Depends(get_current_user)])  # alias documentado
def ses_status(db: Session = Depends(get_db)):
    """Estado de SES: cuotas dinámicas, reputación, identidades y configuración."""
    identities, err = ses_service.list_verified_identities() if settings.ENV != "development" else ([], "desarrollo")
    try:
        quota = ses_service.get_sending_quota()
    except Exception as exc:
        quota = {"error": type(exc).__name__, "region": settings.AWS_REGION}
    # Reputación y estado de la cuenta (consulta dinámica; nunca valores fijos)
    reputacion: Dict[str, Any] = {"disponible": False}
    try:
        client = ses_service._client()
        cuenta = client.get_account()
        reputacion = {
            "disponible": True,
            "envio_habilitado": bool(cuenta.get("SendingEnabled", False)),
            "estado_aplicacion": cuenta.get("EnforcementStatus", ""),
            "modo_produccion": cuenta.get("EnforcementStatus") == "PROD",
            "zona_de_calentamiento": bool(cuenta.get("ProductionAccessDisabled", False) is False
                                          and cuenta.get("EnforcementStatus") not in ("PROD", "")),
        }
    except Exception as exc:
        reputacion["error"] = type(exc).__name__
    # Últimos errores de envío registrados (mensajes con estado error)
    from datetime import timedelta

    from sqlalchemy import func

    from app.core.db import utcnow
    from app.models import CampaignMessage
    errores_24h = (
        db.query(func.count(CampaignMessage.id))
        .filter(CampaignMessage.status == "error",
                CampaignMessage.updated_at >= utcnow() - timedelta(hours=24))
        .scalar() or 0
    )
    cola_sqs = settings.AWS_SQS_EVENTS_QUEUE_URL or ""
    return {
        "region": settings.AWS_REGION,
        "cuota": quota,
        "reputacion": reputacion,
        "identidades": identities,
        "error_identidades": err,
        "remitente_inicial": "notificaciones@leisureparts4u.store",
        "configuration_set": settings.SES_CONFIGURATION_SET,
        "eventos_sqs": {
            "habilitado": bool(cola_sqs),
            # No se expone la URL completa (contiene el ID de la cuenta AWS)
            "cola": cola_sqs.rsplit("/", 1)[-1] if cola_sqs else None,
        },
        "errores_envio_24h": errores_24h,
    }


@router.get("/audit", response_model=List[dict], dependencies=[Depends(require_admin)])
@router.get("/system/audit", response_model=List[dict], dependencies=[Depends(require_admin)])  # alias documentado
def list_audit(db: Session = Depends(get_db), page: int = Query(1, ge=1),
               per_page: int = Query(50, ge=1, le=200), accion: Optional[str] = None):
    query = db.query(AuditLog)
    if accion:
        query = query.filter(AuditLog.action == accion)
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
@router.get("/system/settings", dependencies=[Depends(get_current_user)])  # alias documentado
def get_settings_panel(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stored = {s.key: s.value for s in db.query(AppSetting).all()}
    return {
        "ajustes": {key: (stored.get(key) or {}).get("valor") for key in EDITABLE_SETTINGS},
        "catalogo": EDITABLE_SETTINGS,
        "public_base_url": settings.PUBLIC_BASE_URL,
        "webhook_sns": f"{settings.API_BASE_URL}/api/v1/webhooks/sns?token=***",
    }


@router.put("/settings/{key}", dependencies=[Depends(require_admin)])
@router.put("/system/settings/{key}", dependencies=[Depends(require_admin)])  # alias documentado
def update_setting(key: str, payload: Dict[str, Any], request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if key not in EDITABLE_SETTINGS:
        from fastapi import HTTPException, status as http_status
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Ajuste desconocido")
    value = payload.get("value")
    # Las claves de correo comparten las mismas reglas estrictas que
    # /system/mail-config (dominio exclusivo y anti inyección de cabeceras).
    if key in mail_config.VALIDADORES:
        try:
            value = mail_config.VALIDADORES[key](value)
        except mail_config.MailConfigError as exc:
            from fastapi import HTTPException, status as http_status
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(exc))
    row = db.get(AppSetting, key)
    if row:
        row.value = {"valor": value}
    else:
        db.add(AppSetting(key=key, value={"valor": value}))
    audit(db, "ajuste_actualizado", user_id=admin.id, user_email=admin.email,
          entity_type="setting", entity_id=key, ip=security.client_ip(request))
    db.commit()
    return {"detalle": "Ajuste actualizado"}


@router.get("/system/mail-config", dependencies=[Depends(get_current_user)])
def get_mail_config(db: Session = Depends(get_db), response: Response = None):
    """Configuración de correo efectiva (lectura para cualquier usuario autenticado).

    La usan el apartado 'Configuración de correo', los formularios de campañas
    y los envíos de prueba para mostrar los valores globales reales. Sin caché
    HTTP: guardar un cambio debe reflejarse al reabrir cualquier formulario.
    """
    response.headers["Cache-Control"] = "no-store"
    guardado = mail_config.leer_guardado(db)
    efectiva = mail_config.config_efectiva(db)
    return {
        "from_name": efectiva["from_name"],
        "from_email": efectiva["from_email"],
        "subject": efectiva["subject"],
        "dominio": mail_config.MAIL_DOMAIN,
        "guardado": guardado,
    }


@router.put("/system/mail-config")
def update_mail_config(payload: Dict[str, Any], request: Request,
                       db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Guarda remitente y asunto predeterminados (solo administradores).

    Reutiliza la tabla app_settings; valida dominio del remitente y ausencia
    de saltos de línea (anti inyección de cabeceras) en todos los campos.
    """
    from fastapi import HTTPException, status as http_status

    try:
        valores = mail_config.guardar_config(
            db,
            from_name=payload.get("from_name", ""),
            from_email=payload.get("from_email", ""),
            subject=payload.get("subject", ""),
        )
    except mail_config.MailConfigError as exc:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(exc))
    audit(db, "config_correo_actualizada", user_id=admin.id, user_email=admin.email,
          entity_type="setting", entity_id="mail-config", ip=security.client_ip(request))
    db.commit()
    return {"detalle": "Configuración de correo guardada", "config": valores}
