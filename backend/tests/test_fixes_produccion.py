"""Pruebas de regresión para errores reales detectados en producción (PRO v2).

Cubre:
1. NameError `inline_email_css` en la previsualización de plantillas HTML.
2. Contrato multipart de importación CSV/TXT (campo 'archivo') — el frontend
   enviaba el FormData serializado como JSON ("{}") y la API respondía
   422 "Field required".
3. Eliminación de contactos (individual vía bulk-delete) con integridad:
   cascada de mensajes/consentimiento, conservación de supresiones y auditoría.
4. Eliminación de plantillas: protección de plantillas del sistema y campañas
   asociadas sin ruptura (contenido congelado + referencia SET NULL).
"""
from __future__ import annotations

import io
import uuid

from app.core.db import SessionLocal
from app.models import (
    AuditLog,
    Campaign,
    CampaignMessage,
    ConsentEvent,
    Contact,
    Suppression,
    Template,
)

HTML_PREVIEW = """<html><head><style>
.titulo { color:#e11d48; font-size:28px; }
</style></head><body><h1 class="titulo">Hola {{contact.first_name}}</h1></body></html>"""


def _email(marca: str) -> str:
    return f"{marca}-{uuid.uuid4().hex[:6]}@test.local"


def _crear_contacto(client, marca: str = "fix") -> dict:
    r = client.post("/api/v1/contacts", json={"email": _email(marca)})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# 1. Previsualización de plantillas (NameError: inline_email_css)
# --------------------------------------------------------------------------- #


def test_preview_html_con_css_inline(client, admin_auth):
    """Regresión: POST /templates/preview (mode=html) fallaba con NameError
    porque inline_email_css no estaba importado en templates.py."""
    resp = client.post("/api/v1/templates/preview", json={
        "mode": "html",
        "html": HTML_PREVIEW,
        "sample_email": "ana@destinatario.com",
        "sample_name": "Ana",
    })
    assert resp.status_code == 200, resp.text
    html = resp.json()["html"]
    assert "titulo" in html
    # El CSS del bloque <style> debe quedar aplicado inline
    assert "color:#e11d48" in html or "color: #e11d48" in html, (
        "El CSS personalizado debe aparecer inline en la previsualización"
    )
    assert "<script" not in html


def test_preview_bloques_funciona(client, admin_auth):
    resp = client.post("/api/v1/templates/preview", json={
        "mode": "bloques",
        "blocks": [{"type": "titulo", "props": {"text": "Un titular", "level": 2}}],
    })
    assert resp.status_code == 200, resp.text
    assert "Un titular" in resp.json()["html"]


# --------------------------------------------------------------------------- #
# 2. Importación CSV/TXT: contrato multipart del campo 'archivo'
# --------------------------------------------------------------------------- #


def test_import_preview_solo_archivo_como_el_frontend(client, admin_auth):
    """Contrato exacto del frontend corregido: solo el campo 'archivo'."""
    contenido = f"email,nombre\n{_email('prev')},Ana\n{_email('prev')},Luis\n".encode()
    resp = client.post(
        "/api/v1/contacts/import/preview",
        files={"archivo": ("correos.csv", io.BytesIO(contenido), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "email" in data["encabezados"]
    assert data["filas_validas"] == 2
    assert data["filas_invalidas"] == 0
    assert len(data["muestra"]) == 2


def test_import_archivo_faltante_error_identificable(client, admin_auth):
    """Sin el campo 'archivo' la API responde 422 y el detalle menciona el campo,
    para que la interfaz pueda mostrar un error comprensible."""
    resp = client.post("/api/v1/contacts/import/preview", data={})
    assert resp.status_code == 422
    assert "archivo" in resp.text, "El detalle 422 debe citar el campo faltante"


def test_import_csv_con_consentimiento_minimo(client, admin_auth, importar_csv):
    """Flujo completo de importación con los campos que envía el frontend."""
    email = _email("imp")
    contenido = f"email\n{email}\n".encode()
    progreso = importar_csv(contenido, consent_source="importacion_csv")
    assert progreso["creados"] == 1
    assert progreso["actualizados"] == 0
    assert progreso["duplicados_en_archivo"] == 0


def test_import_csv_invalido_mensaje_comprensible(client, admin_auth):
    """CSV sin columna de correo debe dar un error claro, no un 500."""
    contenido = b"nombre,telefono\nAna,600111222\n"
    resp = client.post(
        "/api/v1/contacts/import/preview",
        files={"archivo": ("sin_email.csv", io.BytesIO(contenido), "text/csv")},
    )
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# 3. Eliminación de contactos: integridad y conservación
# --------------------------------------------------------------------------- #


def _crear_campana_con_mensaje(client, contact_id: str, email: str) -> str:
    """Crea campaña (borrador) + un mensaje del contacto vía BD."""
    db = SessionLocal()
    try:
        campaign = Campaign(
            name=f"Camp {uuid.uuid4().hex[:6]}", subject="Asunto",
            from_name="HJ", from_email="notificaciones@leisureparts4u.store",
            mode="html", html="<p>Hola</p>", status="borrador",
        )
        db.add(campaign)
        db.flush()
        db.add(CampaignMessage(
            campaign_id=campaign.id, contact_id=contact_id, email=email,
            status="entregado", ses_message_id=f"ses-{uuid.uuid4().hex[:10]}",
        ))
        db.add(ConsentEvent(
            contact_id=contact_id, event="alta", source="manual",
            detail={"prueba": True},
        ))
        db.commit()
        return campaign.id
    finally:
        db.close()


def test_eliminar_contacto_individual_con_cascada(client, admin_auth):
    """Eliminar un contacto individual borra sus mensajes y consent_history,
    pero conserva la auditoría."""
    contacto = _crear_contacto(client, "cascada")
    campaign_id = _crear_campana_con_mensaje(client, contacto["id"], contacto["email"])

    resp = client.post("/api/v1/contacts/bulk-delete",
                       json={"ids": [contacto["id"]], "modo": "eliminar", "confirmar": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"procesados": 1, "solicitados": 1, "modo": "eliminar"}

    # El contacto ya no existe
    assert client.get(f"/api/v1/contacts/{contacto['id']}").status_code == 404

    db = SessionLocal()
    try:
        # Cascada: mensajes de campaña del contacto eliminados
        mensajes = db.query(CampaignMessage).filter(
            CampaignMessage.contact_id == contacto["id"]).count()
        assert mensajes == 0, "Los mensajes del contacto deben borrarse en cascada"
        # La campaña sigue existiendo (no se rompe)
        assert db.get(Campaign, campaign_id) is not None
        # Cascada: historial de consentimiento eliminado
        consent = db.query(ConsentEvent).filter(
            ConsentEvent.contact_id == contacto["id"]).count()
        assert consent == 0
        # Auditoría conservada
        audit_reg = (db.query(AuditLog)
                     .filter(AuditLog.action == "contactos_eliminados_masivo")
                     .order_by(AuditLog.created_at.desc()).first())
        assert audit_reg is not None
        assert audit_reg.detail["cantidad"] == 1 and audit_reg.detail["modo"] == "eliminar"
    finally:
        db.close()


def test_eliminar_contacto_conserva_supresion_y_bloquea_reincorporacion(client, admin_auth, importar_csv):
    """Si el contacto tenía supresión (p. ej. por queja), al eliminarlo la
    supresión se conserva: una reimportación no lo reactiva como suscripto."""
    email = _email("supres")
    r = client.post("/api/v1/contacts", json={"email": email})
    cid = r.json()["id"]

    # Supresión manual del correo (existente independientemente del contacto)
    resp_sup = client.post("/api/v1/suppression",
                           json={"email": email, "reason": "manual"})
    assert resp_sup.status_code in (200, 201), resp_sup.text

    # Eliminación definitiva del contacto
    resp = client.post("/api/v1/contacts/bulk-delete",
                       json={"ids": [cid], "modo": "eliminar", "confirmar": True})
    assert resp.status_code == 200

    db = SessionLocal()
    try:
        sup = db.query(Suppression).filter(Suppression.email == email).first()
        assert sup is not None, "La supresión debe conservarse tras eliminar el contacto"
    finally:
        db.close()

    # Reimportar el mismo correo: NO debe quedar suscripto (protección por supresión)
    contenido = f"email\n{email}\n".encode()
    progreso = importar_csv(contenido, consent_source="importacion_csv")
    assert progreso["estado"] == "completado"
    lista = client.get(f"/api/v1/contacts?q={email}").json()
    assert lista["total"] == 1
    assert lista["items"][0]["status"] == "dado_baja", (
        "Un correo suprimido nunca debe volver como suscripto"
    )


def test_contador_de_contactos_se_actualiza_tras_eliminar(client, admin_auth):
    marca = uuid.uuid4().hex[:8]
    c1 = _crear_contacto(client, f"cnt{marca}")
    c2 = _crear_contacto(client, f"cnt{marca}")

    def total() -> int:
        r = client.get(f"/api/v1/contacts?q=cnt{marca}")
        return r.json()["total"]

    assert total() == 2
    client.post("/api/v1/contacts/bulk-delete",
                json={"ids": [c1["id"]], "modo": "eliminar", "confirmar": True})
    assert total() == 1, "El contador de la tabla debe reflejar la eliminación"
    client.post("/api/v1/contacts/bulk-delete",
                json={"ids": [c2["id"]], "modo": "eliminar", "confirmar": True})
    assert total() == 0


def test_bulk_delete_ids_desconocidos_404_y_confirmacion(client, admin_auth):
    r = client.post("/api/v1/contacts/bulk-delete",
                    json={"ids": ["no-existe"], "modo": "eliminar", "confirmar": True})
    assert r.status_code == 404
    contacto = _crear_contacto(client, "conf")
    r2 = client.post("/api/v1/contacts/bulk-delete",
                     json={"ids": [contacto["id"]], "modo": "eliminar", "confirmar": False})
    assert r2.status_code == 400, "Sin confirmación explícita no se elimina"
    assert client.get(f"/api/v1/contacts/{contacto['id']}").status_code == 200


# --------------------------------------------------------------------------- #
# 4. Eliminación de plantillas: sistema protegido, campañas sin ruptura
# --------------------------------------------------------------------------- #


def test_eliminar_plantilla_sistema_rechazada(client, admin_auth):
    db = SessionLocal()
    try:
        t = Template(name=f"Sistema {uuid.uuid4().hex[:5]}", category="sistema",
                     mode="html", html="<p>Sistema</p>", is_system=True)
        db.add(t)
        db.commit()
        tid = t.id
    finally:
        db.close()

    resp = client.delete(f"/api/v1/templates/{tid}")
    assert resp.status_code == 400
    assert "sistema" in resp.json()["detail"].lower()

    # Sigue existiendo
    assert client.get(f"/api/v1/templates/{tid}").status_code == 200


def test_eliminar_plantilla_con_campana_asociada_sin_ruptura(client, admin_auth):
    """Borrar una plantilla usada por campañas: la referencia pasa a nula,
    el contenido congelado de la campaña se conserva y la API informa."""
    # Plantilla personalizada
    r = client.post("/api/v1/templates", json={
        "name": f"Promo {uuid.uuid4().hex[:5]}", "category": "newsletter",
        "mode": "html", "html": "<p>Contenido plantilla</p>",
    })
    assert r.status_code == 201, r.text
    template_id = r.json()["id"]

    # Campaña que la referencia
    db = SessionLocal()
    try:
        campaign = Campaign(
            name=f"Camp ref {uuid.uuid4().hex[:5]}", subject="Asunto",
            from_name="HJ", from_email="notificaciones@leisureparts4u.store",
            mode="html", html="<p>Contenido congelado</p>", status="borrador",
            template_id=template_id,
        )
        db.add(campaign)
        db.commit()
        campaign_id = campaign.id
    finally:
        db.close()

    resp = client.delete(f"/api/v1/templates/{template_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["campanas_asociadas"] == 1
    assert "campaña" in data["detalle"].lower()

    db = SessionLocal()
    try:
        c = db.get(Campaign, campaign_id)
        assert c is not None, "La campaña no debe eliminarse"
        assert c.template_id is None, "La referencia a la plantilla debe quedar nula"
        assert c.html == "<p>Contenido congelado</p>", (
            "El contenido congelado de la campaña debe conservarse intacto"
        )
        # Versiones de la plantilla eliminadas en cascada
        from app.models import TemplateVersion
        versiones = db.query(TemplateVersion).filter(
            TemplateVersion.template_id == template_id).count()
        assert versiones == 0
        # Auditoría con el conteo de campañas
        audit_reg = (db.query(AuditLog)
                     .filter(AuditLog.action == "plantilla_eliminada")
                     .order_by(AuditLog.created_at.desc()).first())
        assert audit_reg is not None
        assert audit_reg.detail["campanas_asociadas"] == 1
    finally:
        db.close()


def test_eliminar_plantilla_sin_campanas_devuelve_204(client, admin_auth):
    r = client.post("/api/v1/templates", json={
        "name": f"Temporal {uuid.uuid4().hex[:5]}", "category": "newsletter",
        "mode": "bloques",
        "blocks": [{"type": "texto", "props": {"content": "<p>Hola</p>"}}],
    })
    assert r.status_code == 201
    tid = r.json()["id"]
    resp = client.delete(f"/api/v1/templates/{tid}")
    assert resp.status_code == 204
    assert client.get(f"/api/v1/templates/{tid}").status_code == 404


def test_eliminar_plantilla_inexistente_404(client, admin_auth):
    resp = client.delete(f"/api/v1/templates/{uuid.uuid4()}")
    assert resp.status_code == 404
