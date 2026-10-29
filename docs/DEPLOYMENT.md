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
| `PUBLIC_BASE_URL` / `API_BASE_URL` | `http://169.58.36.6:8085` durante la puesta en marcha; el dominio HTTPS cuando exista |

Variables opcionales:

| Variable | Cuándo |
|---|---|
| Credenciales AWS (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) | Solo si no usas `/root/.aws` como secreto (ver §6). Si ya existe `/root/.aws/credentials` en el VPS, **déjalas vacías**: `scripts/deploy.sh` montará los archivos como secretos de solo lectura |
| `AWS_PROFILE` | Solo si tu perfil no se llama `default` |
| `WEBHOOK_TOKEN` | Solo para el webhook HTTP alternativo con HTTPS (ver §4). Con la ingesta por SQS no es necesario |

Valida todo **sin desplegar**:

```bash
bash scripts/deploy.sh --check
```

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

## 4. Eventos de SES (entregas, rebotes, quejas) — vía SQS, sin endpoint público

Uso personal y privado ⇒ **no se publica ningún endpoint HTTP a Internet**. La ruta
recomendada es una cola SQS que el worker consume por long polling (solo conexiones
salientes HTTPS):

```
SES (conjunto de configuración) ──eventos──▶ SNS ──suscripción──▶ SQS
                                                              ▲
                                       worker de HJ Campaigns (long polling)
```

El script de configuración es idempotente y crea todo lo necesario (cola + DLQ,
tópico, políticas mínimas, suscripción y destino de eventos en SES):

```bash
docker compose exec -T api python scripts/setup_ses_events.py             # muestra el plan
docker compose exec -T api python scripts/setup_ses_events.py --aplicar   # ejecuta cambios
```

Al terminar, el script imprime la URL de la cola. Añádela al `.env`:

```bash
AWS_SQS_EVENTS_QUEUE_URL=https://sqs.us-east-2.amazonaws.com/TU_CUENTA/hj-ses-eventos
docker compose up -d events-worker    # arranca el consumidor dedicado de eventos
```

Detalles de seguridad:

- La cola solo acepta mensajes del tópico SNS creado (política con `aws:SourceArn`).
- El tópico solo acepta publicaciones de SES de tu cuenta (`AWS:SourceAccount`).
- El sobre SNS llega con firma **SignatureVersion 1** que el worker verifica criptográficamente antes de procesar (configurable con `SQS_VERIFY_SNS_SIGNATURE`).
- Idempotencia por `MessageId` de SNS y por uid de evento; mensaje venenoso → DLQ (`hj-ses-eventos-dlq`) y descarte controlado tras `SQS_MAX_RECEIVE_COUNT` lecturas.
- Permisos IAM del usuario `ses-vps-leisureparts`: además de SES, necesita SQS/SNS (el script imprime la política exacta si le falta algo). Tras la primera configuración puedes revocar los permisos de creación y dejar solo `sqs:ReceiveMessage`, `sqs:DeleteMessage*`, `sqs:GetQueue*`.

### Alternativa: webhook HTTP (solo con HTTPS)

Si algún día publicas el panel con HTTPS (§5), puedes usar el endpoint existente
`/api/v1/webhooks/sns?token=…` (token `WEBHOOK_TOKEN` + verificación de firma).
Mientras el acceso sea por túnel SSH/HTTP puro, usa SQS: no requiere exponer nada.

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

Luego en `.env`: `PUBLIC_BASE_URL=https://panel.tudominio.com`, `API_BASE_URL=https://panel.tudominio.com`, `COOKIE_SECURE=true` y `docker compose up -d` para aplicar. Con HTTPS ya podría usarse también el webhook HTTP alternativo (§4).

Alternativa sin dominio: VPN (WireGuard/Tailscale) y acceder por la IP privada; mantener `BIND_IP=127.0.0.1` + túnel.

## 6. Credenciales SES en el VPS (implementado)

Opción A (**recomendada y ya integrada**): los archivos `/root/.aws/credentials` y
`/root/.aws/config` se copian como **secretos Docker de solo lectura** (UID del
contenedor 10001, modo 400) a `secrets/aws/` — fuera del repositorio — y se montan
en `api`, `worker` y `scheduler` vía `/run/secrets/`. Boto3 los lee con
`AWS_SHARED_CREDENTIALS_FILE`/`AWS_CONFIG_FILE` y el perfil `AWS_PROFILE`
(por defecto `default`, región `us-east-2`).

```bash
sudo bash scripts/deploy.sh        # prepara secrets/aws desde /root/.aws y despliega
bash scripts/deploy.sh --check     # valida sin desplegar
```

- No se copian credenciales al `.env` ni al repositorio (`secrets/` está en `.gitignore`).
- Si ejecutas sin ser root y no puede leer `/root/.aws`, el script te lo dirá con claridad.
- Región forzada por defecto: `AWS_DEFAULT_REGION=us-east-2`.

Opción B: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` en `.env` (usuario IAM
dedicado `ses-vps-leisureparts`; si una de las dos está definida y la otra no, el
despliegue falla con error claro). En los contenedores, una variable **vacía** se
elimina automáticamente para no romper la cadena de credenciales de Boto3.

Permisos mínimos para envíos: `sesv2:SendEmail`, `sesv2:GetAccountSendingCapabilities`,
`sesv2:ListEmailIdentities`. Para la ingesta de eventos por SQS (§4) añade los
permisos que imprime `setup_ses_events.py`.

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
7. Configurar la ingesta de eventos por SQS (§4) y ver entregas reales. ✅
8. Rebote/queja (envío a una dirección inválida o queja real) → supresión automática. ✅
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
