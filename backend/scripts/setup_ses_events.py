"""Configuración idempotente de la ingesta de eventos SES vía SQS.

Flujo que deja montado (todo dentro de tu cuenta AWS, sin endpoints públicos):

    SES (conjunto de configuración) --eventos--> SNS --suscripción--> SQS
                                                        |
                                              worker de HJ Campaigns (long polling)

Uso desde el contenedor del api (dentro del VPS):
    docker compose exec -T api python scripts/setup_ses_events.py             # plan (dry-run)
    docker compose exec -T api python scripts/setup_ses_events.py --aplicar   # ejecuta cambios

Idempotente: puede ejecutarse varias veces sin duplicar recursos.
No envía correos y no toca datos del panel; solo recursos de mensajería AWS.

Permisos IAM adicionales necesarios para el usuario de SES (puedes revocar
los de creación tras la primera ejecución): ver docs/DEPLOYMENT.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402

CUENTA_REGION = settings.AWS_REGION
# Nombres canónicos de los recursos AWS (coherentes con docs/Eventos_SES_SQS.md
# §2, que documenta la configuración REAL del VPS). Si tu cuenta ya tuviera
# recursos con otros nombres (p. ej. creados con la versión antigua de este
# script), ajusta las variables o el .env: la ingesta depende de
# AWS_SQS_EVENTS_QUEUE_URL, no de estos constantes.
NOMBRE_COLA = "hj-ses-eventos"
NOMBRE_DLQ = "hj-ses-eventos-dlq"
NOMBRE_TOPICO = "hj-ses-eventos"
NOMBRE_DESTINO = "hj-eventos"                     # destino de eventos dentro del conjunto
NOMBRE_CONFIG_SET_DEFECTO = "hj-campanas"         # igual que SES_CONFIGURATION_SET en producción
RETENCION_SEG = "1209600"  # 14 días
EVENTOS_BASE = ["SEND", "DELIVERY", "BOUNCE", "COMPLAINT", "REJECT", "RENDERING_FAILURE"]
EVENTOS_EXTRA = ["OPEN", "CLICK"]


def _clientes():
    import boto3
    ses = boto3.client("sesv2", region_name=CUENTA_REGION)
    sns = boto3.client("sns", region_name=CUENTA_REGION)
    sqs = boto3.client("sqs", region_name=CUENTA_REGION)
    iam = boto3.client("sts", region_name=CUENTA_REGION)
    cuenta = iam.get_caller_identity()["Account"]
    return ses, sns, sqs, cuenta


def _cola_url(sqs, nombre: str):
    try:
        return sqs.get_queue_url(QueueName=nombre)["QueueUrl"]
    except Exception:
        return None


def _plan(ses, sns, sqs, cuenta: str, incluir_aperturas: bool) -> dict:
    """Detecta qué recursos faltan (sin modificar nada)."""
    eventos = EVENTOS_BASE + (EVENTOS_EXTRA if incluir_aperturas else [])
    plan: dict = {"cuenta": cuenta, "region": CUENTA_REGION, "acciones": []}

    if _cola_url(sqs, NOMBRE_DLQ) is None:
        plan["acciones"].append(f"crear cola DLQ: {NOMBRE_DLQ}")
    if _cola_url(sqs, NOMBRE_COLA) is None:
        plan["acciones"].append(f"crear cola: {NOMBRE_COLA} (con redrive a la DLQ)")
    else:
        plan["acciones"].append(f"verificar política de la cola {NOMBRE_COLA} (permitir SNS)")

    topicos = sns.list_topics().get("Topics", [])
    arn_topico = next((t["TopicArn"] for t in topicos if t["TopicArn"].endswith(":" + NOMBRE_TOPICO)), None)
    if arn_topico is None:
        plan["acciones"].append(f"crear tópico SNS: {NOMBRE_TOPICO}")
    else:
        plan["acciones"].append(f"verificar política del tópico {NOMBRE_TOPICO} (permitir SES)")

    url_cola = _cola_url(sqs, NOMBRE_COLA)
    arn_cola = sqs.get_queue_attributes(QueueUrl=url_cola, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"] if url_cola else None
    suscripciones = sns.list_subscriptions_by_topic(TopicArn=arn_topico).get("Subscriptions", []) if arn_topico else []
    if arn_cola and not any(s.get("Endpoint") == arn_cola for s in suscripciones):
        plan["acciones"].append(f"suscribir la cola al tópico (raw=false, con firma SNS)")

    cs = settings.SES_CONFIGURATION_SET or NOMBRE_CONFIG_SET_DEFECTO
    try:
        ses.get_configuration_set(ConfigurationSetName=cs)
        existe = True
    except Exception:
        existe = False
    if not existe:
        plan["acciones"].append(f"crear conjunto de configuración SES: {cs}")
    plan["acciones"].append(
        f"conectar eventos SES ({', '.join(eventos)}) del conjunto '{cs}' al tópico SNS")
    plan["queue_url"] = url_cola
    plan["configuration_set"] = cs
    return plan


def _aplicar(ses, sns, sqs, cuenta: str, incluir_aperturas: bool) -> int:
    """Crea/conecta todos los recursos de forma idempotente."""
    eventos = EVENTOS_BASE + (EVENTOS_EXTRA if incluir_aperturas else [])
    arn_cola, arn_dlq, arn_topico = None, None, None

    # 1) DLQ
    url_dlq = _cola_url(sqs, NOMBRE_DLQ)
    if url_dlq is None:
        url_dlq = sqs.create_queue(
            QueueName=NOMBRE_DLQ,
            attributes={"MessageRetentionPeriod": RETENCION_SEG},
        )["QueueUrl"]
        print(f"  ✓ DLQ creada: {NOMBRE_DLQ}")
    else:
        print(f"  • DLQ ya existía: {NOMBRE_DLQ}")
    arn_dlq = sqs.get_queue_attributes(QueueUrl=url_dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

    # 2) Cola principal con redrive a la DLQ
    url_cola = _cola_url(sqs, NOMBRE_COLA)
    if url_cola is None:
        url_cola = sqs.create_queue(
            QueueName=NOMBRE_COLA,
            attributes={
                "MessageRetentionPeriod": RETENCION_SEG,
                "RedrivePolicy": json.dumps({
                    "deadLetterTargetArn": arn_dlq,
                    "maxReceiveCount": int(settings.SQS_MAX_RECEIVE_COUNT),
                }),
            },
        )["QueueUrl"]
        print(f"  ✓ Cola creada: {NOMBRE_COLA}")
    else:
        print(f"  • Cola ya existía: {NOMBRE_COLA}")
    arn_cola = sqs.get_queue_attributes(QueueUrl=url_cola, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

    # 3) Tópico SNS con política que permite publicar a SES (v2)
    topicos = sns.list_topics().get("Topics", [])
    arn_topico = next((t["TopicArn"] for t in topicos if t["TopicArn"].endswith(":" + NOMBRE_TOPICO)), None)
    if arn_topico is None:
        arn_topico = sns.create_topic(Name=NOMBRE_TOPICO)["TopicArn"]
        print(f"  ✓ Tópico SNS creado: {NOMBRE_TOPICO}")
    else:
        print(f"  • Tópico SNS ya existía: {NOMBRE_TOPICO}")

    politica_topico = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "PermitirSESPublicar",
            "Effect": "Allow",
            "Principal": {"Service": "ses.amazonaws.com"},
            "Action": "SNS:Publish",
            "Resource": arn_topico,
            "Condition": {"StringEquals": {"AWS:SourceAccount": cuenta}},
        }],
    }
    sns.set_topic_attributes(TopicArn=arn_topico,
                             AttributeName="Policy",
                             AttributeValue=json.dumps(politica_topico))

    # 4) Suscripción SNS -> SQS (raw=false: llega el sobre con firma verificable)
    suscripciones = sns.list_subscriptions_by_topic(TopicArn=arn_topico).get("Subscriptions", [])
    if not any(s.get("Endpoint") == arn_cola for s in suscripciones):
        sns.subscribe(TopicArn=arn_topico, Protocol="sqs", Endpoint=arn_cola,
                      Attributes={"RawMessageDelivery": "false"})
        print("  ✓ Suscripción SNS→SQS creada (raw=false)")
    else:
        print("  • Suscripción SNS→SQS ya existía")

    # 5) Política de la cola: solo SNS del tópico puede enviar
    politica_cola = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "PermitirSNSEventosSES",
            "Effect": "Allow",
            "Principal": {"Service": "sns.amazonaws.com"},
            "Action": "sqs:SendMessage",
            "Resource": arn_cola,
            "Condition": {"ArnEquals": {"aws:SourceArn": arn_topico}},
        }],
    }
    sqs.set_queue_attributes(QueueUrl=url_cola,
                             Attributes={"Policy": json.dumps(politica_cola)})
    print("  ✓ Política de la cola actualizada (solo SNS del tópico)")

    # 6) Conjunto de configuración SES y destino de eventos
    cs = settings.SES_CONFIGURATION_SET or NOMBRE_CONFIG_SET_DEFECTO
    try:
        ses.get_configuration_set(ConfigurationSetName=cs)
        print(f"  • Conjunto de configuración SES ya existía: {cs}")
    except Exception:
        ses.create_configuration_set(ConfigurationSetName=cs)
        print(f"  ✓ Conjunto de configuración SES creado: {cs}")
    ses.put_configuration_set_event_destination(
        ConfigurationSetName=cs,
        EventDestination={
            "Name": NOMBRE_DESTINO,
            "Enabled": True,
            "MatchingEventTypes": eventos,
            "SnsDestination": {"TopicArn": arn_topico},
        },
    )
    print(f"  ✓ Destino de eventos conectado ({', '.join(eventos)})")

    print("\n== Añade esta variable a tu .env y reinicia el worker ==")
    print(f"AWS_SQS_EVENTS_QUEUE_URL={url_cola}")
    if not settings.SES_CONFIGURATION_SET:
        print(f"SES_CONFIGURATION_SET={cs}")
    print("\nRecuerda: docker compose up -d worker  (para recargar la configuración)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Configura la ingesta de eventos SES vía SQS")
    parser.add_argument("--aplicar", action="store_true",
                        help="ejecuta los cambios (por defecto solo muestra el plan)")
    parser.add_argument("--con-aperturas-clics", action="store_true",
                        help="incluye eventos OPEN/CLICK de SES (el panel ya tiene su propio seguimiento)")
    args = parser.parse_args()

    print(f"== Ingesta de eventos SES vía SQS — región {CUENTA_REGION} ==")
    try:
        ses, sns, sqs, cuenta = _clientes()
    except Exception as exc:
        print(f"✗ No se pudieron crear clientes AWS: {type(exc).__name__}")
        print("  Revisa las credenciales (archivo /root/.aws montado como secreto o variables en .env)")
        return 1

    try:
        if args.aplicar:
            return _aplicar(ses, sns, sqs, cuenta, args.con_aperturas_clics)
        plan = _plan(ses, sns, sqs, cuenta, args.con_aperturas_clics)
        print("Plan de acciones (dry-run; añade --aplicar para ejecutar):")
        for accion in plan["acciones"]:
            print(f"  - {accion}")
        return 0
    except Exception as exc:
        codigo = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
        print(f"✗ Error de AWS: {codigo or type(exc).__name__}")
        if "AccessDenied" in codigo or "AccessDenied" in type(exc).__name__:
            print("\nEl usuario IAM no tiene permisos suficientes. Añádele esta política")
            print("(puedes revocar los ítems de creación tras la primera ejecución):\n")
            print(json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {"Sid": "SQSConsumo", "Effect": "Allow",
                     "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage",
                                "sqs:DeleteMessageBatch", "sqs:GetQueueAttributes",
                                "sqs:GetQueueUrl"],
                     "Resource": f"arn:aws:sqs:{CUENTA_REGION}:{cuenta}:{NOMBRE_COLA}*"},
                    {"Sid": "SQSCreacionTemporal", "Effect": "Allow",
                     "Action": ["sqs:CreateQueue", "sqs:SetQueueAttributes", "sqs:TagQueue",
                                "sqs:ListQueues"],
                     "Resource": "*"},
                    {"Sid": "SNSCreacionTemporal", "Effect": "Allow",
                     "Action": ["sns:CreateTopic", "sns:Subscribe", "sns:SetSubscriptionAttributes",
                                "sns:SetTopicAttributes", "sns:ListTopics",
                                "sns:ListSubscriptionsByTopic"],
                     "Resource": "*"},
                    {"Sid": "SESDestinoTemporal", "Effect": "Allow",
                     "Action": ["ses:CreateConfigurationSet", "ses:GetConfigurationSet",
                                "ses:PutConfigurationSetEventDestination", "ses:ListConfigurationSets"],
                     "Resource": "*"},
                ],
            }, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
