"""Dependencias FastAPI: autenticación, roles y CSRF."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.core.db import get_db
from app.models import User


def _load_user(db: Session, user_id: str) -> Optional[User]:
    return db.get(User, user_id)


def get_current_session(request: Request) -> Dict[str, Any]:
    token = request.cookies.get(security.SESSION_COOKIE)
    session = security.get_session(token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return session


def get_current_user(
    request: Request,
    session: Dict[str, Any] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> User:
    user = _load_user(db, session["user_id"])
    if not user or not user.is_active:
        security.destroy_session(request.cookies.get(security.SESSION_COOKIE, ""))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida")
    security.check_csrf(request, session)
    request.state.user = user
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Se requiere rol de administrador")
    return user


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    token = request.cookies.get(security.SESSION_COOKIE)
    session = security.get_session(token)
    if not session:
        return None
    return _load_user(db, session["user_id"])
