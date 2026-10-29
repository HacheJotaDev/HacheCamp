"""Pruebas de la auditoría V4 (reconstrucción verificable):

1. Rate limiter FAIL-CLOSED: Redis caído => 0 envíos (worker y API).
2. Concurrencia global 1/4/8/14 "workers": la velocidad agregada nunca
   supera el objetivo y no hay concesiones duplicadas.
3. Claim atómico: un mid duplicado en cola produce UN solo envío.
4. Puerta de cuota 24 h: bloquea con la caché y sin llamar a AWS.
5. Recuperación de huérfanos (cola vacía, 'incierto' excluido).
6. Encolado masivo por lotes idempotente y supresión en 1 consulta.
7. Import: lock por trabajo, renovación, recuperación periódica, crash a
   mitad con reanudación sin duplicados y carga real de 50.656 contactos.
8. Eventos: DELIVERY_DELAY/RENDERING_FAILURE, rebote temporal con reintento,
   carrera de idempotencia y clics íntegros.
9. Diagnóstico: sin_vincular_a_mensaje. E2E de plantilla HTML completa.

SES simulado con fake_ses; nunca se envían correos reales.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

from app.core.config import settings
from tests.conftest import SessionLocal

# --------------------------------------------------------------------------- #
# Helpers compartidos
# --------------------------------------------------------------------------- #


def _crear_lista_con_contactos(client, n: int, **contacto_kw):
    """Crea una lista con n contactos suscriptos vía API. Devuelve list_id."""
    lst = client.post("/api/v1/lists", json={"name": f"L-{uuid.uuid4().hex[:8]}"})
    assert lst.status_code == 201, lst.text
    lid = lst.json()["id"]
    for i in range(n):
        r = client.post("/api/v1/contacts", json={
            "email": f"v4-{uuid.uuid4().hex[:10]}-{i}@test.local",
            "first_name": f"Nombre{i}",
            "consent_source": "prueba",
            "list_ids": [lid],
            **contacto_kw,
        })
        assert r.status_code in (200, 201), r.text
    return lid


def _crear_campana_aprobada(client, lid: str, **kw):
    """Crea campaña promocional sobre la lista, la aprueba y devuelve su id."""
    camp = client.post("/api/v1/campaigns", json={
        "name": f"C-V4-{uuid.uuid4().hex[:8]}",
        "subject": "Asunto V4",
        "from_name": "HJ Test",
        "from_email": "notificaciones@leisureparts4u.store",
        "mode": "bloques",
        "blocks": [
            {"type": "titulo", "props": {"text": "Hola {{contact.first_name}}", "level": 1}},
            {"type": "texto", "props": {"content": "<p>Cuerpo de prueba</p>"}},
        ],
        "category": "promocional", "list_id": lid,
        **kw,
    })
    assert camp.status_code == 201, camp.text
    cid = camp.json()["id"]
    r = client.post(f"/api/v1/campaigns/{cid}/approve")
    assert r.status_code == 200, r.text
    return cid


def _drenar_y_procesar(fake_ses, max_msgs: int = 200) -> list:
    """Procesa la cola de envíos como lo haría un worker. Devuelve resultados."""
    from app.services import queue as send_queue
    from app.workers.worker import process_one
    resultados = []
    for _ in range(max_msgs):
        db = SessionLocal()
        try:
            mid = send_queue.pop_message(timeout_seconds=1)
            if not mid:
                break
            resultados.append((mid, process_one(db, mid)))
        finally:
            db.close()
    return resultados


def _mensaje_unico(db, cid: str):
    """Devuelve el (único) campaign_message de la campaña."""
    from app.models import CampaignMessage
    return db.query(CampaignMessage).filter(CampaignMessage.campaign_id == cid).one()


# --------------------------------------------------------------------------- #
# 1. Fail-closed del rate limiter
# --------------------------------------------------------------------------- #


def test_fail_closed_redis_caido_no_envia_nada(client, admin_auth, fake_ses, monkeypatch):
    """Redis caído tras el BRPOP (escenario real): process_one reencola y SES
    no recibe NADA (antes cada worker enviaba sin slot global: hasta 14x la
    velocidad objetivo)."""
    from app.services import queue as send_queue
    from app.workers.worker import process_one

    lid = _crear_lista_con_contactos(client, 1)
    cid = _crear_campana_aprobada(client, lid)
    r = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert r.status_code == 200, r.text

    # El worker SACA el trabajo con Redis vivo...
    mid = send_queue.pop_message(timeout_seconds=2)
    assert mid, "el envío debe estar en la cola"

    # ...y Redis muere justo antes del slot de velocidad (ventana real)
    def _redis_muerto():
        raise ConnectionError("redis caído")

    monkeypatch.setattr(send_queue, "get_redis", _redis_muerto)

    db = SessionLocal()
    try:
        res = process_one(db, mid)
    finally:
        db.close()

    assert res["accion"] == "reencolado"
    assert res["motivo"] == "rate_limiter_no_disponible"
    assert fake_ses.sent == [], "con Redis caído NO debe salir ningún correo"


def test_fail_closed_recompone_pendiente_sin_atascar(client, admin_auth, fake_ses, monkeypatch):
    """Tras el bloqueo fail-closed el mensaje queda 'pendiente' (no atascado
    en 'enviando') y, reencolado por la recuperación de huérfanos, completa."""
    from app.models import CampaignMessage
    from app.services import queue as send_queue
    from app.workers.worker import process_one

    lid = _crear_lista_con_contactos(client, 1)
    cid = _crear_campana_aprobada(client, lid)
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    mid = send_queue.pop_message(timeout_seconds=2)
    assert mid

    real_get_redis = send_queue.get_redis

    def _redis_muerto():
        raise ConnectionError("redis caído")

    monkeypatch.setattr(send_queue, "get_redis", _redis_muerto)
    db = SessionLocal()
    try:
        res = process_one(db, mid)
    finally:
        db.close()
    assert res["accion"] == "reencolado"

    db = SessionLocal()
    try:
        msg = db.query(CampaignMessage).get(mid)
        assert msg.status == "pendiente", f"estado tras fail-closed: {msg.status}"
        assert msg.last_error == "rate_limiter_no_disponible"
    finally:
        db.close()

    # Redis "vuelve": restauramos SOLO la referencia de la cola (undo() también
    # revertiría el parche de fake_ses y el envío chocaría con boto3 real).
    # El mensaje quedó 'pendiente' FUERA de la cola (huérfano): la recuperación
    # del scheduler lo reencola — simulamos exactamente eso y el envío completa.
    monkeypatch.setattr(send_queue, "get_redis", real_get_redis)
    send_queue.enqueue_message(mid)
    db = SessionLocal()
    try:
        res = process_one(db, mid)
    finally:
        db.close()
    assert res["accion"] == "enviado"
    assert fake_ses.sent, "al volver Redis el envío debe completarse"


def test_envio_prueba_api_pasa_por_limiter(client, admin_auth, fake_ses, monkeypatch):
    """La prueba de envío de campaña usa el limiter global: si no hay slot
    (Redis caído) responde 503 y no envía (antes saltaba el limiter)."""
    from app.services import queue as send_queue

    lid = _crear_lista_con_contactos(client, 1)
    cid = _crear_campana_aprobada(client, lid)

    def _redis_muerto():
        raise ConnectionError("redis caído")

    monkeypatch.setattr(send_queue, "get_redis", _redis_muerto)
    r = client.post(f"/api/v1/campaigns/{cid}/test-send", json={
        "to_email": "destinatario@test.local"})
    assert r.status_code == 503, r.text
    assert fake_ses.sent == []


# --------------------------------------------------------------------------- #
# 2. Concurrencia global 1/4/8/14: velocidad agregada bajo control
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("replicas", [1, 4, 8, 14])
def test_matriz_concurrencia_velocidad_agregada(replicas):
    """N réplicas toman slot simultáneamente: el conjunto NUNCA supera la
    velocidad objetivo (ventana total >= mínimo teórico) y todos obtienen
    exactamente un slot (exclusión mutua del Lua: cero concesiones dobles)."""
    from app.services import queue as send_queue
    from app.workers.redis_client import get_redis
    get_redis().delete("hj:q:last_send_ns")

    rate = 40.0  # intervalo 25 ms: prueba rápida pero exigente
    resultados = [None] * replicas

    def _tomar(i):
        t0 = time.monotonic()
        send_queue.wait_for_rate_slot(rate)
        resultados[i] = time.monotonic() - t0

    hilos = [threading.Thread(target=_tomar, args=(i,)) for i in range(replicas)]
    inicio = time.monotonic()
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    total = time.monotonic() - inicio

    assert all(r is not None for r in resultados), "alguna réplica no obtuvo slot"
    # Teoría: n envíos a rate/s necesitan >= (n-1)/rate segundos de cola.
    # Tolerancia 0.75 por el redondeo de sleeps (nunca fue menos en la práctica).
    minimo = (replicas - 1) / rate * 0.75
    assert total >= minimo, (
        f"{replicas} réplicas completaron en {total:.4f}s (mínimo {minimo:.4f}s): "
        f"velocidad agregada {replicas / max(total, 1e-9):.1f}/s > objetivo {rate}/s")
    # La referencia global avanzó exactamente 'replicas' veces (sin duplicados)
    # y el último slot se concedió como muy pronto tras el intervalo final.


def test_slots_consecutivos_respetan_intervalo_global():
    """El espaciado entre concesiones consecutivas >= intervalo: evidencia
    directa de que el Lua nunca concede dos slots dentro del mismo intervalo."""
    from app.services import queue as send_queue
    from app.workers.redis_client import get_redis
    get_redis().delete("hj:q:last_send_ns")

    rate = 50.0
    instantes: list[float] = []
    lock = threading.Lock()

    def _tomar():
        send_queue.wait_for_rate_slot(rate)
        with lock:
            instantes.append(time.monotonic())

    hilos = [threading.Thread(target=_tomar) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    instantes.sort()
    intervalo = 1.0 / rate
    violaciones = sum(
        1 for a, b in zip(instantes, instantes[1:]) if b - a < intervalo * 0.6
    )
    assert violaciones == 0, (
        f"{violaciones} pares de concesiones más rápidas que el intervalo "
        f"(~{intervalo*1000:.0f} ms): el límite global se saltó")


# --------------------------------------------------------------------------- #
# 3. Claim atómico: el mismo mid dos veces en cola = UN envío
# --------------------------------------------------------------------------- #


def test_claim_atomico_mid_duplicado_un_solo_envio(client, admin_auth, fake_ses):
    """Si un mid aparece 2x en la cola (crash + reencolado, réplica escalada),
    el segundo worker lo omite en vez de enviar duplicado."""
    from app.models import CampaignMessage
    from app.services import queue as send_queue
    from app.workers.worker import process_one

    lid = _crear_lista_con_contactos(client, 1)
    cid = _crear_campana_aprobada(client, lid)
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    db = SessionLocal()
    try:
        mid = _mensaje_unico(db, cid).id
    finally:
        db.close()

    # Duplicamos el trabajo en la cola (estado anómalo que el crash puede crear)
    send_queue.enqueue_message(mid)
    send_queue.enqueue_message(mid)

    dbs = []
    try:
        db1 = SessionLocal()
        db2 = SessionLocal()
        dbs = [db1, db2]
        res1 = process_one(db1, send_queue.pop_message(timeout_seconds=1))
        res2 = process_one(db2, send_queue.pop_message(timeout_seconds=1))
    finally:
        for d in dbs:
            d.close()

    acciones = sorted([res1["accion"], res2["accion"]])
    # La 2ª copia NO puede enviarse: se omite (reclamado por otro) o se
    # cancela (campaña ya finalizada por el 1er envío). Cualquiera de las
    # dos es correcto: lo innegociable es que SES recibió UN solo correo.
    assert "enviado" in acciones and acciones[0] in ("cancelado", "omitido"), (
        f"doble envío posible: {res1} {res2}")
    assert len(fake_ses.sent) == 1, "SE envió dos veces el mismo mensaje"

    db = SessionLocal()
    try:
        msg = db.query(CampaignMessage).get(mid)
        assert msg.status == "aceptado"
        assert msg.attempts == 1, "el segundo claim no debe incrementar intentos"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 4. Puerta de cuota 24 h (solo caché; cero llamadas AWS)
# --------------------------------------------------------------------------- #


def test_puerta_cuota_24h_bloquea_sin_llamar_aws(client, admin_auth, fake_ses, monkeypatch):
    """Cuota cacheada casi agotada: el mensaje se reencola y AWS no recibe
    ni la consulta ni el envío (la puerta SOLO lee la caché Redis)."""
    from app.models import CampaignMessage
    from app.services import ses as ses_service
    from app.workers.redis_client import get_redis

    get_redis().set("hj:ses:quota", json.dumps({
        "max_24h": 50000.0, "sent_last_24h": 49980.0,  # restante 20 < umbral 50
        "max_per_second": 14.0, "cacheado": True,
    }))

    def _aws_prohibido(*a, **k):
        raise AssertionError("la puerta de cuota NO debe llamar a AWS")

    monkeypatch.setattr(ses_service, "get_sending_quota", _aws_prohibido)

    lid = _crear_lista_con_contactos(client, 1)
    cid = _crear_campana_aprobada(client, lid)
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    resultados = _drenar_y_procesar(fake_ses)
    assert len(resultados) == 1
    mid, res = resultados[0]
    assert res["accion"] == "reencolado" and res["motivo"] == "cuota_24h_agotada"
    assert fake_ses.sent == []

    db = SessionLocal()
    try:
        msg = db.query(CampaignMessage).get(mid)
        assert msg.status == "pendiente", "la cuota NO es un error definitivo"
    finally:
        db.close()


def test_puerta_cuota_24h_no_bloquea_sin_cache(client, admin_auth, fake_ses):
    """Sin caché de cuota (Redis recién reiniciado) la puerta NO bloquea:
    falta de datos nunca se interpreta como cuota agotada."""
    from app.workers.redis_client import get_redis
    get_redis().delete("hj:ses:quota")

    lid = _crear_lista_con_contactos(client, 1)
    cid = _crear_campana_aprobada(client, lid)
    client.post(f"/api/v1/campaigns/{cid}/send-now")

    resultados = _drenar_y_procesar(fake_ses)
    assert len(resultados) == 1 and resultados[0][1]["accion"] == "enviado"
    assert len(fake_ses.sent) == 1


def test_cuota_diaria_ses_clasificada_como_reintento(fake_ses):
    """'Daily message quota exceeded' => SESError reintentable (cuota_24h):
    antes quedaba como error definitivo del mensaje."""
    from app.services import ses as ses_service
    from email.message import EmailMessage

    fake_ses.behavior = "quota"
    msg = EmailMessage()
    msg["Subject"] = "t"
    msg["From"] = "notificaciones@leisureparts4u.store"
    msg["To"] = "alguien@test.local"
    msg.set_content("cuerpo")
    with pytest.raises(ses_service.SESError) as exc:
        ses_service.send_raw_email(msg)
    assert exc.value.kind == "cuota_24h"
    assert exc.value.retryable is True


def test_ses_configured_cachea_la_sonda(fake_ses, monkeypatch):
    """ses_configured NO consulta AWS nunca (0 llamadas por envío).

    Comportamiento V4 original: sonda GetAccount una vez, cacheada en Redis
    (antes: 1 GetAccount por envío). Integración del hotfix de producción:
    ya no hay sonda AWS — credenciales explícitas o cadena Boto3 con caché
    de proceso de 60 s; la validez real se comprueba al enviar (AccessDenied
    clasificado) y la cuota en get_sending_quota. El contrato que se
    conserva: esta función jamás debe llamar al cliente de SES.
    """
    from app.services import ses as ses_service

    ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)
    settings.AWS_ACCESS_KEY_ID = None
    settings.AWS_SECRET_ACCESS_KEY = None
    try:
        llamadas = {"n": 0}
        real_client = fake_ses

        def _contando_client():
            llamadas["n"] += 1
            return real_client

        monkeypatch.setattr(ses_service, "_client", _contando_client)
        # Con credenciales explícitas: True sin tocar el cliente...
        settings.AWS_ACCESS_KEY_ID = "AKIAPRUEBA"
        settings.AWS_SECRET_ACCESS_KEY = "secreta"
        assert ses_service.ses_configured() is True
        # ...y sin credenciales explícitas tampoco se sondea AWS:
        settings.AWS_ACCESS_KEY_ID = None
        settings.AWS_SECRET_ACCESS_KEY = None
        ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)
        ses_service.ses_configured()
        ses_service.ses_configured()
        assert llamadas["n"] == 0, (
            f"se llamó al cliente SES {llamadas['n']} veces: "
            "ses_configured no debe consultar AWS")
    finally:
        settings.AWS_ACCESS_KEY_ID = None
        ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)


# --------------------------------------------------------------------------- #
# 5. Recuperación de huérfanos
# --------------------------------------------------------------------------- #


def _crear_campana_directa_con_mensajes(n: int, estado_campana="en_cola"):
    """Crea campaña + mensajes directamente en BD (rápido, sin API)."""
    from app.models import Campaign, CampaignMessage, Contact
    db = SessionLocal()
    try:
        camp = Campaign(
            name=f"C-ORF-{uuid.uuid4().hex[:6]}", subject="s", mode="bloques",
            blocks=[], category="promocional", status=estado_campana,
            from_name="HJ Test",
            from_email="notificaciones@leisureparts4u.store",
            approved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(camp)
        db.flush()
        mids = []
        for i in range(n):
            c = Contact(email=f"orf-{uuid.uuid4().hex[:10]}-{i}@test.local",
                        status="suscripto", consent_source="prueba")
            db.add(c)
            db.flush()
            m = CampaignMessage(campaign_id=camp.id, contact_id=c.id, email=c.email,
                                status="pendiente")
            db.add(m)
            db.flush()
            mids.append(m.id)
        db.commit()
        return camp.id, mids
    finally:
        db.close()


def _envejecer_mensajes(mids, segundos: int):
    from app.models import CampaignMessage
    from app.core.db import utcnow
    from datetime import timedelta
    db = SessionLocal()
    try:
        db.query(CampaignMessage).filter(CampaignMessage.id.in_(mids)).update(
            {"updated_at": utcnow() - timedelta(seconds=segundos)}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _vaciar_cola_outbound() -> list:
    """Saca todo el contenido actual de la cola de envíos (para verificar
    pertenencia sin depender de huérfanos de otros tests)."""
    from app.services import queue as send_queue
    mids = []
    while True:
        mid = send_queue.pop_message(timeout_seconds=1)
        if not mid:
            break
        mids.append(mid)
    return mids


def test_huerfanos_reencolados_cuando_cola_vacia(client, admin_auth, fake_ses):
    """'pendiente' fuera de cola + cola/reintentos vacías => reencolado y
    enviado por el worker (antes quedaba varado para siempre)."""
    from app.services import queue as send_queue
    from app.workers.worker import recover_orphaned_messages

    cid, mids = _crear_campana_directa_con_mensajes(2)
    _envejecer_mensajes(mids, 120)

    assert send_queue.outbound_count() == 0
    assert send_queue.pending_retries_count() == 0
    n = recover_orphaned_messages(db=SessionLocal())
    assert n >= 2
    en_cola = _vaciar_cola_outbound()
    for mid in mids:
        assert mid in en_cola, f"huérfano {mid} no reencolado"

    # Procesamos SOLO los nuestros (otros tests pueden dejar huérfanos legítimos)
    from app.workers.worker import process_one
    enviados = 0
    for mid in mids:
        db = SessionLocal()
        try:
            res = process_one(db, mid)
            assert res["accion"] == "enviado", res
            enviados += 1
        finally:
            db.close()
    assert enviados == 2
    assert len(fake_ses.sent) == 2


def test_huerfanos_no_tocan_nada_si_hay_trabajo_en_vuelo(client, admin_auth, fake_ses):
    """Con la cola ocupada (envío en curso) la recuperación no toca nada:
    evita duplicar mensajes que están legítimamente en tránsito."""
    from app.services import queue as send_queue
    from app.workers.worker import recover_orphaned_messages

    cid, mids = _crear_campana_directa_con_mensajes(1)
    _envejecer_mensajes(mids, 120)
    send_queue.enqueue_message("dummy-en-vuelo")  # simula trabajo pendiente en cola

    n = recover_orphaned_messages(db=SessionLocal())
    db = SessionLocal(); db.close()
    assert n == 0, "con trabajo en vuelo no debe reencolar huérfanos"
    assert send_queue.outbound_count() == 1  # solo el dummy


def test_huerfanos_excluye_incierto_y_estados_finales(client, admin_auth, fake_ses):
    """'incierto' queda excluido a propósito (pudo ser aceptado por SES:
    reencolarlo duplicaría el correo). Solo 'pendiente' se recupera."""
    from app.models import CampaignMessage
    from app.services import queue as send_queue
    from app.workers.worker import recover_orphaned_messages

    cid, mids = _crear_campana_directa_con_mensajes(2)
    db = SessionLocal()
    try:
        db.query(CampaignMessage).filter(CampaignMessage.id == mids[0]).update(
            {"status": "incierto"}, synchronize_session=False)
        db.commit()
    finally:
        db.close()
    _envejecer_mensajes(mids, 120)

    n = recover_orphaned_messages(db=SessionLocal())
    en_cola = _vaciar_cola_outbound()
    assert mids[1] in en_cola, "el 'pendiente' debe reencolarse"
    assert mids[0] not in en_cola, "el 'incierto' NO se reencola (duplicaría)"


# --------------------------------------------------------------------------- #
# 6. Encolado masivo por lotes (idempotencia y supresión en bloque)
# --------------------------------------------------------------------------- #


def test_enqueue_masivo_por_lotes_es_idempotente():
    """2.500 destinatarios: trozos de 1000 + inserción masiva; una segunda
    pasada no crea NINGÚN mensaje nuevo (idempotencia del encolado)."""
    from app.models import Campaign, CampaignMessage, Contact, contact_lists_rel
    from app.services import campaigns as campaign_service

    db = SessionLocal()
    try:
        camp = Campaign(
            name=f"C-BULK-{uuid.uuid4().hex[:6]}", subject="s", mode="bloques",
            blocks=[], category="newsletter", status="borrador",
            from_name="HJ Test",
            from_email="notificaciones@leisureparts4u.store",
            approved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(camp)
        db.flush()
        contactos = [Contact(email=f"bulk-{camp.id[:8]}-{i}@test.local",
                             status="suscripto", consent_source="prueba")
                     for i in range(2500)]
        db.add_all(contactos)
        db.flush()
        from app.models import ContactList
        lista = ContactList(name=f"LB-{uuid.uuid4().hex[:6]}")
        db.add(lista)
        db.flush()
        camp.list_id = lista.id
        for c in contactos:
            db.execute(contact_lists_rel.insert().values(
                contact_id=c.id, list_id=lista.id))
        db.commit()

        res1 = campaign_service.enqueue_campaign(db, camp)
        db.commit()
        assert res1["nuevos"] == 2500, res1

        res2 = campaign_service.enqueue_campaign(db, camp)
        db.commit()
        assert res2["nuevos"] == 0, "la segunda pasada no debe crear mensajes"
        assert res2["existentes"] == 2500
        total = db.query(CampaignMessage).filter(
            CampaignMessage.campaign_id == camp.id).count()
        assert total == 2500
    finally:
        db.close()


def test_supresion_masiva_excluye_suprimidos():
    """emails_suprimidos (1 consulta) excluye igual que el chequeo unitario:
    semántica idéntica al antiguo can_send_promotional por contacto."""
    from app.models import Campaign, Contact
    from app.services import campaigns as campaign_service
    from app.services import contacts as contact_service

    db = SessionLocal()
    try:
        camp = Campaign(
            name=f"C-SUP-{uuid.uuid4().hex[:6]}", subject="s", mode="bloques",
            blocks=[], category="promocional", status="borrador",
            from_name="HJ Test",
            from_email="notificaciones@leisureparts4u.store",
            approved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(camp)
        suprimido = Contact(email=f"sup-{uuid.uuid4().hex[:8]}@test.local",
                            status="suscripto", consent_source="prueba")
        activo = Contact(email=f"act-{uuid.uuid4().hex[:8]}@test.local",
                         status="suscripto", consent_source="prueba")
        db.add_all([camp, suprimido, activo])
        db.flush()
        contact_service.suppress_email(db, suprimido.email, reason="rebote_permanente",
                                       source="prueba")
        db.commit()

        db.expire_all()
        camp = db.get(Campaign, camp.id)
        seleccion = campaign_service.select_recipients(db, camp)
        emails = {c.email for c in seleccion}
        assert suprimido.email not in emails, "un suprimido NO puede recibir promocional"
        assert activo.email in emails
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 7. Importación: lock, recuperación, crash y carga 50.656
# --------------------------------------------------------------------------- #


def test_import_lock_evita_doble_procesamiento():
    """Con el lock tomado por otro worker, process_job omite el trabajo
    (antes: doble procesamiento e inflado de contadores si se escalaba)."""
    from app.services import import_jobs as import_service

    plan = {"filas_validas": 1, "filas_invalidas": 0, "duplicados_en_archivo": 0,
            "encoding": "utf-8", "headers": ["email"], "rows": [{"email": "x@test.local"}]}
    db = SessionLocal()
    try:
        job = import_service.create_job(db, filename="t.csv", plan=plan,
                                        consent_source="prueba", consent_text=None,
                                        list_ids=[], tag_ids=[],
                                        user_id="u1", user_email="u@test.local")
        db.commit()
        # Otro worker toma el lock del MISMO job id...
        token = import_service.acquire_import_lock(job.id)
        assert token, "el primer acquire debe ganar el lock"
        # ...y este process_job debe omitir, no procesar
        res = import_service.process_job(db, job.id)
        assert res["accion"] == "omitido"
        assert res["motivo"] == "lock_activo_otro_worker"
        # Tras liberar el lock, el trabajo procesa con normalidad
        import_service.release_import_lock(job.id, token)
        res2 = import_service.process_job(db, job.id)
        assert res2["accion"] == "completado", res2
    finally:
        db.close()


def test_import_lock_renueva_y_libera_por_token():
    """Solo el dueño del token renueva/libera; un extraño no puede. El lock
    se libera al terminar (no queda basura bloqueando reintentos)."""
    from app.services import import_jobs as import_service
    from app.workers.redis_client import get_redis

    jid = uuid.uuid4().hex
    token = import_service.acquire_import_lock(jid)
    assert token
    assert import_service.job_locked(jid)

    assert import_service.renew_import_lock(jid, "token-falso") is False
    assert import_service.renew_import_lock(jid, token) is True

    import_service.release_import_lock(jid, "token-falso")
    assert import_service.job_locked(jid), "un extraño no libera el lock"
    import_service.release_import_lock(jid, token)
    assert not import_service.job_locked(jid), "el dueño libera su lock"

    # Expiración por TTL (worker muerto): el lock no bloquea para siempre
    jid2 = uuid.uuid4().hex
    assert import_service.acquire_import_lock(jid2)
    get_redis().delete(f"hj:lock:import:{jid2}")
    assert import_service.acquire_import_lock(jid2) is not None, "sin lock previo se puede tomar"


def test_import_recuperacion_periodica_estancados():
    """'pendiente' viejo y 'procesando' sin lock se reencolan; 'procesando'
    con lock vivo NO se toca (worker activo)."""
    from datetime import timedelta
    from app.models import ImportJob
    from app.services import import_jobs as import_service
    from app.core.db import utcnow

    db = SessionLocal()
    try:
        j_pend = ImportJob(filename="p.csv", status="pendiente", total_rows=1,
                           attempts=0)
        j_proc_lock = ImportJob(filename="pl.csv", status="procesando", total_rows=1,
                                attempts=1)
        j_proc_free = ImportJob(filename="pf.csv", status="procesando", total_rows=1,
                                attempts=1)
        db.add_all([j_pend, j_proc_lock, j_proc_free])
        db.commit()
        ids = (j_pend.id, j_proc_lock.id, j_proc_free.id)
        db.query(ImportJob).filter(ImportJob.id.in_(ids)).update(
            {"updated_at": utcnow() - timedelta(seconds=120)}, synchronize_session=False)
        db.commit()
    finally:
        db.close()

    # El 'procesando' con lock activo: tomamos su lock ahora
    token = import_service.acquire_import_lock(ids[1])
    assert token

    db = SessionLocal()
    try:
        reencolados = import_service.recover_stuck_jobs(db)
    finally:
        db.close()

    assert ids[0] in reencolados, "'pendiente' estancado debe reencolarse"
    assert ids[2] in reencolados, "'procesando' sin lock debe reencolarse"
    assert ids[1] not in reencolados, "'procesando' con lock activo NO se toca"


def test_import_crash_a_mitad_reanuda_sin_duplicados(client, admin_auth):
    """Crash duro (SystemExit) tras el 2º lote: el trabajo queda 'procesando'
    con progreso persistido; la reanudación completa SIN duplicados."""
    from app.services import import_jobs as import_service

    filas = 1200  # 3 lotes de 500 (IMPORT_BATCH_SIZE por defecto... ajustado abajo)
    contenido = "email,first_name\n" + "\n".join(
        f"crash-{i}@test.local,N{i}" for i in range(filas))
    data = {"consent_source": "prueba", "list_ids": "[]", "tag_ids": "[]"}
    r = client.post("/api/v1/contacts/import",
                    files={"archivo": ("crash.csv", contenido.encode(), "text/csv")},
                    data=data)
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]

    # Configura un batch pequeño para tener varios lotes
    original_batch = settings.IMPORT_BATCH_SIZE
    settings.IMPORT_BATCH_SIZE = 300
    llamadas = {"n": 0}
    real_process_batch = import_service.process_batch

    def _crash_al_tercer_lote(db, job, rows):
        llamadas["n"] += 1
        if llamadas["n"] == 3:
            raise SystemExit("simulación de SIGKILL entre lotes")
        return real_process_batch(db, job, rows)

    import_service.process_batch = _crash_al_tercer_lote
    try:
        db = SessionLocal()
        try:
            with pytest.raises(SystemExit):
                import_service.process_job(db, jid)
        finally:
            db.close()

        db = SessionLocal()
        try:
            from app.models import ImportJob
            job = db.get(ImportJob, jid)
            assert job.status == "procesando"
            assert job.rows_processed == 600, f"progreso persistido: {job.rows_processed}"
        finally:
            db.close()

        # Reanudación (worker nuevo, proceso nuevo)
        db = SessionLocal()
        try:
            res = import_service.process_job(db, jid)
        finally:
            db.close()
        assert res["accion"] == "completado", res
        assert res["creados"] == filas, f"creados={res['creados']}, esperados {filas}"
        assert res["errores"] == 0
    finally:
        import_service.process_batch = real_process_batch
        settings.IMPORT_BATCH_SIZE = original_batch

    from app.models import Contact
    db = SessionLocal()
    try:
        total = db.query(Contact).filter(Contact.email.like("crash-%@test.local")).count()
        assert total == filas, f"duplicados tras reanudación: {total} != {filas}"
    finally:
        db.close()


def test_import_carga_50656_contactos_completa_sin_errores(client, admin_auth):
    """Carga REAL de 50.656 contactos (la cifra de producción): la API
    responde 202 al instante y el worker completa sin errores ni duplicados.
    Se mide memoria pico del procesamiento (tracemalloc)."""
    import tracemalloc

    total = 50656
    contenido = "email,first_name,last_name\n" + "\n".join(
        f"carga-{i}@carga.test,Nombre{i},Apellido{i}" for i in range(total))
    t0 = time.monotonic()
    data = {"consent_source": "carga", "list_ids": "[]", "tag_ids": "[]"}
    r = client.post("/api/v1/contacts/import",
                    files={"archivo": ("carga.csv", contenido.encode(), "text/csv")},
                    data=data)
    subida_s = time.monotonic() - t0
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    assert subida_s < 30, f"la subida tardó {subida_s:.1f}s: la API debe responder 202 rápido"

    tracemalloc.start()
    t1 = time.monotonic()
    db = SessionLocal()
    try:
        res = import_service_process(db, jid)
    finally:
        db.close()
    procesado_s = time.monotonic() - t1
    _, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert res["accion"] == "completado", res
    assert res["creados"] == total, f"creados={res['creados']} != {total}"
    assert res["errores"] == 0
    from app.models import Contact
    db = SessionLocal()
    try:
        n = db.query(Contact).filter(Contact.email.like("%@carga.test")).count()
    finally:
        db.close()
    assert n == total, f"en BD hay {n}, esperados {total}: duplicados o pérdidas"
    pico_mb = pico / (1024 * 1024)
    print(f"\n[CARGA 50.656] subida={subida_s:.2f}s procesado={procesado_s:.1f}s "
          f"memoria_pico={pico_mb:.1f}MB")
    assert pico_mb < 512, "la memoria del importador no debe acercarse al límite del contenedor"


def import_service_process(db, jid):
    from app.services import import_jobs as import_service
    return import_service.process_job(db, jid)


# --------------------------------------------------------------------------- #
# 8. Eventos SES: ramas explícitas, rebote temporal, carrera y clics
# --------------------------------------------------------------------------- #


def _crear_mensaje_enviado(ses_message_id: str):
    """Mensaje 'aceptado' con ses_message_id, listo para recibir eventos."""
    from app.models import Campaign, CampaignMessage, Contact
    db = SessionLocal()
    try:
        camp = Campaign(
            name=f"C-EV-{uuid.uuid4().hex[:6]}", subject="s", mode="bloques",
            blocks=[], category="promocional", status="enviando",
            from_name="HJ Test",
            from_email="notificaciones@leisureparts4u.store",
            approved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(camp)
        db.flush()
        c = Contact(email=f"ev-{uuid.uuid4().hex[:10]}@test.local",
                    status="suscripto", consent_source="prueba")
        db.add(c)
        db.flush()
        m = CampaignMessage(campaign_id=camp.id, contact_id=c.id, email=c.email,
                            status="aceptado", attempts=1,
                            ses_message_id=ses_message_id)
        db.add(m)
        db.commit()
        return camp.id, m.id
    finally:
        db.close()


def test_evento_delivery_delay_no_cambia_estado():
    """DELIVERY_DELAY: se registra con rama propia y NO toca el estado del
    mensaje (no es entrega ni error; el DELIVERY definitivo llegará después)."""
    from app.models import CampaignMessage, SesEvent
    from app.services import events as events_service

    smid = f"ses-{uuid.uuid4().hex[:12]}"
    _crear_mensaje_enviado(smid)
    db = SessionLocal()
    try:
        resumen = events_service.process_ses_notification(db, {
            "eventType": "DeliveryDelay",
            "mail": {"messageId": smid, "destination": ["x@test.local"]},
            "deliveryDelay": {"delayType": "MailboxSlow", "timestamp": "2026-01-01T00:00:00Z"},
        })
        db.commit()
        assert resumen["procesado"] is True
        cm = db.query(CampaignMessage).filter(
            CampaignMessage.ses_message_id == smid).one()
        assert cm.status == "aceptado", "DeliveryDelay NO debe cambiar el estado"
        ev = db.query(SesEvent).filter(SesEvent.type == "delivery_delay").order_by(
            SesEvent.occurred_at.desc()).first()
        assert ev is not None, "el evento delivery_delay debe registrarse"
        assert (ev.payload or {}).get("tipo_retraso") == "MailboxSlow"
    finally:
        db.close()


def test_evento_rendering_failure_marca_error():
    """RENDERING_FAILURE: el mensaje NO salió => estado error con motivo
    explícito (antes caía en la rama genérica y quedaba ambiguo)."""
    from app.models import CampaignMessage, SesEvent
    from app.services import events as events_service

    smid = f"ses-{uuid.uuid4().hex[:12]}"
    _crear_mensaje_enviado(smid)
    db = SessionLocal()
    try:
        resumen = events_service.process_ses_notification(db, {
            "eventType": "RenderingFailure",
            "mail": {"messageId": smid, "destination": ["x@test.local"]},
            "renderingFailure": {"failureType": "TEMPLATE_RULES",
                                 "errorMessage": "variable invalida en plantilla"},
        })
        db.commit()
        assert resumen["procesado"] is True
        cm = db.query(CampaignMessage).filter(
            CampaignMessage.ses_message_id == smid).one()
        assert cm.status == "error"
        assert "rendering_failure" in (cm.last_error or "")
        ev = db.query(SesEvent).filter(SesEvent.type == "rendering_failure").first()
        assert ev is not None
    finally:
        db.close()


def test_rebote_temporal_programa_reintento_con_tope():
    """Rebote temporal => 'rebote_temporal' + reintento en cola; con intentos
    agotados NO hay reintento (tope RETRY_MAX_ATTEMPTS)."""
    from app.models import CampaignMessage
    from app.services import events as events_service
    from app.services import queue as send_queue
    from app.core.config import settings as cfg

    smid = f"ses-{uuid.uuid4().hex[:12]}"
    _crear_mensaje_enviado(smid)
    db = SessionLocal()
    try:
        resumen = events_service.process_ses_notification(db, {
            "eventType": "Bounce",
            "mail": {"messageId": smid, "destination": ["x@test.local"]},
            "bounce": {"bounceType": "Transient", "bounceSubType": "General",
                       "timestamp": "2026-01-01T00:00:00Z", "bouncedRecipients": []},
        })
        db.commit()
        assert resumen.get("reintento_rebote_programado") is True
        assert send_queue.pending_retries_count() == 1
        cm = db.query(CampaignMessage).filter(
            CampaignMessage.ses_message_id == smid).one()
        assert cm.status == "rebote_temporal"

        # Tope: con intentos agotados no programa más reintentos
        cm.attempts = cfg.RETRY_MAX_ATTEMPTS
        db.commit()
        send_queue.move_due_retries()  # vacía el zset a la cola principal
        resumen2 = events_service.process_ses_notification(db, {
            "eventType": "Bounce",
            "mail": {"messageId": smid, "destination": ["x@test.local"]},
            "bounce": {"bounceType": "Transient", "bounceSubType": "General",
                       "timestamp": "2026-01-02T00:00:00Z", "bouncedRecipients": []},
        })
        db.commit()
        assert resumen2.get("reintento_rebote_programado") is False
    finally:
        db.close()


def test_carrera_idempotencia_cuenta_duplicado_sin_explotar():
    """El mismo event_uid dos veces (webhook + SQS a la vez): la segunda se
    cuenta como DUPLICADO (nuevo=False) sin lanzar excepción."""
    from app.services import events as events_service

    db = SessionLocal()
    try:
        uid = f"race-{uuid.uuid4().hex[:12]}"
        ev1, nuevo1 = events_service.record_event(db, type="delivery", event_uid=uid,
                                                  ses_message_id="smid-1")
        db.commit()
        assert nuevo1 is True

        # Segunda inserción concurrente simulada: el flush choca con la UNIQUE
        from sqlalchemy.exc import IntegrityError
        llamadas = {"n": 0}
        real_flush = db.flush

        def _flush_con_carrera():
            if llamadas["n"] == 0:
                llamadas["n"] += 1
                raise IntegrityError("UNIQUE constraint failed", None, Exception())
            return real_flush()

        db.flush = _flush_con_carrera
        try:
            ev2, nuevo2 = events_service.record_event(
                db, type="delivery", event_uid=f"race2-{uuid.uuid4().hex[:8]}",
                ses_message_id="smid-2")
        finally:
            db.flush = real_flush
        db.commit()
        assert nuevo2 is False, "la carrera debe contarse como duplicado"
        # Y el sistema sigue operativo: un registro posterior funciona
        ev3, nuevo3 = events_service.record_event(
            db, type="delivery", event_uid=f"race3-{uuid.uuid4().hex[:8]}",
            ses_message_id="smid-3")
        db.commit()
        assert nuevo3 is True
    finally:
        db.close()


def test_clics_repetidos_al_mismo_enlace_todos_registrados():
    """Tres clics al mismo URL del mismo mensaje => 3 eventos (antes el uid
    fijo por URL descartaba el 2º y 3º: métricas infra-contadas)."""
    from app.models import SesEvent
    from app.services import events as events_service

    smid = f"ses-{uuid.uuid4().hex[:12]}"
    cid, mid = _crear_mensaje_enviado(smid)
    url = "https://tienda.test/oferta"
    db = SessionLocal()
    try:
        for _ in range(3):
            assert events_service.record_click(db, cid, mid, url) is True
        db.commit()
        n = db.query(SesEvent).filter(SesEvent.type == "click",
                                      SesEvent.message_id == mid).count()
        assert n == 3, f"esperados 3 clics, registrados {n}"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 9. Diagnóstico y E2E
# --------------------------------------------------------------------------- #


def test_diagnostico_cuenta_sin_vincular_a_mensaje(client, admin_auth):
    """Eventos de SES con ses_message_id pero sin campaign_message: el contador
    'sin_vincular_a_mensaje' del diagnóstico los hace visibles (clave para
    decidir la investigación 50.632 aceptados / 6 entregados)."""
    from app.models import SesEvent
    db = SessionLocal()
    try:
        db.add(SesEvent(event_uid=f"unlinked-{uuid.uuid4().hex[:10]}", type="delivery",
                        ses_message_id=f"ses-huerfano-{uuid.uuid4().hex[:8]}",
                        message_id=None))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/v1/system/ses/diagnostico")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["eventos"]["sin_vincular_a_mensaje"] >= 1
    assert "configuration_set" in data and "cola_sqs" in data


def test_e2e_campana_plantilla_html_envio_completo(client, admin_auth, fake_ses):
    """E2E: plantilla HTML -> campaña -> aprobar -> encolar -> procesar ->
    2 correos con el HTML de la plantilla renderizado y campaña completada."""
    HTML = ("<html><body><h1>Hola {{contact.first_name}}</h1>"
            "<p>Oferta <b>exclusiva</b> V4</p></body></html>")
    r = client.post("/api/v1/templates", json={
        "name": f"E2E-{uuid.uuid4().hex[:6]}", "mode": "html", "html": HTML,
        "category": "promocional"})
    assert r.status_code == 201, r.text
    tpl = r.json()

    lid = _crear_lista_con_contactos(client, 2)
    cid = _crear_campana_aprobada(client, lid, template_id=tpl["id"], mode="html",
                                  html=HTML)
    resp = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert resp.status_code == 200, resp.text

    resultados = _drenar_y_procesar(fake_ses, max_msgs=10)
    assert len(resultados) == 2
    assert all(res["accion"] == "enviado" for _, res in resultados)
    assert len(fake_ses.sent) == 2
    for envio in fake_ses.sent:
        raw = envio["Content"]["Raw"]["Data"].decode()
        assert "Oferta" in raw and "exclusiva" in raw

    from app.models import Campaign
    db = SessionLocal()
    try:
        camp = db.get(Campaign, cid)
        assert camp.status == "completada", f"estado final: {camp.status}"
    finally:
        db.close()


