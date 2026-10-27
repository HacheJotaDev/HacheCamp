"""Heartbeat de procesos de segundo plano (worker y scheduler).

Los healthchecks de Docker para worker y scheduler no pueden usar HTTP
(no exponen un servidor): comprueban ACTIVIDAD REAL mediante un latido
en Redis que cada ciclo del bucle actualiza.

- beat(name): cada iteración del bucle escribe `hj:hb:<name>` = JSON con
  timestamp, pid, ciclos y TTL mayor que el intervalo máximo entre ciclos.
- is_fresh(name, max_age): el healthcheck considera sano solo si existe un
  latido reciente (< max_age segundos). Un proceso colgado, en bucle
  infinito o bloqueado deja de latir y el contenedor pasa a unhealthy.
- check(name, max_age): verificación completa del healthcheck: Redis
  alcanzable + latido fresco. Devuelve (sano, motivo).

Uso en docker-compose.yml (worker, scheduler):
  test: ["CMD", "python", "-m", "app.workers.heartbeat", "worker", "--max-age", "120"]

Umbral (max_age) debe ser MAYOR que el peor caso entre latidos:
- worker: ciclos normales de 3-8 s; peor caso con campaña pausada ~30 s de
  espera -> 120 s es seguro.
- scheduler: ciclo de 20 s + refresco de cuota SES (conecta 10 s + lee 30 s
  con reintentos adaptativos) -> hasta ~2 min -> 300 s es seguro.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

from app.core.logging_conf import get_logger
from app.workers.redis_client import get_redis

logger = get_logger("hj.heartbeat")

# Claves en Redis (prefijo hj:hb:)
_KEY = "hj:hb:{name}"


def _key(name: str) -> str:
    # El nombre viene de código/CLI, no de usuarios; se normaliza por higiene.
    return _KEY.format(name="".join(c for c in name if c.isalnum() or c in "-_")[:40])


def beat(name: str, ttl_seconds: int = 600, extra: Optional[Dict[str, Any]] = None) -> bool:
    """Registra un latido. Devuelve True si Redis lo aceptó.

    No lanza excepciones: un fallo de Redis no debe tumbar el bucle del
    worker (el healthcheck detectará el latido obsoleto y marcará unhealthy).
    """
    try:
        r = get_redis()
        payload = {
            "ts": time.time(),
            "pid": os.getpid(),
            "ttl": ttl_seconds,
            **(extra or {}),
        }
        r.set(_key(name), json.dumps(payload), ex=ttl_seconds)
        return True
    except Exception as exc:  # pragma: no cover - Redis caído
        logger.warning("Heartbeat '%s' no registrado: %s", name, type(exc).__name__)
        return False


def is_fresh(name: str, max_age_seconds: int) -> Tuple[bool, float]:
    """Devuelve (fresco, edad_en_segundos). Fresco si existe y edad < max_age.

    Sin latido previo (proceso recién arrancado o reiniciado) NO es fresco:
    evita que un contenedor zombie aparente salud tras un reinicio de Redis.
    """
    try:
        raw = get_redis().get(_key(name))
    except Exception as exc:
        return False, -1.0
    if not raw:
        return False, -1.0
    try:
        data = json.loads(raw)
        age = max(0.0, time.time() - float(data.get("ts", 0)))
    except (ValueError, TypeError):
        return False, -1.0
    return age < max_age_seconds, age


def check(name: str, max_age_seconds: int) -> Tuple[bool, str]:
    """Comprobación completa de healthcheck: Redis alcanzable + latido fresco."""
    try:
        get_redis().ping()
    except Exception as exc:
        return False, f"redis_inaccesible: {type(exc).__name__}"
    fresh, age = is_fresh(name, max_age_seconds)
    if fresh:
        return True, f"latido_ok_edad={age:.0f}s"
    if age < 0:
        return False, "sin_latido"
    return False, f"latido_obsoleto_edad={age:.0f}s>max={max_age_seconds}s"


def main(argv: list) -> int:
    """CLI de healthcheck: python -m app.workers.heartbeat <nombre> [--max-age N]"""
    name = ""
    max_age = 120
    args = list(argv)
    i = 0
    while i < len(args):
        if args[i] == "--max-age" and i + 1 < len(args):
            try:
                max_age = int(args[i + 1])
            except ValueError:
                print(f"max-age inválido: {args[i + 1]}", file=sys.stderr)
                return 2
            i += 2
            continue
        name = args[i]
        i += 1
    if not name:
        print("uso: python -m app.workers.heartbeat <nombre> [--max-age N]", file=sys.stderr)
        return 2
    ok, motivo = check(name, max_age)
    print(f"{'OK' if ok else 'FALLO'} {name}: {motivo}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
