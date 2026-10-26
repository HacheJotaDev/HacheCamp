#!/usr/bin/env bash
# HJ Campaigns — actualización desde el repositorio
# Uso: bash scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Guardando copia de seguridad previa =="
bash scripts/backup.sh || echo "(aviso: la copia previa falló; continúa bajo tu responsabilidad)"

echo "== Descargando cambios =="
git pull --ff-only

echo "== Reconstruyendo y aplicando migraciones =="
docker compose build
docker compose run --rm migrate
docker compose up -d

echo "== Estado final =="
docker compose ps
echo "Actualización completada."
