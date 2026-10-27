# Actualización a v2.0.0 en el VPS (Git + Docker Compose)

Guía para aplicar la versión PRO sin perder datos y con plan de reversión.
Ruta del proyecto en el VPS: `/opt/hj-campaigns` (clon de `HacheJotaDev/HacheCamp`).

> Este procedimiento no toca otros proyectos del servidor (`/opt/campanas`,
> `/opt/ses-pruebas` siguen intactos). **Tampoco modifica tu `.env`**: el panel
> conserva el acceso que ya tienes (IP pública del VPS + puerto 8085, definido
> por `BIND_IP`/`PORT_FRONTEND` en `/opt/hj-campaigns/.env`). Se comprueba
> antes y después del despliegue en el paso 3.

## 0. Comprobar el acceso actual (no cambiarlo)

```bash
cd /opt/hj-campaigns
grep -E '^(BIND_IP|PORT_FRONTEND|PORT_API)=' .env
ss -ltnp | grep -E ':8085|:8086'   # anota la vinculación actual (p. ej. 0.0.0.0:8085)
```

Anota esa salida: tras la actualización debe ser **idéntica**. Si hoy accedes
por `http://IP-PÚBLICA:8085`, tu `.env` tiene `BIND_IP=0.0.0.0` (o la IP del
servidor) y así debe seguir; esta actualización no lo altera.

## 1. Preparación

```bash
cd /opt/hj-campaigns
git fetch origin
git status                 # debe estar limpio (sin cambios locales)
git log --oneline -1       # anota el commit actual por si hay que revertir
```

## 2. Respaldo previo (obligatorio)

> El volumen de PostgreSQL se llama **`hj_campaigns_pgdata`** (con guiones
> bajos, igual que en `docker-compose.yml`). Verifícalo antes con
> `docker volume ls | grep hj_campaigns`; si el nombre no coincide, el respaldo
> saldría **vacío** (Docker crearía un volumen nuevo sin datos).

```bash
cd /opt/hj-campaigns
mkdir -p /root/respaldos
FECHA=$(date +%Y%m%d_%H%M%S)

# 2a. Base de datos lógica (contactos, campañas, mensajes, eventos, supresiones)
docker compose exec -T postgres pg_dump -U hj -d hj_campaigns \
  | gzip > /root/respaldos/hj_v2_${FECHA}.sql.gz

# 2b. Volumen de datos de PostgreSQL (copia física del volumen real)
docker run --rm -v hj_campaigns_pgdata:/data:ro -v /root/respaldos:/bk alpine \
  tar czf /bk/pgdata_${FECHA}.tgz -C / data

# 2c. Verificación OBLIGATORIA: existen, no están vacíos y son íntegros
set -e
test -s /root/respaldos/hj_v2_${FECHA}.sql.gz || { echo "ERROR: dump SQL vacío"; exit 1; }
gzip -t /root/respaldos/hj_v2_${FECHA}.sql.gz          # integridad gzip
gunzip -c /root/respaldos/hj_v2_${FECHA}.sql.gz | tail -1 \
  | grep -q "PostgreSQL database dump complete" \
  || { echo "ERROR: dump SQL incompleto"; exit 1; }
test -s /root/respaldos/pgdata_${FECHA}.tgz || { echo "ERROR: tar pgdata vacío"; exit 1; }
gzip -t /root/respaldos/pgdata_${FECHA}.tgz            # integridad tar.gz
docker run --rm -v /root/respaldos:/bk alpine \
  sh -c "tar tzf /bk/pgdata_${FECHA}.tgz | head -3"     # lista contenido real
set +e
ls -lh /root/respaldos/
```

## 3. Actualización y despliegue

> Nota: si aún no se fusionó la PR #1, sustituye `git pull origin main` por
> `git fetch origin feature/pro-v2 && git checkout feature/pro-v2` (o mergea
> primero la PR y usa `main`).

```bash
git pull origin main          # o: git checkout v2.0.0 si se etiqueta
docker compose build          # reconstruye backend (incluye premailer) y frontend
docker compose up -d          # migrate corre primero (alembic upgrade head)
docker compose ps             # postgres/redis/api/worker/scheduler/frontend Up
docker compose logs --since 5m api worker scheduler | tail -60
```

Verificaciones rápidas:

```bash
curl -s http://127.0.0.1:8086/api/v1/healthz | head -5
# {"estado":"ok"|"degradado", ...}  (ses sin verificar si no hay salida a AWS en ese momento)
bash scripts/deploy.sh --check  # validación completa opcional

# ACCESO: idéntico al anotado en el paso 0 (no debe cambiar)
grep -E '^(BIND_IP|PORT_FRONTEND|PORT_API)=' .env
ss -ltnp | grep -E ':8085|:8086'
```

Abre el panel como lo haces siempre (misma URL que antes de actualizar) y
verifica: contactos visibles con su estado, campañas y estadísticas históricas
intactas. Prueba funcional mínima: crear contacto con solo el correo, importar
un TXT, pegar HTML con `<style>` en una plantilla y ver la vista previa con
los estilos.

## 4. Reversión

```bash
cd /opt/hj-campaigns
git checkout <commit-anterior>       # el que anotaste en el paso 1
docker compose build && docker compose up -d
```

Si además hiciera falta restaurar datos:

```bash
gunzip -c /root/respaldos/hj_v2_XXXX.sql.gz | \
  docker compose exec -T postgres psql -U hj -d hj_campaigns
```

Restauración física del volumen (solo si el contenedor postgres está parado):

```bash
docker compose stop postgres
docker run --rm -v hj_campaigns_pgdata:/data -v /root/respaldos:/bk alpine \
  sh -c "rm -rf /data/* && tar xzf /bk/pgdata_XXXX.tgz -C /data --strip-components=1"
docker compose start postgres
docker compose ps   # postgres healthy
```

La reversión es segura: v2.0.0 **no modifica el esquema** (no hay migraciones
Alembic nuevas; solo se añade la dependencia Python `premailer`).

## 5. Notas operativas

- Las programaciones v2 se guardan igual que en v1 (columnas `scheduled_at` y
  `timezone` de `campaigns`): el scheduler las retoma tras reinicios.
- Los eventos SES siguen entrando por SQS (`AWS_SQS_EVENTS_QUEUE_URL`): nada
  cambia para SNS/SQS ni para las credenciales AWS montadas como secretos.
- Si `docker compose build` falla por red al descargar `premailer`, reintentar:
  el paquete es PyPI estándar.
