"""Cliente Redis: soporta Redis real y fakeredis (solo desarrollo/pruebas).

REDIS_URL acepta:
  redis://[:pass@]host:port/db
  rediss://... (TLS)
  fakeredis:///            -> embebido, para desarrollo local sin Docker
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.config import settings

logger = logging.getLogger("hj.redis")

_redis: Any = None
_lock = threading.Lock()


def _create():
    url = settings.REDIS_URL
    if url.startswith("fakeredis://"):
        import fakeredis

        logger.warning("Usando fakeredis embebido: SOLO para desarrollo/pruebas")
        return fakeredis.FakeStrictRedis(decode_responses=True)
    import redis

    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
        health_check_interval=30,
    )


def get_redis():
    global _redis
    if _redis is None:
        with _lock:
            if _redis is None:
                _redis = _create()
                try:
                    _redis.ping()
                except Exception as exc:  # pragma: no cover
                    logger.error("No se pudo conectar a Redis: %s", type(exc).__name__)
                    raise
    return _redis


def redis_ok() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
