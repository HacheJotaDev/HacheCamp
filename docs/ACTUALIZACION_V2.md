# Actualización a v2.0.0 en el VPS (Git + Docker Compose)

Guía para aplicar la versión PRO sin perder datos y con plan de reversión.
Ruta del proyecto en el VPS: `/opt/hj-campaigns` (clon de `HacheJotaDev/HacheCamp`).

> Este procedimiento no toca otros proyectos del servidor (`/opt/campanas`,
> `/opt/ses-pruebas` siguen intactos) y el panel sigue siendo privado
> (`BIND_IP=127.0.0.1`, `PORT_FRONTEND=8085`).

## 1. Preparación

```bash
cd /opt/hj-campaigns
git fetch origin
git status                 # debe estar limpio (sin cambios locales)
git log --oneline -1       # anota el commit actual por si hay que revertir
```

## 2. Respaldo previo (obligatorio)

```bash
# Base de datos (contactos, campañas, mensajes, eventos, supresiones)
docker compose exec -T postgres pg_dump -U hj -d hj_campaigns \
  | gzip > /root/respaldos/hj_v2_$(date +%Y%m%d_%H%M%S).sql.gz

# Volúmenes de datos (pgdata y redis) por seguridad extra
docker run --rm -v hj-campaigns_pgdata:/data -v /root/respaldos:/bk alpine \
  tar czf /bk/pgdata_$(date +%Y%m%d_%H%M%S).tgz /data
```

Comprueba que el archivo existe y tiene tamaño razonable:
`ls -lh /root/respaldos/`.

## 3. Actualización y despliegue

```bash
git pull origin main          # o: git checkout v2.0.0 si se etiqueta
docker compose build          # reconstruye backend (incluye premailer) y frontend
docker compose up -d          # migrate corre primero (alembic upgrade head)
docker compose ps             # postgres/redis/api/worker/scheduler/frontend Up
docker compose logs -f api worker | head -100
```

Verificaciones rápidas:

```bash
curl -s http://127.0.0.1:8086/api/v1/healthz | head -5
# {"estado":"ok"|"degradado", ...}  (ses sin verificar si no hay salida a AWS en ese momento)
bash scripts/deploy.sh --check  # validación completa opcional
```

Prueba funcional mínima en el panel (túnel SSH):
`ssh -L 8085:127.0.0.1:8085 usuario@169.58.36.6` y abrir
`http://127.0.0.1:8085` → crear contacto con solo el correo, importar un TXT,
pegar HTML con `<style>` en una plantilla y ver la vista previa con los estilos.

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

La reversión es segura: v2.0.0 **no modifica el esquema** (no hay migraciones
Alembic nuevas; solo se añade la dependencia Python `premailer`).

## 5. Notas operativas

- Las programaciones v2 se guardan igual que en v1 (columnas `scheduled_at` y
  `timezone` de `campaigns`): el scheduler las retoma tras reinicios.
- Los eventos SES siguen entrando por SQS (`AWS_SQS_EVENTS_QUEUE_URL`): nada
  cambia para SNS/SQS ni para las credenciales AWS montadas como secretos.
- Si `docker compose build` falla por red al descargar `premailer`, reintentar:
  el paquete es PyPI estándar.
