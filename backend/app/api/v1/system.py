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


@router.get("/ses/rendimiento", dependencies=[Depends(get_current_user)])
@router.get("/system/ses/rendimiento", dependencies=[Depends(get_current_user)])  # alias documentado
def ses_rendimiento(db: Session = Depends(get_db)):
    """Métricas REALES de rendimiento de envío (nada de valores fijos).

    - Mensajes aceptados por SES por segundo/minuto (eventos envio_local).
    - Entregas confirmadas por SES en la última ventana.
    - Velocidad configurada, límite de SES y velocidad efectiva.
    - Cuota móvil 24 h con datos de AWS.
    - Pendientes en cola Redis y en base de datos, reintentos y errores.
    - Progreso real de las campañas activas.
    """
    from datetime import timedelta

    from sqlalchemy import func

    from app.core.db import utcnow
    from app.models import Campaign, CampaignMessage, SesEvent
    from app.services import queue as send_queue

    ahora = utcnow()
    ventana_60s = ahora - timedelta(seconds=60)
    ventana_60m = ahora - timedelta(minutes=60)
    ventana_24h = ahora - timedelta(hours=24)

    def _conteo(tipo: str, desde) -> int:
        return (
            db.query(func.count(SesEvent.id))
            .filter(SesEvent.type == tipo, SesEvent.occurred_at >= desde)
            .scalar() or 0
        )

    aceptados_60s = _conteo("envio_local", ventana_60s)
    aceptados_60m = _conteo("envio_local", ventana_60m)
    entregados_60m = _conteo("delivery", ventana_60m)

    try:
        cuota = ses_service.get_sending_quota()
    except Exception as exc:
        cuota = {"error": type(exc).__name__}
    max_24h = float(cuota.get("max_24h") or 0)
    enviados_24h = float(cuota.get("sent_last_24h") or 0)
    cuota = {**cuota, "restante_24h": max(0, max_24h - enviados_24h),
             "usado_pct_24h": round(enviados_24h / max_24h * 100, 1) if max_24h else None}

    pendientes_db = (
        db.query(func.count(CampaignMessage.id))
        .filter(CampaignMessage.status == "pendiente").scalar() or 0
    )
    errores_24h = (
        db.query(func.count(CampaignMessage.id))
        .filter(CampaignMessage.status == "error",
                CampaignMessage.updated_at >= ventana_24h).scalar() or 0
    )
    inciertos = (
        db.query(func.count(CampaignMessage.id))
        .filter(CampaignMessage.status == "incierto").scalar() or 0
    )

    # Progreso real de campañas activas (en_cola / enviando)
    activas = (
        db.query(Campaign)
        .filter(Campaign.status.in_(["en_cola", "enviando"]))
        .order_by(Campaign.created_at.desc()).limit(10).all()
    )
    campanas: List[Dict[str, Any]] = []
    for c in activas:
        estados = dict(
            db.query(CampaignMessage.status, func.count(CampaignMessage.id))
            .filter(CampaignMessage.campaign_id == c.id)
            .group_by(CampaignMessage.status).all()
        )
        total = sum(estados.values())
        campanas.append({
            "id": c.id, "nombre": c.name, "estado": c.status,
            "total": total,
            "pendientes": estados.get("pendiente", 0),
            "aceptados": estados.get("aceptado", 0),
            "entregados": estados.get("entregado", 0),
            "errores": estados.get("error", 0),
            "reintentos": estados.get("rebote_temporal", 0) + estados.get("incierto", 0),
            "progreso_pct": round((total - estados.get("pendiente", 0)) / total * 100, 1) if total else 0.0,
        })

    try:
        efectiva = ses_service.effective_rate_per_second()
    except Exception:
        efectiva = None
    return {
        "aceptados_60s": aceptados_60s,
        "por_segundo": round(aceptados_60s / 60.0, 1),
        "por_minuto": aceptados_60m,
        "entregados_60m": entregados_60m,
        "velocidad": {
            "configurada": settings.SEND_RATE_PER_SECOND,
            "limite_ses": settings.SES_MAX_PER_SECOND,
            "efectiva": efectiva,
        },
        "cuota_24h": cuota,
        "pendientes": {
            "cola_redis": send_queue.outbound_count(),
            "reintentos_redis": send_queue.pending_retries_count(),
            "base_datos": pendientes_db,
        },
        "reintentos_y_errores": {
            "reintentos_programados": send_queue.pending_retries_count(),
            "errores_24h": errores_24h,
            "incierto_total": inciertos,
        },
        "campanas_activas": campanas,
    }


@router.get("/ses/diagnostico", dependencies=[Depends(require_admin)])
@router.get("/system/ses/diagnostico", dependencies=[Depends(require_admin)])  # alias documentado
def ses_diagnostico(db: Session = Depends(get_db)):
    """Diagnóstico del pipeline de eventos SES (SNS/SQS) y del consumidor.

    Responde a la pregunta "¿por qué tengo aceptados y casi ninguna entrega?":
    - configuration_set realmente aplicado a los envíos (None = SES NO emite eventos).
    - Consumidor SQS vivo (latido) y estadísticas del último lote.
    - Profundidad de la cola SQS y de su DLQ.
    - Eventos registrados en BD por tipo y motivo de los descartes.
    - Distribución de estados de mensajes (aceptado ≠ entregado).
    """
    from datetime import timedelta

    from sqlalchemy import func

    from app.core.db import utcnow
    from app.models import CampaignMessage, SesEvent
    from app.services import sqs_events as sqs_service
    from app.workers import heartbeat

    ahora = utcnow()

    # Consumidor SQS: latido (vivo/edad) + contadores del último lote
    _, edad = heartbeat.is_fresh("sqs_consumer", max_age_seconds=600)
    ultimo_lote: Dict[str, Any] = {}
    try:
        from app.workers.redis_client import get_redis
        import json as _json
        raw = get_redis().get("hj:hb:sqs_consumer")
        if raw:
            ultimo_lote = {k: v for k, v in _json.loads(raw).items() if k in
                           ("recibidos", "procesados", "duplicados", "sns_acks",
                            "descartados", "errores", "ts")}
    except Exception:
        pass
    consumidor = {
        "vivo": edad >= 0 and edad < 600,
        "edad_ultimo_latido_s": None if edad < 0 else round(edad, 1),
        "ultimo_lote": ultimo_lote,
    }

    # Profundidad real de la cola SQS y de la DLQ (solo si está configurada)
    cola_sqs: Dict[str, Any] = {
        "habilitado": sqs_service.sqs_enabled(),
        "cola": (settings.AWS_SQS_EVENTS_QUEUE_URL or "").rsplit("/", 1)[-1] or None,
    }
    if sqs_service.sqs_enabled() and settings.ENV != "development":
        try:
            client = sqs_service._client()
            attrs = client.get_queue_attributes(
                QueueUrl=settings.AWS_SQS_EVENTS_QUEUE_URL, AttributeNames=["All"]
            ).get("Attributes", {})
            cola_sqs["mensajes_visibles"] = int(attrs.get("ApproximateNumberOfMessages", 0))
            cola_sqs["mensajes_en_proceso"] = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
            cola_sqs["mensajes_retrasados"] = int(attrs.get("ApproximateNumberOfMessagesDelayed", 0))
            import json as _json2
            redrive = _json2.loads(attrs.get("RedrivePolicy") or "{}")
            dlq_arn = redrive.get("deadLetterTargetArn")
            if dlq_arn:
                cola_sqs["dlq"] = dlq_arn.rsplit(":", 1)[-1]
                try:
                    dlq_url = client.get_queue_url(QueueName=dlq_arn.rsplit(":", 1)[-1])["QueueUrl"]
                    dattrs = client.get_queue_attributes(
                        QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
                    ).get("Attributes", {})
                    cola_sqs["dlq_mensajes"] = int(dattrs.get("ApproximateNumberOfMessages", 0))
                except Exception:
                    cola_sqs["dlq_mensajes"] = None
        except Exception as exc:
            cola_sqs["error"] = type(exc).__name__

    # Eventos registrados en BD por tipo (24 h y 7 días) y último recibido
    def _por_tipo(desde):
        filas = (
            db.query(SesEvent.type, func.count(SesEvent.id))
            .filter(SesEvent.occurred_at >= desde, SesEvent.type != "sqs_descartado")
            .group_by(SesEvent.type).all()
        )
        return {t: n for t, n in filas}

    ultimo = (
        db.query(SesEvent).order_by(SesEvent.occurred_at.desc()).first()
    )
    descartes = (
        db.query(SesEvent)
        .filter(SesEvent.type == "sqs_descartado")
        .order_by(SesEvent.occurred_at.desc()).limit(50).all()
    )
    eventos = {
        "por_tipo_24h": _por_tipo(ahora - timedelta(hours=24)),
        "por_tipo_7d": _por_tipo(ahora - timedelta(days=7)),
        "ultimo_evento": ultimo.occurred_at.isoformat() if ultimo else None,
        "descartes_recientes": [
            {"motivo": (d.payload or {}).get("motivo"),
             "lecturas": (d.payload or {}).get("lecturas"),
             "fecha": d.occurred_at.isoformat()}
            for d in descartes
        ],
    }

    # Distribución global de estados de mensajes: aceptado != entregado
    estados = dict(
        db.query(CampaignMessage.status, func.count(CampaignMessage.id))
        .group_by(CampaignMessage.status).all()
    )
    return {
        "configuration_set": settings.SES_CONFIGURATION_SET,
        "configuration_set_aplicado": bool(settings.SES_CONFIGURATION_SET),
        "consumidor": consumidor,
        "cola_sqs": cola_sqs,
        "eventos": eventos,
        "mensajes_por_estado": estados,
        "nota": ("Sin configuration set los envíos NO generan eventos de SES "
                 "(entregas/rebotes/quejas nunca llegarán).") if not settings.SES_CONFIGURATION_SET else None,
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
