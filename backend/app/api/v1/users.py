"""Gestión de usuarios (solo administradores) y auditoría."""
from __future__ import annotations

import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AuditLog, User
from app.schemas import AdminPasswordReset, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["Usuarios"])


@router.get("", response_model=List[UserOut], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db), q: Optional[str] = None):
    query = db.query(User)
    if q:
        query = query.filter(or_(User.email.ilike(f"%{q}%"), User.name.ilike(f"%{q}%")))
    return query.order_by(User.created_at).all()


@router.post("", response_model=UserOut, status_code=201, dependencies=[Depends(require_admin)])
def create_user(payload: UserCreate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un usuario con ese correo")
    issues = security.password_issues(payload.password)
    if issues:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="; ".join(issues))
    user = User(
        email=payload.email, name=payload.name, role=payload.role,
        password_hash=security.hash_password(payload.password),
        must_change_password=False,
    )
    db.add(user)
    audit(db, "usuario_creado", user_id=admin.id, user_email=admin.email,
          entity_type="user", entity_id=user.id, detail={"rol": user.role, "correo": user.email[:3] + "***"},
          ip=security.client_ip(request))
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut, dependencies=[Depends(require_admin)])
def update_user(user_id: str, payload: UserUpdate, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    changes = {}
    if payload.name is not None:
        user.name = payload.name
        changes["nombre"] = True
    if payload.role is not None:
        if user.id == admin.id and payload.role != "admin":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puede quitarse su propio rol de administrador")
        user.role = payload.role
        changes["rol"] = payload.role
    if payload.is_active is not None:
        if user.id == admin.id and not payload.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puede desactivar su propia cuenta")
        user.is_active = payload.is_active
        if not payload.is_active:
            security.revoke_user_sessions(user.id)
        changes["activo"] = payload.is_active
    audit(db, "usuario_actualizado", user_id=admin.id, user_email=admin.email,
          entity_type="user", entity_id=user.id, detail=changes,
          ip=security.client_ip(request))
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/password", response_model=UserOut, dependencies=[Depends(require_admin)])
def reset_user_password(user_id: str, payload: AdminPasswordReset, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Restablece la contraseña de un usuario y revoca sus sesiones."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    issues = security.password_issues(payload.new_password)
    if issues:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="; ".join(issues))
    user.password_hash = security.hash_password(payload.new_password)
    user.must_change_password = True
    security.revoke_user_sessions(user.id)
    audit(db, "contrasena_restablecida", user_id=admin.id, user_email=admin.email,
          entity_type="user", entity_id=user.id, ip=security.client_ip(request))
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204, dependencies=[Depends(require_admin)])
def deactivate_user(user_id: str, request: Request, db: Session = Depends(get_db),
                    admin: User = Depends(require_admin)):
    """Desactiva (no elimina) un usuario para preservar la trazabilidad."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puede desactivar su propia cuenta")
    user.is_active = False
    security.revoke_user_sessions(user.id)
    audit(db, "usuario_desactivado", user_id=admin.id, user_email=admin.email,
          entity_type="user", entity_id=user.id, ip=security.client_ip(request))
    db.commit()
