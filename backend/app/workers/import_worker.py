"""Worker de importaciones: procesa la cola hj:q:import (independiente).

Separado del worker de envíos a propósito: una importación de 50.000 filas
tarda minutos de CPU/BD y no debe competir con el envío de campañas, ni
bloquear el API (que solo crea el trabajo).

- Un trabajo a la vez; los siguientes quedan en la cola Redis (hj:q:import).
- Lotes con commits cortos (ver app.services.import_jobs.process_job).
- Recuperación tras reinicios: al arrancar reencola trabajos 'pendiente' y
  'procesando' (reanudación idempotente desde el último lote confirmado).
- Latido de actividad para el healthcheck de Docker (como worker/scheduler).

Ejecución: python -m app.workers.import_worker
"""
from __future__ import annotations

import json
import signal
import time

from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.core.logging_conf import get_logger, setup_logging
from app.models import BulkOperation, ImportJob
from app.services import bulk_ops as bulk_service
from app.services import import_jobs as import_service
from app.services import queue as queues
from app.workers import heartbeat
from app.workers.redis_client import redis_ok

logger = get_logger("hj.import_worker")

_running = True


def stop() -> None:
    global _running
    _running = False


def _reencolar_si_cabe(db, jid: str) -> None:
    """Tras un error no controlado: reencola el trabajo (reanudación idempotente)
    salvo que haya superado el límite de intentos -> lo marca fallido."""
    from app.core.config import settings

    job = db.get(ImportJob, jid)
    if not job or job.status in ("completado", "cancelado", "fallido"):
        return
    if job.attempts >= settings.IMPORT_LOT_RETRIES + 1:
        job.status = "fallido"
        job.last_error = f"error_recurrente_tras_{job.attempts}_intentos"
        job.finished_at = utcnow()
        db.commit()
        return
    try:
        queues.enqueue_import_job(jid)
        time.sleep(2)  # evita bucle caliente si el fallo es persistente
    except Exception:
        pass  # quedará 'procesando': recover_pending_jobs lo reencola al reiniciar


def _procesar_operacion_masiva(oid: str) -> None:
    """Procesa una operación masiva (V5) con recuperación ante fallos."""
    db = SessionLocal()
    try:
        result = bulk_service.process_operation(db, oid)
        logger.info("Operación masiva procesada",
                    extra={"resultado": json.dumps(result)[:300]})
    except Exception as exc:
        db.rollback()
        logger.error("Error procesando operación masiva", extra={
            "operacion": oid, "error": type(exc).__name__})
        # Reencolar con límite de intentos (misma política que importaciones)
        op = db.get(BulkOperation, oid)
        if op and op.status in ("pendiente", "procesando"):
            if op.attempts >= 3:
                op.status = "fallida"
                op.last_error = f"error_recurrente_tras_{op.attempts}_intentos"
                op.finished_at = utcnow()
                db.commit()
            else:
                op.attempts = (op.attempts or 0) + 1
                db.commit()
                try:
                    queues.enqueue_bulk_operation(oid)
                except Exception:
                    pass  # recover_pending_operations la reencolará
    finally:
        db.close()


def run_forever(poll_interval: int = 5) -> None:
    """Bucle principal del worker de importaciones (+ operaciones masivas V5)."""
    setup_logging()
    logger.info("Worker de importaciones iniciado")
    last_recovery = 0.0
    while _running:
        try:
            heartbeat.beat("import_worker")
            if not redis_ok():
                logger.error("Redis no disponible; reintentando en 5s")
                time.sleep(poll_interval)
                continue

            # Recuperación tras reinicio: reencola vivos (pendiente/procesando).
            # Se hace una vez por arranque; la recuperación PERIÓDICA de
            # estancados va más abajo (cubre trabajos varados sin reinicio).
            if not getattr(run_forever, "_recovered", False):
                db = SessionLocal()
                try:
                    import_service.recover_pending_jobs(db)
                    bulk_service.recover_pending_operations(db, min_age_seconds=5)
                finally:
                    db.close()
                run_forever._recovered = True  # type: ignore[attr-defined]

            # Recuperación periódica: 'pendiente' que nadie tomó y 'procesando'
            # sin lock vivo (worker muerto a mitad), con edad mínima. Idempotente:
            # el reanudado continúa desde rows_processed y el lock evita solapes.
            now = time.time()
            if now - last_recovery >= max(settings.IMPORT_RECOVERY_INTERVAL_SECONDS, 30):
                last_recovery = now
                db = SessionLocal()
                try:
                    import_service.recover_stuck_jobs(db)
                    bulk_service.recover_pending_operations(db)
                except Exception as exc:
                    logger.error("Recuperación periódica falló", extra={"error": type(exc).__name__})
                finally:
                    db.close()

            # Operaciones masivas (V5): sondeo NO bloqueante para no robarle
            # el bloqueo a la cola de importaciones (se procesan en serie).
            oid = queues.pop_bulk_operation(timeout_seconds=0)
            if oid:
                _procesar_operacion_masiva(oid)
                continue

            jid = queues.pop_import_job(timeout_seconds=3)
            if not jid:
                continue

            db = SessionLocal()
            try:
                result = import_service.process_job(db, jid)
                logger.info("Importación procesada", extra={"resultado": json.dumps(result)[:300]})
            except Exception as exc:
                db.rollback()
                logger.error("Error procesando importación", extra={
                    "job": jid, "error": type(exc).__name__})
                _reencolar_si_cabe(db, jid)
            finally:
                db.close()
        except KeyboardInterrupt:
            logger.info("Worker de importaciones detenido por señal")
            break
        except Exception as exc:
            logger.error("Error en ciclo del worker de importaciones",
                         extra={"error": type(exc).__name__})
            time.sleep(poll_interval)


if __name__ == "__main__":
    # Apagado limpio con SIGTERM (docker stop) y SIGINT
    signal.signal(signal.SIGTERM, lambda *_: stop())
    signal.signal(signal.SIGINT, lambda *_: stop())
    run_forever()
