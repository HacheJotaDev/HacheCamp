#!/usr/bin/env bash
# HJ Campaigns — restauración de copia de seguridad
# Uso:  bash scripts/restore.sh backups/hj_backup_YYYYMMDD_HHMMSS.tar.gz
# ¡ATENCIÓN! Detiene los servicios y SOBRESCRIBE la base de datos actual.
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/lib.sh

COPIA="${1:-}"
[[ -f "${COPIA}" ]] || { echo "Uso: $0 <ruta/hj_backup_XXXX.tar.gz>"; exit 1; }

read -rp "Esto SOBRESCRIBE la base de datos actual. Escribe RESTAURAR para continuar: " CONF
[[ "${CONF}" == "RESTAURAR" ]] || { echo "Cancelado."; exit 1; }

cargar_env_seguro .env
preparar_secretos_aws --silencioso || true

TMP="$(mktemp -d)"
tar -xzf "${COPIA}" -C "${TMP}"
INTERIOR="$(find "${TMP}" -maxdepth 1 -type d -name 'hj_backup_*' | head -1)"
[[ -n "${INTERIOR}" ]] || { error "la copia no contiene el directorio esperado"; rm -rf "${TMP}"; exit 1; }

echo "== Deteniendo servicios de aplicación =="
docker compose stop worker scheduler api frontend

echo "== Restaurando PostgreSQL =="
# Termina las conexiones activas a la base de datos (excepto esta sesión).
# Las comillas: el sh del contenedor expande $POSTGRES_USER; 'db' con comillas
# simples dentro de la orden doblemente citada.
docker compose exec -T postgres sh -c \
  "psql -U \"\$POSTGRES_USER\" -d postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid();\"" \
  >/dev/null 2>&1 || true
# Vuelca el respaldo
gzip -dc "${INTERIOR}/postgres.sql.gz" | docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'

echo "== Restaurando Redis =="
if [[ -d "${INTERIOR}/redis_aof" ]]; then
  docker compose stop redis
  docker compose cp "${INTERIOR}/redis_aof/." redis:/data/appendonlydir/ 2>/dev/null \
    || aviso "no se pudo copiar el AOF; la cola de envío se reconstruye sola"
  docker compose start redis
elif [[ -f "${INTERIOR}/dump.rdb" ]]; then
  docker compose stop redis
  docker compose cp "${INTERIOR}/dump.rdb" redis:/data/dump.rdb 2>/dev/null \
    || aviso "no se pudo copiar el dump; la cola de envío se reconstruye sola"
  docker compose start redis
else
  aviso "sin estado de Redis en la copia; la cola de envío se reconstruye sola"
fi

echo "== Arrancando de nuevo =="
docker compose up -d
sleep 5
docker compose ps
echo "Restauración completada. Verifica: bash scripts/status.sh"
rm -rf "${TMP}"
