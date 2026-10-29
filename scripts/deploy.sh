#!/usr/bin/env bash
# ============================================================ #
# HJ Campaigns — despliegue inicial en el VPS                  #
#                                                              #
# Uso (como root en el VPS):                                   #
#   1. git clone https://github.com/HacheJotaDev/HacheCamp.git /opt/hj-campaigns
#   2. cd /opt/hj-campaigns && cp .env.example .env && nano .env
#   3. bash scripts/deploy.sh            (despliega)           #
#      bash scripts/deploy.sh --check    (solo valida)         #
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
SOLO_CHECK=false
[[ "${1:-}" == "--check" ]] && SOLO_CHECK=true

source scripts/lib.sh

info "HJ Campaigns — despliegue"
echo "Proyecto: ${PROYECTO}"

# 1) Comprobaciones previas ----------------------------------------------------
command -v docker >/dev/null || { error "Docker no está instalado"; exit 1; }
docker compose version >/dev/null 2>&1 || { error "docker compose no está disponible"; exit 1; }

[[ -f .env ]] || { error "falta el archivo .env (copia la plantilla: cp .env.example .env)"; exit 1; }

# Permisos del .env: nunca legible por otros usuarios
PERMS_ACTUALES="$(stat -c '%a' .env 2>/dev/null || echo '600')"
if [[ "$PERMS_ACTUALES" != "600" && "$PERMS_ACTUALES" != "400" ]]; then
  chmod 600 .env && ok "permisos de .env ajustados a 600 (antes ${PERMS_ACTUALES})"
fi

# 2) Carga y validación SEGURA del .env ----------------------------------------
# No se ejecuta nada del archivo: solo pares CLAVE=valor con nombres válidos.
cargar_env_seguro .env
validar_configuracion || exit 1
ok "configuración validada (SECRET_KEY de ${#SECRET_KEY} caracteres, puerto ${PORT_FRONTEND:-8085})"

# 3) Credenciales AWS como secretos Docker (api, worker y scheduler) ------------
preparar_secretos_aws

# 4) Puerto del panel libre ------------------------------------------------------
PUERTO="${PORT_FRONTEND:-8085}"
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${PUERTO}$"; then
  error "el puerto ${PUERTO} ya está en uso en este host"
  error "elige otro PORT_FRONTEND en .env y vuelve a intentarlo"
  exit 1
fi

# 5) Validación del docker-compose.yml (sin arrancar nada) ----------------------
if ! docker compose config --quiet; then
  error "docker-compose.yml o .env tienen errores (ver arriba)"
  exit 1
fi
ok "docker compose config válido"

if $SOLO_CHECK; then
  ok "modo --check: validación completada sin desplegar nada"
  exit 0
fi

# 6) Construir y arrancar (migraciones primero, sin exponer nada público) -------
info "Construyendo imágenes (la primera vez tarda varios minutos)"
docker compose build

info "Aplicando migraciones de base de datos"
docker compose up -d postgres redis
docker compose run --rm migrate

info "Arrancando servicios"
docker compose up -d
sleep 5
docker compose ps

# 7) Comprobación de salud (curl viene instalado en la imagen del backend) ------
esperar_salud_api 24 5 || true

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
       ssh -L ${PUERTO}:127.0.0.1:${PUERTO} root@TU_SERVIDOR
     y abre http://127.0.0.1:${PUERTO} en tu equipo (o usa la app de túnel en Android).
  3. Activa los eventos de SES por SQS (canal privado, sin endpoint público):
       docker compose exec -T api python scripts/setup_ses_events.py            # plan
       docker compose exec -T api python scripts/setup_ses_events.py --aplicar  # ejecuta
     y añade AWS_SQS_EVENTS_QUEUE_URL=<cola> a .env; luego: docker compose up -d events-worker
  4. Verifica SES y realiza UN envío de prueba autorizado:
       docker compose exec -T api python scripts/check_ses.py

El proyecto anterior en /opt/campanas no ha sido tocado.
============================================================
FINAL
