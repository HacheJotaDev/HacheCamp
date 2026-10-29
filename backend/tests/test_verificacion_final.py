"""Verificación funcional final antes del push (bloques 1-3 del informe).

Cubre:
1. Estadísticas reales: rechazados, retrasos (delivery_delay) e inciertos
   expuestos en métricas/dashboard/entregabilidad (antes los eventos
   delivery_delay se registraban pero no se mostraban en ninguna parte).
2. Cuota real SES: TTL 60 s, último dato válido con marca «desactualizado»,
   clasificación de errores IAM, puerta de cuota con last_good y el payload
   del dashboard con objetivo y última consulta.
3. Comprobaciones finales: configuration set hj-campanas aplicado a los
   envíos, nombres canónicos del pipeline de eventos (hj-eventos /
   hj-campanas) y refresco de cuota del scheduler a 60 s.

Todo se ejecuta con SQLite en memoria + fakeredis + SES simulado
(conftest.py): ninguna prueba toca AWS ni envía correos reales.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest


# --------------------------------------------------------------------------- #
# 1. Estadísticas reales: retrasos, rechazados e inciertos
# --------------------------------------------------------------------------- #

@pytest.fixture
def escenario_eventos(db):
    """Campaña con mensajes en estados variados y eventos SES reales.

    Devuelve también las líneas base GLOBALES (la BD de pruebas persiste entre
    tests): las agregaciones sin filtro de campaña se afirman como base+delta.
    """
    from sqlalchemy import func
    from app.models import Campaign, CampaignMessage, Contact, SesEvent

    def _eventos(tipo):
        return db.query(func.count(SesEvent.id)).filter(SesEvent.type == tipo).scalar() or 0

    base = {
        "retrasos": _eventos("delivery_delay"),
        "rechazados": _eventos("reject"),
        "complaints": _eventos("complaint"),
        "incierto": db.query(func.count(CampaignMessage.id)).filter(
            CampaignMessage.status == "incierto").scalar() or 0,
    }

    campana = Campaign(
        name="Verificación final", subject="Asunto", from_name="HJ",
        from_email="notificaciones@leisureparts4u.store", status="enviando",
    )
    db.add(campana)
    db.flush()

    # Un mensaje (y contacto) por estado: unicidad (campaña, contacto)
    estados = ["entregado", "rebote_permanente", "rebote_temporal", "queja",
               "error", "incierto", "pendiente"]
    mensajes = []
    for i, estado in enumerate(estados):
        c = Contact(email=f"stat-{uuid.uuid4().hex[:8]}-{i}@test.local", status="suscripto")
        db.add(c)
        db.flush()
        cm = CampaignMessage(campaign_id=campana.id, contact_id=c.id,
                             email=c.email, status=estado)
        db.add(cm)
        db.flush()
        mensajes.append(cm)

    ent, duro, suave, queja, error, incierto, pend = mensajes

    # Prefijo único por ejecución: los uids tienen restricción UNIQUE y la BD
    # de pruebas persiste entre tests.
    run = uuid.uuid4().hex[:8]

    # Eventos reales de SES (los mismos uids que produce el consumidor)
    db.add_all([
        SesEvent(event_uid=f"{run}:m1:delivery", type="delivery", campaign_id=campana.id,
                 message_id=ent.id, ses_message_id="m1", email=ent.email),
        SesEvent(event_uid=f"{run}:m2:bounce:Permanent:t1", type="bounce", campaign_id=campana.id,
                 message_id=duro.id, ses_message_id="m2", email=duro.email,
                 payload={"tipo": "Permanent"}),
        SesEvent(event_uid=f"{run}:m3:bounce:Transient:t2", type="bounce", campaign_id=campana.id,
                 message_id=suave.id, ses_message_id="m3", email=suave.email,
                 payload={"tipo": "Transient"}),
        SesEvent(event_uid=f"{run}:m4:complaint:t3", type="complaint", campaign_id=campana.id,
                 message_id=queja.id, ses_message_id="m4", email=queja.email),
        SesEvent(event_uid=f"{run}:m5:reject", type="reject", campaign_id=campana.id,
                 ses_message_id="m5", email=error.email),
        # Retrasos: el punto ciego anterior (delivery_delay sin exposición)
        SesEvent(event_uid=f"{run}:m1:delivery_delay:t4", type="delivery_delay",
                 campaign_id=campana.id, message_id=ent.id, ses_message_id="m1",
                 email=ent.email, payload={"tipo_retraso": "Internal"}),
        SesEvent(event_uid=f"{run}:m6:delivery_delay:t5", type="delivery_delay",
                 campaign_id=campana.id, ses_message_id="m6", email=pend.email),
    ])
    db.commit()
    return {"campaign_id": campana.id, "base": base}


def test_metrics_expone_rechazados_retrasos_e_incierto(db, escenario_eventos):
    from app.services import stats as stats_service

    m = stats_service.metrics_summary(db, campaign_id=escenario_eventos["campaign_id"])
    assert m["entregados"] == 1
    assert m["rebotes"] == 1 and m["rebotes_temporales"] == 1
    assert m["quejas"] == 1
    assert m["fallidos"] == 1
    assert m["rechazados"] == 1          # evento reject (antes invisible aquí)
    assert m["retrasos"] == 2            # eventos delivery_delay (antes invisibles)
    assert m["incierto"] == 1            # antes sin clave propia
    assert m["pendientes"] == 1
    # Denominadores intactos: aceptados = entregado+duro+suave+queja+incierto
    assert m["aceptados_ses"] == 5
    assert "retrasos" in m["notas"]


def test_entregabilidad_incluye_retrasos(db, escenario_eventos):
    from app.services import stats as stats_service

    base = escenario_eventos["base"]
    d = stats_service.deliverability_summary(db)
    assert d["retrasos_entrega"] == base["retrasos"] + 2
    assert d["rechazados"] == base["rechazados"] + 1
    assert d["quejas_spam"] == base["complaints"] + 1
    assert d["inbox_confirmado"] is None  # honestidad: SES no informa ubicación


def test_dashboard_summary_incluye_nuevos_contadores(db, escenario_eventos):
    from app.services import stats as stats_service

    base = escenario_eventos["base"]
    r = stats_service.dashboard_summary(db)
    assert r["retrasos"] == base["retrasos"] + 2
    assert r["rechazados"] == base["rechazados"] + 1
    assert r["incierto"] == base["incierto"] + 1


def test_api_metrics_y_dashboard_exponen_todo(client, admin_auth, escenario_eventos):
    resp = client.get("/api/v1/stats/metrics")
    assert resp.status_code == 200
    m = resp.json()
    assert {"rechazados", "retrasos", "incierto"} <= set(m)

    resp = client.get("/api/v1/stats/deliverability")
    assert resp.status_code == 200
    assert "retrasos_entrega" in resp.json()


# --------------------------------------------------------------------------- #
# 2. Cuota real de SES (GetAccount) con caché de 60 s y honestidad de fallos
# --------------------------------------------------------------------------- #

def test_cuota_cache_ttl_60s_y_ultimo_dato_valido(fake_ses):
    from app.services import ses as ses_service
    from app.workers.redis_client import get_redis

    quota = ses_service.get_sending_quota(force_refresh=True)
    assert quota["disponible"] is True and quota["desactualizado"] is False
    assert quota["max_24h"] == 50000.0

    r = get_redis()
    ttl = r.ttl(ses_service.QUOTA_CACHE_KEY)
    assert 0 < ttl <= 60
    # last_good para mostrar datos honestos si AWS deja de responder
    assert r.get(ses_service.QUOTA_LAST_GOOD_KEY)


def _error_aws(codigo: str, mensaje: str = "denied") -> Exception:
    exc = Exception(mensaje)
    exc.response = {"Error": {"Code": codigo, "Message": mensaje}}
    return exc


def test_cuota_aws_caido_devuelve_ultimo_dato_marcado_desactualizado(fake_ses):
    """AWS deja de responder tras una consulta correcta: el panel recibe el
    último dato válido con desactualizado=True y su edad, NUNCA ceros."""
    from app.services import ses as ses_service

    primero = ses_service.get_sending_quota(force_refresh=True)
    assert primero["max_24h"] == 50000.0

    def _caido():
        raise _error_aws("ServiceUnavailable", "Service Unavailable")
    fake_ses.get_account = _caido

    quota = ses_service.get_sending_quota(force_refresh=True)
    assert quota["desactualizado"] is True
    assert quota["disponible"] is False
    assert quota["max_24h"] == 50000.0          # último dato válido, no ceros
    assert quota["sent_last_24h"] == 0.0
    assert isinstance(quota["edad_s"], int) and quota["edad_s"] < 120
    assert "AWS no responde" in quota["mensaje"] or "disponibilidad" in quota["mensaje"]


def test_cuota_sin_datos_y_sin_permisos_iam_mensaje_claro(fake_ses):
    """Nunca hubo dato válido y AWS deniega (IAM): disponible=False y mensaje
    que apunta a permisos, sin cifras que parezcan reales."""
    from app.services import ses as ses_service

    def _denegado():
        raise _error_aws("AccessDenied", "User is not authorized to perform ses:GetAccount")
    fake_ses.get_account = _denegado

    quota = ses_service.get_sending_quota(force_refresh=True)
    assert quota["disponible"] is False
    assert quota["desactualizado"] is False
    assert "permisos IAM" in quota["mensaje"]
    assert quota["error"] == "AccessDenied"


def test_puerta_cuota_24h_usa_ultimo_dato_valido(fake_ses):
    """Si la caché principal caducó (AWS caído > 60 s), la puerta de cuota del
    worker sigue operativa gracias al last_good en lugar de quedarse ciega."""
    from app.services import ses as ses_service
    from app.workers.redis_client import get_redis

    ses_service.get_sending_quota(force_refresh=True)   # llena cuota + last_good
    get_redis().delete(ses_service.QUOTA_CACHE_KEY)     # simula caducidad
    # FakeSES: SentLast24Hours=0, Max24HourSend=50000 -> restante 50000
    restante = ses_service.cuota_24h_restante()
    assert restante == 50000.0


def test_dashboard_incluye_objetivo_y_ultima_consulta(client, admin_auth, fake_ses):
    """El payload del panel lleva velocidad objetivo (12/s) y fecha/hora de la
    última consulta a AWS, separadas de las métricas por campaña."""
    from app.core.config import settings

    resp = client.get("/api/v1/stats/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    cuota = data["cuota_ses"]
    assert cuota["objetivo_por_segundo"] == settings.SEND_RATE_PER_SECOND
    assert cuota["ultima_consulta_iso"]
    assert cuota["max_24h"] == 50000.0          # real de GetAccount, no fijo
    assert cuota["max_per_second"] == 14.0
    # El consumo de la cuenta SES va SEPARADO de las métricas de campañas
    assert "metricas" in data and "aceptados_ses" in data["metricas"]


def test_healthz_reporta_ses_desactualizado(client, admin_auth, fake_ses):
    """healthz distingue SES inaccesible (datos desactualizados) de caído."""
    from app.services import ses as ses_service
    from app.workers.redis_client import get_redis

    ses_service.get_sending_quota(force_refresh=True)
    # Simula la caducidad de la caché (AWS caído más de 60 s: el scheduler
    # no pudo refrescar) para forzar la ruta del último dato válido.
    get_redis().delete(ses_service.QUOTA_CACHE_KEY)
    def _caido():
        raise _error_aws("EndpointConnectionError", "Could not connect")
    fake_ses.get_account = _caido

    resp = client.get("/api/v1/healthz")
    assert resp.status_code == 200
    checks = resp.json()["comprobaciones"]
    assert checks["ses"] == "desactualizado"


# --------------------------------------------------------------------------- #
# 3. Configuration set hj-campanas, nombres canónicos y refresco a 60 s
# --------------------------------------------------------------------------- #

def test_configuration_set_hj_campanas_aplicado_a_cada_envio(fake_ses, monkeypatch):
    """send_raw_email pasa SES_CONFIGURATION_SET (hj-campanas en producción) a
    SES en todos los envíos: sin configuration set SES no emite eventos."""
    from app.core.config import settings
    from app.services import ses as ses_service
    from email.message import EmailMessage

    monkeypatch.setattr(settings, "SES_CONFIGURATION_SET", "hj-campanas")
    msg = EmailMessage()
    msg["Subject"] = "verificación"
    msg["From"] = "notificaciones@leisureparts4u.store"
    msg["To"] = "destino@test.local"
    msg.set_content("cuerpo")
    ses_service.send_raw_email(msg)

    assert len(fake_ses.sent) == 1
    assert fake_ses.sent[0].get("ConfigurationSetName") == "hj-campanas"


def test_setup_ses_eventos_usa_nombres_canonicos():
    """El script de setup usa los nombres reales del VPS: conjunto hj-campanas,
    destino hj-eventos (coherentes con docs/Eventos_SES_SQS.md)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import setup_ses_events as setup_script

    assert setup_script.NOMBRE_DESTINO == "hj-eventos"
    assert setup_script.NOMBRE_CONFIG_SET_DEFECTO == "hj-campanas"
    assert setup_script.NOMBRE_COLA == "hj-ses-eventos"
    assert setup_script.NOMBRE_DLQ == "hj-ses-eventos-dlq"


def test_scheduler_refresca_la_cuota_cada_60_segundos():
    from app.workers import scheduler
    from app.services import ses as ses_service

    assert scheduler.CUOTA_REFRESH_SEGUNDOS <= 60
    assert ses_service.QUOTA_CACHE_TTL <= 60


def test_env_example_documenta_objetivo_y_configuration_set():
    """La plantilla .env documenta los valores de producción reales."""
    from pathlib import Path
    contenido = (Path(__file__).resolve().parent.parent.parent / ".env.example").read_text()
    assert "SES_CONFIGURATION_SET=hj-campanas" in contenido
    assert "SEND_RATE_PER_SECOND=12" in contenido
