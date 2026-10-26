#!/usr/bin/env bash
# ============================================================ #
# HJ Campaigns — despliegue inicial en el VPS                  #
#                                                              #
# Uso (como root en el VPS):                                   #
#   1. git clone https://github.com/HacheJotaDev/HacheCamp.git /opt/hj-campaigns
#   2. cd /opt/hj-campaigns && cp .env.example .env && nano .env
#   3. bash scripts/deploy.sh                                  #
#                                                              #
# No toca ni detiene otros proyectos (p. ej. /opt/campanas).   #
# ============================================================ #
set -euo pipefail

cd "$(dirname "$0")/.."
PROYECTO="$(pwd -P)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '1,12p' "$0"
  exit 0
fi

echo "== HJ Campaigns — despliegue =="
echo "Proyecto: ${PROYECTO}"

# 1) Comprobaciones previas
command -v docker >/dev/null || { echo "ERROR: Docker no está instalado"; exit 1; }
docker compose version >/dev/null || { echo "ERROR: docker compose no disponible"; exit 1; }
[[ -f .env ]] || { echo "ERROR: falta el archivo .env (copia .env.example y configúralo)"; exit 1; }

source .env 2>/dev/null || true
SECRET_LEN=${#SECRET_KEY:-0}
if [[ -z "${SECRET_KEY:-}" || "${SECRET_KEY}" == CAMBIA* ]]; then
  echo "ERROR: define SECRET_KEY en .env"; exit 1
fi
if [[ -z "${WEBHOOK_TOKEN:-}" ]]; then
  echo "AVISO: WEBHOOK_TOKEN vacío: el webhook SNS quedará deshabilitado hasta configurarlo."
fi

# 2) Comprobar que el puerto elegido no está ocupado
PUERTO="${PORT_FRONTEND:-8085}"
if ss -ltn | awk '{print $4}' | grep -qE "[:.]${PUERTO}$"; then
  echo "ERROR: el puerto ${PUERTO} ya está en uso en este host."
  echo "       Elige otro PORT_FRONTEND en .env y vuelve a intentarlo."
  exit 1
fi

# 3) Construir y arrancar (migraciones primero, sin exponer nada público)
echo "== Construyendo imágenes (la primera vez tarda varios minutos) =="
docker compose build

echo "== Aplicando migraciones de base de datos =="
docker compose up -d postgres redis
docker compose run --rm migrate

echo "== Arrancando servicios =="
docker compose up -d

echo "== Estado =="
sleep 5
docker compose ps
echo
echo "Comprobación de salud:"
docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/healthz || \
  echo "(la API aún está arrancando; reintenta en unos segundos: bash scripts/status.sh)"

cat << FINAL

============================================================
Despliegue completado.
  Panel (local):   http://127.0.0.1:${PUERTO}
  API (local):     http://127.0.0.1:${PORT_API:-8086}/api/v1/healthz

Siguientes pasos:
  1. Crea el administrador:
       docker compose exec -T -e HJ_BOOTSTRAP_PASSWORD='UnaClaveMuyLarga123' api \
         python scripts/bootstrap_admin.py --email tu@dominio.com
  2. Accede por túnel SSH mientras no haya HTTPS:
       ssh -L 8085:127.0.0.1:${PUERTO} root@169.58.36.6
     y abre http://127.0.0.1:8085 en tu equipo (o usa la app de túnel en Android).
  3. Configura el webhook SNS (ver docs/DEPLOYMENT.md).
  4. Realiza UN envío de prueba autorizado con SES.

El proyecto anterior en /opt/campanas no ha sido tocado.
============================================================
FINAL
