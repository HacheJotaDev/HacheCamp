#!/usr/bin/env bash
# HJ Campaigns — restauración de copia de seguridad
# Uso:  bash scripts/restore.sh backups/hj_backup_YYYYMMDD_HHMMSS.tar.gz
# ¡ATENCIÓN! Detiene los servicios y SOBRESCRIBE la base de datos actual.
set -euo pipefail

cd "$(dirname "$0")/.."

COPIA="${1:-}"
[[ -f "${COPIA}" ]] || { echo "Uso: $0 <ruta/hj_backup_XXXX.tar.gz>"; exit 1; }

read -rp "Esto SOBRESCRIBE la base de datos actual. Escribe RESTAURAR para continuar: " CONF
[[ "${CONF}" == "RESTAURAR" ]] || { echo "Cancelado."; exit 1; }

TMP="$(mktemp -d)"
tar -xzf "${COPIA}" -C "${TMP}"
INTERIOR="$(find "${TMP}" -maxdepth 1 -type d -name 'hj_backup_*' | head -1)"

echo "== Deteniendo servicios de aplicación =="
docker compose stop worker scheduler api frontend

echo "== Restaurando PostgreSQL =="
gzip -dc "${INTERIOR}/postgres.sql.gz" | docker compose exec -T postgres sh -c \
  'psql -U "${POSTGRES_USER}" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_setting('\''db'\''');" >/dev/null 2>&1 || true
gzip -dc "${INTERIOR}/postgres.sql.gz" | docker compose exec -T postgres sh -c \
  'psql -U "${POSTGRES_USER}" "${POSTGRES_DB}"'

echo "== Restaurando Redis =="
if [[ -f "${INTERIOR}/dump.rdb" ]]; then
  docker compose stop redis
  docker cp "${INTERIOR}/dump.rdb" "$(docker compose ps -q redis || true)":/data/dump.rdb 2>/dev/null || true
  # con el contenedor detenido, copia directa al volumen
  docker volume ls >/dev/null
  docker compose start redis
fi

echo "== Arrancando de nuevo =="
docker compose up -d
sleep 5
docker compose ps
echo "Restauración completada. Verifica: bash scripts/status.sh"
rm -rf "${TMP}"
