# Permisos IAM mínimos para HJ Campaigns (SES v2)

Diagnóstico del error de producción:

```
AccessDeniedException  —  ses:SendRawEmail  (identidad leisureparts4u.store)
```

## 1. Qué operación usa realmente el código

Todo el envío pasa por `backend/app/services/ses.py` → `send_raw_email()`,
que llama a la **API v2 de SES** (`boto3.client("sesv2")`):

```python
client.send_email(
    FromEmailAddress=...,
    Destination={"ToAddresses": [...]},
    Content={"Raw": {"Data": msg.as_bytes()}},   # MIME raw
)
```

La operación SESv2 es **`SendEmail`** (con contenido `Raw`), y su acción IAM es
**`ses:SendEmail`**.

`ses:SendRawEmail` es la acción de la **API v1 (clásica)** de SES: el código de
este proyecto **no** la usa. Una política que solo concede
`ses:SendRawEmail` deja sin permiso las llamadas de la v2 — ese es el origen
del `AccessDeniedException`. Contenido "raw" en v2 **no** equivale a
`ses:SendRawEmail` en IAM.

## 2. Operaciones que usa la aplicación (auditoría del código)

| Llamada (`sesv2`) | Dónde | Acción IAM necesaria |
|---|---|---|
| `send_email(Content.Raw)` | envío de campañas y pruebas | `ses:SendEmail` |
| `get_account()` | cuotas (`get_sending_quota`) y reputación (`/system/ses/status`) | `ses:GetAccount` |
| `list_email_identities()` | diagnóstico de identidades | `ses:ListEmailIdentities` |

Si se configura la ingesta de eventos por SQS (ver `docs/Eventos_SES_SQS.md`),
el consumidor añade:

| Llamada (`sqs`) | Acción IAM necesaria |
|---|---|
| `receive_message(...)` | `sqs:ReceiveMessage` |
| `delete_message_batch(...)` | `sqs:DeleteMessage` |

No se usa ninguna otra operación de SES/SNS/SQS en el código: no hacen falta
`ses:SendRawEmail`, `ses:SendEmail` v1, `sns:*` ni `sqs:GetQueueAttributes`
para funcionar.

## 3. Política mínima recomendada (usuario `ses-vps-leisureparts`)

Crea el archivo `hj-minimo.json` en el VPS (sustituye `<ACCOUNT_ID>` por el ID
de tu cuenta AWS, que puedes ver con `aws sts get-caller-identity`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EnviarDesdeIdentidadVerificada",
      "Effect": "Allow",
      "Action": "ses:SendEmail",
      "Resource": "arn:aws:ses:us-east-2:<ACCOUNT_ID>:identity/leisureparts4u.store",
      "Condition": {
        "StringLike": { "ses:FromAddress": "notificaciones@leisureparts4u.store" }
      }
    },
    {
      "Sid": "CuotasYDiagnostico",
      "Effect": "Allow",
      "Action": ["ses:GetAccount", "ses:ListEmailIdentities"],
      "Resource": "*"
    }
  ]
}
```

Aplicarla (la ejecutas tú en tu estación con tus credenciales de administración
IAM; la aplicación nunca pide ni maneja credenciales de administración):

```bash
aws iam put-user-policy \
  --user-name ses-vps-leisureparts \
  --policy-name HJCampaignsMinimo \
  --policy-document file://hj-minimo.json
```

Si el envío siguiera denegado con la política acotada a la identidad, prueba la
variante sin `Resource`/`Condition` (permiso `ses:SendEmail` a nivel de cuenta)
para descartar un ARN mal formado, y vuelve a acotar en cuanto confirme envío:

```json
{ "Sid": "EnviarDiagnostico", "Effect": "Allow", "Action": "ses:SendEmail", "Resource": "*" }
```

Verificación sin exponer nada (en la estación de administración):

```bash
aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::<ACCOUNT_ID>:user/ses-vps-leisureparts" \
  --action-names ses:SendEmail ses:GetAccount ses:ListEmailIdentities \
                 sqs:ReceiveMessage sqs:DeleteMessage
```

Cada fila debe devolver `allowed: true` (los `sqs:*` solo tras configurar
eventos, ver `docs/Eventos_SES_SQS.md`).

## 4. Higiene de permisos

- `ses:SendRawEmail` (v1) puede **quitarse** de la política: el código no lo
  usa. Quitarlo no arregla el error; añadir `ses:SendEmail` sí.
- No concedas `ses:*` ni `*` completos: con las tres acciones de la tabla del
  §2 la aplicación queda completa.
- La identidad `leisureparts4u.store` debe seguir **verificada** en SES
  (DKIM/SPF ya configurados); eso no es un problema de IAM.
- Tras aplicar la política, comprueba en el panel: **Ajustes → estado SES**
  (`GET /api/v1/system/ses/status`) debe mostrar `cuota.max_24h = 50000` y
  `reputacion.envio_habilitado = true`, y el envío de prueba debe devolver
  `MessageId`.
