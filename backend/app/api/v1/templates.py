"""Plantillas de correo: CRUD, versiones, previsualización y envío de pruebas."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import audit
from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import Template, TemplateVersion, User
from app.schemas import (
    PreviewRequest,
    SendTestRequest,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    TemplateVersionOut,
)
from app.services.renderer import (
    build_context,
    blocks_to_html,
    render_campaign_email,
    render_template_string,
    validate_template_source,
)
from app.services.sanitizer import SanitizationError, sanitize_editor_blocks, sanitize_email_html

router = APIRouter(prefix="/templates", tags=["Plantillas"])


def _validate_content(payload) -> None:
    if payload.mode == "html" and payload.html is not None:
        err = validate_template_source(payload.html)
        if err:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Sintaxis de plantilla inválida: {err}")
        if len(payload.html) > 500_000:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "HTML demasiado extenso")
    if payload.blocks is not None:
        if len(payload.blocks) > 200:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Demasiados bloques")
        try:
            sanitize_editor_blocks(payload.blocks)
        except SanitizationError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("", response_model=List[TemplateOut])
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                   categoria: Optional[str] = None):
    query = db.query(Template)
    if categoria:
        query = query.filter(Template.category == categoria)
    return query.order_by(Template.updated_at.desc()).all()


@router.post("", response_model=TemplateOut, status_code=201)
def create_template(payload: TemplateCreate, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _validate_content(payload)
    try:
        blocks = sanitize_editor_blocks(payload.blocks) if payload.blocks else None
        html = sanitize_email_html(payload.html) if (payload.mode == "html" and payload.html) else payload.html
    except SanitizationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    template = Template(
        name=payload.name, description=payload.description, category=payload.category,
        mode=payload.mode, blocks=blocks, html=html,
        created_by=user.id,
    )
    db.add(template)
    db.flush()
    # Versión inicial
    db.add(TemplateVersion(template_id=template.id, version=1, mode=template.mode,
                           blocks=blocks, html=html, created_by=user.id))
    audit(db, "plantilla_creada", user_id=user.id, user_email=user.email,
          entity_type="template", entity_id=template.id,
          detail={"nombre": payload.name}, ip=security.client_ip(request))
    db.commit()
    db.refresh(template)
    return template


@router.get("/variables")
def template_variables():
    from app.services.renderer import known_variables
    return known_variables()


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(template_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")
    return template


@router.put("/{template_id}", response_model=TemplateOut)
def update_template(template_id: str, payload: TemplateUpdate, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")

    _validate_content(payload)
    changed_content = False
    if payload.name is not None:
        template.name = payload.name
    if payload.description is not None:
        template.description = payload.description
    if payload.category is not None:
        template.category = payload.category
    if payload.mode is not None and payload.mode != template.mode:
        template.mode = payload.mode
        changed_content = True
    if payload.blocks is not None:
        template.blocks = sanitize_editor_blocks(payload.blocks)
        changed_content = True
    if payload.html is not None:
        template.html = sanitize_email_html(payload.html)
        changed_content = True

    if changed_content:
        # Guarda versión nueva del contenido (historial)
        template.version += 1
        db.add(TemplateVersion(template_id=template.id, version=template.version,
                               mode=template.mode, blocks=template.blocks, html=template.html,
                               created_by=user.id))
    audit(db, "plantilla_actualizada", user_id=user.id, user_email=user.email,
          entity_type="template", entity_id=template.id,
          detail={"version": template.version}, ip=security.client_ip(request))
    db.commit()
    db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")
    if template.is_system:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No se puede eliminar una plantilla del sistema")
    db.delete(template)
    audit(db, "plantilla_eliminada", user_id=user.id, user_email=user.email,
          entity_type="template", entity_id=template_id, ip=security.client_ip(request))
    db.commit()


@router.get("/{template_id}/versions", response_model=List[TemplateVersionOut])
def template_versions(template_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")
    return template.versions


@router.post("/{template_id}/duplicate", response_model=TemplateOut, status_code=201)
def duplicate_template(template_id: str, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")
    copy = Template(
        name=f"{template.name} (copia)",
        description=template.description, category=template.category,
        mode=template.mode, blocks=template.blocks, html=template.html,
        created_by=user.id,
    )
    db.add(copy)
    db.flush()
    db.add(TemplateVersion(template_id=copy.id, version=1, mode=copy.mode,
                           blocks=copy.blocks, html=copy.html, created_by=user.id))
    db.commit()
    db.refresh(copy)
    return copy


@router.post("/{template_id}/restore/{version_number}", response_model=TemplateOut)
def restore_version(template_id: str, version_number: int, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada")
    version = (
        db.query(TemplateVersion)
        .filter(TemplateVersion.template_id == template_id,
                TemplateVersion.version == version_number)
        .first()
    )
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Versión no encontrada")
    template.mode = version.mode
    template.blocks = version.blocks
    template.html = version.html
    template.version += 1
    db.add(TemplateVersion(template_id=template.id, version=template.version,
                           mode=template.mode, blocks=template.blocks, html=template.html,
                           created_by=user.id))
    audit(db, "plantilla_version_restaurada", user_id=user.id, user_email=user.email,
          entity_type="template", entity_id=template.id,
          detail={"version": version_number}, ip=security.client_ip(request))
    db.commit()
    db.refresh(template)
    return template


# --------------------------------------------------------------------------- #
# Previsualización y pruebas
# --------------------------------------------------------------------------- #


@router.post("/preview")
def preview_template(payload: PreviewRequest, user: User = Depends(get_current_user)):
    """HTML sanitizado para previsualizar en iframe aislado (sandbox)."""
    context = build_context(
        contact={"email": payload.sample_email, "first_name": payload.sample_name},
        campaign={"name": "Campaña de ejemplo", "subject": "Vista previa"},
        unsubscribe_url="https://ejemplo.local/baja?token=VISTA_PREVIA",
    )
    if payload.mode == "html":
        raw = payload.html or ""
        html = inline_email_css(sanitize_email_html(render_template_string(raw, context)))
    else:
        html = blocks_to_html(payload.blocks or [], click_signer=None)
        html = render_template_string(html, context)
    return {"html": html}


@router.post("/test-send")
def send_test(payload: SendTestRequest, request: Request,
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Envío de prueba a una dirección autorizada mediante SES."""
    from app.services import ses as ses_service

    context = build_context(
        contact={"email": payload.to_email, "first_name": "Prueba"},
        campaign={"name": "Prueba", "subject": payload.subject},
        unsubscribe_url="https://ejemplo.local/baja?token=PRUEBA",
    )
    try:
        subject, html_final, plain = render_campaign_email(
            mode=payload.mode, blocks=payload.blocks, html_source=payload.html,
            subject=payload.subject, context=context,
        )
    except SanitizationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    try:
        ses_id = ses_service.send_test_email(
            to_email=payload.to_email, subject=subject, html=html_final, text=plain,
            from_email=payload.from_email, from_name=payload.from_name,
            reply_to=payload.reply_to,
        )
    except ses_service.SESNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except ses_service.SESError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"SES: {exc.detail}")

    audit(db, "envio_prueba", user_id=user.id, user_email=user.email,
          detail={"destinatario": payload.to_email[:3] + "***"},
          ip=security.client_ip(request))
    db.commit()
    return {"detalle": "Prueba enviada", "ses_message_id": ses_id}
