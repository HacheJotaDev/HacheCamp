# Actualización a v2.0.0 en el VPS (Git + Docker Compose)

Guía para aplicar la versión PRO sin perder datos y con plan de reversión.
Ruta del proyecto en el VPS: `/opt/hj-campaigns` (clon de `HacheJotaDev/HacheCamp`).

> Este procedimiento no toca otros proyectos del servidor (`/opt/campanas`,
> `/opt/ses-pruebas` siguen intactos). **Tampoco modifica tu `.env`**: el panel
> conserva el acceso que ya tienes (IP pública del VPS + puerto 8085). Desde
> esta versión la separación es explícita: la API se vincula a
> `BIND_IP_API` (127.0.0.1, privada) y el panel a `BIND_IP_FRONTEND` (público
> en tu VPS). Se comprueba antes y después del despliegue en el paso 3.

## 0. Comprobar el acceso actual (no cambiarlo)

```bash
cd /opt/hj-campaigns
grep -E '^(BIND_IP|BIND_IP_API|BIND_IP_FRONTEND|PORT_FRONTEND|PORT_API)=' .env
ss -ltnp | grep -E ':8085|:8086'   # anota la vinculación actual (p. ej. 0.0.0.0:8085)
```

Anota esa salida: tras la actualización debe ser **idéntica**. Si hoy accedes
por `http://IP-PÚBLICA:8085` y la API queda privada, el comportamiento objetivo
es: `BIND_IP_FRONTEND=0.0.0.0` + `BIND_IP_API=127.0.0.1` (o sin definir).
**Añade esas dos líneas a `.env` ANTES de `docker compose up -d`** (ver paso 3):
el nuevo compose usa variables por servicio y sin ellas el panel quedaría
privado por defecto.

## 1. Preparación

```bash
cd /opt/hj-campaigns
git fetch origin
git status                 # debe estar limpio (sin cambios locales)
git log --oneline -1       # anota el commit actual por si hay que revertir
```

## 2. Respaldo previo (obligatorio)

> **Respaldos en caliente**: un `tar` del volumen `pgdata` con PostgreSQL en
> marcha **no garantiza consistencia** (puede copiar un estado intermedio del
> disco). El respaldo obligatorio de esta actualización es **`pg_dump`** (copia
> lógica consistente vía snapshot MVCC de PostgreSQL). La copia física es solo
> un extra opcional con `pg_basebackup` (mecanismo consistente de PostgreSQL).

```bash
cd /opt/hj-campaigns
mkdir -p /root/respaldos
FECHA=$(date +%Y%m%d_%H%M%S)
docker volume ls | grep hj_campaigns   # anota los nombres reales de los volúmenes

# 2a. Dump lógico consistente (contactos, campañas, mensajes, eventos, supresiones)
#     pipefail: si pg_dump falla a mitad, el pipeline NO se traga el error
set -o pipefail
docker compose exec -T postgres pg_dump -U hj -d hj_campaigns \
  | gzip > /root/respaldos/hj_v2_${FECHA}.sql.gz
set +o pipefail

# 2b. Verificación OBLIGATORIA: tamaño no nulo, gzip íntegro y dump completo
test -s /root/respaldos/hj_v2_${FECHA}.sql.gz || { echo "ERROR: dump SQL vacío"; exit 1; }
gzip -t /root/respaldos/hj_v2_${FECHA}.sql.gz || { echo "ERROR: gzip corrupto"; exit 1; }
gunzip -c /root/respaldos/hj_v2_${FECHA}.sql.gz | tail -1 \
  | grep -q "PostgreSQL database dump complete" \
  || { echo "ERROR: dump SQL incompleto (repetir el respaldo)"; exit 1; }
ls -lh /root/respaldos/

# 2c. Restauración de PRUEBA (no destructiva): carga el dump en un contenedor
#     desechable sin tocar la base de datos real ni el volumen hj_campaigns_pgdata
docker run -d --rm --name hj_prueba_restaurar -e POSTGRES_PASSWORD=prueba \
  -e POSTGRES_DB=hj_campaigns -e POSTGRES_USER=hj postgres:16-alpine
for i in $(seq 1 30); do
  docker exec hj_prueba_restaurar pg_isready -U hj -d hj_campaigns -q && break
  sleep 1
done
gunzip -c /root/respaldos/hj_v2_${FECHA}.sql.gz | \
  docker exec -i hj_prueba_restaurar psql -U hj -d hj_campaigns -q \
  || { echo "ERROR: el dump NO es restaurable"; docker rm -f hj_prueba_restaurar; exit 1; }
docker exec hj_prueba_restaurar psql -U hj -d hj_campaigns -tAc \
  "SELECT 'contactos='||count(*) FROM contacts"   # debe devolver contactos=N (N real)
docker exec hj_prueba_restaurar psql -U hj -d hj_campaigns -tAc \
  "SELECT 'campanas='||count(*) FROM campaigns"   # debe devolver campanas=N (N real)
docker rm -f hj_prueba_restaurar                   # borra el contenedor y su volumen temporal
```

### 2d. (Opcional) Copia física consistente con pg_basebackup

Si además quieres copia física, usa **`pg_basebackup`** (replica en caliente
con el WAL, produce una copia consistente por diseño), nunca un `tar` del
volumen en marcha:

```bash
# Requisitos: contenedor postgres en marcha; espacio libre suficiente en el
# VPS (copia completa del clúster); la imagen oficial postgres:16 permite
# replicación local (pg_basebackup se conecta por socket interno al contenedor).
# -u postgres: pg_basebackup se niega a correr como root (usuario por defecto
# de docker exec en la imagen oficial).
docker compose exec -u postgres postgres pg_basebackup -U hj -D /tmp/basebk -Ft -X stream -P -c fast
docker compose cp postgres:/tmp/basebk/base.tar.gz /root/respaldos/pgbase_${FECHA}.tar.gz
docker compose exec postgres rm -rf /tmp/basebk
gzip -t /root/respaldos/pgbase_${FECHA}.tar.gz && ls -lh /root/respaldos/
```

> Si `pg_basebackup` devolviera un error de conexión de replicación en tu
> instalación, no bloquees la actualización: el respaldo obligatorio ya es
> consistente (2a–2c). Documenta el error y revisa `pg_hba.conf` con tu
> administrador.

## 3. Actualización y despliegue

> Nota: si aún no se fusionó la PR #1, sustituye `git pull origin main` por
> `git checkout feature/pro-v2 && git pull origin feature/pro-v2` (o mergea
> primero la PR y usa `main`). Si tenías `docker-compose.yml` modificado
> localmente (puertos de API/frontend), la rama ya expresa esa separación con
> `BIND_IP_API`/`BIND_IP_FRONTEND`: conserva tu copia por si acaso
> (`cp docker-compose.yml /root/compose.anterior.yml`), restaura el del repo
> (`git checkout -- docker-compose.yml`) y añade al `.env`:
> `BIND_IP_FRONTEND=0.0.0.0` (panel público) y `BIND_IP_API=127.0.0.1`
> (API privada).

```bash
git pull origin main          # o: git checkout v2.0.0 si se etiqueta
docker compose build          # reconstruye backend (incluye premailer) y frontend
docker compose up -d          # migrate corre primero (alembic upgrade head)
docker compose ps             # postgres/redis/api/worker/scheduler/frontend Up
                              # worker y scheduler: healthy por heartbeat (~1 min)
docker compose logs --since 5m api worker scheduler | tail -60
```

Verificaciones rápidas:

```bash
curl -s http://127.0.0.1:8086/api/v1/healthz | head -5
# {"estado":"ok"|"degradado", ...}  (ses sin verificar si no hay salida a AWS en ese momento)
bash scripts/deploy.sh --check  # validación completa opcional

# ACCESO: idéntico al anotado en el paso 0 (no debe cambiar)
grep -E '^(BIND_IP_API|BIND_IP_FRONTEND|PORT_FRONTEND|PORT_API)=' .env
ss -ltnp | grep -E ':8085|:8086'
# worker y scheduler deben pasar a "healthy" (heartbeat) en ~1-2 min:
docker compose ps worker scheduler
```

Estado SES y eventos (honesto: solo datos reales):

```bash
curl -s http://127.0.0.1:8086/api/v1/system/ses/status | head -3
# cuota.max_24h debe ser 50000 (vía GetAccount) y reputacion.envio_habilitado true
docker compose logs worker | grep -i "Eventos SES por SQS" | tail -2
# si dice "deshabilitados": entregas/rebotes/quejas NO se miden todavía;
# conexión y verificación del flujo completo: docs/Eventos_SES_SQS.md
# permisos IAM mínimos (ses:SendEmail, no ses:SendRawEmail): docs/SES_PERMISOS.md
```

Abre el panel como lo haces siempre (misma URL que antes de actualizar) y
verifica: contactos visibles con su estado, campañas y estadísticas históricas
intactas. Prueba funcional mínima: crear contacto con solo el correo, importar
un TXT, pegar HTML con `<style>` en una plantilla y ver la vista previa con
los estilos.

## 4. Reversión

### 4a. Volver al código anterior (habitual, sin tocar datos)

```bash
cd /opt/hj-campaigns
git checkout <commit-anterior>       # el que anotaste en el paso 1
docker compose build && docker compose up -d
```

La reversión es segura: v2.0.0 **no modifica el esquema** (no hay migraciones
Alembic nuevas; solo se añade la dependencia Python `premailer`).

### 4b. Restauración de datos (SOLO si fuera imprescindible)

> ⚠️ **No es un procedimiento automático**: detiene servicios y requiere
> **confirmación explícita**. La estrategia es no destructiva: se restaura el
> dump en un **volumen NUEVO** y se apunta el stack a él con un override; el
> volumen original `hj_campaigns_pgdata` **queda intacto** como red de
> seguridad hasta que verifiques los datos.

```bash
cd /opt/hj-campaigns

# Confirmación explícita obligatoria (escribe RESTAURAR para continuar)
read -r -p "Se detendrán los servicios y se restaurará la BD del dump. Escribe RESTAURAR para confirmar: " CONFIRMACION
[ "$CONFIRMACION" = "RESTAURAR" ] || { echo "Cancelado."; exit 1; }

# 1. Parar servicios (postgres incluido; el volumen original no se borra)
docker compose stop worker scheduler api frontend postgres

# 2. Restaurar el dump en un volumen NUEVO con un contenedor temporal
docker volume create hj_campaigns_pgdata_restaurada
docker run -d --name hj_restaurar -e POSTGRES_PASSWORD=temporal \
  -e POSTGRES_DB=hj_campaigns -e POSTGRES_USER=hj \
  -v hj_campaigns_pgdata_restaurada:/var/lib/postgresql/data postgres:16-alpine
for i in $(seq 1 30); do
  docker exec hj_restaurar pg_isready -U hj -d hj_campaigns -q && break
  sleep 1
done
gunzip -c /root/respaldos/hj_v2_XXXX.sql.gz | \
  docker exec -i hj_restaurar psql -U hj -d hj_campaigns -q
docker exec hj_restaurar psql -U hj -d hj_campaigns -tAc \
  "SELECT 'contactos='||count(*) FROM contacts"    # verifica que N coincide con el esperado
docker rm -f hj_restaurar

# 3. Apuntar el stack al volumen restaurado (override temporal)
cat > docker-compose.override.yml <<'YAML'
services:
  postgres:
    volumes:
      - pgdata_restaurada:/var/lib/postgresql/data
volumes:
  pgdata_restaurada:
    name: hj_campaigns_pgdata_restaurada
YAML

# 4. Arrancar y verificar
docker compose up -d
docker compose ps
curl -s http://127.0.0.1:8086/api/v1/healthz | head -3

# 5. Cuando confirmes que todo está bien y quieras volver al estado normal:
#    borra el override, y decide qué volumen conservar (sin borrar nada en caliente)
#    rm docker-compose.override.yml && docker compose up -d
```

> Si prefieres hacer del volumen restaurado el definitivo, detén el stack y
> renombra los volúmenes con Docker parado (`docker compose down`), moviendo
> `hj_campaigns_pgdata` a `hj_campaigns_pgdata_antigua` y
> `hj_campaigns_pgdata_restaurada` a `hj_campaigns_pgdata`. Nunca `rm -rf`
> sobre un volumen con servicios en marcha.

## 5. Notas operativas

- Las programaciones v2 se guardan igual que en v1 (columnas `scheduled_at` y
  `timezone` de `campaigns`): el scheduler las retoma tras reinicios.
- Los eventos SES siguen entrando por SQS (`AWS_SQS_EVENTS_QUEUE_URL`): nada
  cambia para SNS/SQS ni para las credenciales AWS montadas como secretos.
- Si `docker compose build` falla por red al descargar `premailer`, reintentar:
  el paquete es PyPI estándar.
