# Despliegue de HJ Campaigns en el VPS

> Objetivo: instalar la plataforma en `/opt/hj-campaigns` (Contabo, Ubuntu 24.04, 169.58.36.6) **sin tocar el proyecto anterior** de `/opt/campanas` ni sus servicios.

## 0. Auditoría previa (solo lectura)

Antes de instalar, ejecuta y guarda el resultado:

```bash
# Estado general
uname -a; df -h /; free -h
docker --version; docker compose version

# Contenedores existentes (no detener nada)
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Puertos ocupados: confirma que 8085 y 8086 están libres
ss -ltnp | sort -k4

# Proyecto anterior intacto
ls /opt/campanas
docker volume ls | grep campanas || true

# Conectividad con SES (debe responder 200)
curl -sI https://email.us-east-2.amazonaws.com | head -1
```

Si `8085` estuviera ocupado, elige otro `PORT_FRONTEND` en `.env`; nunca detengas servicios existentes.

## 1. Instalación

```bash
git clone https://github.com/HacheJotaDev/HacheCamp.git /opt/hj-campaigns
cd /opt/hj-campaigns
cp .env.example .env
chmod 600 .env
nano .env
```

Valores obligatorios del `.env`:

| Variable | Cómo generarla |
|---|---|
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `POSTGRES_PASSWORD` y la clave dentro de `DATABASE_URL` | `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` (la misma en ambas) |
| `WEBHOOK_TOKEN` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Del usuario IAM `ses-vps-leisureparts` (solo permisos SES). Si el VPS ya tiene `~/.aws/credentials` válidos, pueden dejarse vacías y montarlas como secreto de solo lectura (ver §6) |
| `PUBLIC_BASE_URL` / `API_BASE_URL` | `http://169.58.36.6:8085` durante la puesta en marcha; el dominio HTTPS cuando exista |

Despliegue:

```bash
bash scripts/deploy.sh
```

El script: construye imágenes → arranca `postgres`/`redis` → aplica migraciones (`migrate`) → levanta `api`, `worker`, `scheduler`, `frontend` → comprueba `/api/v1/healthz`. **No inicia ningún envío.**

## 2. Administrador inicial

```bash
docker compose exec -T api python scripts/bootstrap_admin.py --email tu@correo.com
# o con contraseña propia (≥12 caracteres, con número y letra):
docker compose exec -T -e HJ_BOOTSTRAP_PASSWORD='TuClaveLarga123' api \
  python scripts/bootstrap_admin.py --email tu@correo.com
```

El comando siembra también las 5 plantillas del sistema. Inicia sesión y **configura MFA** (Ajustes → Cuenta): es obligatorio para administradores.

## 3. Acceso seguro sin HTTPS (túnel SSH)

La pila se publica en `127.0.0.1:8085` por defecto: no hay login expuesto a Internet.

**Desde tu equipo:**

```bash
ssh -L 8085:127.0.0.1:8085 root@169.58.36.6
# abre http://127.0.0.1:8085 en el navegador local
```

**Desde Android (Termux):**

```bash
pkg install openssh
ssh -L 8085:127.0.0.1:8085 root@169.58.36.6
# abre http://127.0.0.1:8085 en el navegador del teléfono
```

Alternativa en Android sin Termux: apps de túnel SSH (p. ej. «SSH Tunnel» o similar) configurando local 8085 → `127.0.0.1:8085` del VPS.

> Publicar `BIND_IP=0.0.0.0` sin HTTPS queda expresamente desaconsejado: expondría credenciales en claro.

## 4. Webhook de eventos SNS (entregas, rebotes, quejas)

1. Consola AWS → SNS (us-east-2) → crea un tópico estándar, p. ej. `hj-campaigns-eventos`.
2. SES → Configuration sets (o identidades) → suscribe eventos (bounces, complaints, deliveries, send, reject) al tópico.
3. Suscripción HTTP al endpoint:
   ```
   http://169.58.36.6:8085/api/v1/webhooks/sns?token=VALOR_DE_WEBHOOK_TOKEN
   ```
   SNS enviará `SubscriptionConfirmation`; la API confirma automáticamente la URL (validada contra hosts Amazon).
4. Opcional pero recomendado: restringe `SNS_TOPIC_ARNS` en `.env` al ARN del tópico creado.

La firma SNS se verifica criptográficamente en cada notificación; las notificaciones sin firma válida se rechazan (403) y quedan en el log.

> Mientras el acceso sea por HTTP puro o túnel, SNS solo puede alcanzar el webhook si el puerto 8085 es alcanzable por AWS. Para producción con HTTPS (siguiente sección) esto queda resuelto de forma limpia.

## 5. Publicación por HTTPS (recomendado antes de usar en serio)

Opción Caddy (certificados automáticos con dominio):

```bash
apt install caddy   # o binario oficial
# /etc/caddy/Caddyfile:
#   panel.tudominio.com {
#       reverse_proxy 127.0.0.1:8085
#   }
systemctl reload caddy
```

Luego en `.env`: `PUBLIC_BASE_URL=https://panel.tudominio.com`, `API_BASE_URL=https://panel.tudominio.com`, `COOKIE_SECURE=true` y `docker compose up -d` para aplicar. El webhook pasa a `https://panel.tudominio.com/api/v1/webhooks/sns?token=…`.

Alternativa sin dominio: VPN (WireGuard/Tailscale) y acceder por la IP privada; mantener `BIND_IP=127.0.0.1` + túnel.

## 6. Credenciales SES en el VPS

Opción A (recomendada por el encargo si ya existe `~/.aws/credentials`): **no** copiar claves al `.env`; en su lugar dar al contenedor acceso de solo lectura al perfil:

```yaml
# docker-compose.override.yml (local, no versionado)
services:
  api:
    volumes:
      - /root/.aws/credentials:/home/hj/.aws/credentials:ro
      - /root/.aws/config:/home/hj/.aws/config:ro
```

Boto3 usará automáticamente la cadena de credenciales del host.

Opción B: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` en `.env` (usuario IAM dedicado, mínimos permisos: `sesv2:SendEmail`, `sesv2:GetAccountSendingCapabilities`, `sesv2:ListEmailIdentities`).

Verificación (sin revelar claves):

```bash
docker compose exec api python scripts/check_ses.py
# envío real de prueba (pide confirmación interactiva):
docker compose exec api python scripts/check_ses.py --send-to tu@correo.com
```

> Si la cuenta SES está en **sandbox**, solo puede enviar a direcciones verificadas. Solicita producción en la consola SES antes de campañas reales.

## 7. Verificación final (criterios de aceptación)

```bash
bash scripts/status.sh                       # servicios + salud + cola
docker compose exec api curl -fsS http://127.0.0.1:8000/api/v1/healthz
```

1. Iniciar sesión con el administrador y activar MFA. ✅
2. Crear/editar una plantilla y previsualizarla (escritorio y móvil). ✅
3. Importar contactos con consentimiento (usa la simulación primero). ✅
4. Crear una campaña desde plantilla, dirigida a una lista. ✅
5. Aprobar (admin) y enviar **una prueba** autorizada con SES. ✅
6. Programar / pausar / cancelar. ✅
7. Métricas reales en el panel (tras eventos SNS). ✅
8. Rebote/queja simulados en el tópico SNS → supresión automática. ✅
9. Baja desde el pie de un correo → supresión e impedimento de futuros envíos. ✅
10. `docker compose restart` → los datos persisten (volúmenes). ✅
11. `/opt/campanas` y sus contenedores intactos. ✅

## 8. Actualizaciones

```bash
bash scripts/update.sh    # copia de seguridad + git pull + build + migrate + up
```

## 9. Desinstalación limpia (si algún día hiciera falta)

```bash
docker compose down          # detiene y elimina contenedores/red (conserva volúmenes)
docker compose down -v       # además borra datos — SOLO si estás seguro
```
