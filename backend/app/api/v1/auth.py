"""Autenticación: inicio/cierre de sesión, MFA TOTP, sesiones, cambio de clave.

Protecciones implementadas:
- Respuestas genéricas ante credenciales inválidas (anti-enumeración).
- Bloqueo temporal tras intentos fallidos (por correo e IP).
- Segundo paso TOTP para administradores + códigos de recuperación.
- Tokens MFA temporales de un solo uso con expiración de 5 minutos.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.deps import get_current_session, get_current_user, require_admin
from app.core.logging_conf import get_logger
from app.models import User
from app.schemas import (
    LoginRequest,
    MFAEnableRequest,
    MFATokenResponse,
    MFASetupResponse,
    MFAVerifyRequest,
    PasswordChange,
    SessionOut,
    TokenResponse,
    UserOut,
)
from app.workers.redis_client import get_redis

logger = get_logger("hj.auth")
router = APIRouter(prefix="/auth", tags=["Autenticación"])

MFA_TOKEN_TTL = 300  # 5 minutos


def _mfa_token_key(token: str) -> str:
    return f"hj:mfa:{token}"


@router.post("/login", response_model=TokenResponse | MFATokenResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = security.client_ip(request)
    ua = request.headers.get("user-agent", "")

    # Bloqueo por fuerza bruta
    locked = security.login_locked(payload.email, ip)
    if locked:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos fallidos. Espere {max(locked // 60, 1)} minuto(s).",
        )

    user = db.query(User).filter(User.email == payload.email).first()
    # Verificación uniforme para no filtrar existencia de cuentas
    password_ok = security.verify_password(payload.password, user.password_hash) if user else False
    if not user or not user.is_active or not password_ok:
        security.register_login_failure(payload.email, ip)
        audit(db, "login_fallido", detail={"correo": payload.email[:3] + "***"}, ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    # MFA para administradores (o para cualquier usuario con TOTP activo)
    if user.totp_enabled or (user.role == "admin" and settings.MFA_REQUIRED_FOR_ADMIN):
        if not user.totp_enabled:
            # Administrador sin MFA configurado: exige configurarlo (sin sesión)
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="El administrador debe configurar MFA. Use la CLI de bootstrap.",
            )
        mfa_token = secrets.token_urlsafe(24)
        get_redis().set(_mfa_token_key(mfa_token), user.id, ex=MFA_TOKEN_TTL)
        return MFATokenResponse(mfa_token=mfa_token)

    return _create_login_session(user, request, response, db, ip, ua)


@router.post("/login/mfa", response_model=TokenResponse)
def login_mfa(payload: MFAVerifyRequest, request: Request, response: Response,
              db: Session = Depends(get_db)):
    ip = security.client_ip(request)
    r = get_redis()
    key = _mfa_token_key(payload.mfa_token)
    user_id = r.get(key)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token MFA inválido o expirado")

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    code = payload.code.strip().replace(" ", "")
    verified = False
    used_recovery = False
    if pyotp.TOTP(user.totp_secret or "").verify(code, valid_window=1):
        verified = True
    elif user.recovery_codes:
        for hashed in list(user.recovery_codes):
            if security.verify_password(code, hashed):
                verified = True
                used_recovery = True
                user.recovery_codes.remove(hashed)
                break
    if not verified:
        r.delete(key)
        security.register_login_failure(user.email, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Código inválido")

    r.delete(key)
    if used_recovery:
        audit(db, "mfa_recuperacion_usada", user_id=user.id, user_email=user.email, ip=ip)
        db.commit()
    return _create_login_session(user, request, response, db, ip, request.headers.get("user-agent", ""))


def _create_login_session(user, request, response, db, ip: str, ua: str) -> TokenResponse:
    token, csrf = security.create_session(user.id, user.email, user.role, ip=ip, user_agent=ua)
    security.set_session_cookie(response, token)
    security.reset_login_failures(user.email)
    user.last_login_at = utcnow()
    db.commit()
    audit(db, "login", user_id=user.id, user_email=user.email, ip=ip)
    db.commit()
    return TokenResponse(csrf_token=csrf, user=UserOut.model_validate(user))


@router.post("/logout")
def logout(request: Request, response: Response,
           session: Dict[str, Any] = Depends(get_current_session)):
    security.check_csrf(request, session)
    token = request.cookies.get(security.SESSION_COOKIE, "")
    if token:
        security.destroy_session(token)
    security.clear_session_cookie(response)
    return {"detalle": "Sesión cerrada"}


@router.get("/me", response_model=TokenResponse)
def me(request: Request, session: Dict[str, Any] = Depends(get_current_session),
       db: Session = Depends(get_db)):
    """Devuelve el usuario actual + token CSRF de la sesión (para refresco)."""
    user = db.get(User, session["user_id"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida")
    return TokenResponse(csrf_token=session["csrf"], user=UserOut.model_validate(user))


@router.post("/mfa/setup", response_model=MFASetupResponse)
def mfa_setup(user: User = Depends(get_current_user)):
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA ya está activado")
    secret = pyotp.random_base32()
    otpauth = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.email, issuer_name="HJ Campaigns"
    )
    return MFASetupResponse(secret=secret, otpauth_url=otpauth)


@router.post("/mfa/enable")
def mfa_enable(payload: MFAEnableRequest, request: Request,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA ya está activado")
    totp = pyotp.TOTP(payload.secret)
    if not totp.verify(payload.code.strip().replace(" ", ""), valid_window=1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Código inválido")
    user.totp_secret = payload.secret
    user.totp_enabled = True
    # Genera 8 códigos de recuperación de un solo uso
    recovery_codes = [secrets.token_hex(5) for _ in range(8)]
    user.recovery_codes = [security.hash_password(c) for c in recovery_codes]
    audit(db, "mfa_activado", user_id=user.id, user_email=user.email,
          ip=security.client_ip(request))
    db.commit()
    # Los códigos en claro se muestran UNA sola vez
    return {"detalle": "MFA activado", "codigos_recuperacion": recovery_codes}


@router.post("/mfa/disable", dependencies=[Depends(require_admin)])
def mfa_disable(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                request: Request = None):
    user.totp_enabled = False
    user.totp_secret = None
    user.recovery_codes = None
    audit(db, "mfa_desactivado", user_id=user.id, user_email=user.email)
    db.commit()
    return {"detalle": "MFA desactivado"}


@router.post("/password")
def change_password(payload: PasswordChange, request: Request,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not security.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Contraseña actual incorrecta")
    issues = security.password_issues(payload.new_password)
    if issues:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="; ".join(issues))
    user.password_hash = security.hash_password(payload.new_password)
    user.must_change_password = False
    # Revoca todas las demás sesiones por seguridad
    security.revoke_user_sessions(user.id)
    audit(db, "cambio_contrasena", user_id=user.id, user_email=user.email,
          ip=security.client_ip(request))
    db.commit()
    return {"detalle": "Contraseña actualizada. Vuelva a iniciar sesión."}


@router.get("/sessions", response_model=List[SessionOut])
def list_sessions(session: Dict[str, Any] = Depends(get_current_session)):
    """Sesiones activas del usuario actual."""
    result = []
    r = get_redis()
    for key in r.scan_iter("hj:sess:*"):
        raw = r.get(key)
        if not raw:
            continue
        data = __import__("json").loads(raw)
        if data.get("user_id") == session["user_id"]:
            result.append(SessionOut(
                created_at=data.get("created_at", 0),
                last_seen=data.get("last_seen", 0),
                ip=data.get("ip", ""),
            ))
    return result


@router.post("/sessions/revoke")
def revoke_all_sessions(request: Request, session: Dict[str, Any] = Depends(get_current_session),
                        db: Session = Depends(get_db)):
    security.check_csrf(request, session)
    count = security.revoke_user_sessions(session["user_id"])
    audit(db, "revocacion_sesiones", user_id=session["user_id"],
          detail={"cantidad": count})
    db.commit()
    return {"detalle": f"{count} sesión(es) revocada(s). Inicie sesión de nuevo."}
