# Operación diaria de HJ Campaigns

## 1. Comandos esenciales

```bash
cd /opt/hj-campaigns

docker compose ps                  # estado de contenedores
bash scripts/status.sh             # salud completa + cola
docker compose logs -f api         # logs de la API (Ctrl+C para salir)
docker compose logs -f worker      # logs del worker de envío
docker compose logs -f import-worker  # logs del worker de importaciones
docker compose logs -f scheduler   # logs del scheduler
docker compose restart api         # reiniciar un servicio
docker compose stop                # detener todo (conserva datos)
docker compose up -d               # arrancar de nuevo
bash scripts/update.sh             # actualizar desde el repositorio
```

## 2. Cola de envío

Consultar el estado:

```bash
docker compose exec redis redis-cli LLEN hj:q:outbound   # pendientes en cola
docker compose exec redis redis-cli ZCARD hj:q:retry     # reintentos programados
```

Operaciones:

| Acción | Cómo |
|---|---|
| Pausar una campaña | Panel → Campaña → **Pausar** (el worker reencola con retardo) |
| Reanudar | Panel → **Reanudar** (reencola los pendientes) |
| Cancelar | Panel → **Cancelar** (pendientes → cancelado; los ya aceptados por SES no se pueden retirar) |
| Cambiar velocidad global | `SEND_RATE_PER_SECOND` en `.env` + `docker compose up -d` (respeta siempre el máximo real de SES) |
| Drenar cola sin enviar | Panel → Cancelar campaña: los mensajes pendientes se cancelan |

### Después de un reinicio o caída

- La cola vive en Redis con AOF: los mensajes en vuelo sobreviven al reinicio.
- Los mensajes que quedaron `enviando` más de `STALE_SENDING_MINUTES` (10 min) son marcados `incierto` por el scheduler. Revísalos en la campaña (Destinatarios → estado *Incierto*):
  - Si SES los aceptó (verificable por `ses_message_id` en `ses_events`), no hagas nada: los eventos de SES (vía SQS) los reconciliarán a `entregado`/`rebote`.
  - Si seguro no llegaron a SES, un administrador puede reenviar el mensaje concreto desde la base de datos reponiéndolo a `pendiente` (nunca automático: evita duplicados).

## 3. Entregabilidad responsable

- **SES aceptado ≠ entregado**: la columna «Aceptados SES» cuenta solicitudes aceptadas; «Entregados» cuenta eventos `delivery` de SNS.
- **Aperturas aproximadas**: bloqueadores de imágenes, Apple Mail Privacy y proxies distorsionan el dato; úsalo como tendencia, no como verdad absoluta.
- **Rebotes permanentes** suprimen automáticamente la dirección: no intentes «limpiarla» sin nueva confirmación de consentimiento.
- **Quejas (complaints)** suprimen de inmediato: una queja reincidente daña la reputación del dominio; el sistema lo impide.
- Mantén la velocidad por debajo del máximo de SES (`MaxPerSecond` se consulta en vivo) y los volúmenes coherentes con tu cuota de 24 h.
- Ninguna configuración garantiza bandeja de entrada (Inbox): eso depende de reputación, contenido y autenticación del dominio (SPF/DKIM/DMARC ya configurados para `leisureparts4u.store`).

## 4. Reconciliación y alarmas sugeridas

Revisa periódicamente:

1. `ses_events` con `type='bounce'` — patrón de rebotes alto → revisa la calidad de la lista.
2. Campañas `fallida` — motivo en el historial de la campaña.
3. Mensajes `incierto` — decidir según lo anterior.
4. Espacio en disco de `backups/` y volúmenes (`df -h`).
5. **Cola de eventos SQS**: `ApproximateNumberOfMessages` debe tender a 0; la DLQ `hj-campaigns-ses-events-dlq` debe estar vacía.

```bash
# Profundidad de la cola de eventos y de la DLQ (aws CLI en el VPS o con el mismo usuario IAM)
aws sqs get-queue-attributes --queue-url "$AWS_SQS_EVENTS_QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region us-east-2
aws sqs get-queue-attributes --queue-url "$(aws sqs get-queue-url --queue-name hj-campaigns-ses-events-dlq --region us-east-2 --query QueueUrl --output text)" \
  --attribute-names ApproximateNumberOfMessages --region us-east-2
```

Si la DLQ acumula mensajes, inspéctalos (`aws sqs receive-message ...`) antes de reencolar: suelen ser eventos con formato inesperado o firmas no verificadas.

## 5. Tareas programadas recomendadas (crontab)

```cron
# Copia de seguridad diaria 03:00
0 3 * * * /opt/hj-campaigns/scripts/backup.sh >> /var/log/hj-backup.log 2>&1

# Estado semanal (opcional, correo o log)
0 8 * * 1 /opt/hj-campaigns/scripts/status.sh >> /var/log/hj-status.log 2>&1
```

## 6. Problemas frecuentes

| Síntoma | Diagnóstico | Solución |
|---|---|---|
| Login devuelve 429 | Bloqueo por fuerza bruta | Espera 15 min o limpia la clave Redis `hj:lfa:*` |
| La campaña no pasa de «En cola» | Worker parado | `docker compose ps` → `docker compose up -d worker`; revisa `logs worker` |
| «La campaña no tiene destinatarios» | Lista vacía o todos suprimidos | Revisa contactos y supresión; campañas promocionales exigen `suscripto` sin supresión |
| «SES no está configurado» | Sin credenciales IAM | `check_ses.py` y revisa §6 de DEPLOYMENT.md (`/root/.aws` como secreto o variables AWS_* en `.env`) |
| Errores `ThrottlingException` repetidos | Velocidad sobre el límite de SES | Baja `SEND_RATE_PER_SECOND`; los reintentos con backoff lo resuelven solo |
| No llegan eventos de entrega/rebote (SQS) | Worker sin `AWS_SQS_EVENTS_QUEUE_URL`, sin permisos SQS, o tópico/cola mal conectados | 1) `docker compose logs worker | grep SQS`; 2) `docker compose exec -T api python scripts/setup_ses_events.py` (plan y diagnóstico); 3) comprueba la profundidad de la cola (§4) |
| Mensajes en la DLQ de SQS | Eventos con formato inesperado o firma no verificada | Inspecciona con `aws sqs receive-message`; si son legítimos, reencola; revisa `SQS_VERIFY_SNS_SIGNATURE` |
| Webhook SNS devuelve 403 firma inválida | Cabecera/cuerpo alterados o proxy que modifica el cuerpo (solo modalidad webhook HTTPS) | Asegura que el proxy pasa el cuerpo intacto (no re-comprime) y que `WEBHOOK_TOKEN` coincide |
| La vista previa sale vacía | Bloqueos del iframe | Comprueba que la API responde: `/api/v1/templates/preview` |
| Contraseña del admin perdida | — | Otro admin usa «Restablecer contraseña» (Usuarios); sin otro admin, bootstrap con `--force` crea uno nuevo |
| El panel no carga tras reinicio | Frontend aún construyendo | Espera healthcheck; `docker compose logs frontend` |

## 7. Mantenimiento de base de datos

```bash
# Tamaño actual
docker compose exec postgres psql -U hj -d hj_campaigns -c \
  "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;"

# Limpieza de eventos antiguos (>180 días) si el volumen crece
docker compose exec postgres psql -U hj -d hj_campaigns -c \
  "DELETE FROM ses_events WHERE occurred_at < now() - interval '180 days';"
```

(Añade esta limpieza como tarea mensual si el número de eventos lo justifica.)

## 8. Escalado

- Más rendimiento de envío: `docker compose up -d --scale worker=2` (el control de velocidad global es compartido vía Redis; no duplicará envíos gracias a la idempotencia).
- Aislar la API para más tráfico: aumentar `--workers` de uvicorn en el Dockerfile o recursos del servicio.
