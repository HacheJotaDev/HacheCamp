"""Logging estructurado JSON con limpieza de datos sensibles."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.config import settings

# Claves cuyo valor jamás debe aparecer en los logs
_SENSITIVE_KEYS = re.compile(
    r"(password|passwd|secret|token|authorization|cookie|api[_-]?key|access[_-]?key|"
    r"credential|totp|signature|ses_message|aws)",
    re.IGNORECASE,
)


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "nivel": record.levelname.lower(),
            "modulo": record.name,
            "mensaje": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            payload["excepcion"] = record.exc_info[0].__name__
        # Limpieza de datos sensibles en atributos extra
        for key, value in record.__dict__.items():
            if key in ("args", "exc_info", "exc_text", "stack_info", "msg", "levelname",
                       "levelno", "pathname", "filename", "module", "name", "lineno",
                       "funcName", "created", "msecs", "relativeCreated", "thread",
                       "threadName", "processName", "process", "taskName", "message"):
                continue
            if _SENSITIVE_KEYS.search(key):
                payload[key] = "[REDACTADO]"
            else:
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    if settings.LOG_JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    # Silenciar ruido
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
