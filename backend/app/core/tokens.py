"""Tokens firmados con HMAC-SHA256 para URLs públicas (seguimiento y bajas).

Formato: base64url(json_payload) + "." + hmac_hex(payload)
Protege contra manipulación; los clics firmados evitan redirecciones abiertas.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from app.core.config import settings


def _secret() -> bytes:
    return settings.ensure_secret().encode()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sign_payload(payload: Dict[str, Any]) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Devuelve el payload si el token es válido; si no, None."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    return payload


# Constructores de tokens específicos ----------------------------------------- #


def open_token(campaign_id: str, message_id: str) -> str:
    return sign_payload({"t": "o", "c": campaign_id, "m": message_id})


def click_token(campaign_id: str, message_id: str, url: str) -> str:
    return sign_payload({"t": "c", "c": campaign_id, "m": message_id, "u": url})


def unsubscribe_token(contact_id: str, campaign_id: Optional[str] = None) -> str:
    return sign_payload({"t": "u", "k": contact_id, "c": campaign_id or ""})


def parse_open_token(token: str) -> Optional[tuple[str, str]]:
    p = verify_token(token)
    if p and p.get("t") == "o":
        return p["c"], p["m"]
    return None


def parse_click_token(token: str) -> Optional[tuple[str, str, str]]:
    p = verify_token(token)
    if p and p.get("t") == "c":
        return p["c"], p["m"], p["u"]
    return None


def parse_unsub_token(token: str) -> Optional[tuple[str, Optional[str]]]:
    p = verify_token(token)
    if p and p.get("t") == "u":
        return p["k"], p.get("c") or None
    return None
