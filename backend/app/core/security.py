"""Seguridad: hashing Argon2id, sesiones en Redis, CSRF y limitación de intentos.

- Sesiones: token opaco de 256 bits en cookie HttpOnly + SameSite=Lax.
  El estado de la sesión vive en Redis con TTL deslizante.
- CSRF: token por sesión enviado por el cliente en la cabecera X-CSRF-Token
  para todas las operaciones de mutación.
- Fuerza bruta: contadores por correo e IP en Redis con bloqueo temporal.
- Nunca se registran contraseñas, tokens ni secretos en los logs.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Response, status

from app.core.config import settings
from app.workers.redis_client import get_redis

ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=2,
    hash_len=32,
    salt_len=16,
)

SESSION_COOKIE = "hj_session"
CSRF_HEADER = "x-csrf-token"


# --------------------------------------------------------------------------- #
# Contraseñas
# --------------------------------------------------------------------------- #


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def password_issues(password: str) -> list[str]:
    """Validación de política de contraseñas. Devuelve lista de problemas."""
    issues: list[str] = []
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        issues.append(f"Debe tener al menos {settings.PASSWORD_MIN_LENGTH} caracteres")
    if not any(c.isdigit() for c in password):
        issues.append("Debe incluir al menos un número")
    if not any(c.isalpha() for c in password):
        issues.append("Debe incluir al menos una letra")
    return issues


# --------------------------------------------------------------------------- #
# Sesiones
# --------------------------------------------------------------------------- #


def _sess_key(token: str) -> str:
    return f"hj:sess:{token}"


def create_session(
    user_id: str,
    email: str,
    role: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[str, str]:
    """Crea la sesión. Devuelve (token, csrf_token)."""
    r = get_redis()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    data: Dict[str, Any] = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "csrf": csrf,
        "ip": ip or "",
        "ua": (user_agent or "")[:200],
        "created_at": int(__import__("time").time()),
        "last_seen": int(__import__("time").time()),
    }
    ttl = settings.SESSION_TTL_MINUTES * 60
    r.set(_sess_key(token), json.dumps(data), ex=ttl)
    return token, csrf


def get_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    r = get_redis()
    raw = r.get(_sess_key(token))
    if not raw:
        return None
    data = json.loads(raw)
    # TTL deslizante: renovar si ha pasado más de 15 minutos
    now = int(__import__("time").time())
    if now - data.get("last_seen", 0) > 900:
        data["last_seen"] = now
        ttl = settings.SESSION_TTL_MINUTES * 60
        r.set(_sess_key(token), json.dumps(data), ex=ttl)
    return data


def destroy_session(token: str) -> None:
    get_redis().delete(_sess_key(token))


def revoke_user_sessions(user_id: str) -> int:
    """Revoca todas las sesiones activas de un usuario."""
    r = get_redis()
    count = 0
    for key in r.scan_iter("hj:sess:*"):
        raw = r.get(key)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("user_id") == user_id:
            r.delete(key)
            count += 1
    return count


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.SESSION_TTL_MINUTES * 60,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/", domain=settings.COOKIE_DOMAIN)


# --------------------------------------------------------------------------- #
# Limitación de intentos de acceso
# --------------------------------------------------------------------------- #


def login_locked(identifier_email: str, ip: str) -> Optional[int]:
    """Devuelve los segundos restantes de bloqueo si está bloqueado; si no, None."""
    r = get_redis()
    ttl_e = r.ttl(f"hj:lfa:e:{identifier_email.lower()}")
    ttl_i = r.ttl(f"hj:lfa:i:{ip}")
    candidates = [t for t in (ttl_e, ttl_i) if t and t > 0]
    if not candidates:
        return None
    # ¿Supera el umbral de fallos?
    fails_e = int(r.get(f"hj:lfa:e:{identifier_email.lower()}") or 0)
    fails_i = int(r.get(f"hj:lfa:i:{ip}") or 0)
    if fails_e >= settings.LOGIN_MAX_ATTEMPTS_EMAIL or fails_i >= settings.LOGIN_MAX_ATTEMPTS_IP:
        return max(candidates)
    return None


def register_login_failure(email: str, ip: str) -> None:
    r = get_redis()
    ttl = settings.LOGIN_LOCK_MINUTES * 60
    pipe = r.pipeline()
    ekey, ikey = f"hj:lfa:e:{email.lower()}", f"hj:lfa:i:{ip}"
    pipe.incr(ekey)
    pipe.expire(ekey, ttl)
    pipe.incr(ikey)
    pipe.expire(ikey, ttl)
    pipe.execute()


def reset_login_failures(email: str) -> None:
    get_redis().delete(f"hj:lfa:e:{email.lower()}")


# --------------------------------------------------------------------------- #
# Utilidades de petición
# --------------------------------------------------------------------------- #


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def check_csrf(request: Request, session: Dict[str, Any]) -> None:
    """Verifica el token CSRF de las mutaciones autenticadas."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    header = request.headers.get(CSRF_HEADER, "")
    if not header or not secrets.compare_digest(header, session.get("csrf", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token CSRF ausente o inválido",
        )
