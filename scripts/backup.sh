#!/usr/bin/env bash
# HJ Campaigns — copia de seguridad (PostgreSQL + Redis + .env)
# Uso:  bash scripts/backup.sh
# Programación sugerida en crontab:  0 3 * * * /opt/hj-campaigns/scripts/backup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
DIR="backups"
FECHA="$(date +%Y%m%d_%H%M%S)"
DESTINO="${DIR}/hj_backup_${FECHA}"
mkdir -p "${DESTINO}"

echo "== Copia de seguridad ${FECHA} =="

# PostgreSQL (verificada: no se aceptan dumps vacíos o incompletos)
docker compose exec -T postgres sh -c \
  'pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}"' | gzip > "${DESTINO}/postgres.sql.gz"

test -s "${DESTINO}/postgres.sql.gz" \
  || { echo "ERROR: el dump de PostgreSQL está vacío"; exit 1; }
gzip -t "${DESTINO}/postgres.sql.gz" \
  || { echo "ERROR: dump de PostgreSQL corrupto (gzip)"; exit 1; }
gunzip -c "${DESTINO}/postgres.sql.gz" | tail -1 | grep -q "PostgreSQL database dump complete" \
  || { echo "ERROR: dump de PostgreSQL incompleto"; exit 1; }

# Redis (copia del AOF/RDB en caliente)
docker compose exec -T redis redis-cli BGSAVE >/dev/null 2>&1 || true
sleep 2
docker cp "$(docker compose ps -q redis)":/data/appendonlydir "${DESTINO}/redis_aof" 2>/dev/null || \
  docker cp "$(docker compose ps -q redis)":/data/dump.rdb "${DESTINO}/dump.rdb" 2>/dev/null || \
  echo "(aviso: no se pudo copiar el estado de Redis; la cola se reconstruye sola)"

# Configuración (contiene secretos: protege el archivo resultante)
[[ -f .env ]] && cp .env "${DESTINO}/env.backup"

tar -czf "${DESTINO}.tar.gz" -C "${DIR}" "hj_backup_${FECHA}"
test -s "${DESTINO}.tar.gz" || { echo "ERROR: el paquete final está vacío"; exit 1; }
gzip -t "${DESTINO}.tar.gz" || { echo "ERROR: paquete final corrupto"; exit 1; }
rm -rf "${DESTINO}"

# Retención: conserva las 14 más recientes
ls -1t "${DIR}"/hj_backup_*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f

# El paquete contiene secretos (.env): restringir permisos de verdad
chmod 600 "${DESTINO}.tar.gz"

echo "Copia creada y verificada: ${DESTINO}.tar.gz (permisos 600 aplicados)"
