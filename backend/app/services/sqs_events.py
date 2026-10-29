"""Consumo de eventos de SES mediante una cola SQS (canal privado).

Diseño solicitado para uso personal/privado sin endpoint HTTP público:

    SES (conjunto de configuración) -> SNS -> SQS -> worker (long polling)

- El worker consulta la cola saliente (solo conexiones HTTPS de salida);
  no se publica ningún puerto ni endpoint del panel a Internet.
- Si el mensaje llega dentro del sobre de SNS (raw=false) se verifica su
  firma criptográfica (SignatureVersion 1) antes de procesarlo.
- Idempotencia: cada notificación SNS se registra por MessageId, igual que
  en el webhook HTTP; los eventos SES usan sus propios uids.
- Mensajes venenosos: tras SQS_MAX_RECEIVE_COUNT lecturas se descartan con
  registro (además de la DLQ opcional creada siguiendo
  docs/Eventos_SES_SQS.md).
- Nunca se registran en el log cuerpos completos (contienen correos).
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging_conf import get_logger
from app.models import SesEvent
from app.services import events as events_service
from app.workers import heartbeat

logger = get_logger("hj.sqs")


class SQSNotConfigured(RuntimeError):
    pass


def sqs_enabled() -> bool:
    """True si hay URL de cola de eventos configurada."""
    return bool(settings.AWS_SQS_EVENTS_QUEUE_URL)


def _client():
    """Cliente SQS (credenciales por la cadena habitual de Boto3)."""
    if not sqs_enabled():
        raise SQSNotConfigured("AWS_SQS_EVENTS_QUEUE_URL no está definida")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover
        raise SQSNotConfigured("Boto3 no está instalado") from exc
    return boto3.client(
        "sqs",
        region_name=settings.AWS_REGION,
        config=Config(connect_timeout=10, read_timeout=40, retries={"max_attempts": 3, "mode": "standard"}),
    )


# --------------------------------------------------------------------------- #
# Procesamiento de un mensaje de la cola
# --------------------------------------------------------------------------- #


def _topic_authorized(topic_arn: str) -> bool:
    if not settings.SNS_TOPIC_ARNS:
        return True
    allowed = [a.strip() for a in settings.SNS_TOPIC_ARNS.split(",") if a.strip()]
    return topic_arn in allowed


def _extraer_evento(cuerpo: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Devuelve (evento SES, sns_envelope|None) o None si no es procesable."""
    if not isinstance(cuerpo, dict):
        return None
    if "Type" in cuerpo and "Message" in cuerpo:
        # Sobre de SNS (suscripción con raw message delivery desactivado)
        return None if not isinstance(cuerpo.get("Message"), str) else {"__sns__": cuerpo}
    # Evento SES directo (raw message delivery activado)
    if "eventType" in cuerpo or "notificationType" in cuerpo:
        return cuerpo
    return None


def _registrar_descarte(db, sqs_id: str, motivo: str, receive_count: int) -> None:
    """Deja rastro consultable en BD de todo descarte (para diagnóstico).

    Idempotente por sqs_id: si el mensaje reaparece no duplica filas.
    """
    try:
        existente = db.query(SesEvent).filter(
            SesEvent.event_uid == f"sqs:{sqs_id}:descartado").first()
        if existente:
            return
        db.add(SesEvent(event_uid=f"sqs:{sqs_id}:descartado", type="sqs_descartado",
                        payload={"motivo": motivo, "lecturas": receive_count}))
        db.commit()
    except Exception:
        db.rollback()


def procesar_mensaje(db, sqs_message: Dict[str, Any]) -> Dict[str, Any]:
    """Procesa un mensaje recibido de SQS. Lanza ValueError si debe descartarse.

    Devuelve un resumen {accion: ...} para logs/métricas.
    """
    sqs_id = sqs_message.get("messageId", "")
    raw_body = sqs_message.get("body") or ""

    try:
        cuerpo = json.loads(raw_body)
    except json.JSONDecodeError:
        # Cuerpo ilegible: no tiene remedio, se descarta para no reintentar eternamente
        logger.warning("Mensaje SQS con cuerpo no JSON; se descarta", extra={"sqs_id": sqs_id[:36]})
        return {"accion": "descartado", "motivo": "json_invalido"}

    extraido = _extraer_evento(cuerpo)
    if extraido is None:
        logger.warning("Mensaje SQS con formato desconocido; se descarta", extra={"sqs_id": sqs_id[:36]})
        return {"accion": "descartado", "motivo": "formato_desconocido"}

    sns_envelope: Optional[Dict[str, Any]] = None
    ses_event: Dict[str, Any]
    if "__sns__" in extraido:
        sns_envelope = extraido["__sns__"]
        try:
            ses_event = json.loads(sns_envelope.get("Message", "{}"))
        except json.JSONDecodeError:
            return {"accion": "descartado", "motivo": "mensaje_sns_no_json"}

        # Idempotencia a nivel de notificación (mismo identificador que el webhook HTTP)
        sns_message_id = str(sns_envelope.get("MessageId") or "")
        uid_notificacion = f"sns:{sns_message_id}" if sns_message_id else ""
        if uid_notificacion:
            existente = db.query(SesEvent).filter(SesEvent.event_uid == uid_notificacion).first()
            if existente:
                return {"accion": "duplicado_ignorado"}

        # Seguridad: firma del sobre SNS y lista blanca de tópicos
        if settings.SQS_VERIFY_SNS_SIGNATURE:
            events_service.verify_sns_signature(sns_envelope)  # puede lanzar SNSSecurityError
        if not _topic_authorized(str(sns_envelope.get("TopicArn", ""))):
            raise ValueError("TopicArn no autorizado")

        tipo_sobre = str(sns_envelope.get("Type", "Notification"))
        if tipo_sobre == "SubscriptionConfirmation":
            ok = events_service.confirm_subscription(str(sns_envelope.get("SubscribeURL", "")))
            logger.info("Suscripción SNS confirmada desde SQS", extra={"resultado": ok})
            if uid_notificacion:
                db.add(SesEvent(event_uid=uid_notificacion, type="sns_ack",
                                payload={"tipo": "suscripcion_confirmada", "via": "sqs"}))
                db.commit()
            return {"accion": "suscripcion_confirmada"}
        if tipo_sobre == "UnsubscribeConfirmation":
            logger.warning("SNS confirmó la desuscripción del tópico (SQS dejará de recibir eventos)")
            return {"accion": "desuscripcion_registrada"}
    else:
        ses_event = extraido
        # Sin sobre: idempotencia propia de eventos (uid ses:...); añadimos
        # una marca por mensaje SQS para evitar reprocesos del mismo cuerpo.
        uid_notificacion = f"sqs:{sqs_id}" if sqs_id else ""
        if uid_notificacion:
            existente = db.query(SesEvent).filter(SesEvent.event_uid == uid_notificacion).first()
            if existente:
                return {"accion": "duplicado_ignorado"}

    resumen = events_service.process_ses_notification(db, ses_event)
    if uid_notificacion:
        db.add(SesEvent(event_uid=uid_notificacion, type="sns_ack",
                        payload={"tipo_evento": resumen.get("tipo"), "via": "sqs"}))
    db.commit()
    resumen["accion"] = "procesado"
    return resumen


# --------------------------------------------------------------------------- #
# Bucle de consumo
# --------------------------------------------------------------------------- #


def poll_once(client: Any, *, max_messages: Optional[int] = None,
              wait_seconds: Optional[int] = None) -> Dict[str, int]:
    """Una recepción larga de la cola. Devuelve contadores del lote."""
    queue_url = settings.AWS_SQS_EVENTS_QUEUE_URL or ""
    max_messages = min(max_messages or settings.SQS_MAX_MESSAGES, 10)
    wait_seconds = min(wait_seconds if wait_seconds is not None else settings.SQS_WAIT_SECONDS, 20)

    resp = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=max_messages,
        WaitTimeSeconds=wait_seconds,
        MessageAttributeNames=["All"],
        AttributeNames=["ApproximateReceiveCount"],
    )
    mensajes = resp.get("Messages", [])
    contadores = {"recibidos": len(mensajes), "procesados": 0, "duplicados": 0,
                  "sns_acks": 0, "descartados": 0, "errores": 0}
    if not mensajes:
        return contadores

    a_borrar: List[Dict[str, str]] = []
    db = SessionLocal()
    try:
        for m in mensajes:
            receipt = m.get("ReceiptHandle", "")
            sqs_id = m.get("MessageId", "")
            attrs = m.get("Attributes") or {}
            receive_count = int(attrs.get("ApproximateReceiveCount", "0") or 0)
            item = {
                "messageId": sqs_id,
                "body": m.get("Body", ""),
                "receive_count": receive_count,
            }
            try:
                if receive_count > settings.SQS_MAX_RECEIVE_COUNT:
                    # Mensaje venenoso (sin DLQ o redrive no alcanzado): descartar con registro
                    logger.error("Mensaje SQS descartado tras superarle lecturas",
                                 extra={"sqs_id": sqs_id[:36], "lecturas": receive_count})
                    db.add(SesEvent(event_uid=f"sqs:{sqs_id}:descartado", type="sqs_descartado",
                                    payload={"lecturas": receive_count}))
                    db.commit()
                    a_borrar.append({"Id": sqs_id, "ReceiptHandle": receipt})
                    contadores["descartados"] += 1
                    continue

                resultado = procesar_mensaje(db, item)
                accion = resultado.get("accion")
                if accion == "procesado":
                    contadores["procesados"] += 1
                    logger.info("Evento SES procesado (SQS)",
                                extra={"tipo": resultado.get("tipo"), "sqs_id": sqs_id[:36]})
                    a_borrar.append({"Id": sqs_id, "ReceiptHandle": receipt})
                elif accion == "duplicado_ignorado":
                    contadores["duplicados"] += 1
                    a_borrar.append({"Id": sqs_id, "ReceiptHandle": receipt})
                elif accion in ("suscripcion_confirmada", "desuscripcion_registrada"):
                    # Gestión administrativa de SNS: NO es un descarte ni un evento
                    # perdido; se contabiliza aparte para no falsear el diagnóstico.
                    contadores["sns_acks"] += 1
                    logger.info("Gestión SNS en cola SQS",
                                extra={"accion": accion, "sqs_id": sqs_id[:36]})
                    a_borrar.append({"Id": sqs_id, "ReceiptHandle": receipt})
                elif accion == "descartado":
                    contadores["descartados"] += 1
                    _registrar_descarte(db, sqs_id, str(resultado.get("motivo", "desconocido")),
                                        receive_count)
                    a_borrar.append({"Id": sqs_id, "ReceiptHandle": receipt})
                else:
                    contadores["errores"] += 1
            except events_service.SNSSecurityError as exc:
                # Firma inválida: NO se borra (visibilidad lo reintentará) y se avisa fuerte
                contadores["errores"] += 1
                db.rollback()
                logger.error("Mensaje SQS con firma SNS inválida; se retiene",
                             extra={"sqs_id": sqs_id[:36], "motivo": str(exc)[:200]})
            except Exception as exc:
                contadores["errores"] += 1
                db.rollback()
                logger.error("Error procesando mensaje SQS",
                             extra={"sqs_id": sqs_id[:36], "error": type(exc).__name__,
                                    "lecturas": receive_count})
    finally:
        db.close()

    if a_borrar:
        try:
            client.delete_message_batch(QueueUrl=queue_url, Entries=a_borrar)
        except Exception as exc:
            logger.error("No se pudo confirmar (delete) el lote SQS",
                         extra={"error": type(exc).__name__, "cantidad": len(a_borrar)})
            contadores["errores"] += len(a_borrar)
    # Resumen estructurado del lote: permite ver la salud del pipeline de
    # eventos sin depender de líneas sueltas. Los sns_acks NO son descartes.
    logger.info("Lote SQS consumido", extra={**contadores})
    return contadores


# --------------------------------------------------------------------------- #
# Hilo consumidor integrado en el worker
# --------------------------------------------------------------------------- #

_stop_event: Optional[threading.Event] = None


def start_consumer() -> Optional[threading.Thread]:
    """Arranca el consumidor SQS en un hilo daemon si está configurado."""
    global _stop_event
    if not sqs_enabled():
        logger.info(
            "Eventos SES por SQS deshabilitados (AWS_SQS_EVENTS_QUEUE_URL vacía): "
            "entregas/rebotes/quejas NO se miden. Conexión y verificación: "
            "docs/Eventos_SES_SQS.md"
        )
        return None
    _stop_event = threading.Event()
    hilo = threading.Thread(target=_bucle_consumidor, args=(_stop_event,),
                            name="hj-sqs-events", daemon=True)
    hilo.start()
    logger.info("Consumidor de eventos SES por SQS iniciado",
                extra={"region": settings.AWS_REGION})
    return hilo


def stop_consumer() -> None:
    if _stop_event is not None:
        _stop_event.set()


def _bucle_consumidor(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            cliente = _client()
            contadores = poll_once(cliente)
            # Latido del consumidor: visible en /system/ses/diagnostico y usado
            # para saber si el pipeline de eventos está vivo.
            heartbeat.beat("sqs_consumer", ttl_seconds=600, extra=contadores)
        except SQSNotConfigured:
            logger.warning("SQS deshabilitado a mitad de ejecución; el hilo termina")
            return
        except Exception as exc:
            logger.error("Error en consumidor SQS; reintento en 5 s",
                         extra={"error": type(exc).__name__})
            stop.wait(5)
