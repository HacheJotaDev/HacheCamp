"""Registro de auditoría de acciones administrativas."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    ip: Optional[str] = None,
    commit: bool = False,
) -> None:
    """Registra una acción en el log de auditoría.

    Nunca incluir contraseñas, tokens ni secretos en `detail`.
    """
    entry = AuditLog(
        user_id=user_id,
        user_email=user_email,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
        ip=ip,
    )
    db.add(entry)
    if commit:
        db.commit()
