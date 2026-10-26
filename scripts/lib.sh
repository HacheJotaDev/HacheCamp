#!/usr/bin/env bash
# ============================================================ #
# HJ Campaigns — funciones compartidas de los scripts          #
#                                                              #
# Se importa desde los demás scripts:                          #
#   source "$(dirname "$0")/lib.sh"                            #
#                                                              #
# Seguridad del .env:                                          #
#   - cargar_env_seguro NO ejecuta nada del archivo: solo      #
#     acepta líneas CLAVE=valor con nombres válidos.           #
#   - Ignora variables protegidas del shell (PATH, IFS...).    #
#   - Los errores de configuración se muestran con claridad.   #
# ============================================================ #

# Evitar doble importación
[[ -n "${HJ_LIB_CARGADA:-}" ]] && return 0
HJ_LIB_CARGADA=1

# Globos extendidos para el recorte de comentarios en línea
shopt -s extglob

# --- Mensajes ----------------------------------------------------------------

_msg_color() { # $1=código color  $2=texto
  if [[ -t 1 ]]; then printf '\033[%sm%s\033[0m\n' "$1" "$2"; else printf '%s\n' "$2"; fi
}
ok()      { _msg_color "0;32" "✓ $*"; }
aviso()   { _msg_color "0;33" "! $*"; }
error()   { _msg_color "0;31" "✗ ERROR: $*" >&2; }
info()    { _msg_color "0;36" "== $* =="; }

# --- Carga segura de .env ------------------------------------------------------

# Variables que .env no puede sobrescribir (protección del shell y del entorno).
# Nota: ENV es una variable legítima de la aplicación y se permite.
_HJ_VARS_PROTEGIDAS="PATH HOME PWD OLDPWD SHELL IFS BASH_ENV SHELLOPTS POSIXLY_CORRECT LD_PRELOAD LD_LIBRARY_PATH PS1 PS2 PS4 BASH_FUNC"

_es_protegida() {
  local v
  for v in $_HJ_VARS_PROTEGIDAS; do
    [[ "$1" == "$v" ]] && return 0
  done
  return 1
}

# Uso: cargar_env_seguro [archivo]   (por defecto .env)
# Devuelve 1 con mensaje claro si el archivo no existe o tiene líneas inválidas.
cargar_env_seguro() {
  local archivo="${1:-.env}"
  if [[ ! -f "$archivo" ]]; then
    error "no existe el archivo '$archivo'. Copia la plantilla: cp .env.example .env"
    return 1
  fi
  local n=0 linea clave valor original
  while IFS= read -r linea || [[ -n "$linea" ]]; do
    n=$((n + 1))
    linea="${linea%$'\r'}"                                   # fin de línea Windows
    linea="${linea#"${linea%%[![:space:]]*}"}"               # recorte izquierda
    linea="${linea%"${linea##*[![:space:]]}"}"               # recorte derecha
    [[ -z "$linea" || "$linea" == \#* ]] && continue         # vacía o comentario
    original="$linea"
    linea="${linea#export }"
    clave="${linea%%=*}"
    if [[ "$clave" == "$linea" ]]; then
      error "$archivo línea $n: no contiene '=' (se esperaba CLAVE=valor): $(printf '%q' "${original:0:60}")"
      return 1
    fi
    valor="${linea#*=}"
    if [[ ! "$clave" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      error "$archivo línea $n: nombre de variable inválido '$clave' (usa letras, números y _)"
      return 1
    fi
    if _es_protegida "$clave"; then
      aviso "$archivo línea $n: variable protegida '$clave' ignorada"
      continue
    fi
    # Sin comillas: quita comentario en línea "# ..." (tras espacio en blanco)
    if [[ "$valor" != \"* && "$valor" != \'* ]]; then
      valor="${valor%%+([[:space:]])\#*}"
      valor="${valor%"${valor##*[![:space:]]}"}"
    fi
    # Quita comillas envolventes simples o dobles
    if [[ ${#valor} -ge 2 ]]; then
      if [[ "$valor" == \'*\' ]]; then valor="${valor:1:${#valor}-2}"
      elif [[ "$valor" == \"*\" ]]; then valor="${valor:1:${#valor}-2}"
      fi
    fi
    printf -v "$clave" '%s' "$valor"
    export "$clave"
  done < "$archivo"
}

# --- Validación de configuración -----------------------------------------------

# Uso: validar_configuracion   (requiere cargar_env_seguro antes)
# Muestra los problemas por pantalla y devuelve 1 si hay errores graves.
validar_configuracion() {
  local fallos=0

  # SECRET_KEY: obligatoria, >= 32 caracteres, no la de la plantilla
  if [[ -z "${SECRET_KEY:-}" ]]; then
    error "define SECRET_KEY en .env (genera una: python3 -c \"import secrets; print(secrets.token_hex(32))\")"
    fallos=$((fallos + 1))
  elif [[ "${SECRET_KEY}" == CAMBIA* ]]; then
    error "SECRET_KEY sigue siendo la de la plantilla. Genera una real y pégala en .env"
    fallos=$((fallos + 1))
  elif [[ ${#SECRET_KEY} -lt 32 ]]; then
    error "SECRET_KEY demasiado corta (${#SECRET_KEY} caracteres; mínimo 32)"
    fallos=$((fallos + 1))
  fi

  # POSTGRES_PASSWORD
  if [[ -z "${POSTGRES_PASSWORD:-}" || "${POSTGRES_PASSWORD}" == CAMBIA* ]]; then
    error "define POSTGRES_PASSWORD en .env (genera una: openssl rand -base64 24)"
    fallos=$((fallos + 1))
  elif [[ ${#POSTGRES_PASSWORD} -lt 8 ]]; then
    error "POSTGRES_PASSWORD demasiado corta (mínimo 8 caracteres)"
    fallos=$((fallos + 1))
  fi

  # DATABASE_URL coherente con POSTGRES_USER/PASSWORD/DB (evita el fallo típico
  # de cambiar la contraseña en un sitio y no en el otro)
  if [[ -n "${DATABASE_URL:-}" && -n "${POSTGRES_PASSWORD:-}" ]]; then
    if [[ "$DATABASE_URL" == *CAMBIA* ]]; then
      error "DATABASE_URL contiene 'CAMBIA': sustitúyela por la contraseña real de POSTGRES_PASSWORD"
      fallos=$((fallos + 1))
    elif [[ "$POSTGRES_PASSWORD" =~ ^[A-Za-z0-9._-]+$ && "$DATABASE_URL" != *":${POSTGRES_PASSWORD}@"* ]]; then
      error "DATABASE_URL no coincide con POSTGRES_PASSWORD. Usa exactamente:"
      error "  DATABASE_URL=postgresql+psycopg://${POSTGRES_USER:-hj}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-hj_campaigns}"
      fallos=$((fallos + 1))
    fi
  fi

  # Puertos
  local p
  for p in "${PORT_FRONTEND:-8085}" "${PORT_API:-8086}"; do
    if ! [[ "$p" =~ ^[0-9]+$ ]] || (( p < 1024 || p > 65535 )); then
      error "puerto inválido '$p' (usa un número entre 1024 y 65535)"
      fallos=$((fallos + 1))
    fi
  done

  # BIND_IP
  case "${BIND_IP:-127.0.0.1}" in
    127.0.0.1) : ;;  # recomendado: acceso privado por túnel SSH
    0.0.0.0)
      aviso "BIND_IP=0.0.0.0 expone el panel a Internet: solo con proxy inverso HTTPS delante" ;;
    *)
      if ! [[ "${BIND_IP}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        error "BIND_IP inválido '${BIND_IP}' (usa 127.0.0.1, 0.0.0.0 o la IP del servidor)"
        fallos=$((fallos + 1))
      fi ;;
  esac

  # WEBHOOK_TOKEN: opcional con la ingesta por SQS
  if [[ -z "${WEBHOOK_TOKEN:-}" ]]; then
    aviso "WEBHOOK_TOKEN vacío: el webhook SNS HTTP queda deshabilitado (no afecta a la ingesta por SQS)"
  fi

  # AWS: credenciales por archivo (recomendado) o variables
  if [[ -n "${AWS_ACCESS_KEY_ID:-}" && -z "${AWS_SECRET_ACCESS_KEY:-}" ]] || \
     [[ -z "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    error "define AMBAS AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY en .env, o ninguna (y usa /root/.aws)"
    fallos=$((fallos + 1))
  fi

  if (( fallos > 0 )); then
    error "$fallos problema(s) de configuración. Corrige .env y vuelve a ejecutar."
    return 1
  fi
  return 0
}

# --- Secretos AWS ---------------------------------------------------------------

# Uso: preparar_secretos_aws [--silencioso]
# Copia /root/.aws a secrets/aws/ con propietario UID 10001 y modo 400 (solo el
# contenedor puede leerlos). Sin /root/.aws, usa variables del .env o deja
# archivos vacíos para que compose valide y SES quede simplemente sin configurar.
preparar_secretos_aws() {
  local silencio="${1:-}"
  local uid_cont=10001   # mantener en sincronía con backend/Dockerfile (useradd --uid 10001)
  mkdir -p secrets/aws

  # Comprobación de sincronía con la imagen
  if [[ -f backend/Dockerfile ]] && ! grep -q -- "--uid ${uid_cont}" backend/Dockerfile; then
    aviso "el UID del contenedor (${uid_cont}) no coincide con backend/Dockerfile; revisa el permiso de lectura"
  fi

  if [[ -r /root/.aws/credentials ]]; then
    if [[ "$(id -u)" != "0" ]]; then
      error "/root/.aws existe pero necesitas ser root para prepararlo: ejecuta 'sudo bash scripts/deploy.sh'"
      error "(alternativa: define AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY en .env)"
      return 1
    fi
    install -m 400 -o "$uid_cont" /root/.aws/credentials secrets/aws/credentials
    if [[ -r /root/.aws/config ]]; then
      install -m 400 -o "$uid_cont" /root/.aws/config secrets/aws/config
    else
      printf '[default]\nregion=%s\n' "${AWS_REGION:-us-east-2}" > /tmp/hj_aws_config
      install -m 400 -o "$uid_cont" /tmp/hj_aws_config secrets/aws/config
      rm -f /tmp/hj_aws_config
    fi
    [[ "$silencio" != "--silencioso" ]] && ok "credenciales AWS preparadas en secrets/aws/ (UID ${uid_cont}, modo 400, región ${AWS_REGION:-us-east-2})"
    return 0
  fi

  if [[ -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    # Boto3 usará las variables de entorno; placeholders vacíos para que compose valide
    [[ -f secrets/aws/credentials ]] || : > secrets/aws/credentials
    [[ -f secrets/aws/config ]] || printf '[default]\nregion=%s\n' "${AWS_REGION:-us-east-2}" > secrets/aws/config
    chmod 600 secrets/aws/credentials secrets/aws/config 2>/dev/null || true
    aviso "usando credenciales AWS del .env (variables AWS_*); lo recomendado es /root/.aws como secreto"
    return 0
  fi

  # Sin credenciales: placeholders vacíos (SES quedará sin configurar, con aviso claro)
  [[ -f secrets/aws/credentials ]] || : > secrets/aws/credentials
  [[ -f secrets/aws/config ]] || printf '[default]\nregion=%s\n' "${AWS_REGION:-us-east-2}" > secrets/aws/config
  chmod 644 secrets/aws/credentials secrets/aws/config 2>/dev/null || true
  aviso "sin credenciales de AWS (ni /root/.aws ni AWS_* en .env): SES/SQS quedarán sin configurar"
  aviso "para activarlos más tarde ejecuta de nuevo este script como root, o rellena AWS_* en .env"
  return 0
}

# --- Salud de la API --------------------------------------------------------------

# Espera hasta que la API responda dentro del contenedor (curl verificado en la
# imagen; respaldo con Python estándar si no estuviera disponible).
esperar_salud_api() {
  local intentos="${1:-24}" espera="${2:-5}" i=0
  info "Esperando la salud de la API (máx. $(( intentos * espera )) s)"
  while (( i < intentos )); do
    if docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/healthz >/dev/null 2>&1; then
      ok "API saludable"
      docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/healthz
      printf '\n'
      return 0
    fi
    if docker compose exec -T api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/healthz', timeout=3)" >/dev/null 2>&1; then
      ok "API saludable (vía Python: curl no disponible en la imagen)"
      return 0
    fi
    i=$((i + 1))
    sleep "$espera"
  done
  aviso "la API no respondió a tiempo; revisa: docker compose logs api"
  return 1
}
