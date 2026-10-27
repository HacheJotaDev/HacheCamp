"""Pruebas de la versión PRO (v2): sanitizador con CSS inline, contactos
solo-email, importación TXT, borrado masivo, preview de destinatarios,
programación, cola activa, métricas y entregabilidad.
"""
from __future__ import annotations

import io
import uuid

import pytest

# --------------------------------------------------------------------------- #
# 1. Sanitizador: <style> conservado + CSS inline + protección
# --------------------------------------------------------------------------- #


HTML_CON_CSS = """<html><head><style>
.titulo { color:#e11d48; font-size:28px; }
.cta { background:#10b981; color:white; padding:12px 24px; border-radius:8px; }
@media (max-width:600px) { .titulo { font-size:20px; } }
</style></head><body>
<h1 class="titulo">Hola Mundo</h1>
<a class="cta" href="https://ejemplo.com">Ver más</a>
<script>alert('xss')</script>
<a href="javascript:alert(1)">mal</a>
</body></html>"""


def test_sanitize_conserva_style_y_css():
    from app.services.sanitizer import sanitize_email_html

    out = sanitize_email_html(HTML_CON_CSS)
    assert "<style" in out, "El bloque <style> no debe eliminarse"
    assert ".titulo" in out and "#e11d48" in out, "El CSS personalizado debe conservarse"
    assert "<script" not in out and "alert(" not in out, "El JS debe eliminarse"
    assert "javascript:" not in out, "URLs javascript: deben bloquearse"


def test_inline_css_convierte_estilos_inline():
    from app.services.sanitizer import inline_email_css, sanitize_email_html

    sanitized = sanitize_email_html(HTML_CON_CSS)
    inlined = inline_email_css(sanitized)
    assert 'style="' in inlined
    assert "color:#e11d48" in inlined, "La regla .titulo debe quedar inline"
    assert "#10b981" in inlined, "La regla .cta debe quedar inline"
    assert "<style" in inlined, "Las media queries deben conservarse en <style>"
    assert "@media" in inlined


def test_prepare_email_html_pipeline_completo():
    from app.services.sanitizer import prepare_email_html

    out = prepare_email_html(HTML_CON_CSS)
    assert "font-size:28px" in out  # inlineado
    assert "alert(" not in out and "javascript:" not in out  # seguro


def test_render_campaign_email_conserva_diseño():
    from app.services.renderer import render_campaign_email

    context = {
        "contact": {"email": "a@b.co", "first_name": "Ana", "last_name": "", "attributes": {}},
        "campaign": {"name": "C1", "subject": "Asunto"},
        "unsubscribe_url": "https://x.local/baja",
    }
    subject, html, plain = render_campaign_email(
        mode="html", blocks=None, html_source=HTML_CON_CSS,
        subject="Asunto", context=context,
    )
    assert "font-size:28px" in html, "El CSS debe llegar inline al HTML final"
    assert "unsubscribe_url" not in html or "https://x.local/baja" in html


def test_sanitizador_sin_style_no_falla():
    from app.services.sanitizer import prepare_email_html

    out = prepare_email_html("<p>Solo texto</p>")
    assert "Solo texto" in out


# --------------------------------------------------------------------------- #
# 2. Contactos solo-email + importación TXT + borrado masivo
# --------------------------------------------------------------------------- #


def test_crear_contacto_solo_email(client, admin_auth):
    email = f"solo-{uuid.uuid4().hex[:6]}@test.local"
    resp = client.post("/api/v1/contacts", json={"email": email})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["email"] == email
    assert data["status"] == "suscripto"


def test_crear_contacto_duplicado_no_duplica(client, admin_auth):
    email = f"dup-{uuid.uuid4().hex[:6]}@test.local"
    r1 = client.post("/api/v1/contacts", json={"email": email})
    r2 = client.post("/api/v1/contacts", json={"email": email.upper()})
    assert r1.status_code == 201 and r2.status_code in (200, 201)


def test_import_txt_un_correo_por_linea(client, admin_auth):
    contenido = "\n".join(
        [f"txt{i}-{uuid.uuid4().hex[:5]}@test.local" for i in range(4)] + ["linea-mala", ""]
    ).encode()
    resp = client.post(
        "/api/v1/contacts/import",
        files={"archivo": ("contactos.txt", io.BytesIO(contenido), "text/plain")},
        data={"consent_source": "", "dry_run": "false"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["creados"] == 4
    assert data["filas_invalidas"] == 1


def test_import_csv_duplicados_en_archivo(client, admin_auth):
    email = f"csv-{uuid.uuid4().hex[:6]}@test.local"
    contenido = f"email\n{email}\n{email}\n{email}\n".encode()
    resp = client.post(
        "/api/v1/contacts/import",
        files={"archivo": ("l.csv", io.BytesIO(contenido), "text/csv")},
        data={"consent_source": "", "dry_run": "false"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["creados"] == 1
    assert data["duplicados_en_archivo"] == 2


def test_bulk_delete_eliminar(client, admin_auth):
    ids = []
    for _ in range(3):
        r = client.post("/api/v1/contacts", json={"email": f"bulk-{uuid.uuid4().hex[:6]}@test.local"})
        ids.append(r.json()["id"])
    resp = client.post("/api/v1/contacts/bulk-delete",
                       json={"ids": ids, "modo": "eliminar", "confirmar": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["procesados"] == 3
    # Ya no existen
    for cid in ids:
        assert client.get(f"/api/v1/contacts/{cid}").status_code == 404


def test_bulk_delete_requiere_confirmacion(client, admin_auth):
    r = client.post("/api/v1/contacts", json={"email": f"conf-{uuid.uuid4().hex[:6]}@test.local"})
    cid = r.json()["id"]
    resp = client.post("/api/v1/contacts/bulk-delete", json={"ids": [cid], "modo": "eliminar"})
    assert resp.status_code == 422  # sin confirmar=True se rechaza
    # El contacto sigue existiendo
    assert client.get(f"/api/v1/contacts/{cid}").status_code == 200


def test_bulk_delete_dar_baja_suprime(client, admin_auth):
    email = f"baja-{uuid.uuid4().hex[:6]}@test.local"
    r = client.post("/api/v1/contacts", json={"email": email})
    cid = r.json()["id"]
    resp = client.post("/api/v1/contacts/bulk-delete",
                       json={"ids": [cid], "modo": "dar_baja", "confirmar": True})
    assert resp.status_code == 200
    detalle = client.get(f"/api/v1/contacts/{cid}").json()
    assert detalle["status"] == "dado_baja"


def test_buscador_y_contador_contactos(client, admin_auth):
    marcador = uuid.uuid4().hex[:8]
    client.post("/api/v1/contacts", json={"email": f"busca-{marcador}@test.local"})
    resp = client.get(f"/api/v1/contacts?q=busca-{marcador}")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


# --------------------------------------------------------------------------- #
# 3. Campañas: preview de destinatarios y remitente automático
# --------------------------------------------------------------------------- #


def _crear_lista(client) -> str:
    r = client.post("/api/v1/lists", json={"name": f"Lista {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_recipients_preview(client, admin_auth):
    lista = _crear_lista(client)
    for _ in range(3):
        r = client.post("/api/v1/contacts", json={"email": f"prev-{uuid.uuid4().hex[:6]}@test.local",
                                                  "list_ids": [lista]})
        assert r.status_code == 201
    resp = client.post("/api/v1/campaigns/recipients-preview",
                       json={"list_id": lista, "category": "promocional"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["destinatarios"] == 3


def test_crear_campana_remitente_automatico(client, admin_auth):
    lista = _crear_lista(client)
    r = client.post("/api/v1/contacts", json={"email": f"rem-{uuid.uuid4().hex[:6]}@test.local",
                                              "list_ids": [lista]})
    assert r.status_code == 201
    resp = client.post("/api/v1/campaigns", json={
        "name": "Campaña sin remitente",
        "subject": "Hola",
        "mode": "html",
        "html": "<p>Contenido</p>",
        "list_id": lista,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["from_email"] == "notificaciones@leisureparts4u.store", \
        "Debe usar el remitente configurado del proyecto"
    assert data["from_name"]


# --------------------------------------------------------------------------- #
# 4. Programación: programar, reprogramar, cancelar programación
# --------------------------------------------------------------------------- #


def _campana_aprobada(client, admin_auth) -> str:
    lista = _crear_lista(client)
    client.post("/api/v1/contacts", json={"email": f"sch-{uuid.uuid4().hex[:6]}@test.local",
                                          "list_ids": [lista]})
    r = client.post("/api/v1/campaigns", json={
        "name": f"Programada {uuid.uuid4().hex[:5]}",
        "subject": "Próximamente",
        "mode": "html",
        "html": "<p>Hola</p>",
        "list_id": lista,
    })
    cid = r.json()["id"]
    resp = client.post(f"/api/v1/campaigns/{cid}/approve")
    assert resp.status_code == 200, resp.text
    return cid


def test_programar_reprogramar_y_cancelar(client, admin_auth):
    cid = _campana_aprobada(client, admin_auth)
    # Programar
    r1 = client.post(f"/api/v1/campaigns/{cid}/schedule",
                     json={"scheduled_at": "2030-06-01T09:00:00Z", "timezone": "America/Lima"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["estado"] == "programada"
    # Próximas
    r2 = client.get("/api/v1/campaigns/scheduled/upcoming")
    assert r2.status_code == 200
    assert any(c["id"] == cid for c in r2.json()["items"])
    # Reprogramar
    r3 = client.put(f"/api/v1/campaigns/{cid}/schedule",
                    json={"scheduled_at": "2030-07-02T10:30:00Z", "timezone": "UTC"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["scheduled_at"].startswith("2030-07-02T10:30")
    # Cancelar la programación (vuelve a borrador)
    r4 = client.post(f"/api/v1/campaigns/{cid}/schedule/cancel")
    assert r4.status_code == 200, r4.text
    assert r4.json()["estado"] == "borrador"
    detalle = client.get(f"/api/v1/campaigns/{cid}").json()
    assert detalle["scheduled_at"] is None


def test_zona_horaria_invalida_rechazada(client, admin_auth):
    cid = _campana_aprobada(client, admin_auth)
    resp = client.post(f"/api/v1/campaigns/{cid}/schedule",
                       json={"scheduled_at": "2030-06-01T09:00:00Z", "timezone": "Marte/Olympus"})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# 5. Cola activa: progreso, velocidad, pausa y reanudación
# --------------------------------------------------------------------------- #


def test_cola_activa_vacia(client, admin_auth):
    resp = client.get("/api/v1/campaigns/queue/active")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_pausa_y_reanudacion_encolada(client, admin_auth, fake_ses):
    lista = _crear_lista(client)
    for _ in range(3):
        client.post("/api/v1/contacts", json={"email": f"q-{uuid.uuid4().hex[:6]}@test.local",
                                              "list_ids": [lista]})
    r = client.post("/api/v1/campaigns", json={
        "name": f"Cola {uuid.uuid4().hex[:5]}",
        "subject": "Test cola",
        "mode": "html",
        "html": "<p>Contenido de prueba</p>",
        "list_id": lista,
    })
    cid = r.json()["id"]
    client.post(f"/api/v1/campaigns/{cid}/approve")
    encolar = client.post(f"/api/v1/campaigns/{cid}/send-now")
    assert encolar.status_code == 200, encolar.text
    assert encolar.json()["destinatarios"] == 3

    cola = client.get("/api/v1/campaigns/queue/active").json()
    item = next(i for i in cola["items"] if i["id"] == cid)
    assert item["total"] == 3
    assert item["pendientes"] == 3

    # Pausar
    p = client.post(f"/api/v1/campaigns/{cid}/pause")
    assert p.status_code == 200 and p.json()["estado"] == "pausada"
    # Reanudar
    r2 = client.post(f"/api/v1/campaigns/{cid}/resume")
    assert r2.status_code == 200 and r2.json()["estado"] in ("enviando", "en_cola")


# --------------------------------------------------------------------------- #
# 6. Métricas con denominadores correctos
# --------------------------------------------------------------------------- #


def _campana_con_mensajes(db, fake_ses) -> tuple[str, dict]:
    """Crea campaña + mensajes con estados controlados y eventos asociados."""
    from app.core.db import utcnow
    from app.models import Campaign, CampaignMessage, Contact, SesEvent, contact_lists_rel
    from sqlalchemy import insert

    lista_id = str(uuid.uuid4())
    from app.models import ContactList
    db.add(ContactList(id=lista_id, name=f"L-{uuid.uuid4().hex[:8]}"))
    db.flush()

    cid = str(uuid.uuid4())
    camp = Campaign(
        id=cid, name=f"Métricas {uuid.uuid4().hex[:5]}", subject="s",
        from_name="F", from_email="f@test.local", mode="html", html="<p>x</p>",
        status="completada", category="promocional", list_id=lista_id,
        approved_at=utcnow(), completed_at=utcnow(),
    )
    db.add(camp)

    estados = {
        "entregado": 6, "rebote_permanente": 2, "rebote_temporal": 1,
        "error": 1, "queja": 1, "incierto": 1, "pendiente": 3, "cancelado": 1,
    }
    mid_by_estado: dict[str, list[str]] = {}
    for estado, n in estados.items():
        for _ in range(n):
            email = f"m-{uuid.uuid4().hex[:8]}@test.local"
            contact = Contact(id=str(uuid.uuid4()), email=email, status="suscripto")
            db.add(contact)
            db.flush()
            db.execute(insert(contact_lists_rel).values(contact_id=contact.id, list_id=lista_id))
            mid = str(uuid.uuid4())
            db.add(CampaignMessage(
                id=mid, campaign_id=cid, contact_id=contact.id, email=email,
                status=estado, sent_at=utcnow() if estado != "pendiente" else None,
                ses_message_id=f"ses-{mid[:12]}" if estado != "pendiente" else None,
            ))
            db.flush()
            mid_by_estado.setdefault(estado, []).append(mid)
    db.commit()

    # Aperturas: 2 destinatarios únicos entregados (uno con 2 aperturas en días distintos)
    entregados = mid_by_estado["entregado"]
    db.add(SesEvent(event_uid=f"o1-{uuid.uuid4().hex[:6]}", type="open", campaign_id=cid,
                    message_id=entregados[0], email="x@test.local"))
    db.add(SesEvent(event_uid=f"o2-{uuid.uuid4().hex[:6]}", type="open", campaign_id=cid,
                    message_id=entregados[1], email="y@test.local"))
    # Un tercer mensaje entregado con DOS aperturas: cuenta 1 persona
    db.add(SesEvent(event_uid=f"o3-{uuid.uuid4().hex[:6]}", type="open", campaign_id=cid,
                    message_id=entregados[2], email="z@test.local"))
    db.add(SesEvent(event_uid=f"o4-{uuid.uuid4().hex[:6]}", type="open", campaign_id=cid,
                    message_id=entregados[2], email="z@test.local"))
    # Clics: 1 único destinatario
    db.add(SesEvent(event_uid=f"c1-{uuid.uuid4().hex[:6]}", type="click", campaign_id=cid,
                    message_id=entregados[0], email="x@test.local", url="https://a.co"))
    db.commit()
    return cid, mid_by_estado


def test_metricas_globales_denominadores(db, fake_ses):
    from app.services.stats import metrics_summary

    cid, _ = _campana_con_mensajes(db, fake_ses)
    m = metrics_summary(db, campaign_id=cid)
    # Aceptados por SES = entregado 6 + duro 2 + suave 1 + queja 1 + incierto 1 = 11
    assert m["aceptados_ses"] == 11
    # Definitivos = 6 + 2 + 1 + 1 + 1 = 11 ; success = 6/11 = 54.5
    assert m["success_rate"] == 54.5
    # Bounce rate = (2+1)/11 = 27.3
    assert m["bounce_rate"] == 27.3
    # Open rate = 3 personas únicas / 6 entregados = 50.0
    assert m["open_rate"] == 50.0
    assert m["aperturas_unicas"] == 3, "Varias aperturas del mismo destinatario cuentan 1"
    # Click rate = 1 / 6 = 16.7
    assert m["click_rate"] == 16.7
    assert m["fallidos"] == 1
    assert "denominadores" in m


def test_metricas_sin_denominador_devuelven_none(db, fake_ses):
    from app.services.stats import metrics_summary

    m = metrics_summary(db, campaign_id="no-existe")
    assert m["success_rate"] is None
    assert m["bounce_rate"] is None
    assert m["open_rate"] is None


def test_endpoint_metricas_por_campana(client, admin_auth, db, fake_ses):
    from app.services.stats import metrics_summary

    cid, _ = _campana_con_mensajes(db, fake_ses)
    esperado = metrics_summary(db, campaign_id=cid)
    resp = client.get(f"/api/v1/stats/metrics?campana={cid}")
    assert resp.status_code == 200
    assert resp.json()["success_rate"] == esperado["success_rate"]
    # El detalle de campaña también incluye métricas
    detalle = client.get(f"/api/v1/campaigns/{cid}").json()
    assert detalle["metricas"]["aceptados_ses"] == 11


# --------------------------------------------------------------------------- #
# 7. Entregabilidad honesta
# --------------------------------------------------------------------------- #


def test_entregabilidad_sin_inventar_inbox_spam(client, admin_auth):
    resp = client.get("/api/v1/stats/deliverability")
    assert resp.status_code == 200
    data = resp.json()
    assert data["inbox_confirmado"] is None
    assert data["spam_confirmado"] is None
    assert data["inbox_testing"]["disponible"] is False
    assert "SES no informa" in data["inbox_testing"]["motivo"]
    assert "nota" in data


# --------------------------------------------------------------------------- #
# 8. Serie diaria con filtro de campaña
# --------------------------------------------------------------------------- #


def test_serie_filtrada_por_campana(client, admin_auth, db, fake_ses):
    cid, _ = _campana_con_mensajes(db, fake_ses)
    resp = client.get(f"/api/v1/stats/series?dias=14&campana={cid}")
    assert resp.status_code == 200
    puntos = resp.json()["puntos"]
    assert len(puntos) == 14
    total_aperturas = sum(p["open"] for p in puntos)
    assert total_aperturas >= 4
