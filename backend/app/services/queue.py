"""Cola persistente sobre Redis para envíos asíncronos.

Estructuras:
  hj:q:outbound   LIST   -> trabajos listos: JSON {"mid": <campaign_message_id>}
  hj:q:retry      ZSET   -> reintentos diferidos: member=JSON trabajo, score=epoch de vencimiento
  hj:q:tokenbucket HASH  -> control de velocidad global

Los trabajos son reencolables y la cola sobrevive a reinicios (AOF).
La idempotencia final la garantiza la unicidad (campaign_id, contact_id) y los
estados persistentes en PostgreSQL.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.workers.redis_client import get_redis

OUTBOUND_KEY = "hj:q:outbound"
RETRY_KEY = "hj:q:retry"
LAST_SEND_KEY = "hj:q:last_send_ns"


def enqueue_message(message_id: str) -> None:
    get_redis().lpush(OUTBOUND_KEY, json.dumps({"mid": message_id}))


def enqueue_messages(message_ids: List[str]) -> int:
    if not message_ids:
        return 0
    r = get_redis()
    pipe = r.pipeline()
    payload = [json.dumps({"mid": mid}) for mid in message_ids]
    pipe.lpush(OUTBOUND_KEY, *payload)
    pipe.execute()
    return len(payload)


def pop_message(timeout_seconds: int = 5) -> Optional[str]:
    """BRPOP con bloqueo; devuelve el id de mensaje o None."""
    result = get_redis().brpop(OUTBOUND_KEY, timeout=timeout_seconds)
    if not result:
        return None
    _, raw = result
    try:
        data = json.loads(raw)
        return data.get("mid")
    except Exception:
        return None


def schedule_retry(message_id: str, attempt: int) -> int:
    """Programa un reintento con backoff exponencial. Devuelve el retraso (s)."""
    delay = settings.RETRY_BACKOFF_SECONDS * (2 ** max(attempt - 1, 0))
    delay = min(delay, 1800)  # máximo 30 minutos entre reintentos
    get_redis().zadd(RETRY_KEY, {json.dumps({"mid": message_id, "t": int(time.time())}): time.time() + delay})
    return delay


def move_due_retries(limit: int = 50) -> int:
    """Mueve reintentos vencidos a la cola principal. Devuelve cuántos."""
    r = get_redis()
    moved = 0
    items = r.zrangebyscore(RETRY_KEY, "-inf", time.time(), start=0, num=limit)
    for raw in items:
        if r.zrem(RETRY_KEY, raw):
            r.lpush(OUTBOUND_KEY, raw)
            moved += 1
    return moved


def pending_retries_count() -> int:
    return int(get_redis().zcard(RETRY_KEY) or 0)


def outbound_count() -> int:
    return int(get_redis().llen(OUTBOUND_KEY) or 0)


# --------------------------------------------------------------------------- #
# Cola de importaciones (independiente de la de envíos)
# --------------------------------------------------------------------------- #

IMPORT_KEY = "hj:q:import"  # LIST -> trabajos listos: JSON {"jid": <import_job_id>}


def enqueue_import_job(job_id: str) -> None:
    """Encola un trabajo de importación para el worker importador."""
    get_redis().lpush(IMPORT_KEY, json.dumps({"jid": job_id}))


def pop_import_job(timeout_seconds: int = 5) -> Optional[str]:
    """BRPOP bloqueante sobre la cola de importaciones. Devuelve job_id o None."""
    result = get_redis().brpop(IMPORT_KEY, timeout=timeout_seconds)
    if not result:
        return None
    _, raw = result
    try:
        data = json.loads(raw)
        return data.get("jid")
    except Exception:
        return None


def import_queue_count() -> int:
    return int(get_redis().llen(IMPORT_KEY) or 0)


# --------------------------------------------------------------------------- #
# Control de velocidad (bucket con intervalo mínimo entre envíos)
# --------------------------------------------------------------------------- #


def wait_for_rate_slot(rate_per_second: float) -> float:
    """Bloquea hasta que toque el siguiente envío. Devuelve la espera (s)."""
    if rate_per_second <= 0:
        return 0.0
    min_interval = 1.0 / rate_per_second
    r = get_redis()
    while True:
        now = time.time_ns()
        with r.pipeline() as pipe:
            try:
                pipe.watch(LAST_SEND_KEY)
                last = int(pipe.get(LAST_SEND_KEY) or 0)
                next_allowed = last + int(min_interval * 1e9)
                if now < next_allowed:
                    sleep_s = (next_allowed - now) / 1e9
                    pipe.unwatch()
                    time.sleep(min(sleep_s, 0.5))
                    continue
                pipe.multi()
                pipe.set(LAST_SEND_KEY, now)
                pipe.execute()
                return 0.0
            except Exception:
                time.sleep(min_interval)
                return 0.0
