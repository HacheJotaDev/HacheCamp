"""Configuración centralizada de correo: remitente y asunto predeterminados.

Persiste los valores en la tabla existente `app_settings` (clave-valor JSON),
reutilizando el sistema de ajustes de la aplicación; NO requiere migraciones.

Claves (mismas que el catálogo EDITABLE_SETTINGS del panel):
- default_from_name   Nombre del remitente (ej.: "HJ Campaigns")
- default_from_email  Correo del remitente (ej.: notificaciones@leisureparts4u.store)
- default_subject     Asunto predeterminado (ej.: "Novedades especiales para ti")

Reglas de validación (aplicadas en backend y replicadas en frontend):
- El correo del remitente debe pertenecer exclusivamente al dominio verificado
  en SES (MAIL_DOMAIN). Cualquier otro dominio se rechaza.
- Sin saltos de línea ni caracteres de control: impide inyección de cabeceras
  (RFC 5322) tanto en el nombre como en el asunto.
- Longitudes acotadas según los límites de las columnas de `campaigns`.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import AppSetting

MAIL_DOMAIN = "leisureparts4u.store"
DEFAULT_FROM_NAME = "HJ Campaigns"
DEFAULT_FROM_EMAIL = f"notificaciones@{MAIL_DOMAIN}"

# Claves persistidas en app_settings (coinciden con EDITABLE_SETTINGS)
KEY_FROM_NAME = "default_from_name"
KEY_FROM_EMAIL = "default_from_email"
KEY_SUBJECT = "default_subject"
MAIL_KEYS = (KEY_FROM_NAME, KEY_FROM_EMAIL, KEY_SUBJECT)

# Salto de línea CR/LF: nunca admitido en cabeceras controladas por el usuario
_CRLF_RE = re.compile(r"[\r\n]")
# Correo local razonable + dominio EXACTO del proyecto (case-insensitive)
_FROM_EMAIL_RE = re.compile(
    rf"^[A-Za-z0-9._%+\-]+@{re.escape(MAIL_DOMAIN)}$", re.IGNORECASE
)


class MailConfigError(ValueError):
    """Valor inválido en la configuración de correo."""


def _limpiar(valor: Optional[str]) -> str:
    return (valor or "").strip()


def _rechazar_controles(valor: str, campo: str) -> None:
    if _CRLF_RE.search(valor):
        raise MailConfigError(f"El campo '{campo}' no puede contener saltos de línea")
    if any(ord(c) < 32 or c == "\x7f" for c in valor):
        raise MailConfigError(f"El campo '{campo}' contiene caracteres no permitidos")


def validar_from_name(valor: Optional[str]) -> str:
    """Nombre del remitente: 2-120 caracteres, sin saltos de línea ni controles."""
    v = _limpiar(valor)
    _rechazar_controles(v, "nombre del remitente")
    if not (2 <= len(v) <= 120):
        raise MailConfigError(
            "El nombre del remitente debe tener entre 2 y 120 caracteres"
        )
    return v


def validar_from_email(valor: Optional[str]) -> str:
    """Correo del remitente: formato válido y dominio exclusivo del proyecto."""
    v = _limpiar(valor).lower()
    _rechazar_controles(v, "correo del remitente")
    if not _FROM_EMAIL_RE.match(v):
        raise MailConfigError(
            f"El correo del remitente debe pertenecer al dominio @{MAIL_DOMAIN} "
            f"(verificado en Amazon SES)"
        )
    if len(v) > 255:
        raise MailConfigError("El correo del remitente es demasiado largo")
    return v


def validar_subject(valor: Optional[str]) -> str:
    """Asunto: 1-255 caracteres, sin saltos de línea ni controles."""
    v = _limpiar(valor)
    _rechazar_controles(v, "asunto")
    if not (1 <= len(v) <= 255):
        raise MailConfigError("El asunto debe tener entre 1 y 255 caracteres")
    return v


VALIDADORES = {
    KEY_FROM_NAME: validar_from_name,
    KEY_FROM_EMAIL: validar_from_email,
    KEY_SUBJECT: validar_subject,
}


def leer_guardado(db: Session) -> Dict[str, Optional[str]]:
    """Valores guardados sin aplicar valores por defecto (None si no hay)."""
    filas = db.query(AppSetting).filter(AppSetting.key.in_(MAIL_KEYS)).all()
    stored = {s.key: (s.value or {}).get("valor") for s in filas}
    return {k: stored.get(k) for k in MAIL_KEYS}


def config_efectiva(db: Session) -> Dict[str, str]:
    """Valores efectivos para precargar formularios y envíos de prueba.

    - from_name / from_email: con fallback a las constantes del proyecto
      (mismo comportamiento que tenía la creación de campañas).
    - subject: vacío si no se configuró, para no inventar asuntos.
    """
    guardado = leer_guardado(db)
    return {
        "from_name": guardado[KEY_FROM_NAME] or DEFAULT_FROM_NAME,
        "from_email": guardado[KEY_FROM_EMAIL] or DEFAULT_FROM_EMAIL,
        "subject": guardado[KEY_SUBJECT] or "",
    }


def guardar_config(db: Session, *, from_name: str, from_email: str,
                   subject: str) -> Dict[str, str]:
    """Valida y persiste los tres valores de forma atómica (sin commitear)."""
    valores = {
        KEY_FROM_NAME: validar_from_name(from_name),
        KEY_FROM_EMAIL: validar_from_email(from_email),
        KEY_SUBJECT: validar_subject(subject),
    }
    for clave, valor in valores.items():
        fila = db.get(AppSetting, clave)
        if fila:
            fila.value = {"valor": valor}
        else:
            db.add(AppSetting(key=clave, value={"valor": valor}))
    return {
        "from_name": valores[KEY_FROM_NAME],
        "from_email": valores[KEY_FROM_EMAIL],
        "subject": valores[KEY_SUBJECT],
    }
