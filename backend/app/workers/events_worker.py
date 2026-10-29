"""Events worker: consumidor DEDICADO del pipeline SES -> SNS -> SQS.

Ejecución: python -m app.workers.events_worker

Responsabilidad única: recibir entregas, rebotes, quejas y demás eventos de
SES (y confirmar suscripciones SNS) procesando la cola SQS configurada en
AWS_SQS_EVENTS_QUEUE_URL. Sustituye al consumidor integrado en el worker de
envío, que con 14 réplicas significaba 14 consumidores redundantes y un
latido 'sqs_consumer' compartido (un healthcheck ciego a réplicas muertas).

Comportamiento:
- Long polling SQS con borrado selectivo e idempotencia por event_uid
  (ver app/services/sqs_events.py).
- Latido en Redis (hj:hb:sqs_consumer) tras cada lote: healthcheck real de
  Docker (python -m app.workers.heartbeat sqs_consumer --max-age 300).
- Sin AWS_SQS_EVENTS_QUEUE_URL configurada: el proceso permanece vivo e
  inactivo marcando el latido con estado 'inactivo' (no tumbar el contenedor
  por una característica deshabilitada a propósito) y avisa cada 5 minutos.
- Apagado limpio con SIGTERM/SIGINT: el mensaje en curso no se borra de SQS
  y reaparece tras la visibilidad (procesamiento idempotente).
"""
from __future__ import annotations

import signal
import threading
import time
from typing import Optional

from app.core.config import settings
from app.core.logging_conf import get_logger, setup_logging
from app.services import sqs_events
from app.workers import heartbeat

logger = get_logger("hj.events_worker")

_running = True


def stop() -> None:
    """Apagado limpio (SIGTERM de docker stop)."""
    global _running
    _running = False


def _espera_con_apagado(segundos: float) -> None:
    """Duerme fraccionado para responder a SIGTERM en <=1 s."""
    restante = float(segundos)
    while _running and restante > 0:
        time.sleep(min(1.0, restante))
        restante -= 1.0


def run_forever(poll_interval: int = 5) -> None:
    """Bucle principal del consumidor dedicado de eventos."""
    setup_logging()
    logger.info("Events worker iniciado (consumidor dedicado SES/SQS)",
                extra={"region": settings.AWS_REGION})
    ultimo_aviso = 0.0
    while _running:
        if not sqs_events.sqs_enabled():
            # SQS sin configurar: inactivo pero vivo (latido con estado).
            # Aviso periódico sin inundar el log.
            heartbeat.beat("sqs_consumer", extra={"estado": "inactivo_sin_configurar"})
            ahora = time.time()
            if ahora - ultimo_aviso > 300:
                logger.warning("SQS deshabilitado (AWS_SQS_EVENTS_QUEUE_URL vacía); "
                               "entregas/rebotes/quejas NO se registran")
                ultimo_aviso = ahora
            _espera_con_apagado(poll_interval * 6)
            continue
        stop_event = threading.Event()
        hilo: Optional[threading.Thread] = threading.Thread(
            target=sqs_events._bucle_consumidor, args=(stop_event,),
            name="hj-sqs-events", daemon=True,
        )
        hilo.start()
        # El hilo solo termina si SQS se deshabilita en caliente o ante un
        # error que agote sus reintentos internos: se re-lanza mientras el
        # proceso siga vivo (reinicios transparentes del bucle de consumo).
        while _running and hilo is not None and hilo.is_alive():
            _espera_con_apagado(0.5)
        if _running:
            logger.warning("Bucle de consumo SQS terminado; reanudando en 5 s")
            _espera_con_apagado(poll_interval)
    sqs_events.stop_consumer()


if __name__ == "__main__":
    # Apagado limpio: el lote en curso termina y el resto vuelve a la cola
    signal.signal(signal.SIGTERM, lambda *_: stop())
    signal.signal(signal.SIGINT, lambda *_: stop())
    run_forever()
