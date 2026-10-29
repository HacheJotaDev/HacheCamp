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
from typing import Any, List, Optional

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
BULK_KEY = "hj:q:bulk"      # LIST -> operaciones masivas V5: JSON {"oid": <bulk_operation_id>}


def enqueue_bulk_operation(op_id: str) -> None:
    """Encola una operación masiva (V5) para el worker importador."""
    get_redis().lpush(BULK_KEY, json.dumps({"oid": op_id}))


def pop_bulk_operation(timeout_seconds: int = 0) -> Optional[str]:
    """Saca una operación masiva de la cola. Con timeout=0 no bloquea (LPOP):
    el worker importador sondea la cola masiva SIN robarle bloqueo a la de
    importaciones; ambas comparten proceso pero no capacidad.
    """
    if timeout_seconds and timeout_seconds > 0:
        result = get_redis().brpop(BULK_KEY, timeout=timeout_seconds)
    else:
        result = get_redis().lpop(BULK_KEY)
        if result:
            result = (None, result)  # misma forma que brpop
    if not result:
        return None
    _, raw = result
    try:
        data = json.loads(raw)
        return data.get("oid")
    except Exception:
        return None


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
#
# El límite es GLOBAL y compartido: la referencia vive en Redis
# (hj:q:last_send_ns), así que N réplicas del worker (docker compose
# --scale worker=N) respetan en conjunto la velocidad autorizada por SES.
# La toma de slot es atómica vía Lua: evita la carrera del antiguo
# WATCH/MULTI, que ante WatchError dejaba pasar envíos sin slot.
# --------------------------------------------------------------------------- #

# Toma un slot de forma atómica. ARGV: [intervalo_ns].
# Devuelve los segundos de espera como cadena ("0" si el slot es nuestro).
# LA HORA SE TOMA DENTRO DEL SCRIPT (redis.call('TIME')): si cada cliente
# capturase su propio time_ns() antes del EVAL, un hilo desplanzado llega con
# un timestamp ANTIGUO, la rama "reloj retrocedido" disparaba y concedía el
# slot sin esperar (hallazgo del cierre V5 contra Redis real: 4 pruebas de
# concurrencia fallaban solo con redis-server real; fakeredis serializa en
# proceso y no reproducía la ventana). Con la hora del servidor la lectura es
# atómica respecto al SET y no existe ventana. Además la espera se devuelve
# con tostring(): Redis trunca a ENTERO los números que devuelve Lua.
_SLOT_LUA = """
local t = redis.call('TIME')
local now = t[1] * 1000000000 + t[2] * 1000
local last = tonumber(redis.call('GET', KEYS[1]) or '0')
local interval = tonumber(ARGV[1])
if last > now then
  -- Reloj retrocedido de verdad (NTP): reiniciamos la referencia
  redis.call('SET', KEYS[1], tostring(now))
  return '0'
end
local next_allowed = last + interval
if now < next_allowed then
  return tostring((next_allowed - now) / 1e9)
end
redis.call('SET', KEYS[1], tostring(now))
return '0'
"""


def _tomar_slot(r: Any, intervalo_ns: int) -> float:
    """Intento atómico de tomar slot. Devuelve segundos a esperar (0 = logrado).

    Se usa EVAL directo (no register_script/EVALSHA) para compatibilidad con
    cualquier Redis, incluido fakeredis en pruebas. A 5-14 envíos/s el coste
    de reenviar el script es trivial.
    """
    resultado = r.eval(_SLOT_LUA, 1, LAST_SEND_KEY, intervalo_ns)
    if isinstance(resultado, (bytes, bytearray)):
        resultado = resultado.decode()
    return float(resultado or 0)


class RateLimiterUnavailable(RuntimeError):
    """Redis no disponible con política fail-closed: NO se autoriza enviar.

    Con N réplicas del worker, enviar sin el límite global significaría
    hasta N × velocidad objetivo (p. ej. 14 × 12 = 168 msg/s contra SES).
    La política por defecto (RATE_LIMIT_FAIL_CLOSED) bloquea el envío y
    reprograma el mensaje; el limiter nunca falla abierto en producción.
    """


def wait_for_rate_slot(rate_per_second: float, *, fail_closed: Optional[bool] = None) -> float:
    """Bloquea hasta que toque el siguiente envío. Devuelve la espera (s).

    Política ante Redis caído:
    - fail_closed=True (defecto, settings.RATE_LIMIT_FAIL_CLOSED): lanza
      RateLimiterUnavailable; el llamador repone el mensaje a 'pendiente' y
      lo reprograma. Ningún correo sale sin slot global.
    - fail_closed=False (legado, solo pruebas): degrada con una pausa local.
    """
    if rate_per_second <= 0:
        return 0.0
    min_interval = 1.0 / rate_per_second
    if fail_closed is None:
        fail_closed = settings.RATE_LIMIT_FAIL_CLOSED
    try:
        r = get_redis()
        while True:
            espera = _tomar_slot(r, int(min_interval * 1e9))
            if espera <= 0:
                return 0.0
            time.sleep(min(espera, 0.5))
    except RateLimiterUnavailable:
        raise
    except Exception as exc:
        if fail_closed:
            raise RateLimiterUnavailable(
                f"Redis no disponible ({type(exc).__name__}): envío bloqueado"
            ) from exc
        time.sleep(min_interval)
        return 0.0
