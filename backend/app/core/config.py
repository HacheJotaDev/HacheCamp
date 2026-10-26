"""Configuración central de HJ Campaigns (pydantic-settings).

Todos los valores pueden sobrescribirse por variables de entorno o archivo .env.
Los secretos NUNCA se incluyen en el repositorio.
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Aplicación -------------------------------------------------------
    APP_NAME: str = "HJ Campaigns"
    ENV: str = "production"  # development | production
    API_PREFIX: str = "/api/v1"
    SECRET_KEY: str = ""  # Obligatoria en producción (32+ bytes)

    # --- Base de datos / Redis -------------------------------------------
    DATABASE_URL: str = "sqlite:///./hj_campaigns.db"
    REDIS_URL: str = "redis://redis:6379/0"

    # --- Sesiones y seguridad ---------------------------------------------
    SESSION_TTL_MINUTES: int = 480          # 8 horas
    LOGIN_MAX_ATTEMPTS_EMAIL: int = 5       # intentos por correo antes de bloqueo
    LOGIN_MAX_ATTEMPTS_IP: int = 20         # intentos por IP
    LOGIN_LOCK_MINUTES: int = 15
    COOKIE_SECURE: bool = True              # False solo en desarrollo HTTP local
    COOKIE_DOMAIN: Optional[str] = None
    MFA_REQUIRED_FOR_ADMIN: bool = True     # exigir TOTP a administradores
    PASSWORD_MIN_LENGTH: int = 12

    # --- CORS ---------------------------------------------------------------
    ALLOWED_ORIGINS: str = ""  # separado por comas; vacío = solo mismo origen

    # --- Amazon SES ---------------------------------------------------------
    AWS_REGION: str = "us-east-2"           # Región obligatoria del proyecto
    AWS_ACCESS_KEY_ID: Optional[str] = None  # Preferible: cadena de credenciales IAM del host
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    SES_CONFIGURATION_SET: Optional[str] = None
    SES_CONTACT_LIST: Optional[str] = None   # ListManagementOptions opcional
    SES_MAX_PER_SECOND: Optional[int] = None  # Límite propio; si no, usa el de SES
    SES_RETRIES_MAX: int = 4

    # --- URLs públicas -------------------------------------------------------
    PUBLIC_BASE_URL: str = "http://127.0.0.1:8085"   # URL pública del frontend
    API_BASE_URL: str = "http://api:8000"            # URL del API para cabeceras de baja
    WEBHOOK_TOKEN: str = ""  # Token de la URL del webhook SNS
    SNS_TOPIC_ARNS: str = ""  # Lista blanca opcional de tópicos (separada por comas)

    # --- Envío ---------------------------------------------------------------
    SEND_RATE_PER_SECOND: float = 5.0        # Límite configurable de la cola
    SEND_BATCH_SIZE: int = 100
    RETRY_BACKOFF_SECONDS: int = 60          # Base exponencial (60, 120, 240...)
    RETRY_MAX_ATTEMPTS: int = 4
    STALE_SENDING_MINUTES: int = 10          # 'enviando' antiguo -> estado incierto

    # --- Cargas ---------------------------------------------------------------
    CSV_MAX_MB: int = 5

    # --- Logging ---------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("SECRET_KEY debe tener al menos 32 caracteres")
        return v

    _dev_secret: str = ""  # caché interna (se excluye del modelo pydantic)

    def ensure_secret(self) -> str:
        """Devuelve una clave secreta válida. En desarrollo genera una estable."""
        if self.SECRET_KEY:
            return self.SECRET_KEY
        if self.ENV == "development":
            if not self._dev_secret:
                self._dev_secret = secrets.token_hex(32)
            return self._dev_secret
        raise RuntimeError(
            "SECRET_KEY no configurada: defínala en .env (32+ caracteres aleatorios)"
        )

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.ENV == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
