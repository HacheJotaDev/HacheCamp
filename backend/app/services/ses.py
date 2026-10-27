"""Integración con Amazon SES (API v2, Boto3).

- Región obligatoria: us-east-2 (configurable).
- Envío con contenido MIME Raw: permite cabeceras List-Unsubscribe (RFC 8058),
  multipart/alternativo (HTML + texto plano) y control total de cabeceras.
- Consulta de cuotas reales de envío (cacheada en Redis).
- Reintentos con backoff exponencial ante throttling.
- Las credenciales llegan por entorno/secreto, nunca por código fuente.
"""
from __future__ import annotations

import json
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings
from app.core.logging_conf import get_logger
from app.workers.redis_client import get_redis

logger = get_logger("hj.ses")

QUOTA_CACHE_KEY = "hj:ses:quota"
QUOTA_CACHE_TTL = 600  # 10 minutos

# Errores de SES que justifican reintento (transitorios)
RETRYABLE_ERRORS = {"ThrottlingException", "TooManyRequestsException", "LimitExceededException",
                    "InternalServerError", "RequestTimeoutException", "ServiceUnavailable"}


class SESError(Exception):
    def __init__(self, kind: str, detail: str, retryable: bool = False):
        super().__init__(detail)
        self.kind = kind          # throttling | rechazado | error | configuracion
        self.detail = detail
        self.retryable = retryable


class SESNotConfigured(SESError):
    def __init__(self, detail: str = "SES no está configurado"):
        super().__init__("configuracion", detail, retryable=False)


def _client():
    """Crea el cliente SESv2. Las credenciales provienen del entorno o del host."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover
        raise SESNotConfigured("Boto3 no está instalado") from exc

    kwargs: Dict[str, Any] = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    # Si no hay credenciales explícitas, Boto3 usa su cadena habitual
    # (variables de entorno, perfil, rol IAM...).
    cfg = Config(
        retries={"max_attempts": settings.SES_RETRIES_MAX, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=30,
    )
    return boto3.client("sesv2", config=cfg, **kwargs)


def ses_configured() -> bool:
    """Indica si hay credenciales disponibles en la cadena de Boto3."""
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        return True
    try:
        _client().get_account()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Cuotas
# --------------------------------------------------------------------------- #


def get_sending_quota(force_refresh: bool = False) -> Dict[str, Any]:
    """Cuotas reales de SES con caché en Redis.

    Usa `GetAccount` (operación real de la API SESv2). El sdk no ofrece
    `GetAccountSendingCapabilities` en sesv2: llamarlo rompía la consulta
    en producción. `GetAccount` devuelve las cuotas anidadas:
      SendQuota: {Max24HourSend, SentLast24Hours, MaxSendRate}
    y `SendingEnabled` al nivel raíz.
    """
    r = get_redis()
    if not force_refresh:
        cached = r.get(QUOTA_CACHE_KEY)
        if cached:
            data = json.loads(cached)
            data["cacheado"] = True
            return data
    try:
        client = _client()
        cuenta = client.get_account()
        send_quota = cuenta.get("SendQuota") or {}
        quota = {
            "max_24h": float(send_quota.get("Max24HourSend", 0) or 0),
            "sent_last_24h": float(send_quota.get("SentLast24Hours", 0) or 0),
            "max_per_second": float(send_quota.get("MaxSendRate", 1) or 1),
            "reputacion": bool(cuenta.get("SendingEnabled", False)),
            "region": settings.AWS_REGION,
            "cacheado": False,
            "actualizado": int(time.time()),
        }
        r.set(QUOTA_CACHE_KEY, json.dumps(quota), ex=QUOTA_CACHE_TTL)
        return quota
    except SESNotConfigured:
        raise
    except Exception as exc:
        logger.warning("No se pudo consultar la cuota de SES: %s", type(exc).__name__)
        return {
            "max_24h": 0, "sent_last_24h": 0, "max_per_second": 0,
            "region": settings.AWS_REGION, "cacheado": False,
            "error": type(exc).__name__,
        }


def effective_rate_per_second() -> float:
    """Velocidad de envío efectiva: mínimo entre nuestra configuración y SES."""
    configured = settings.SEND_RATE_PER_SECOND
    ses_limit = settings.SES_MAX_PER_SECOND
    if ses_limit is None:
        try:
            ses_limit = get_sending_quota().get("max_per_second") or 14
        except Exception:
            ses_limit = 14
    return max(0.1, min(float(configured), float(ses_limit)))


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #


def build_raw_message(
    *,
    from_email: str,
    from_name: Optional[str],
    to_email: str,
    subject: str,
    html: str,
    text: str,
    reply_to: Optional[str] = None,
    unsubscribe_post_url: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
    campaign_id: Optional[str] = None,
    message_id: Optional[str] = None,
    category: str = "promocional",
) -> EmailMessage:
    """Construye el mensaje MIME multipart/alternativo con cabeceras de baja."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name or "", from_email)) if from_name else from_email
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain=from_email.split("@")[-1])

    if category == "promocional" and unsubscribe_url:
        # RFC 2369 + RFC 8058 (baja en un clic)
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>, <{unsubscribe_post_url or unsubscribe_url}>"
        if unsubscribe_post_url:
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg["Feedback-ID"] = f"{campaign_id or 's'}:hj-campaigns"

    if campaign_id and message_id:
        msg["X-HJ-Campaign"] = campaign_id
        msg["X-HJ-Message"] = message_id

    msg.set_content(text or " ")
    msg.add_alternative(html or "<p></p>", subtype="html")
    return msg


def send_raw_email(msg: EmailMessage) -> str:
    """Envía por SES v2 (contenido Raw). Devuelve el MessageId de SES.

    Lanza SESError con clasificación del fallo.
    """
    if not ses_configured() and settings.ENV == "production":
        # En producción sin credenciales se aborta con error claro;
        # en desarrollo se permite continuar (las pruebas usan mocks).
        raise SESNotConfigured(
            "No hay credenciales de AWS: configure AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "o la cadena de credenciales del host"
        )
    try:
        client = _client()
        kwargs: Dict[str, Any] = {
            "FromEmailAddress": msg["From"],
            "Destination": {"ToAddresses": [msg["To"]]},
            "Content": {"Raw": {"Data": msg.as_bytes()}},
        }
        if settings.SES_CONFIGURATION_SET:
            kwargs["ConfigurationSetName"] = settings.SES_CONFIGURATION_SET
        if settings.SES_CONTACT_LIST:
            kwargs["ListManagementOptions"] = {
                "ContactListName": settings.SES_CONTACT_LIST,
            }
        resp = client.send_email(**kwargs)
        return resp["MessageId"]
    except Exception as exc:
        name = type(exc).__name__
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
        detail_code = code or name
        if detail_code in RETRYABLE_ERRORS or "Throttling" in detail_code:
            raise SESError("throttling", f"SES limitando envío: {detail_code}", retryable=True)
        if "MessageRejected" in detail_code or "MessageRejected" in name:
            raise SESError("rechazado", f"SES rechazó el mensaje: {detail_code}")
        if "AccessDenied" in detail_code or "Unauthorized" in detail_code:
            raise SESError("configuracion", f"Sin permisos IAM para SES: {detail_code}")
        raise SESError("error", f"Fallo de SES: {detail_code}")


def send_test_email(*, to_email: str, subject: str, html: str, text: str,
                    from_email: str, from_name: str, reply_to: Optional[str] = None) -> str:
    msg = build_raw_message(
        from_email=from_email, from_name=from_name, to_email=to_email,
        subject=subject, html=html, text=text, reply_to=reply_to,
        category="transaccional",
    )
    return send_raw_email(msg)


def list_verified_identities() -> Tuple[list, Optional[str]]:
    """Identidades verificadas en SES (para diagnóstico)."""
    try:
        client = _client()
        resp = client.list_email_identities(PageSize=50)
        identities = [
            {"identity": i["IdentityName"], "tipo": i["IdentityType"], "verificado": i.get("SendingEnabled", False)}
            for i in resp.get("Identities", [])
        ]
        return identities, None
    except Exception as exc:
        return [], type(exc).__name__
