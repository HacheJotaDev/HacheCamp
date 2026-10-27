"""Pruebas del heartbeat de worker/scheduler (healthchecks de Docker).

Los healthchecks de worker y scheduler comprueban ACTIVIDAD REAL (latido
en Redis), no que el proceso exista ni HTTP (no exponen servidor).
"""
from __future__ import annotations

import json
import time

from app.workers import heartbeat
from app.workers.redis_client import get_redis


def _hb(name: str) -> dict:
    raw = get_redis().get(f"hj:hb:{name}")
    assert raw is not None
    return json.loads(raw)


class TestLatido:
    def test_latido_registra_y_esta_fresco(self):
        assert heartbeat.beat("worker") is True
        fresh, age = heartbeat.is_fresh("worker", max_age_seconds=120)
        assert fresh is True
        assert 0 <= age < 5
        data = _hb("worker")
        assert "ts" in data and "pid" in data

    def test_sin_latido_no_esta_fresco(self):
        fresh, age = heartbeat.is_fresh("inexistente", max_age_seconds=120)
        assert fresh is False
        assert age < 0  # sin latido previo

    def test_latido_obsoleto_no_esta_fresco(self):
        # Latido simulado con edad 999 s: proceso colgado / bucle bloqueado
        get_redis().set("hj:hb:scheduler",
                        json.dumps({"ts": time.time() - 999, "pid": 123}), ex=600)
        fresh, age = heartbeat.is_fresh("scheduler", max_age_seconds=300)
        assert fresh is False
        assert age >= 999

    def test_carga_util_extra(self):
        heartbeat.beat("worker", extra={"region": "us-east-2"})
        assert _hb("worker").get("region") == "us-east-2"

    def test_ttl_aplicado(self):
        heartbeat.beat("worker", ttl_seconds=600)
        ttl = get_redis().ttl("hj:hb:worker")
        assert 0 < ttl <= 600


class TestCheck:
    def test_check_ok(self):
        heartbeat.beat("worker")
        ok, motivo = heartbeat.check("worker", max_age_seconds=120)
        assert ok is True
        assert "latido_ok" in motivo

    def test_check_sin_latido(self):
        ok, motivo = heartbeat.check("nunca-latido", max_age_seconds=120)
        assert ok is False and motivo == "sin_latido"

    def test_check_obsoleto(self):
        get_redis().set("hj:hb:worker",
                        json.dumps({"ts": time.time() - 5000, "pid": 1}), ex=6000)
        ok, motivo = heartbeat.check("worker", max_age_seconds=120)
        assert ok is False and "obsoleto" in motivo

    def test_check_redis_inaccesible(self, monkeypatch):
        from app.workers import redis_client

        def _romper():
            raise ConnectionError("redis abajo")

        monkeypatch.setattr("app.workers.heartbeat.get_redis", _romper)
        ok, motivo = heartbeat.check("worker", max_age_seconds=120)
        assert ok is False and "redis_inaccesible" in motivo


class TestCliHealthcheck:
    def test_saludable_devuelve_0(self):
        heartbeat.beat("worker")
        assert heartbeat.main(["worker", "--max-age", "120"]) == 0

    def test_sin_latido_devuelve_1(self):
        assert heartbeat.main(["inexistente-cli", "--max-age", "120"]) == 1

    def test_argumentos_invalidos_devuelve_2(self):
        assert heartbeat.main([]) == 2
        assert heartbeat.main(["x", "--max-age", "no-numero"]) == 2


class TestBuclesReales:
    def test_worker_registra_latido_en_su_bucle(self, monkeypatch, fake_ses):
        """El bucle del worker (una iteración) deja latido fresco en Redis."""
        from app.workers import worker as worker_mod

        # La cola está vacía: pop devuelve None; tras el primer ciclo, parar
        llamadas = {"n": 0}

        def _pop(timeout_seconds=3):
            llamadas["n"] += 1
            worker_mod.stop()          # termina tras esta iteración
            return None

        monkeypatch.setattr(worker_mod.send_queue, "pop_message", _pop)
        monkeypatch.setattr(worker_mod.send_queue, "move_due_retries", lambda: None)
        monkeypatch.setattr(worker_mod.sqs_events, "start_consumer", lambda: None)
        worker_mod._running = True
        worker_mod.run_forever(poll_interval=0)
        worker_mod._running = True   # restaurar para otras pruebas

        assert llamadas["n"] >= 1
        fresh, age = heartbeat.is_fresh("worker", max_age_seconds=120)
        assert fresh is True, "el worker debe latir en cada ciclo del bucle"

    def test_scheduler_registra_latido_en_su_bucle(self, monkeypatch):
        """El bucle del scheduler (una iteración) deja latido fresco en Redis."""
        from app.workers import scheduler as sched_mod

        monkeypatch.setattr(sched_mod, "process_scheduled_campaigns",
                            lambda db: sched_mod.stop())  # para tras 1 ciclo
        monkeypatch.setattr(sched_mod.send_queue, "move_due_retries", lambda: None)
        sched_mod._running = True
        sched_mod.run_forever(interval_seconds=0)
        sched_mod._running = True   # restaurar para otras pruebas

        fresh, _ = heartbeat.is_fresh("scheduler", max_age_seconds=300)
        assert fresh is True, "el scheduler debe latir en cada ciclo del bucle"
