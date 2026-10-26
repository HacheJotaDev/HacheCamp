# Copias de seguridad y restauración

## 1. Qué se protege

| Componente | Herramienta | Contenido |
|---|---|---|
| PostgreSQL | `pg_dump` (gzip) | Todos los datos: usuarios, contactos, campañas, plantillas, eventos |
| Redis | `BGSAVE` + copia del `dump.rdb`/AOF | Sesiones y cola de envío (reconstruibles; la perdida de la cola no duplica envíos) |
| `.env` | copia directa | Configuración y secretos (proteger el archivo resultante) |

El código y las plantillas viven en el repositorio Git: la copia del código es `git`.

## 2. Crear copia

```bash
cd /opt/hj-campaigns
bash scripts/backup.sh
# -> backups/hj_backup_YYYYMMDD_HHMMSS.tar.gz
chmod 600 backups/*.tar.gz
```

La copia es en caliente (no detiene servicios). Retención automática: conserva las 14 más recientes.

## 3. Programación

```cron
0 3 * * * /opt/hj-campaigns/scripts/backup.sh >> /var/log/hj-backup.log 2>&1
```

## 4. Copia externa al VPS (imprescindible)

El servidor solo protege contra fallos lógicos, no contra pérdida del disco. Copia fuera del host:

```bash
# desde tu equipo (ejemplo diario)
rsync -az root@169.58.36.6:/opt/hj-campaigns/backups/ ~/respaldos/hj-campaigns/
```

## 5. Restaurar

```bash
cd /opt/hj-campaigns
bash scripts/restore.sh backups/hj_backup_YYYYMMDD_HHMMSS.tar.gz
# pide escribir RESTAURAR para confirmar; detiene servicios, restaura y rearranca
```

Qué hace: detiene `worker`, `scheduler`, `api` y `frontend` → restaura PostgreSQL con `psql` → restaura Redis si la copia lo incluye → rearranca todo → imprime el estado. Tras restaurar, verifica con `bash scripts/status.sh` y revisa una campaña y un contacto en el panel.

## 6. Restauración solo de PostgreSQL

```bash
gzip -dc backups/hj_backup_XXXX/postgres.sql.gz | \
  docker compose exec -T postgres psql -U hj -d hj_campaigns
```

(Si la base tiene datos en conflicto, crea primero una base limpia o usa `--clean` en un `pg_dump` nuevo.)

## 7. Restauración en un servidor nuevo

1. Instala Docker y clona el repositorio (DEPLOYMENT.md §1).
2. Recupera el `.env` de la copia (`env.backup`) — conserva `SECRET_KEY` para que los tokens de baja/clic firmados sigan siendo válidos.
3. `bash scripts/deploy.sh`.
4. Restaura PostgreSQL con la copia (§6) **antes** de crear el administrador (el bootstrap avisa si ya existe uno).
5. Reconfigura el tópico SNS con el `WEBHOOK_TOKEN` actual.

## 8. Prueba periódica recomendada

Una restauración no probada no es una copia de seguridad. Cada mes (o tras cambios mayores), restaura la última copia en un entorno temporal:

```bash
docker run --rm -d --name hj_test_pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
gzip -dc backups/hj_backup_XXXX/postgres.sql.gz | \
  docker exec -i hj_test_pg psql -U postgres -d postgres
docker stop hj_test_pg
```

Si el dump entra sin errores, la copia es válida.
