# Eventos de SES por SQS — conexión y verificación del flujo completo

> **Estado honesto**: mientras este flujo NO esté configurado y verificado, los
> contadores de *entregado*, *rebotes* y *quejas* del panel permanecen en **0**
> aunque los envíos funcionen: el worker registra el envío localmente
> (`envio_local`), pero SES no informa de vuelta. No confundir "enviado" con
> "medido". El propio panel lo refleja en
> `GET /api/v1/system/ses/status → eventos_sqs.habilitado = false`.

## 1. Situación actual

- El worker arranca su consumidor SQS en un hilo; si
  `AWS_SQS_EVENTS_QUEUE_URL` está vacía registra:
  `Eventos SES por SQS deshabilitados (AWS_SQS_EVENTS_QUEUE_URL vacía)` y
  continúa enviando sin ingesta de eventos. **Ese es el estado actual del VPS.**
- Arquitectura soportada (sin endpoint HTTP público):
  `SES (conjunto de configuración) → SQS → worker (long polling)`.
  El consumidor procesa tanto eventos SES directos como sobres de SNS
  (con verificación de firma si `SQS_VERIFY_SNS_SIGNATURE=true`).

## 2. Conexión (comandos AWS, los ejecutas tú con credenciales de administración)

```bash
REGION=us-east-2
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 2a. Cola de eventos + DLQ
aws sqs create-queue --queue-name hj-ses-eventos --region $REGION \
  --attributes '{
    "VisibilityTimeout": "120",
    "MessageRetentionPeriod": "345600",
    "RedrivePolicy": "{\"deadLetterTargetArn\":\"arn:aws:sqs:'$REGION':'$ACCOUNT_ID':hj-ses-eventos-dlq\",\"maxReceiveCount\":5}"
  }'
aws sqs create-queue --queue-name hj-ses-eventos-dlq --region $REGION \
  --attributes '{"MessageRetentionPeriod": "1209600"}'

QUEUE_URL=$(aws sqs get-queue-url --queue-name hj-ses-eventos --region $REGION --output text)
QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" \
  --region $REGION --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

# 2b. Política de la cola: permitir que el servicio SES entregue mensajes
cat > sqs-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PermitirSESEntregar",
    "Effect": "Allow",
    "Principal": {"Service": "ses.amazonaws.com"},
    "Action": "sqs:SendMessage",
    "Resource": "$QUEUE_ARN",
    "Condition": {"StringEquals": {"AWS:SourceAccount": "$ACCOUNT_ID"}}
  }]
}
JSON
aws sqs set-queue-attributes --queue-url "$QUEUE_URL" --region $REGION \
  --attributes "Policy=$(cat sqs-policy.json | jq -c .)"

# 2c. Destino de eventos del conjunto de configuración (SES v2 permite SQS directo)
aws sesv2 create-configuration-set --configuration-set-name hj-campanas \
  --region $REGION 2>/dev/null || true   # ya existirá si SES_CONFIGURATION_SET lo usa
aws sesv2 create-configuration-set-event-destination \
  --configuration-set-name hj-campanas --region $REGION \
  --event-destination-name hj-eventos \
  --event-destination '{
    "Enabled": true,
    "MatchingEventTypes": ["send","delivery","bounce","complaint","reject","open","click"],
    "SqsDestination": {"QueueArn": "'$QUEUE_ARN'"}
  }'
```

> Alternativa SNS→SQS (si prefieres un tópico): crea el tópico, suscríbete la
> cola con `--protocol sqs --notification-endpoint "$QUEUE_ARN"` y usa
> `SnsDestination` en el destino de eventos. El consumidor ya entiende ambos
> sobres.

## 3. Configuración de la aplicación (VPS, exclusivamente /opt/hj-campaigns)

```bash
cd /opt/hj-campaigns
echo "AWS_SQS_EVENTS_QUEUE_URL=$QUEUE_URL" >> .env   # o edítalo con nano
# Permisos del usuario de envío (añade a la política de docs/SES_PERMISOS.md):
#   sqs:ReceiveMessage, sqs:DeleteMessage  ->  Resource: $QUEUE_ARN
docker compose up -d worker     # recrea solo el worker con la variable nueva
```

## 4. Verificación del flujo completo (obligatoria antes de darlo por funcional)

```bash
# 4a. El worker debe registrar el consumidor iniciado (NO el aviso de deshabilitado)
docker compose logs --since 2m worker | grep -i sqs
#   esperado: "Consumidor de eventos SES por SQS iniciado"

# 4b. Panel: eventos_sqs.habilitado debe ser true
curl -s -b cookies.txt http://127.0.0.1:8086/api/v1/system/ses/status | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['eventos_sqs'])"

# 4c. Envía una campaña de prueba a una dirección REAL propia y espera ~1 min
# 4d. Comprueba que la cola drena y los eventos llegan a la base de datos
aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --region $REGION \
  --attribute-names ApproximateNumberOfMessages --query 'Attributes'
docker compose exec postgres psql -U hj -d hj_campaigns -tAc \
  "SELECT type, count(*) FROM ses_events GROUP BY type ORDER BY 1;"

# 4e. El panel (Entregabilidad/Estadísticas) debe reflejar entregados/rebotes
#     reales coherentes con 4d — mismo número, no inventados.
```

Criterio de aceptación: tras un envío real, `delivery` aparece en `ses_events`
y el contador *Entregado* del panel aumenta. Solo entonces la medición de
entregas/rebotes/quejas puede declararse funcional; hasta ese momento permanece
marcada como **pendiente de verificación**.

## 5. Errores habituales

- `AccessDenied` al recibir: faltan `sqs:ReceiveMessage`/`sqs:DeleteMessage`
  en la política del usuario (§3 / `docs/SES_PERMISOS.md`).
- La cola crece sin drenar: el worker no arrancó el consumidor (variable vacía)
  o la política de cola (2b) no permite `ses.amazonaws.com`.
- Eventos duplicados: la ingesta es idempotente por `event_uid`; duplicados
  reales indican reintentos SQS por fallos de proceso — revisa logs del worker.
