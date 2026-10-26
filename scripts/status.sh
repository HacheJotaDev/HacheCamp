#!/usr/bin/env bash
# HJ Campaigns — estado de los servicios y salud
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

# Asegura que existan los archivos de secretos AWS para que compose valide
cargar_env_seguro .env 2>/dev/null || true
preparar_secretos_aws --silencioso || true

echo "== Contenedores =="
docker compose ps
echo
echo "== Salud de la API =="
if docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/healthz 2>/dev/null || \
   docker compose exec -T api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/healthz', timeout=3)" 2>/dev/null; then
  :
else
  echo "(API no responde)"
fi
echo
echo "== Cola de envío (Redis) =="
docker compose exec -T redis redis-cli LLEN hj:q:outbound 2>/dev/null | xargs echo "  mensajes en cola:"
docker compose exec -T redis redis-cli ZCARD hj:q:retry 2>/dev/null | xargs echo "  reintentos programados:"
echo
echo "== Últimos logs (worker) =="
docker compose logs --tail 5 worker 2>/dev/null | tail -6
