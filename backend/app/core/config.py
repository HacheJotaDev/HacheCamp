"""Configuración central de HJ Campaigns (pydantic-settings).

Todos los valores pueden sobrescribirse por variables de entorno o archivo .env.
Los secretos NUNCA se incluyen en el repositorio.

Credenciales de AWS (SES/SQS): Boto3 resuelve su cadena habitual
  1. Variables de entorno AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (opcional),
  2. Archivo compartido apuntado por AWS_SHARED_CREDENTIALS_FILE/AWS_CONFIG_FILE
     (en Docker: secretos montados en /run/secrets, recomendado en el VPS).
Una variable de entorno VACÍA rompería esa cadena, por eso se sanea abajo.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _limpiar_aws_env_vacias() -> None:
    """Elimina variables AWS de entorno vacías (p. ej. AWS_ACCESS_KEY_ID='')

    Un valor vacío cuenta como 'credencial presente' para botocore y anula el
    resto de la cadena (archivo compartido/perfil), provocando fallos de auth.
    Con este saneamiento, si el .env las trae vacías, Boto3 usa el archivo
    de credenciales montado como secreto.
    """
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        if os.environ.get(var, "") == "":
            os.environ.pop(var, None)


_limpiar_aws_env_vacias()


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
    AWS_ACCESS_KEY_ID: Optional[str] = None  # Opcional: preferible archivo de credenciales (secreto)
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_PROFILE: str = "default"             # Perfil dentro del archivo de credenciales
    SES_CONFIGURATION_SET: Optional[str] = None
    SES_CONTACT_LIST: Optional[str] = None   # ListManagementOptions opcional
    SES_MAX_PER_SECOND: Optional[int] = None  # Límite propio; si no, usa el de SES
    SES_RETRIES_MAX: int = 4

    # --- Eventos de SES por SQS (canal privado, sin endpoint público) -------
    # Si AWS_SQS_EVENTS_QUEUE_URL está definida, el worker consume la cola.
    AWS_SQS_EVENTS_QUEUE_URL: Optional[str] = None
    SQS_WAIT_SECONDS: int = 20               # Long polling (máx. 20 s)
    SQS_MAX_MESSAGES: int = 10               # Lote por recepción (máx. 10)
    SQS_MAX_RECEIVE_COUNT: int = 5           # Mensaje venenoso: se descarta tras N lecturas
    SQS_VERIFY_SNS_SIGNATURE: bool = True    # Verificar firma SNS del sobre

    # --- URLs públicas -------------------------------------------------------
    PUBLIC_BASE_URL: str = "http://127.0.0.1:8085"   # URL pública del frontend
    API_BASE_URL: str = "http://api:8000"            # URL del API para cabeceras de baja
    WEBHOOK_TOKEN: str = ""  # Token de la URL del webhook SNS
    SNS_TOPIC_ARNS: str = ""  # Lista blanca opcional de tópicos (separada por comas)

    # --- Envío ---------------------------------------------------------------
    # Objetivo global de 12 destinatarios/s compartido por todos los workers
    # vía Redis (la efectiva es min(este valor, MaxSendRate real de SES)).
    SEND_RATE_PER_SECOND: float = 12.0
    SEND_BATCH_SIZE: int = 100
    RETRY_BACKOFF_SECONDS: int = 60          # Base exponencial (60, 120, 240...)
    RETRY_MAX_ATTEMPTS: int = 4
    STALE_SENDING_MINUTES: int = 10          # 'enviando' antiguo -> estado incierto
    RATE_LIMIT_FAIL_CLOSED: bool = True      # Redis caído => NO enviar (nada sin slot)
    QUOTA_24H_MIN_REMAINING: int = 50        # Puerta de cuota 24 h: pausa al bajar de aquí
    ORPHAN_MIN_AGE_SECONDS: int = 60         # Edad mínima para reencolar huérfanos

    # --- Cargas ---------------------------------------------------------------
    CSV_MAX_MB: int = 5
    IMPORT_MAX_ROWS: int = 200_000          # filas válidas máximas por archivo

    # --- Importación asíncrona -------------------------------------------------
    IMPORT_BATCH_SIZE: int = 500            # filas por lote (un commit por lote)
    IMPORT_LOT_RETRIES: int = 3             # reintentos de un lote antes de fallar el trabajo
    IMPORT_LOCK_TTL_SECONDS: int = 900      # lock Redis por trabajo (renovado por lote)
    IMPORT_RECOVERY_INTERVAL_SECONDS: int = 60  # recuperación periódica de estancados
    IMPORT_JOB_MIN_AGE_SECONDS: int = 60    # edad mínima para considerar un trabajo estancado

    # --- Logging ---------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("SECRET_KEY debe tener al menos 32 caracteres")
        return v

    @field_validator("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SQS_EVENTS_QUEUE_URL", mode="before")
    @classmethod
    def _cadena_vacia_a_none(cls, v):
        """Trata cadenas vacías como ausentes (evita credenciales inválidas)."""
        if isinstance(v, str) and v.strip() == "":
            return None
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
