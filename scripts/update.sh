#!/usr/bin/env bash
# HJ Campaigns — actualización desde el repositorio
# Uso: bash scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

echo "== Guardando copia de seguridad previa =="
bash scripts/backup.sh || echo "(aviso: la copia previa falló; continúa bajo tu responsabilidad)"

echo "== Descargando cambios =="
git pull --ff-only

# Validación previa y secretos AWS para que compose resuelva correctamente
[[ -f .env ]] || { error "falta .env tras la actualización (cp .env.example .env)"; exit 1; }
cargar_env_seguro .env
validar_configuracion || exit 1
preparar_secretos_aws
docker compose config --quiet || { error "docker-compose.yml inválido tras la actualización"; exit 1; }

echo "== Reconstruyendo y aplicando migraciones =="
docker compose build
docker compose run --rm migrate
docker compose up -d

echo "== Salud =="
esperar_salud_api 12 5 || true
echo "Actualización completada."
