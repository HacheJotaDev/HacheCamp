#!/usr/bin/env bash
# HJ Campaigns — estado de los servicios y salud
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Contenedores =="
docker compose ps
echo
echo "== Salud de la API =="
docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/healthz 2>/dev/null || echo "(API no responde)"
echo
echo
echo "== Cola de envío (Redis) =="
docker compose exec -T redis redis-cli LLEN hj:q:outbound 2>/dev/null | xargs echo "  mensajes en cola:"
docker compose exec -T redis redis-cli ZCARD hj:q:retry 2>/dev/null | xargs echo "  reintentos programados:"
echo
echo "== Últimos logs (worker) =="
docker compose logs --tail 5 worker 2>/dev/null | tail -6
